"""Tests for AnthropicModel and run_agent.

All tests use a fake client — no network calls, no API key required.
"""
import json
import os
import pytest

from agentbox.agent import (
    AnthropicModel,
    AnthropicModelError,
    run_agent,
)


# ---------------------------------------------------------------------------
# Fake SDK types
# ---------------------------------------------------------------------------

class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeNonTextBlock:
    def __init__(self):
        self.type = "image"
        self.text = None


class FakeResponse:
    def __init__(self, content, stop_reason: str = "end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessagesEndpoint:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("No more fake responses configured.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = FakeMessagesEndpoint(responses)


# Fake SDK exception types — mirrors the real SDK hierarchy without importing it.

class _FakeAPIError(Exception):
    pass

class _FakeAuthenticationError(_FakeAPIError):
    pass

class _FakeRateLimitError(_FakeAPIError):
    pass

class _FakeInternalServerError(_FakeAPIError):
    pass

class _FakeAPITimeoutError(_FakeAPIError):
    pass

class _FakeAPIConnectionError(_FakeAPIError):
    pass


def _user_message():
    return [{"role": "user", "content": "Write fib.py and run it."}]


# ---------------------------------------------------------------------------
# Helper: build a model with an instant sleep so retries don't slow tests
# ---------------------------------------------------------------------------

def _model(responses, *, max_retries=3, invalid_reply_retries=2):
    client = FakeAnthropicClient(responses)
    return AnthropicModel(
        client=client,
        max_retries=max_retries,
        retry_backoff_s=0.0,
        sleep_fn=lambda _: None,   # instant — no real sleep
        invalid_reply_retries=invalid_reply_retries,
    )


# ===========================================================================
# 1. Valid JSON — command
# ===========================================================================

def test_valid_json_command():
    resp = FakeResponse([FakeTextBlock('{"thought": "write fib script", "command": "cat << \'EOF\' > fib.py"}')])
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    action = model.next_action(_user_message())
    assert action == {"thought": "write fib script", "command": "cat << 'EOF' > fib.py"}
    assert len(client.messages.calls) == 1
    # Default model must be claude-sonnet-5-5 (Req #3)
    assert client.messages.calls[0]["model"] == "claude-sonnet-5-5"


# ===========================================================================
# 2. Valid JSON — final answer
# ===========================================================================

def test_valid_json_final_answer():
    resp = FakeResponse([FakeTextBlock('{"thought": "completed task", "final": "The script printed 0, 1, 1, 2, 3, 5."}')])
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    action = model.next_action(_user_message())
    assert action == {"thought": "completed task", "final": "The script printed 0, 1, 1, 2, 3, 5."}


# ===========================================================================
# 3. Fenced JSON
# ===========================================================================

def test_fenced_json():
    # Test ```json fence
    fenced_with_tag = FakeResponse([FakeTextBlock("```json\n{\"thought\": \"fenced\", \"command\": \"ls -l\"}\n```")])
    client = FakeAnthropicClient([fenced_with_tag])
    model = AnthropicModel(client=client)
    action = model.next_action(_user_message())
    assert action == {"thought": "fenced", "command": "ls -l"}

    # Test ``` fence without tag
    fenced_no_tag = FakeResponse([FakeTextBlock("```\n{\"thought\": \"no tag\", \"final\": \"done\"}\n```")])
    client2 = FakeAnthropicClient([fenced_no_tag])
    model2 = AnthropicModel(client=client2)
    action2 = model2.next_action(_user_message())
    assert action2 == {"thought": "no tag", "final": "done"}


# ===========================================================================
# 4. JSON with surrounding prose
# ===========================================================================

def test_json_with_surrounding_prose():
    # Prose before and after fenced block
    text1 = (
        "I will now create the Fibonacci script.\n"
        "```json\n"
        "{\"thought\": \"generating code\", \"command\": \"python3 fib.py\"}\n"
        "```\n"
        "This will execute the solution."
    )
    client1 = FakeAnthropicClient([FakeResponse([FakeTextBlock(text1)])])
    model1 = AnthropicModel(client=client1)
    action1 = model1.next_action(_user_message())
    assert action1 == {"thought": "generating code", "command": "python3 fib.py"}

    # Prose before and after raw JSON object
    text2 = "Here is my proposed command: {\"thought\": \"inline\", \"command\": \"echo 'hi'\"} which should print hi."
    client2 = FakeAnthropicClient([FakeResponse([FakeTextBlock(text2)])])
    model2 = AnthropicModel(client=client2)
    action2 = model2.next_action(_user_message())
    assert action2 == {"thought": "inline", "command": "echo 'hi'"}


# ===========================================================================
# 5. Invalid JSON
# ===========================================================================

def test_invalid_json():
    client = FakeAnthropicClient([FakeResponse([FakeTextBlock("I cannot run bash commands or format JSON.")])])
    model = AnthropicModel(client=client)

    with pytest.raises(AnthropicModelError, match="Unparseable reply"):
        model.next_action(_user_message())

    # Malformed JSON
    client_broken = FakeAnthropicClient([FakeResponse([FakeTextBlock("```json\n{ thought: broken\n```")])])
    model_broken = AnthropicModel(client=client_broken)
    with pytest.raises(AnthropicModelError, match="Unparseable reply"):
        model_broken.next_action(_user_message())


# ===========================================================================
# 6. Empty content / non-text block / whitespace text
# ===========================================================================

def test_empty_content():
    # Empty list
    client1 = FakeAnthropicClient([FakeResponse([])])
    model1 = AnthropicModel(client=client1)
    with pytest.raises(AnthropicModelError, match="empty content"):
        model1.next_action(_user_message())

    # Non-text block
    client2 = FakeAnthropicClient([FakeResponse([FakeNonTextBlock()])])
    model2 = AnthropicModel(client=client2)
    with pytest.raises(AnthropicModelError, match="not text"):
        model2.next_action(_user_message())

    # Text block with whitespace-only string
    client3 = FakeAnthropicClient([FakeResponse([FakeTextBlock("   ")])])
    model3 = AnthropicModel(client=client3)
    with pytest.raises(AnthropicModelError, match="empty text"):
        model3.next_action(_user_message())


# ===========================================================================
# 7. Truncated reply must never be executed
# ===========================================================================

def test_truncated_reply():
    resp = FakeResponse(
        [FakeTextBlock('{"thought": "writing a very long script", "command": "cat << \'EOF\' > script.py\n# code...')],
        stop_reason="max_tokens",
    )
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    with pytest.raises(AnthropicModelError, match="truncated"):
        model.next_action(_user_message())


# ===========================================================================
# 8. Transient API error followed by a successful retry
# ===========================================================================

def test_api_error_followed_by_successful_retry():
    rate_err = _FakeRateLimitError("Rate limit reached")
    success_resp = FakeResponse([FakeTextBlock('{"thought": "retry succeeded", "command": "ls"}')])
    client = FakeAnthropicClient([rate_err, success_resp])
    model = AnthropicModel(client=client, max_retries=3, retry_backoff_s=0.0, sleep_fn=lambda _: None)

    action = model.next_action(_user_message())
    assert action == {"thought": "retry succeeded", "command": "ls"}
    assert len(client.messages.calls) == 2


# ===========================================================================
# 9. Retries exhausted — give up with a clear message
# ===========================================================================

def test_api_error_retries_exhausted():
    err = _FakeInternalServerError("Server error")
    client = FakeAnthropicClient([err, err, err, err])
    model = AnthropicModel(client=client, max_retries=2, retry_backoff_s=0.0, sleep_fn=lambda _: None)

    with pytest.raises(AnthropicModelError, match="retries"):
        model.next_action(_user_message())
    assert len(client.messages.calls) == 3  # initial + 2 retries


# ===========================================================================
# 10. Auth error must fail fast — no retry
# ===========================================================================

def test_api_authentication_error_no_retry():
    auth_err = _FakeAuthenticationError("Invalid API key")
    client = FakeAnthropicClient([auth_err])
    model = AnthropicModel(client=client, max_retries=3, retry_backoff_s=0.0, sleep_fn=lambda _: None)

    with pytest.raises(AnthropicModelError, match="Authentication failure"):
        model.next_action(_user_message())
    assert len(client.messages.calls) == 1


# ===========================================================================
# 11. API key must NEVER appear in error messages (Req #7)
# ===========================================================================

def test_api_key_never_in_error_messages(monkeypatch):
    """Even if ANTHROPIC_API_KEY is set, errors must not echo it."""
    fake_key = "sk-ant-super-secret-key-1234567890"
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_key)

    auth_err = _FakeAuthenticationError(f"Invalid key: {fake_key}")
    client = FakeAnthropicClient([auth_err])
    model = AnthropicModel(client=client, max_retries=0, sleep_fn=lambda _: None)

    with pytest.raises(AnthropicModelError) as exc_info:
        model.next_action(_user_message())

    # The raised message must not contain the raw key.
    assert fake_key not in str(exc_info.value), (
        f"API key appeared in error message: {exc_info.value}"
    )


