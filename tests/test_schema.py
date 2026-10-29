import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from agentbox import Recorder
from agentbox.executor import run_command
from agentbox.signer import load_or_create_key, manifest_path, verify_log

ROOT = Path(__file__).resolve().parents[1]
RECORD = json.loads((ROOT / "docs" / "schema.json").read_text())
MANIFEST = json.loads((ROOT / "docs" / "manifest.schema.json").read_text())
STANDALONE = ROOT / "examples" / "verify_standalone.py"


class FakeSandbox:
    """Local runner that attaches a container snapshot, so sandbox events get written."""

    def __init__(self, workspace, snapshot):
        self.workspace, self.snapshot = workspace, snapshot

    def describe(self):
        return {"backend": "docker", "image": "python:3.12-slim", "network": "none", "memory": "512m",
                "cpus": "1", "pids_limit": 128, "read_only_rootfs": True, "cap_drop": "ALL",
                "resource_stats": True, "user": "1000:1000"}

    def run(self, cmd, timeout_s=60, **_):
        obs = run_command(cmd, self.workspace, timeout_s)
        obs["sandbox"] = dict(self.snapshot)
        return obs

    def stop(self):
        pass


def _records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def _first_step(rich):
    return next(r for r in _records(rich / "runs" / "rich.ndjson") if r["event"]["event_type"] == "step")


def _build_rich_log(directory: Path) -> Path:
    """A log that contains every event type and every optional field."""
    sb = FakeSandbox(str(directory / "ws"), {
        "running": True, "mem_bytes": 1000, "mem_limit_bytes": 5000, "mem_pct": 20.0, "cpu_pct": None,
        "pids": 2, "oom_events": 1, "oom_killed": False, "exit_code": 0, "error": None})
    rec = Recorder("rich", workspace=str(directory / "ws"), log_dir=str(directory / "runs"),
                   key_path=directory / "key", sandbox=sb, agent_id="coder-1")
    rec.run("echo hi", thought="start", tool="bash", usage={"prompt_tokens": 10, "completion_tokens": 3})
    rec.run("python3 -c 'import sys; sys.exit(3)'", parent_step=1)
    rec.run("echo API_TOKEN=abcdef123456", thought="password=hunter2hunter2")
    rec.run("sleep 5", timeout_s=1)
    for _ in range(3):
        rec.run("false")                      # repeated failures raise analysis.signal events
    rec.close("done")
    return rec.log_path


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("template")
    _build_rich_log(directory)
    return directory


@pytest.fixture
def rich(tmp_path, _template):
    """A fresh copy of the rich log, its manifest and key, safe to damage."""
    shutil.copytree(_template, tmp_path / "copy")
    return tmp_path / "copy"


def test_both_schemas_are_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(RECORD)
    jsonschema.Draft202012Validator.check_schema(MANIFEST)


def test_every_record_and_the_manifest_validate(rich):
    path = rich / "runs" / "rich.ndjson"
    records = _records(path)
    types = {r["event"]["event_type"] for r in records}
    assert {"session.start", "step", "analysis.signal", "sandbox.resource",
            "sandbox.crash", "session.end"} <= types
    validator = jsonschema.Draft202012Validator(RECORD)
    for r in records:
        validator.validate(r)
    jsonschema.Draft202012Validator(MANIFEST).validate(json.loads(manifest_path(path).read_text()))


def test_optional_fields_are_recorded(rich):
    records = _records(rich / "runs" / "rich.ndjson")
    steps = [r["event"]["data"] for r in records if r["event"]["event_type"] == "step"]
    assert steps[0]["agent_id"] == "coder-1" and steps[0]["tool"] == "bash"
    assert steps[0]["usage"] == {"prompt_tokens": 10, "completion_tokens": 3}
    assert steps[1]["parent_step_index"] == 1
    start = next(r["event"]["data"] for r in records if r["event"]["event_type"] == "session.start")
    assert start["agent_id"] == "coder-1"


def test_logs_without_optional_fields_still_validate(tmp_path):
    rec = Recorder("plain", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key")
    rec.run("echo hi")
    rec.close("done")
    validator = jsonschema.Draft202012Validator(RECORD)
    for r in _records(rec.log_path):
        validator.validate(r)


def test_a_missing_required_field_is_rejected(rich):
    bad = copy.deepcopy(_first_step(rich))
    del bad["event"]["data"]["exit_code"]
    assert not jsonschema.Draft202012Validator(RECORD).is_valid(bad)


def test_a_wrong_type_is_rejected(rich):
    bad = copy.deepcopy(_first_step(rich))
    bad["event"]["data"]["timed_out"] = "no"
    assert not jsonschema.Draft202012Validator(RECORD).is_valid(bad)


def test_unknown_envelope_keys_are_rejected(rich):
    bad = copy.deepcopy(_first_step(rich))
    bad["extra"] = 1
    assert not jsonschema.Draft202012Validator(RECORD).is_valid(bad)


def test_unknown_event_types_and_extra_data_keys_are_allowed(rich):
    base = _first_step(rich)
    unknown = copy.deepcopy(base)
    unknown["event"]["event_type"] = "something.new"
    unknown["event"]["data"] = {"anything": [1, 2, 3]}
    assert jsonschema.Draft202012Validator(RECORD).is_valid(unknown)
    extra = copy.deepcopy(base)
    extra["event"]["data"]["future_field"] = True
    assert jsonschema.Draft202012Validator(RECORD).is_valid(extra)


# ---- the independent verifier must agree with agentbox ------------------------
def _standalone(log, key):
    r = subprocess.run([sys.executable, str(STANDALONE), str(log), "--key-file", str(key)],
                       capture_output=True, text=True)
    return r.returncode


def _agentbox_exit_code(log, key_path):
    problems, incomplete = verify_log(log, load_or_create_key(key_path))
    return 1 if problems else 2 if incomplete else 0


def _damage_modify(log):
    log.write_text(log.read_text().replace("echo hi", "echo HI", 1))


def _damage_truncate_keep_manifest(log):
    log.write_text("\n".join(log.read_text().splitlines()[:-3]) + "\n")


def _damage_truncate_drop_manifest(log):
    _damage_truncate_keep_manifest(log)
    manifest_path(log).unlink()


def _damage_reorder(log):
    lines = log.read_text().splitlines()
    lines[3], lines[4] = lines[4], lines[3]
    log.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("damage,expected", [
    (None, 0),
    (_damage_modify, 1),
    (_damage_truncate_keep_manifest, 1),
    (_damage_truncate_drop_manifest, 2),
    (_damage_reorder, 1),
])
def test_standalone_verifier_agrees_with_agentbox(rich, damage, expected):
    log, key = rich / "runs" / "rich.ndjson", rich / "key"
    if damage:
        damage(log)
    assert _standalone(log, key) == expected
    assert _agentbox_exit_code(log, key) == expected


def test_standalone_verifier_rejects_a_wrong_key(rich, tmp_path):
    other = tmp_path / "other-key"
    other.write_text("00" * 32)
    assert _standalone(rich / "runs" / "rich.ndjson", other) == 1


def test_standalone_verifier_reports_bad_input(tmp_path):
    assert _standalone(tmp_path / "missing.ndjson", tmp_path / "key") == 3
