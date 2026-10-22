from agentbox.loop_detector import LoopDetector


def step(cmd="x", err="E:1", ws="a", code=1):
    return {"command_fingerprint": cmd, "error_signature": err, "workspace_hash": ws, "exit_code": code}


def test_exact_repeat_flags():
    d = LoopDetector()
    out = []
    for _ in range(3):
        out = d.observe(step())
    assert any(s["kind"] == "loop.exact" for s in out)


def test_varied_progress_is_quiet():
    d = LoopDetector()
    for i in range(6):
        out = d.observe(step(cmd=f"c{i}", err=None, ws=f"w{i}", code=0))
    assert out == []
