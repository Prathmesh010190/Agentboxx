import pytest

from agentbox.docker_backend import DockerSandbox, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker not available")


def test_snapshot_reports_state_and_resources(tmp_path):
    sb = DockerSandbox(str(tmp_path))
    sb.start()
    try:
        snap = sb.run("echo hi")["sandbox"]
        assert snap["running"] is True and snap["oom_events"] == 0
        assert snap["mem_limit_bytes"] == 512 * 1024 ** 2
        assert snap["mem_bytes"] and snap["pids"] >= 1
    finally:
        sb.stop()


def test_oom_kill_is_detected(tmp_path):
    sb = DockerSandbox(str(tmp_path), memory="64m", resource_stats=False)
    sb.start()
    try:
        r = sb.run('python3 -c "x = bytearray(300 * 1024 * 1024); print(len(x))"', timeout_s=60)
        assert r["exit_code"] == 137
        assert r["sandbox"]["oom_events"] >= 1
        assert sb.run("echo alive")["exit_code"] == 0      # the container itself survives
    finally:
        sb.stop()
