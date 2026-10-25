from agentbox import Recorder
from agentbox.signer import load_or_create_key, manifest_path, verify_log


def _rec(tmp_path, **kw):
    return Recorder("s", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                    key_path=tmp_path / "key", **kw)


def _closed(tmp_path):
    rec = _rec(tmp_path)
    rec.run("echo hi")
    rec.run("echo there")
    rec.close("done")
    return rec.log_path, load_or_create_key(tmp_path / "key")


def test_complete_log_verifies(tmp_path):
    path, key = _closed(tmp_path)
    assert manifest_path(path).exists()
    assert verify_log(path, key) == ([], [])


def test_truncated_tail_is_tampering_when_manifest_exists(tmp_path):
    path, key = _closed(tmp_path)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-2]) + "\n")
    problems, _ = verify_log(path, key)
    assert any("manifest" in p for p in problems)


def test_truncated_tail_without_manifest_is_incomplete(tmp_path):
    path, key = _closed(tmp_path)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-2]) + "\n")
    manifest_path(path).unlink()
    problems, incomplete = verify_log(path, key)
    assert problems == [] and incomplete


def test_crashed_session_is_incomplete_not_tampered(tmp_path):
    rec = _rec(tmp_path)
    rec.run("echo hi")          # never closed
    problems, incomplete = verify_log(rec.log_path, load_or_create_key(tmp_path / "key"))
    assert problems == [] and incomplete


def test_modified_record_is_tampering(tmp_path):
    path, key = _closed(tmp_path)
    path.write_text(path.read_text().replace("echo hi", "echo HI", 1))
    problems, _ = verify_log(path, key)
    assert problems


def test_forged_manifest_is_rejected(tmp_path):
    path, key = _closed(tmp_path)
    mp = manifest_path(path)
    mp.write_text(mp.read_text().replace('"records": ', '"records": 1', 1))
    problems, _ = verify_log(path, key)
    assert any("manifest" in p for p in problems)


def test_overwrite_removes_stale_manifest(tmp_path):
    path, key = _closed(tmp_path)
    fresh = _rec(tmp_path, overwrite=True)      # new session, not closed yet
    assert not manifest_path(fresh.log_path).exists()
    problems, incomplete = verify_log(fresh.log_path, key)
    assert problems == [] and incomplete
