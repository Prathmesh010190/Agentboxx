"""A minimal ReAct-style agent loop wired to the recorder.

Loop: model proposes a command -> sandbox runs it -> recorder logs it ->
the observation (plus any loop-detector hint) goes back to the model.
"""
import json
import os
import re
import time

SYSTEM_PROMPT = (
    "You are a coding agent working in a sandbox with a bash tool. "
    "Reply with ONLY one JSON object, no other text. "
    'To run a command: {"thought": "why", "command": "bash command"}. '
    'When the goal is done: {"thought": "why", "final": "short answer"}. '
    "If you receive an [agentbox hint], change your approach."
)

# Valid top-level keys that constitute a usable action.
_VALID_ACTIONS = frozenset({"command", "final"})

# Corrective prompt sent back when the model returns an unusable reply.
_CORRECTIVE_PROMPT = (
    '[agentbox] Your last reply was not a valid JSON action. '
    'Reply with ONLY one JSON object. '
    'To run a command: {"thought": "why", "command": "bash command"}. '
    'When the goal is done: {"thought": "why", "final": "short answer"}.'
)


class AnthropicModelError(RuntimeError):
    """Raised when AnthropicModel cannot obtain or parse a valid response."""
    pass


ModelError = AnthropicModelError


def extract_json(text: str) -> dict:
    """Extract a JSON object from model reply text.

    Handles:
    - Pure JSON strings
    - Markdown fenced code blocks (```json ... ``` or ``` ... ```)
    - JSON objects embedded within surrounding prose
    """
    text = text.strip()
    if not text:
        raise AnthropicModelError("Model returned empty text.")

    # 1. Direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 2. Markdown fenced code blocks
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    for match in fence_pattern.finditer(text):
        content = match.group(1).strip()
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3. Locate JSON object in surrounding prose using JSONDecoder.raw_decode
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        pos = text.find("{", pos)
        if pos == -1:
            break
        try:
            data, _ = decoder.raw_decode(text[pos:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        pos += 1

    raise AnthropicModelError(f"Unparseable reply: no valid JSON object found in response: {text[:500]}")


def _validate_action(action: dict) -> str | None:
    """Return a human-readable reason if *action* is unusable, else None."""
    if "command" not in action and "final" not in action:
        return "reply is missing 'command' or 'final'"
    if "command" in action and not isinstance(action["command"], str):
        return f"'command' must be a string, got {type(action['command']).__name__}"
    if "final" in action and not isinstance(action["final"], str):
        return f"'final' must be a string, got {type(action['final']).__name__}"
    # Unknown top-level action key (neither command nor final)
    extra = set(action.keys()) - {"thought", "command", "final"}
    if extra and "command" not in action and "final" not in action:
        return f"unknown action keys: {sorted(extra)}"
    return None


class ScriptedModel:
    """A fake model for demos and tests. Retries a failing command, then
    changes approach once it receives an [agentbox hint]."""

    def __init__(self):
        self.plan = [
            ("Create a small script.", "printf 'x = 0\\nprint(10 / x)\\n' > main.py"),
            ("Run it.", "python3 main.py"),
        ]
        self.recovery = [
            ("The hint is right: x = 0 causes the crash. Fix the value.", "sed -i 's/x = 0/x = 2/' main.py"),
            ("Run it again.", "python3 main.py"),
            ("DONE", None),
        ]
        self.i, self.recovering = 0, False

    def next_action(self, messages):
        if not self.recovering and "[agentbox hint]" in messages[-1]["content"]:
            self.recovering, self.i = True, 0
        steps = self.recovery if self.recovering else self.plan
        thought, cmd = steps[min(self.i, len(steps) - 1)]
        self.i += 1
        if cmd is None:
            return {"thought": "The script runs now.", "final": "Fixed the divide-by-zero; script prints 5.0."}
        return {"thought": thought, "command": cmd}


class AnthropicModel:
    """Real model via the Anthropic API. Needs ``pip install anthropic`` and
    the ANTHROPIC_API_KEY environment variable.

    Parameters
    ----------
    model:
        Anthropic model name. Falls back to the AGENTBOX_MODEL env var, then
        ``claude-sonnet-5-5``.
    client:
        Inject a pre-built (or fake) client for testing. When *None*, a real
        ``anthropic.Anthropic`` client is created with ``max_retries=0`` so
        that all retry logic lives here.
    max_retries:
        Number of retry attempts for transient API errors (429, 529, 5xx,
        timeouts, connection errors).
    retry_backoff_s:
        Base backoff in seconds between retries. Actual delay is
        ``retry_backoff_s * 2**attempt``.
    sleep_fn:
        Callable used for sleeping between retries. Defaults to
        ``time.sleep``. Pass a no-op for tests so they run instantly.
    invalid_reply_retries:
        How many times to send a corrective observation back to the model
        before giving up with ``invalid_reply`` as the stop reason.
    """

    DEFAULT_MODEL = "claude-sonnet-5-5"

    def __init__(
        self,
        model: str | None = None,
        client=None,
        max_retries: int = 3,
        retry_backoff_s: float = 0.5,
        sleep_fn=None,
        invalid_reply_retries: int = 2,
    ):
        self.model = model or os.environ.get("AGENTBOX_MODEL") or self.DEFAULT_MODEL
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self.invalid_reply_retries = invalid_reply_retries

        if client is not None:
            self.client = client
        else:
            # Import lazily so the package works without ``anthropic`` installed.
            try:
                import anthropic as _anthropic
                # max_retries=0: we own all retry logic; do NOT stack SDK retries.
                self.client = _anthropic.Anthropic(max_retries=0)
            except ImportError:
                raise ImportError(
                    "The 'anthropic' package is required to use AnthropicModel. "
                    "Run 'pip install anthropic'."
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_api(self, messages: list) -> object:
        """Call the Anthropic API with retry logic for transient errors.

        Error messages intentionally omit the API key and request headers.
        """
        # Lazy import so tests don't need the package installed.
        try:
            import anthropic as _anthropic
        except ImportError:
            _anthropic = None

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=1000,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                )
            except Exception as e:
                last_exc = e

                if _anthropic is not None:
                    # ---- Fast-fail on 4xx that we cannot recover from ----
                    if isinstance(e, _anthropic.AuthenticationError):
                        raise AnthropicModelError(
                            "Authentication failure: check your ANTHROPIC_API_KEY."
                        ) from e
                    if isinstance(e, _anthropic.PermissionDeniedError):
                        raise AnthropicModelError("Permission denied by Anthropic API.") from e
                    if isinstance(e, _anthropic.NotFoundError):
                        raise AnthropicModelError(
                            f"Model '{self.model}' not found (404)."
                        ) from e
                    if isinstance(e, _anthropic.BadRequestError):
                        raise AnthropicModelError(
                            "Bad request to Anthropic API (400)."
                        ) from e

                    # ---- Transient errors: retry with backoff ----
                    transient_classes = (
                        _anthropic.RateLimitError,          # 429
                        _anthropic.InternalServerError,     # 5xx (incl. 529)
                        _anthropic.APITimeoutError,
                        _anthropic.APIConnectionError,
                    )
                    # Some SDK versions expose OverloadedError separately.
                    overloaded = getattr(_anthropic, "OverloadedError", None)
                    if overloaded is not None:
                        transient_classes = transient_classes + (overloaded,)

                    if isinstance(e, transient_classes):
                        if attempt < self.max_retries:
                            self._sleep(self.retry_backoff_s * (2 ** attempt))
                            continue
                        raise AnthropicModelError(
                            f"API error after {self.max_retries} retries "
                            f"({type(e).__name__})."
                        ) from e

                    # Any other SDK error we didn't classify.
                    if isinstance(e, _anthropic.APIError):
                        raise AnthropicModelError(
                            f"Anthropic API error ({type(e).__name__})."
                        ) from e

                # ---- Fallback: heuristic on exception class name ----
                err_name = type(e).__name__
                # Fast-fail on auth/permission errors (no retry).
                if any(t in err_name for t in ("Authentication", "PermissionDenied", "NotFound", "BadRequest")):
                    raise AnthropicModelError(
                        f"Authentication failure: check your ANTHROPIC_API_KEY."
                        if "Authentication" in err_name
                        else f"Non-retryable API error ({err_name})."
                    ) from e
                if any(t in err_name for t in (
                    "RateLimit", "InternalServer", "Overloaded", "Timeout", "Connection"
                )):
                    if attempt < self.max_retries:
                        self._sleep(self.retry_backoff_s * (2 ** attempt))
                        continue
                    raise AnthropicModelError(
                        f"API error after {self.max_retries} retries ({err_name})."
                    ) from e

                raise AnthropicModelError(
                    f"API error ({type(e).__name__})."
                ) from e

        # Should be unreachable, but guard anyway.
        raise AnthropicModelError("Failed to obtain a response from the Anthropic API.") from last_exc

    def _parse_response(self, r) -> dict:
        """Turn a raw API response into a parsed action dict.

        Raises AnthropicModelError for: truncated reply, empty content,
        non-text first block, whitespace-only text, or unparseable JSON.
        Truncated replies are NEVER executed.
        """
        if getattr(r, "stop_reason", None) == "max_tokens":
            raise AnthropicModelError(
                "Model response was truncated (reached max_tokens limit). "
                "The partial reply will not be executed."
            )

        content = getattr(r, "content", None)
        if not content:
            raise AnthropicModelError("Model returned empty content.")

        block = content[0]
        block_type = getattr(block, "type", "text")
        block_text = getattr(block, "text", None)
        if block_type != "text" or block_text is None:
            raise AnthropicModelError(
                f"First content block is not text (type={block_type})."
            )
        if not block_text.strip():
            raise AnthropicModelError("Model returned empty text in the first content block.")

        return extract_json(block_text)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def next_action(self, messages: list) -> dict:
        """Return the next action dict for the given message history.

        Validates that history starts with a user message and roles alternate.
        """
        if not messages or messages[0].get("role") != "user":
            raise AnthropicModelError("Message history must start with a user message.")
        for i in range(1, len(messages)):
            if messages[i].get("role") == messages[i - 1].get("role"):
                raise AnthropicModelError(
                    f"Messages must alternate roles: message {i} and {i-1} "
                    f"both have role '{messages[i].get('role')}'."
                )
        if messages[-1].get("role") != "user":
            raise AnthropicModelError(
                "Last message sent to model must have role 'user'."
            )

        r = self._call_api(messages)
        return self._parse_response(r)


def _format_observation(obs: dict) -> str:
    return (f"exit_code={obs['exit_code']}\n"
            f"stdout:\n{obs['stdout']['text'][-1500:]}\n"
            f"stderr:\n{obs['stderr']['text'][-1500:]}")


def _append_user(messages: list, text: str) -> None:
    """Append a user message, merging with the previous one if needed.

    Requirement: roles must alternate. If the last message is already a user
    message (which can happen when we need to send a corrective observation
    after an observation was just appended), merge both texts into one.
    """
    if messages and messages[-1]["role"] == "user":
        messages[-1] = {
            "role": "user",
            "content": messages[-1]["content"] + "\n\n" + text,
        }
    else:
        messages.append({"role": "user", "content": text})


def run_agent(goal, model, recorder, max_steps=15, verbose=True) -> dict:
    messages = [{"role": "user", "content": f"Goal: {goal}"}]
    reason, final = "max_steps", None

    for _ in range(max_steps):
        # ---- Try to get a valid action, with invalid-reply retries ----
        action = None
        invalid_tries = 0
        max_invalid = getattr(model, "invalid_reply_retries", 2)

        while True:
            try:
                raw_action = model.next_action(messages)
            except AnthropicModelError as e:
                if verbose:
                    print(f"    !! model error: {e}")
                reason = f"model_error: {e}"
                return {"reason": reason, "final": None}

            # Validate the shape of the action.
            bad = _validate_action(raw_action)
            if bad is None:
                action = raw_action
                break

            # Unusable reply — send corrective observation.
            invalid_tries += 1
            if verbose:
                print(f"    !! invalid reply ({bad}); sending corrective prompt "
                      f"(attempt {invalid_tries}/{max_invalid})")

            # The model just replied (assistant turn). Append its reply so the
            # history stays well-formed, then append the corrective user prompt.
            messages.append({"role": "assistant", "content": json.dumps(raw_action)})
            _append_user(messages, _CORRECTIVE_PROMPT)

            if invalid_tries >= max_invalid:
                reason = "invalid_reply"
                # Write the stop reason to the signed log if recorder supports it.
                if hasattr(recorder, "writer") and hasattr(recorder.writer, "append"):
                    recorder.writer.append(
                        "session.invalid_reply",
                        {"reason": reason, "last_bad_reply": bad},
                    )
                if verbose:
                    print(f"    !! giving up after {max_invalid} invalid replies; "
                          f"stop reason: {reason}")
                return {"reason": reason, "final": None}

        # ---- action is valid ----
        messages.append({"role": "assistant", "content": json.dumps(action)})

        if action.get("final") is not None:
            reason, final = "final_answer", action["final"]
            break

        cmd = action.get("command")
        if not cmd:
            _append_user(messages, "Reply with a command or a final answer.")
            continue

        result = recorder.run(cmd, thought=action.get("thought", ""))
        obs, signals = result["observation"], result["signals"]
        if verbose:
            print(f"[{recorder.step_index}] {action.get('thought', '')}\n"
                  f"    $ {cmd}  -> exit {obs['exit_code']}")
            for s in signals:
                print(f"    !! {s['severity']}: {s['kind']}")

        critical = [s for s in signals if s["severity"] == "critical"]
        if critical:
            reason = f"terminated: {critical[0]['kind']}"
            break

        text = _format_observation(obs)
        if signals:
            text += "\n\n[agentbox hint] " + " ".join(sorted({s["hint"] for s in signals}))

        # Use _append_user so that an observation + hint never creates two
        # consecutive user messages (which would violate alternation rules).
        _append_user(messages, text)

    return {"reason": reason, "final": final}
