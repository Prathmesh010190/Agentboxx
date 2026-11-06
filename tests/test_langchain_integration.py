"""Tests for LangChain integration (AgentboxxCallbackHandler)."""
import json
import uuid

import pytest

# Skip cleanly if langchain_core is not installed
langchain_core = pytest.importorskip("langchain_core")

from agentbox.integrations.langchain import AgentboxxCallbackHandler  # noqa: E402
from agentbox.signer import (  # noqa: E402
    generate_ed25519_keypair,
    load_or_create_key,
    manifest_path,
    receipt_path,
    verify_log,
    verify_receipt,
)


def test_tool_start_end_success(tmp_path):
    """Test standard single tool invocation recording and log verification."""
    handler = AgentboxxCallbackHandler(
        session_id="test-lc-tool",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
    )

    tool_run_id = uuid.uuid4()
    handler.on_tool_start(
        {"name": "calculator"},
        "2 + 2",
        run_id=tool_run_id,
    )
    handler.on_tool_end("4", run_id=tool_run_id)

    # Outermost chain completion closes session
    chain_run_id = uuid.uuid4()
    handler.on_chain_end({"output": "The answer is 4"}, run_id=chain_run_id, parent_run_id=None)

    assert handler.log_path.exists(), "ndjson log was not created"
    assert manifest_path(handler.log_path).exists(), "manifest was not written"

    key = load_or_create_key()
    problems, incomplete = verify_log(handler.log_path, key)
    assert not problems, f"Verification failed: {problems}"
    assert not incomplete, f"Log incomplete: {incomplete}"

    # Verify records contents
    lines = [json.loads(line) for line in handler.log_path.read_text(encoding="utf-8").splitlines()]
    event_types = [r["event"]["event_type"] for r in lines]
    assert event_types == ["session.start", "step", "session.end"]

    step_data = lines[1]["event"]["data"]
    assert step_data["tool"] == "calculator"
    assert step_data["command"] == "2 + 2"
    assert step_data["stdout"]["text"] == "4"
    assert step_data["exit_code"] == 0


def test_tool_error(tmp_path):
    """Test tool failure recording with error message and non-zero exit code."""
    handler = AgentboxxCallbackHandler(
        session_id="test-lc-error",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
    )

    tool_run_id = uuid.uuid4()
    handler.on_tool_start(
        {"name": "api_fetch"},
        "GET /invalid",
        run_id=tool_run_id,
    )
    handler.on_tool_error(RuntimeError("404 Not Found"), run_id=tool_run_id)
    handler.close("finished_with_error")

    key = load_or_create_key()
    problems, incomplete = verify_log(handler.log_path, key)
    assert not problems
    assert not incomplete

    lines = [json.loads(line) for line in handler.log_path.read_text(encoding="utf-8").splitlines()]
    step_data = lines[1]["event"]["data"]
    assert step_data["tool"] == "api_fetch"
    assert step_data["exit_code"] == 1
    assert "404 Not Found" in step_data["stderr"]["text"]


def test_parallel_tools_matched_by_run_id(tmp_path):
    """Test that concurrent/interleaved tool calls are tracked and recorded accurately."""
    handler = AgentboxxCallbackHandler(
        session_id="test-lc-parallel",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
    )

    run_id_a = uuid.uuid4()
    run_id_b = uuid.uuid4()

    # Tool A starts first, then Tool B starts
    handler.on_tool_start({"name": "search"}, "query A", run_id=run_id_a)
    handler.on_tool_start({"name": "fetch"}, "url B", run_id=run_id_b)

    # Tool B finishes before Tool A
    handler.on_tool_end("result B", run_id=run_id_b)
    handler.on_tool_end("result A", run_id=run_id_a)

    handler.close("done")

    key = load_or_create_key()
    problems, incomplete = verify_log(handler.log_path, key)
    assert not problems
    assert not incomplete

    lines = [json.loads(line) for line in handler.log_path.read_text(encoding="utf-8").splitlines()]
    steps = [r["event"]["data"] for r in lines if r["event"]["event_type"] == "step"]
    assert len(steps) == 2

    # Step 1 should be B (finished first), Step 2 should be A (finished second)
    assert steps[0]["tool"] == "fetch"
    assert steps[0]["command"] == "url B"
    assert steps[0]["stdout"]["text"] == "result B"

    assert steps[1]["tool"] == "search"
    assert steps[1]["command"] == "query A"
    assert steps[1]["stdout"]["text"] == "result A"