# ===========================================================================
# 12. Model configuration: constructor arg, env var, default
# ===========================================================================

def test_model_configuration(monkeypatch):
    # Constructor argument overrides everything
    client1 = FakeAnthropicClient([FakeResponse([FakeTextBlock('{"thought": "t", "command": "c"}')])])
    model1 = AnthropicModel(model="claude-3-7-sonnet-latest", client=client1)
    model1.next_action(_user_message())
    assert client1.messages.calls[0]["model"] == "claude-3-7-sonnet-latest"

    # AGENTBOX_MODEL env var is used when no constructor arg
    monkeypatch.setenv("AGENTBOX_MODEL", "claude-custom-env")
    client2 = FakeAnthropicClient([FakeResponse([FakeTextBlock('{"thought": "t", "command": "c"}')])])
    model2 = AnthropicModel(client=client2)
    model2.next_action(_user_message())
    assert client2.messages.calls[0]["model"] == "claude-custom-env"

    # Without env var, the default is claude-sonnet-5-5 (Req #3)
    monkeypatch.delenv("AGENTBOX_MODEL", raising=False)
    client3 = FakeAnthropicClient([FakeResponse([FakeTextBlock('{"thought": "t", "command": "c"}')])])
    model3 = AnthropicModel(client=client3)
    model3.next_action(_user_message())
    assert client3.messages.calls[0]["model"] == AnthropicModel.DEFAULT_MODEL == "claude-sonnet-5-5"


