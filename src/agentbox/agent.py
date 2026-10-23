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
    """Real model via the Anthropic API. Needs `pip install anthropic` and
    the ANTHROPIC_API_KEY environment variable."""

    def __init__(self, model: str | None = None, client=None, max_retries: int = 3, retry_backoff_s: float = 0.5):
        self.model = model or os.environ.get("AGENTBOX_MODEL") or "claude-3-5-sonnet-latest"
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        if client is not None:
            self.client = client
        else:
            try:
                import anthropic
                self.client = anthropic.Anthropic()
            except ImportError:
                raise ImportError("The 'anthropic' package is required to use AnthropicModel. Run 'pip install anthropic'.")

    def next_action(self, messages):
        if not messages or messages[0].get("role") != "user":
            raise AnthropicModelError("Message history must start with a user message.")
        for i in range(1, len(messages)):
            if messages[i].get("role") == messages[i - 1].get("role"):
                raise AnthropicModelError(
                    f"Messages must alternate roles: message {i} and {i-1} both have role '{messages[i].get('role')}'."
                )
        if messages[-1].get("role") != "user":
            raise AnthropicModelError("Last message sent to model must have role 'user'.")

        try:
            import anthropic
        except ImportError:
            anthropic = None

        r = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.messages.create(
                    model=self.model,
                    max_tokens=1000,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                )
                break
            except Exception as e:
                if anthropic:
                    if isinstance(e, anthropic.AuthenticationError):
                        raise AnthropicModelError(f"Authentication failure: check your ANTHROPIC_API_KEY. ({e})") from e
                    if isinstance(e, anthropic.PermissionDeniedError):
                        raise AnthropicModelError(f"Permission denied: {e}") from e
                    if isinstance(e, anthropic.NotFoundError):
                        raise AnthropicModelError(f"Model '{self.model}' not found: {e}") from e
                    if isinstance(e, anthropic.BadRequestError):
                        raise AnthropicModelError(f"Bad request to Anthropic API: {e}") from e

                    transient_classes = (
                        anthropic.RateLimitError,
                        anthropic.InternalServerError,
                        getattr(anthropic, "OverloadedError", anthropic.InternalServerError),
                        anthropic.APITimeoutError,
                        anthropic.APIConnectionError,
                    )
                    if isinstance(e, transient_classes):
                        if attempt < self.max_retries:
                            time.sleep(self.retry_backoff_s * (2 ** attempt))
                            continue
                        raise AnthropicModelError(f"API error after {self.max_retries} retries ({type(e).__name__}): {e}") from e
                    if isinstance(e, anthropic.APIError):
                        raise AnthropicModelError(f"Anthropic API error: {e}") from e

                err_name = type(e).__name__
                if any(t in err_name for t in ("RateLimit", "InternalServer", "Overloaded", "Timeout", "Connection")):
                    if attempt < self.max_retries:
                        time.sleep(self.retry_backoff_s * (2 ** attempt))
                        continue
                    raise AnthropicModelError(f"API error after {self.max_retries} retries ({err_name}): {e}") from e

                raise AnthropicModelError(f"API error: {e}") from e

        if r is None:
            raise AnthropicModelError("Failed to obtain response from Anthropic API.")

        if getattr(r, "stop_reason", None) == "max_tokens":
            raise AnthropicModelError("Model response was truncated (reached max_tokens).")

        content = getattr(r, "content", None)
        if not content:
            raise AnthropicModelError("Model returned empty content.")

        block = content[0]
        block_type = getattr(block, "type", "text")
        block_text = getattr(block, "text", None)
        if block_type != "text" or block_text is None:
            raise AnthropicModelError(f"First content block is not text (type={block_type}).")

        return extract_json(block_text)


def _format_observation(obs: dict) -> str:
    return (f"exit_code={obs['exit_code']}\n"
            f"stdout:\n{obs['stdout']['text'][-1500:]}\n"
            f"stderr:\n{obs['stderr']['text'][-1500:]}")


def run_agent(goal, model, recorder, max_steps=15, verbose=True) -> dict:
    messages = [{"role": "user", "content": f"Goal: {goal}"}]
    reason, final = "max_steps", None
    for _ in range(max_steps):
        try:
            action = model.next_action(messages)
        except AnthropicModelError as e:
            if verbose:
                print(f"    !! model error: {e}")
            reason = f"model_error: {e}"
            break

        messages.append({"role": "assistant", "content": json.dumps(action)})
        if action.get("final") is not None:
            reason, final = "final_answer", action["final"]
            break
        cmd = action.get("command")
        if not cmd:
            messages.append({"role": "user", "content": "Reply with a command or a final answer."})
            continue
        result = recorder.run(cmd, thought=action.get("thought", ""))
        obs, signals = result["observation"], result["signals"]
        if verbose:
            print(f"[{recorder.step_index}] {action.get('thought', '')}\n    $ {cmd}  -> exit {obs['exit_code']}")
            for s in signals:
                print(f"    !! {s['severity']}: {s['kind']}")
        critical = [s for s in signals if s["severity"] == "critical"]
        if critical:
            reason = f"terminated: {critical[0]['kind']}"
            break
        text = _format_observation(obs)
        if signals:
            text += "\n\n[agentbox hint] " + " ".join(sorted({s["hint"] for s in signals}))
        messages.append({"role": "user", "content": text})
    return {"reason": reason, "final": final}