def test_nested_chains_do_not_close_early(tmp_path):
    """Test that inner chains ending with parent_run_id do not trigger session.end."""
    handler = AgentboxxCallbackHandler(
        session_id="test-lc-nested",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
    )

    outer_chain_id = uuid.uuid4()
    inner_chain_id = uuid.uuid4()
    tool_id = uuid.uuid4()

    # Tool execution inside nested chain
    handler.on_tool_start({"name": "helper"}, "input", run_id=tool_id, parent_run_id=inner_chain_id)
    handler.on_tool_end("output", run_id=tool_id, parent_run_id=inner_chain_id)

    # Inner chain ends (parent_run_id is set) -> must NOT close
    handler.on_chain_end({"inner": "done"}, run_id=inner_chain_id, parent_run_id=outer_chain_id)

    assert not manifest_path(handler.log_path).exists(), "Manifest should not exist yet"
    assert not handler.recorder.closed

    # Outer chain ends (parent_run_id is None) -> closes
    handler.on_chain_end({"outer": "done"}, run_id=outer_chain_id, parent_run_id=None)

    assert manifest_path(handler.log_path).exists(), "Manifest should exist now"
    assert handler.recorder.closed


def test_idempotent_close(tmp_path):
    """Closing multiple times should not add duplicate session.end records or crash."""
    handler = AgentboxxCallbackHandler(
        session_id="test-lc-idempotent",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
    )

    tool_id = uuid.uuid4()
    handler.on_tool_start({"name": "ping"}, "test", run_id=tool_id)
    handler.on_tool_end("pong", run_id=tool_id)

    handler.close("first")
    handler.close("second")
    handler.close("third")

    lines = [json.loads(line) for line in handler.log_path.read_text(encoding="utf-8").splitlines()]
    end_records = [r for r in lines if r["event"]["event_type"] == "session.end"]
    assert len(end_records) == 1, "There should be exactly one session.end record"


def test_ed25519_receipt_generation(tmp_path):
    """Test that Ed25519 run receipts are created and verify correctly when configured."""
    key_path = tmp_path / "ed25519.key"
    pub_path = tmp_path / "ed25519.pub"
    generate_ed25519_keypair(key_path=key_path, pub_path=pub_path)

    handler = AgentboxxCallbackHandler(
        session_id="test-lc-receipt",
        log_dir=str(tmp_path / "runs"),
        workspace=str(tmp_path / "ws"),
        ed25519_key_path=key_path,
    )

    tool_id = uuid.uuid4()
    handler.on_tool_start({"name": "sign_tool"}, "payload", run_id=tool_id)
    handler.on_tool_end("ok", run_id=tool_id)
    handler.close("done")

    rc_path = receipt_path(handler.log_path)
    assert rc_path.exists(), "Ed25519 receipt was not generated"

    problems, payload = verify_receipt(handler.log_path, rc_path, pub_path)
    assert not problems, f"Receipt verification failed: {problems}"
    assert payload["record_count"] == 3


def test_import_error_when_langchain_missing():
    """Verify that importing without langchain-core raises helpful ImportError."""
    import importlib
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"langchain_core": None, "langchain_core.callbacks.base": None}):
        sys.modules.pop("agentbox.integrations.langchain", None)
        with pytest.raises(ImportError, match=r"pip install agentboxx\[langchain\]"):
            importlib.import_module("agentbox.integrations.langchain")

