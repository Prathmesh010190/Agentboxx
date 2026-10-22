import pytest

from agentbox import Recorder, verify_file
from agentbox.signer import load_or_create_key


def _rec(tmp_path, **kw):
    return Recorder("s", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                    key_path=tmp_path / "key", **kw)


def test_existing_log_is_refused(tmp_path):
    _rec(tmp_path)
    with pytest.raises(FileExistsError):
        _rec(tmp_path)


def test_overwrite_starts_a_clean_verifiable_log(tmp_path):
    first = _rec(tmp_path)
    first.close()
    second = _rec(tmp_path, overwrite=True)
    second.close()
    assert verify_file(second.log_path, load_or_create_key(tmp_path / "key")) == []
