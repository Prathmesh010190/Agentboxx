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
