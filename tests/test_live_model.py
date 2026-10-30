"""Integration test: run the full pipeline with a live LLM.

Skipped automatically unless GEMINI_API_KEY is set.
Not part of CI — run manually with:
    GEMINI_API_KEY=... pytest tests/test_live_model.py -v
"""
import json
import os
import pytest

from agentbox.agent import GeminiModel, run_agent
from agentbox.recorder import Recorder
from agentbox.signer import load_or_create_key, verify_log
from agentbox.cli import _read_events
from agentbox.report import build_report_data

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set (live model test)",
)


def test_full_pipeline_with_live_model(tmp_path):
    """End-to-end: real LLM -> recorder -> signed log -> verify -> report."""
    key_path = tmp_path / "key"
    rec = Recorder(
        "live-test",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        key_path=key_path,
    )
    try:
        result = run_agent(
            "Create a file called hi.py that prints 'hello world', then run it.",
            GeminiModel(),
            rec,
            max_steps=8,
            verbose=True,
        )
    finally:
        rec.close("live test finished")

    # The agent should have finished (not been terminated)
    assert result["reason"] in ("final_answer", "max_steps")

    # The signed log must verify cleanly
    key = load_or_create_key(key_path)
    problems, incomplete = verify_log(rec.log_path, key)
    assert not problems, f"log is tampered: {problems}"

    # The report must parse without errors
    events = _read_events(rec.log_path)
    data = build_report_data(events)
    assert data["session"]["session_id"] == "live-test"

    # JSON report must be valid
    report_json = json.dumps(data)
    assert json.loads(report_json)

    print(f"\nLive test passed: {data['summary']['total_steps']} steps, "
          f"verify=OK, reason={result['reason']}")
