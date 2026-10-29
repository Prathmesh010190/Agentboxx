"""Tests for text, JSON, and self-contained HTML reports."""
import json
import re
from agentbox import cli
from agentbox.recorder import Recorder
from agentbox.report import build_report_data, render_html, render_json, render_text


def _sample_events():
    return [
        {
            "seq": 1,
            "event_type": "session.start",
            "ts": 1700000000.0,
            "data": {
                "session_id": "test-sess-1",
                "isolation": {"backend": "local"},
                "agent_id": "test-agent",
            },
        },
        {
            "seq": 2,
            "event_type": "step",
            "ts": 1700000001.0,
            "data": {
                "step_index": 1,
                "command": "echo hello <script>alert(1)</script>",
                "exit_code": 0,
                "duration_ms": 15,
                "stdout": {"text": "hello\n", "total_bytes": 6, "truncated": False},
                "stderr": {"text": "", "total_bytes": 0, "truncated": False},
                "redactions": {"test_pattern": 2},
            },
        },
        {
            "seq": 3,
            "event_type": "step",
            "ts": 1700000002.0,
            "data": {
                "step_index": 2,
                "command": "cat nonexistent",
                "exit_code": 1,
                "duration_ms": 10,
                "thought": "Let's read the file",
                "error_class": "file_not_found",
                "error_signature": "No such file or directory",
                "stdout": {"text": "", "total_bytes": 0, "truncated": False},
                "stderr": {"text": "cat: nonexistent: No such file or directory\n", "total_bytes": 45, "truncated": False},
            },
        },
        {
            "seq": 4,
            "event_type": "analysis.signal",
            "ts": 1700000003.0,
            "data": {
                "step_index": 2,
                "kind": "error_repeat",
                "severity": "warn",
                "count": 2,
                "hint": "Check if file exists before reading",
            },
        },
        {
            "seq": 5,
            "event_type": "sandbox.crash",
            "ts": 1700000004.0,
            "data": {
                "step_index": 2,
                "kind": "oom",
                "exit_code": 137,
                "oom_killed": True,
            },
        },
        {
            "seq": 6,
            "event_type": "session.end",
            "ts": 1700000005.0,
            "data": {"reason": "completed", "steps": 2},
        },
    ]


def test_build_report_data_aggregates_correctly():
    events = _sample_events()
    data = build_report_data(events)
    assert data["session"]["session_id"] == "test-sess-1"
    assert data["session"]["duration_s"] == 5.0
    assert data["summary"]["total_steps"] == 2
    assert data["summary"]["successful_steps"] == 1
    assert data["summary"]["failed_steps"] == 1
    assert data["summary"]["signals"] == 1
    assert data["summary"]["crashes"] == 1
    assert data["summary"]["total_redactions"] == 2


def test_render_json_valid_and_complete():
    events = _sample_events()
    data = build_report_data(events)
    out = render_json(data)
    parsed = json.loads(out)
    assert parsed["session"]["session_id"] == "test-sess-1"
    assert len(parsed["steps"]) == 2
    assert len(parsed["signals"]) == 1
    assert len(parsed["sandbox_crashes"]) == 1


def test_render_html_is_self_contained_and_escapes():
    events = _sample_events()
    data = build_report_data(events)
    html_out = render_html(data)

    # Valid HTML structure
    assert html_out.startswith("<!DOCTYPE html>")
    assert "</html>" in html_out
    assert "agentbox flight report" in html_out

    # Strictly self-contained: no external stylesheets, fonts, or scripts
    assert "<script" not in html_out.lower()
    assert "<link" not in html_out.lower()
    assert "@import" not in html_out.lower()
    assert not re.search(r"https?://", html_out)

    # Proper XSS escaping
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out


def test_render_text_contains_steps_and_crashes():
    events = _sample_events()
    data = build_report_data(events)
    txt = render_text(data, color=False)
    assert "[  1] ok  exit=0" in txt
    assert "[  2] ERR exit=1" in txt
    assert "2 steps, 1 signals" in txt
    assert "crash [oom] exit=137" in txt


def test_cli_report_json_and_html(tmp_path, capsys):
    rec = Recorder("test-run", workspace=str(tmp_path / "ws"),
                   log_dir=str(tmp_path / "runs"), key_path=tmp_path / "key")
    rec.run("echo test_cli")
    rec.close("done")

    # JSON to stdout
    assert cli.main(["report", str(rec.log_path), "--format", "json"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["session"]["session_id"] == "test-run"
    assert parsed["summary"]["total_steps"] == 1

    # HTML to file
    html_file = tmp_path / "report.html"
    assert cli.main(["report", str(rec.log_path), "-f", "html", "-o", str(html_file)]) == cli.EXIT_OK
    assert html_file.exists()
    assert "<!DOCTYPE html>" in html_file.read_text(encoding="utf-8")

    # Bad output path fails cleanly
    bad_path = tmp_path / "missing_subdir" / "out.html"
    assert cli.main(["report", str(rec.log_path), "-o", str(bad_path)]) == cli.EXIT_ERROR
    assert "cannot write to" in capsys.readouterr().err