# ===========================================================================
# 13. Role rules: starts with user, alternates
# ===========================================================================

def test_message_role_rules_validation():
    client = FakeAnthropicClient([])
    model = AnthropicModel(client=client)

    # Must start with user
    with pytest.raises(AnthropicModelError, match="start with a user message"):
        model.next_action([{"role": "assistant", "content": "hello"}])

    # Must alternate roles
    with pytest.raises(AnthropicModelError, match="alternate roles"):
        model.next_action([
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "hi again"},
        ])

    # Must end with user
    with pytest.raises(AnthropicModelError, match="Last message sent to model must have role 'user'"):
        model.next_action([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])


# ===========================================================================
# 14. Wrong shape: missing command, non-string command, unknown action
# ===========================================================================

def test_wrong_shape_missing_command_or_final(tmp_path):
    """A reply with neither 'command' nor 'final' is unusable."""
    from agentbox import Recorder

    # Model keeps returning a bad shape — no 'command' or 'final'
    bad_reply = '{"thought": "thinking"}'
    responses = [FakeResponse([FakeTextBlock(bad_reply)])] * 4

    client = FakeAnthropicClient(responses)
    model = AnthropicModel(
        client=client,
        sleep_fn=lambda _: None,
        invalid_reply_retries=2,
    )
    rec = Recorder(
        "t-missing-cmd",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
    )
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    assert out["reason"] == "invalid_reply"
    assert out["final"] is None


def test_wrong_shape_non_string_command(tmp_path):
    """A reply where 'command' is not a string is unusable."""
    from agentbox import Recorder

    bad_reply = '{"thought": "t", "command": 42}'
    responses = [FakeResponse([FakeTextBlock(bad_reply)])] * 4

    client = FakeAnthropicClient(responses)
    model = AnthropicModel(
        client=client,
        sleep_fn=lambda _: None,
        invalid_reply_retries=2,
    )
    rec = Recorder(
        "t-non-str-cmd",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
    )
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    assert out["reason"] == "invalid_reply"
    assert out["final"] is None


