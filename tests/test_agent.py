from agentbox import Recorder, verify_file
from agentbox.agent import ScriptedModel, run_agent
from agentbox.signer import load_or_create_key
import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch
import pytest
from agentbox.agent import GeminiModel, _get_platform_py, SYSTEM_PROMPT

def test_agent_gets_hint_and_recovers(tmp_path):
    key = tmp_path / "key"
    rec = Recorder("t", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"), key_path=key)
    out = run_agent("fix main.py", ScriptedModel(), rec, verbose=False)
    rec.close("done")
    assert out["reason"] == "final_answer"
    log = rec.log_path.read_text()
    assert "loop.exact" in log
    assert verify_file(rec.log_path, load_or_create_key(key)) == []

def test_scripted_model_custom_python_cmd():
    m = ScriptedModel(python_cmd="my_python")
    assert any("my_python main.py" in step[1] for step in m.plan if step[1])
    assert any("my_python main.py" in step[1] for step in m.recovery if step[1])


def test_scripted_model_platform_defaults(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    m_win = ScriptedModel()
    assert any("python main.py" in step[1] for step in m_win.plan if step[1])

    monkeypatch.setattr(sys, "platform", "linux")
    m_lin = ScriptedModel()
    assert any("python3 main.py" in step[1] for step in m_lin.plan if step[1])




def test_platform_py_hint():
    py = _get_platform_py()
    assert py in ("python", "python3")
    assert f"'{py}'" in SYSTEM_PROMPT


def test_gemini_thinking_tokens():
    """Test that Gemini 2.5/3.5 thinking tokens in candidate parts are handled safely."""
    mock_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "Internal reasoning step..."},
                        {"text": '```json\n{"thought": "Run command", "command": "python script.py"}\n```'}
                    ]
                }
            }
        ]
    }
    
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
        model = GeminiModel()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        
        with patch("urllib.request.urlopen", return_value=mock_resp):
            action = model.next_action([{"role": "user", "content": "hi"}])
            assert action["command"] == "python script.py"
            assert action["thought"] == "Run command"


def test_gemini_http_error_decoding():
    """Test that HTTP errors from Google API decode the JSON error message."""
    err_body = json.dumps({"error": {"message": "Model not available to new users"}}).encode("utf-8")
    http_err = urllib.error.HTTPError(
        url="http://fake", code=404, msg="Not Found", hdrs={}, fp=BytesIO(err_body)
    )
    
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
        model = GeminiModel()
        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(RuntimeError) as exc_info:
                model.next_action([{"role": "user", "content": "hi"}])
            assert "Model not available to new users" in str(exc_info.value)
