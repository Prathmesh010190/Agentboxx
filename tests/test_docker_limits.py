import pytest

from agentbox.docker_backend import DockerSandbox, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker not available")


def test_docker_output_cap_and_cleanup(tmp_path):
    sb = DockerSandbox(str(tmp_path))
    sb.start()
    try:
        r = sb.run("yes filler", timeout_s=60, max_output_bytes=1_000_000)
        assert r["output_limit_exceeded"] is True and r["exit_code"] == 137
        assert len(r["stdout"]["text"]) < 4200
        left = sb.run("for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done | grep -c '^yes'")
        assert left["stdout"]["text"].strip() == "0"
        assert sb.run("echo alive")["exit_code"] == 0       # container is still usable
    finally:
        sb.stop()


def test_docker_timeout_kills_background_children(tmp_path):
    sb = DockerSandbox(str(tmp_path))
    sb.start()
    try:
        r = sb.run("sleep 300 & sleep 300", timeout_s=2)
        assert r["timed_out"] is True and r["exit_code"] == 124
        left = sb.run("for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done | grep -c '^sleep 300'")
        assert left["stdout"]["text"].strip() == "0"
    finally:
        sb.stop()
