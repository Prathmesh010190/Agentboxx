"""Unit tests for TaskContract and host-side success assertions."""
import hashlib
import json
import time
from pathlib import Path

import jsonschema

from agentbox import (
    Recorder,
    TaskContract,
    assert_command,
    assert_file_contains,
    assert_file_exists,
    assert_file_hash,
)
from agentbox.contract import Assertion
from agentbox.report import build_report_data, render_html, render_json, render_text
from agentbox.signer import load_or_create_key, verify_log

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs" / "schema.json").read_text(encoding="utf-8"))


def test_file_exists_assertion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ready.txt").write_text("ok", encoding="utf-8")

    a_pass = assert_file_exists("ready.txt")
    res_pass = a_pass.check(ws)
    assert res_pass.passed is True
    assert "Path exists" in res_pass.details

    a_fail = assert_file_exists("missing.txt")
    res_fail = a_fail.check(ws)
    assert res_fail.passed is False
    assert "Path does not exist" in res_fail.details


def test_file_contains_regex_assertion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.json").write_text('{"status": "SUCCESS", "code": 200}', encoding="utf-8")

    a_pass = assert_file_contains("config.json", r'"status":\s*"SUCCESS"')
    assert a_pass.check(ws).passed is True

    a_fail = assert_file_contains("config.json", r'"status":\s*"FAILED"')
    res_fail = a_fail.check(ws)
    assert res_fail.passed is False
    assert "Pattern not found" in res_fail.details

    a_missing = assert_file_contains("not_there.txt", r"foo")
    assert a_missing.check(ws).passed is False


def test_file_hash_assertion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    content = b"agentbox verified content"
    expected_hash = hashlib.sha256(content).hexdigest()
    (ws / "artifact.bin").write_bytes(content)

    a_pass = assert_file_hash("artifact.bin", expected_hash)
    assert a_pass.check(ws).passed is True

    a_fail = assert_file_hash("artifact.bin", "0" * 64)
    res_fail = a_fail.check(ws)
    assert res_fail.passed is False
    assert "Hash mismatch" in res_fail.details


def test_command_assertion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    # Successful command
    a_pass = assert_command("python3 -c 'print(\"check passed\")'")
    assert a_pass.check(ws).passed is True

    # Failing command (non-zero exit code)
    a_fail = assert_command("python3 -c 'import sys; sys.exit(42)'")
    res_fail = a_fail.check(ws)
    assert res_fail.passed is False
    assert "Exit code 42 != 0" in res_fail.details

    # Timeout
    a_timeout = Assertion(kind="command", target="python3 -c 'import time; time.sleep(1)'", timeout_s=0.05)
    res_to = a_timeout.check(ws)
    assert res_to.passed is False
    assert "timed out" in res_to.details


def test_contract_serialization_roundtrip():
    contract = TaskContract(
        task_id="task-101",
        description="Verify build output",
        max_steps=5,
        max_duration_s=60.0,
        deadline_s=120.0,
        assertions=[
            assert_file_exists("dist/main.js"),
            assert_file_contains("out.txt", r"OK"),
            assert_file_hash("file.txt", "a" * 64),
            assert_command("npm test"),
        ],
    )
    d = contract.to_dict()
    assert d["task_id"] == "task-101"
    assert len(d["assertions"]) == 4

    restored = TaskContract.from_dict(d)
    assert restored.task_id == contract.task_id
    assert restored.max_steps == 5
    assert len(restored.assertions) == 4
    assert restored.assertions[0].kind == "file_exists"
    assert restored.assertions[1].pattern == r"OK"


