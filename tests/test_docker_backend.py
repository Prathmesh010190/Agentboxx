import pytest

from agentbox.docker_backend import DockerSandbox, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker not available")


def test_runs_commands_and_blocks_writes_outside_workspace(tmp_path):
    sb = DockerSandbox(str(tmp_path))
    sb.start()
    try:
        ok = sb.run("echo hi")
        assert ok["exit_code"] == 0 and "hi" in ok["stdout"]["text"]
        assert sb.run("touch /etc/x")["exit_code"] != 0
    finally:
        sb.stop()
