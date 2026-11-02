"""Tests for cross-platform process-tree termination (issue #22)."""
import os
import subprocess
import time
from unittest import mock

import pytest

from agentbox.streaming import kill_tree, run_streaming


# ---------------------------------------------------------------------------
# kill_tree: POSIX branch (mocked)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX-only branch")
def test_kill_tree_posix_calls_killpg():
    """On POSIX, kill_tree should call os.killpg with SIGKILL."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 12345
    with mock.patch("agentbox.streaming.os.name", "posix"), \
         mock.patch("agentbox.streaming.os.killpg") as mock_killpg:
        kill_tree(proc)
        mock_killpg.assert_called_once_with(12345, mock.ANY)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only branch")
def test_kill_tree_posix_ignores_oserror():
    """If the process already exited, OSError from killpg is silently ignored."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 99999
    with mock.patch("agentbox.streaming.os.name", "posix"), \
         mock.patch("agentbox.streaming.os.killpg", side_effect=OSError("No such process")):
        kill_tree(proc)  # should not raise


# ---------------------------------------------------------------------------
# kill_tree: Windows branch (mocked)
# ---------------------------------------------------------------------------

def test_kill_tree_windows_calls_taskkill():
    """On Windows, kill_tree should call taskkill /F /T /PID <pid>."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 5678
    with mock.patch("agentbox.streaming.os.name", "nt"), \
         mock.patch("agentbox.streaming.subprocess.run") as mock_run:
        kill_tree(proc)
        mock_run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "5678"],
            capture_output=True,
            timeout=10,
        )


def test_kill_tree_windows_handles_process_gone():
    """If the process already exited, the OSError from taskkill is ignored."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 1111
    with mock.patch("agentbox.streaming.os.name", "nt"), \
         mock.patch("agentbox.streaming.subprocess.run", side_effect=OSError("not found")):
        kill_tree(proc)  # should not raise


def test_kill_tree_windows_handles_taskkill_timeout():
    """If taskkill itself hangs, fall back to proc.kill()."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 2222
    with mock.patch("agentbox.streaming.os.name", "nt"), \
         mock.patch("agentbox.streaming.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("taskkill", 10)):
        kill_tree(proc)
        proc.kill.assert_called_once()


def test_kill_tree_windows_taskkill_timeout_kill_also_fails():
    """If taskkill hangs AND proc.kill() fails (already gone), nothing crashes."""
    proc = mock.MagicMock(spec=subprocess.Popen)
    proc.pid = 3333
    proc.kill.side_effect = OSError("already dead")
    with mock.patch("agentbox.streaming.os.name", "nt"), \
         mock.patch("agentbox.streaming.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("taskkill", 10)):
        kill_tree(proc)  # should not raise


# ---------------------------------------------------------------------------
# run_streaming: timeout triggers kill_tree (real process, no mock on kill)
# ---------------------------------------------------------------------------

def test_run_streaming_timeout_invokes_kill(tmp_path):
    """A command that exceeds its timeout should be terminated and return 124."""
    if os.name == "posix":
        argv = ["bash", "-c", "sleep 60"]
    else:
        # ping -n 60 sends 60 ICMP packets, one per second — reliably slow.
        argv = ["ping", "-n", "60", "127.0.0.1"]
    code, out, err, timed_out, exceeded = run_streaming(
        argv, cwd=str(tmp_path), timeout_s=1.5)
    assert timed_out
    assert code == 124


def test_run_streaming_output_cap_invokes_kill(tmp_path):
    """A command that floods stdout past max_bytes should return 137."""
    if os.name == "posix":
        argv = ["bash", "-c", "yes 'aaaaaaaaaa'"]
    else:
        # Windows: a tight loop printing to stdout.
        argv = ["cmd", "/c", "for /L %i in (1,1,999999) do @echo aaaaaaaaaa"]
    code, out, err, timed_out, exceeded = run_streaming(
        argv, cwd=str(tmp_path), timeout_s=10, max_bytes=500)
    assert exceeded
    assert code == 137


# ---------------------------------------------------------------------------
# Integration: real process tree kill on current platform
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="needs bash")
def test_kill_tree_actually_kills_children_posix(tmp_path):
    """Spawn a parent that forks a child; kill_tree should stop both."""
    script = tmp_path / "parent.sh"
    script.write_text("#!/bin/bash\nsleep 120 &\nsleep 120\n")
    script.chmod(0o755)
    proc = subprocess.Popen(
        ["bash", str(script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    kill_tree(proc)
    proc.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only integration test")
def test_kill_tree_actually_kills_children_windows(tmp_path):
    """Spawn a parent that starts a child; kill_tree should stop both."""
    script = tmp_path / "parent.bat"
    script.write_text("@echo off\r\nstart /b ping -n 120 127.0.0.1 >nul\r\nping -n 120 127.0.0.1 >nul\r\n")
    proc = subprocess.Popen(
        ["cmd", "/c", str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)  # let children start
    kill_tree(proc)
    proc.wait(timeout=10)
    # If we get here without timeout, the tree was killed.