def test_contract_step_budget_exhaustion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    contract = TaskContract(max_steps=2)

    rec = Recorder("step-budget", workspace=str(ws), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key", contract=contract)
    rec.run("echo step 1")
    assert not rec.closed

    rec.run("echo step 2")
    assert not rec.closed

    # Exceeding budget triggers automatic close with signed reason
    rec.run("echo step 3")
    assert rec.closed

    # Verify log and contract.verdict
    lines = [json.loads(line) for line in rec.log_path.read_text(encoding="utf-8").splitlines()]
    event_types = [r["event"]["event_type"] for r in lines]
    assert "contract.verdict" in event_types
    assert "session.end" in event_types

    end_data = next(r["event"]["data"] for r in lines if r["event"]["event_type"] == "session.end")
    assert end_data["reason"] == "contract_budget_exceeded:max_steps"

    verdict_data = next(r["event"]["data"] for r in lines if r["event"]["event_type"] == "contract.verdict")
    assert verdict_data["passed"] is False
    assert verdict_data["budget_exceeded"]["limit"] == "max_steps"
    assert verdict_data["budget_exceeded"]["budget"] == 2
    assert verdict_data["budget_exceeded"]["actual"] == 3


def test_contract_deadline_exhaustion(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    contract = TaskContract(deadline_s=0.01)

    rec = Recorder("deadline-budget", workspace=str(ws), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key", contract=contract)
    time.sleep(0.02)
    rec.run("echo step 1")
    assert rec.closed

    lines = [json.loads(line) for line in rec.log_path.read_text(encoding="utf-8").splitlines()]
    end_data = next(r["event"]["data"] for r in lines if r["event"]["event_type"] == "session.end")
    assert end_data["reason"] == "contract_budget_exceeded:deadline_s"


def test_agent_claims_done_but_host_assertion_fails(tmp_path):
    """Core acceptance criterion: agent claims completion, but host-side assertions fail."""
    ws = tmp_path / "ws"
    ws.mkdir()
    contract = TaskContract(
        assertions=[
            assert_file_exists("expected_output.csv", name="Output file must exist"),
        ]
    )

    rec = Recorder("agent-claim", workspace=str(ws), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key", contract=contract)
    rec.run("echo 'I am done!'", thought="Task is completed successfully")
    # Agent declares done
    rec.close(reason="agent_claimed_done")

    lines = [json.loads(line) for line in rec.log_path.read_text(encoding="utf-8").splitlines()]
    verdict = next(r["event"]["data"] for r in lines if r["event"]["event_type"] == "contract.verdict")
    assert verdict["passed"] is False
    assert "1/1 assertions failed" in verdict["summary"]
    assert verdict["assertions"][0]["passed"] is False
    assert "Path does not exist" in verdict["assertions"][0]["details"]


def test_contract_success_and_cryptographic_verification(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    contract = TaskContract(
        task_id="task-success",
        assertions=[
            assert_file_exists("solution.txt"),
            assert_file_contains("solution.txt", r"ANSWER=42"),
        ]
    )

    rec = Recorder("success-contract", workspace=str(ws), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key", contract=contract)
    # Step creates the required file
    rec.run("python3 -c 'open(\"solution.txt\", \"w\").write(\"ANSWER=42\\n\")'")
    rec.close("done")

    key = load_or_create_key(tmp_path / "key")
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems, f"Verification failed: {problems}"
    assert not incomplete

    # Validate against JSON schema
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for line in rec.log_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        validator.validate(record)


def test_contract_surfaced_in_reports(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    contract = TaskContract(
        task_id="report-task",
        assertions=[
            assert_file_exists("created.txt"),
            assert_file_exists("missing.txt"),
        ]
    )
    rec = Recorder("report-contract", workspace=str(ws), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key", contract=contract)
    rec.run("python3 -c 'open(\"created.txt\", \"w\").write(\"ok\")'")
    rec.close("done")

    events = [json.loads(line)["event"] for line in rec.log_path.read_text(encoding="utf-8").splitlines()]
    report_data = build_report_data(events)

    # 1. Check report data dictionary
    assert report_data["contract"] is not None
    assert report_data["contract"]["verdict"]["passed"] is False

    # 2. Check text report
    text_out = render_text(report_data, color=False)
    assert "--- Task Contract: FAILED ---" in text_out
    assert "[✓] file_exists: created.txt" in text_out
    assert "[✗] file_exists: missing.txt" in text_out

    # 3. Check JSON report
    json_out = render_json(report_data)
    parsed_json = json.loads(json_out)
    assert "contract" in parsed_json
    assert parsed_json["contract"]["verdict"]["passed"] is False

    # 4. Check HTML report
    html_out = render_html(report_data)
    assert "Task Contract" in html_out
    assert "badge-err" in html_out
    assert "created.txt" in html_out
