import os
import pytest

from agentbox.agent import AnthropicModel, AnthropicModelError, run_agent


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


def _user_message():
    return [{"role": "user", "content": "Write fib.py and run it."}]


def test_valid_json_command():
    resp = FakeResponse([FakeTextBlock('{"thought": "write fib script", "command": "cat << \'EOF\' > fib.py"}')])
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    action = model.next_action(_user_message())
    assert action == {"thought": "write fib script", "command": "cat << 'EOF' > fib.py"}
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0]["model"] == "claude-3-5-sonnet-latest"


def test_valid_json_final_answer():
    resp = FakeResponse([FakeTextBlock('{"thought": "completed task", "final": "The script printed 0, 1, 1, 2, 3, 5."}')])
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    action = model.next_action(_user_message())
    assert action == {"thought": "completed task", "final": "The script printed 0, 1, 1, 2, 3, 5."}


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

    # Text block with empty string
    client3 = FakeAnthropicClient([FakeResponse([FakeTextBlock("   ")])])
    model3 = AnthropicModel(client=client3)
    with pytest.raises(AnthropicModelError, match="empty text"):
        model3.next_action(_user_message())


def test_truncated_reply():
    resp = FakeResponse(
        [FakeTextBlock('{"thought": "writing a very long script", "command": "cat << \'EOF\' > script.py\n# code...')],
        stop_reason="max_tokens",
    )
    client = FakeAnthropicClient([resp])
    model = AnthropicModel(client=client)

    with pytest.raises(AnthropicModelError, match="truncated"):
        model.next_action(_user_message())


def test_api_error_followed_by_successful_retry():
    try:
        import anthropic
        import httpx2
        req = httpx2.Request("POST", "https://api.anthropic.com")
        resp429 = httpx2.Response(429, request=req)
        rate_err = anthropic.RateLimitError("Rate limit reached", response=resp429, body=None)
    except Exception:
        class RateLimitError(Exception):
            pass
        rate_err = RateLimitError("Rate limit reached")

    success_resp = FakeResponse([FakeTextBlock('{"thought": "retry succeeded", "command": "ls"}')])
    client = FakeAnthropicClient([rate_err, success_resp])
    model = AnthropicModel(client=client, max_retries=3, retry_backoff_s=0.001)

    action = model.next_action(_user_message())
    assert action == {"thought": "retry succeeded", "command": "ls"}
    assert len(client.messages.calls) == 2


def test_api_error_retries_exhausted():
    try:
        import anthropic
        import httpx2
        req = httpx2.Request("POST", "https://api.anthropic.com")
        resp500 = httpx2.Response(500, request=req)
        err = anthropic.InternalServerError("Server error", response=resp500, body=None)
    except Exception:
        class InternalServerError(Exception):
            pass
        err = InternalServerError("Server error")

    client = FakeAnthropicClient([err, err, err, err])
    model = AnthropicModel(client=client, max_retries=2, retry_backoff_s=0.001)

    with pytest.raises(AnthropicModelError, match="retries"):
        model.next_action(_user_message())
    assert len(client.messages.calls) == 3


def test_api_authentication_error_no_retry():
    try:
        import anthropic
        import httpx2
        req = httpx2.Request("POST", "https://api.anthropic.com")
        resp401 = httpx2.Response(401, request=req)
        auth_err = anthropic.AuthenticationError("Invalid API key", response=resp401, body=None)
    except Exception:
        class AuthenticationError(Exception):
            pass
        auth_err = AuthenticationError("Invalid API key")

    client = FakeAnthropicClient([auth_err])
    model = AnthropicModel(client=client, max_retries=3, retry_backoff_s=0.001)

    with pytest.raises(AnthropicModelError, match="Authentication failure"):
        model.next_action(_user_message())
    assert len(client.messages.calls) == 1


def test_model_configuration(monkeypatch):
    # Constructor argument
    client1 = FakeAnthropicClient([FakeResponse([FakeTextBlock('{"thought": "t", "command": "c"}')])])
    model1 = AnthropicModel(model="claude-3-7-sonnet-latest", client=client1)
    model1.next_action(_user_message())
    assert client1.messages.calls[0]["model"] == "claude-3-7-sonnet-latest"

    # AGENTBOX_MODEL env var
    monkeypatch.setenv("AGENTBOX_MODEL", "claude-custom-env")
    client2 = FakeAnthropicClient([FakeResponse([FakeTextBlock('{"thought": "t", "command": "c"}')])])
    model2 = AnthropicModel(client=client2)
    model2.next_action(_user_message())
    assert client2.messages.calls[0]["model"] == "claude-custom-env"


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


def test_run_agent_reports_unparseable_reply_clearly(tmp_path):
    from agentbox import Recorder

    client = FakeAnthropicClient([FakeResponse([FakeTextBlock("I cannot speak JSON at all.")])])
    model = AnthropicModel(client=client)
    rec = Recorder("test-unparseable", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"))
    try:
        out = run_agent("test goal", model, rec, verbose=False)
    finally:
        rec.close("done")

    # Crucially, it must NOT be silently treated as a successful final answer
    assert out["reason"].startswith("model_error:")
    assert out["final"] is None
