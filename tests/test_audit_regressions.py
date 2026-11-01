"""Regression tests for v0.2.1 audit findings."""
from agentbox import Recorder
from agentbox.cli import main
from agentbox.signer import load_or_create_key, manifest_path, verify_log


# --- Fix 1: Missing manifest on closed session must be INCOMPLETE ---

def test_deleted_manifest_on_closed_session_is_incomplete(tmp_path):
    """F1: A closed session whose manifest is deleted must not report OK."""
    key_path = tmp_path / "key"
    rec = Recorder("f1", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=key_path)
    rec.run("echo step1")
    rec.run("echo step2")
    rec.close("done")

    key = load_or_create_key(key_path)
    mp = manifest_path(rec.log_path)

    # Intact: OK
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems and not incomplete

    # Delete manifest: must be INCOMPLETE, not OK
    mp.unlink()
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems, "should not report tampering"
    assert incomplete, "should report incomplete when manifest is missing on closed session"
    assert "manifest" in incomplete[0].lower()


def test_missing_manifest_without_session_end_is_still_incomplete(tmp_path):
    """Verify that truncated logs without session.end remain INCOMPLETE."""
    key_path = tmp_path / "key"
    rec = Recorder("f1b", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=key_path)
    rec.run("echo step1")
    # Do NOT close — no session.end, no manifest
    key = load_or_create_key(key_path)
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems
    assert incomplete
    assert "session.end" in incomplete[0] or "crashed" in incomplete[0]


def test_intact_session_with_manifest_is_ok(tmp_path):
    """Control: a properly closed session with manifest must verify OK."""
    key_path = tmp_path / "key"
    rec = Recorder("f1c", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=key_path)
    rec.run("echo ok")
    rec.close("done")

    key = load_or_create_key(key_path)
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems and not incomplete


# --- Fix 2: PosixPath in CLI argv must not crash ---

def test_main_accepts_path_objects_in_argv(tmp_path, capsys):
    """F2: main() must accept Path objects in argv, not just strings."""
    key_path = tmp_path / "key"
    rec = Recorder("f2", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=key_path)
    rec.run("echo path-test")
    rec.close("done")

    # Pass PosixPath directly — must not raise TypeError
    assert main(["report", rec.log_path, "--format", "json"]) == 0
    out = capsys.readouterr().out
    assert '"session_id": "f2"' in out


def test_main_accepts_path_for_output(tmp_path, capsys):
    """F2: --output with a Path object must work."""
    key_path = tmp_path / "key"
    rec = Recorder("f2b", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=key_path)
    rec.run("echo out-test")
    rec.close("done")

    html_out = tmp_path / "report.html"
    assert main(["report", rec.log_path, "-f", "html", "-o", html_out]) == 0
    assert html_out.exists()
    assert "<!DOCTYPE html>" in html_out.read_text(encoding="utf-8")