# ===========================================================================
# 15. invalid_reply stop reason is written to the signed log (Req #12)
# ===========================================================================

def test_invalid_reply_stop_written_to_log(tmp_path):
    """When the model repeatedly returns unusable replies, the signed log
    must contain a 'session.invalid_reply' record so it's tamper-evidently
    documented."""
    from agentbox import Recorder, verify_file
    from agentbox.signer import load_or_create_key

    bad_reply = '{"thought": "just thinking, no command"}'
    responses = [FakeResponse([FakeTextBlock(bad_reply)])] * 5
    key = tmp_path / "key"

    client = FakeAnthropicClient(responses)
    model = AnthropicModel(
        client=client,
        sleep_fn=lambda _: None,
        invalid_reply_retries=2,
    )
    rec = Recorder(
        "t-log-check",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        key_path=key,
    )
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    assert out["reason"] == "invalid_reply"

    # The log must contain the invalid_reply record and still be integrity-valid.
    log_text = rec.log_path.read_text()
    assert "invalid_reply" in log_text
    errors = verify_file(rec.log_path, load_or_create_key(key))
    assert errors == [], f"Log integrity check failed: {errors}"


# ===========================================================================
# 16. Unparseable reply stops the run clearly (no silent final answer)
# ===========================================================================

def test_run_agent_reports_unparseable_reply_clearly(tmp_path):
    from agentbox import Recorder

    client = FakeAnthropicClient([FakeResponse([FakeTextBlock("I cannot speak JSON at all.")])])
    model = AnthropicModel(client=client)
    rec = Recorder("test-unparseable", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"))
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    # Must NOT be silently treated as a successful final answer
    assert out["reason"].startswith("model_error:")
    assert out["final"] is None


# ===========================================================================
# 17. Merged observation + corrective hint (Req #14)
#     Two consecutive user messages must be merged into one.
# ===========================================================================

def test_merged_observation_and_corrective_hint(tmp_path):
    """When an invalid reply follows an observation, the corrective prompt
    must be merged into the same user message (no two consecutive user roles)."""
    from agentbox import Recorder

    # First response: invalid (no command/final). Second: valid.
    bad = FakeResponse([FakeTextBlock('{"thought": "hmm"}')])
    good = FakeResponse([FakeTextBlock('{"thought": "done", "final": "all good"}')])

    client = FakeAnthropicClient([bad, good])
    model = AnthropicModel(
        client=client,
        sleep_fn=lambda _: None,
        invalid_reply_retries=2,
    )
    rec = Recorder(
        "t-merge",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
    )
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    assert out["reason"] == "final_answer"
    assert out["final"] == "all good"

    # Inspect the message history that was actually sent.
    # After the bad reply, there must be no two consecutive user messages.
    calls = client.messages.calls
    for call in calls:
        msgs = call["messages"]
        for i in range(1, len(msgs)):
            assert msgs[i]["role"] != msgs[i - 1]["role"], (
                f"Consecutive user messages found at index {i-1}/{i}: {msgs}"
            )


# ===========================================================================
# 18. Sleep is injectable (instant in tests, Req #4)
# ===========================================================================

def test_sleep_fn_is_called_between_retries():
    """Injectable sleep_fn must be invoked for each retry."""
    sleep_calls = []
    err = _FakeRateLimitError("Rate limit")
    success = FakeResponse([FakeTextBlock('{"command": "ls"}')])

    client = FakeAnthropicClient([err, err, success])
    model = AnthropicModel(
        client=client,
        max_retries=3,
        retry_backoff_s=1.0,
        sleep_fn=lambda s: sleep_calls.append(s),
    )
    action = model.next_action(_user_message())
    assert action["command"] == "ls"
    # Two errors → two sleeps
    assert len(sleep_calls) == 2
    # Exponential backoff: 1.0 * 2^0, 1.0 * 2^1
    assert sleep_calls[0] == pytest.approx(1.0)
    assert sleep_calls[1] == pytest.approx(2.0)
