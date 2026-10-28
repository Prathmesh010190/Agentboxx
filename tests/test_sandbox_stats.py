import json

from agentbox import Recorder
from agentbox.executor import run_command
from agentbox.sandbox_stats import EventCounter, parse_size, parse_state, parse_stats
from agentbox.signer import load_or_create_key, verify_log


def test_parse_size():
    assert parse_size("12.5MiB") == int(12.5 * 1024 ** 2)
    assert parse_size("512MiB") == 512 * 1024 ** 2
    assert parse_size("1.5GB") == 1_500_000_000
    assert parse_size("0B") == 0
    assert parse_size("garbage") is None and parse_size("") is None and parse_size(None) is None


def test_parse_stats():
    line = json.dumps({"MemUsage": "12.5MiB / 512MiB", "MemPerc": "2.44%", "CPUPerc": "0.50%", "PIDs": "3"})
    got = parse_stats(line)
    assert got["mem_limit_bytes"] == 512 * 1024 ** 2 and got["mem_pct"] == 2.44
    assert got["cpu_pct"] == 0.5 and got["pids"] == 3
    assert parse_stats("not json") == {}
    assert parse_stats(json.dumps({"PIDs": "--"}))["pids"] is None


def test_parse_state():
    got = parse_state(json.dumps({"Running": False, "OOMKilled": True, "ExitCode": 137, "Error": ""}))
    assert got == {"running": False, "oom_killed": True, "exit_code": 137, "error": None}
    assert parse_state("nonsense") == {} and parse_state("[]") == {}


def test_event_counter():
    c = EventCounter()
    c.feed(json.dumps({"Action": "oom"}))
    c.feed(json.dumps({"status": "die"}))
    c.feed(json.dumps({"Action": "oom"}))
    c.feed("not json")
    assert c.count("oom") == 2 and c.count("die") == 1 and c.count("kill") == 0


class FakeSandbox:
    """Stands in for DockerSandbox: runs locally and attaches a canned container snapshot."""

    def __init__(self, workspace, snapshot):
        self.workspace, self.snapshot = workspace, snapshot

    def describe(self):
        return {"backend": "fake"}

    def run(self, cmd, timeout_s=60, **_):
        obs = run_command(cmd, self.workspace, timeout_s)
        obs["sandbox"] = dict(self.snapshot)
        return obs

    def stop(self):
        pass


def _events(rec):
    return [json.loads(ln)["event"] for ln in rec.log_path.read_text().splitlines()]


def _recorder(tmp_path, snapshot):
    sb = FakeSandbox(str(tmp_path / "ws"), snapshot)
    return Recorder("s", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                    key_path=tmp_path / "key", sandbox=sb)


def test_resource_event_is_written_and_log_verifies(tmp_path):
    snap = {"running": True, "mem_bytes": 1000, "mem_limit_bytes": 5000, "mem_pct": 20.0,
            "cpu_pct": 1.0, "pids": 2, "oom_events": 0}
    rec = _recorder(tmp_path, snap)
    result = rec.run("echo hi")
    rec.close("done")
    kinds = [e["event_type"] for e in _events(rec)]
    assert "sandbox.resource" in kinds and "sandbox.crash" not in kinds
    step = next(e for e in _events(rec) if e["event_type"] == "step")
    assert "sandbox" not in step["data"]
    assert result["sandbox"]["mem_bytes"] == 1000
    assert verify_log(rec.log_path, load_or_create_key(tmp_path / "key")) == ([], [])


def test_oom_writes_a_crash_event(tmp_path):
    snap = {"running": True, "mem_pct": 99.0, "oom_events": 1}
    rec = _recorder(tmp_path, snap)
    rec.run("echo hi")
    rec.close("done")
    crash = next(e for e in _events(rec) if e["event_type"] == "sandbox.crash")
    assert crash["data"]["kind"] == "oom_kill" and crash["data"]["step_index"] == 1


def test_stopped_container_writes_a_crash_event(tmp_path):
    rec = _recorder(tmp_path, {"running": False, "oom_killed": False, "exit_code": 1, "oom_events": 0})
    rec.run("echo hi")
    rec.close("done")
    crash = next(e for e in _events(rec) if e["event_type"] == "sandbox.crash")
    assert crash["data"]["kind"] == "container_stopped"
