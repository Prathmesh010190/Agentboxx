import os
import time

import pytest

from agentbox.executor import run_command
from agentbox.redact import redact
from agentbox.streaming import Capture

posix_only = pytest.mark.skipif(os.name != "posix" or not os.path.isdir("/proc"),
                                reason="needs POSIX process groups and /proc")


def test_small_output_is_complete(tmp_path):
    r = run_command("python3 -c \"print('a' * 100)\"", str(tmp_path))
    assert r["stdout"]["truncated"] is False
    assert "a" * 100 in r["stdout"]["text"]
    assert r["output_limit_exceeded"] is False


def test_large_output_keeps_head_and_tail(tmp_path):
    cmd = "python3 -c \"print('HEAD'); print('x' * 20000); print('TAIL')\""
    r = run_command(cmd, str(tmp_path))
    s = r["stdout"]
    assert s["truncated"] is True and s["total_bytes"] > 20000
    assert "HEAD" in s["text"] and "TAIL" in s["text"] and "omitted" in s["text"]
    assert len(s["text"]) < 4200


def test_endless_output_is_cut_off_at_the_cap(tmp_path):
    start = time.monotonic()
    r = run_command("yes filler", str(tmp_path), timeout_s=30, max_output_bytes=1_000_000)
    assert r["output_limit_exceeded"] is True
    assert r["exit_code"] == 137
    assert r["stdout"]["truncated"] is True
    assert len(r["stdout"]["text"]) < 4200
    assert time.monotonic() - start < 20


def test_timeout_reports_124(tmp_path):
    r = run_command("sleep 30", str(tmp_path), timeout_s=1)
    assert r["timed_out"] is True and r["exit_code"] == 124


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@posix_only
def test_timeout_kills_background_children(tmp_path):
    r = run_command("sleep 120 & echo $! > pid.txt; wait", str(tmp_path), timeout_s=2)
    assert r["timed_out"] is True
    pid = int((tmp_path / "pid.txt").read_text())
    time.sleep(0.3)
    assert not _alive(pid)


@posix_only
def test_output_cap_kills_background_children(tmp_path):
    cmd = "sleep 120 & echo $! > pid.txt; yes filler"
    r = run_command(cmd, str(tmp_path), timeout_s=30, max_output_bytes=500_000)
    assert r["output_limit_exceeded"] is True
    pid = int((tmp_path / "pid.txt").read_text())
    time.sleep(0.3)
    assert not _alive(pid)


def test_cut_never_leaves_half_a_token():
    cap = Capture(head=10, tail=10)
    cap.feed(b"aaaa bbbbSECRETXX yyyy zzzz wwww qqqq rrrr")
    text = cap.text()
    assert cap.truncated
    assert "bbbbS" not in text and "SECRET" not in text


def test_key_block_cut_by_truncation_is_still_redacted():
    text = "-----BEGIN RSA PRIVATE" + " KEY-----\nMIIEpartialbody"
    out, counts = redact(text)
    assert counts == {"private_key": 1} and "MIIEpartialbody" not in out
