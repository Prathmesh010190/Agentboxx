import os
from agentbox.signer import ChainWriter, verify_file

KEY = os.urandom(32)


def _make(tmp_path):
    p = tmp_path / "log.ndjson"
    w = ChainWriter(p, KEY)
    for i in range(3):
        w.append("step", {"i": i})
    return p


def test_intact_log_verifies(tmp_path):
    assert verify_file(_make(tmp_path), KEY) == []


def test_edit_is_detected(tmp_path):
    p = _make(tmp_path)
    p.write_text(p.read_text().replace('"i": 1', '"i": 99'))
    assert verify_file(p, KEY) != []


def test_deleted_line_is_detected(tmp_path):
    p = _make(tmp_path)
    lines = p.read_text().splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert verify_file(p, KEY) != []
