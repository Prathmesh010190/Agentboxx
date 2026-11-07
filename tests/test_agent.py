from agentbox import Recorder, verify_file
from agentbox.agent import ScriptedModel, run_agent
from agentbox.signer import load_or_create_key


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

