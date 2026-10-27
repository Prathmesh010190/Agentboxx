import os

import pytest

import agentbox.docker_backend as db
from agentbox.docker_backend import DockerSandbox, container_user


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="Unix only")
def test_unix_uses_the_host_user():
    assert container_user() == f"{os.getuid()}:{os.getgid()}"


def test_without_unix_ids_a_fixed_non_root_user_is_used(monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)    # what Windows looks like
    monkeypatch.delattr(os, "getgid", raising=False)
    assert container_user() == "1000:1000"


def _start_command(monkeypatch, tmp_path, **kwargs):
    seen = {}

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **_):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(db, "docker_available", lambda: True)
    monkeypatch.setattr(db.subprocess, "run", fake_run)
    monkeypatch.setattr(DockerSandbox, "_start_event_watcher", lambda self: None)
    DockerSandbox(str(tmp_path), **kwargs).start()
    return seen["cmd"]


def test_windows_start_command_keeps_every_security_flag(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.delattr(os, "getgid", raising=False)
    cmd = _start_command(monkeypatch, tmp_path)
    assert cmd[cmd.index("--user") + 1] == "1000:1000"
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in cmd and "no-new-privileges:true" in cmd


def test_user_can_be_overridden(monkeypatch, tmp_path):
    cmd = _start_command(monkeypatch, tmp_path, user="2000:2000")
    assert cmd[cmd.index("--user") + 1] == "2000:2000"


def test_describe_reports_the_user():
    assert DockerSandbox("/tmp/x", user="2000:2000").describe()["user"] == "2000:2000"
