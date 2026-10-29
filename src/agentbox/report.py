"""Report generation for agentbox runs: text, structured JSON, and self-contained HTML."""
import html
import json
from datetime import datetime
from typing import Any, Dict, List

_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{_RESET}" if enabled else text


def build_report_data(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate raw log events into a clean structured dictionary."""
    session_info: Dict[str, Any] = {
        "session_id": "unknown",
        "isolation": {},
        "agent_id": None,
        "started_at": None,
        "ended_at": None,
        "duration_s": None,
        "reason": None,
    }
    steps: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    resources: List[Dict[str, Any]] = []
    crashes: List[Dict[str, Any]] = []
    total_redactions = 0

    for ev in events:
        etype = ev.get("event_type")
        ts = ev.get("ts")
        data = ev.get("data") or {}

        if etype == "session.start":
            session_info["session_id"] = data.get("session_id", "unknown")
            session_info["isolation"] = data.get("isolation", {})
            session_info["agent_id"] = data.get("agent_id")
            session_info["started_at"] = ts
        elif etype == "session.end":
            session_info["ended_at"] = ts
            session_info["reason"] = data.get("reason")
        elif etype == "step":
            steps.append(data)
            red = data.get("redactions")
            if isinstance(red, dict):
                total_redactions += sum(red.values())
        elif etype == "analysis.signal":
            signals.append(data)
        elif etype == "sandbox.resource":
            resources.append(data)
        elif etype == "sandbox.crash":
            crashes.append(data)

    if session_info["started_at"] is not None and session_info["ended_at"] is not None:
        session_info["duration_s"] = round(session_info["ended_at"] - session_info["started_at"], 3)

    successful = sum(1 for s in steps if s.get("exit_code") == 0)
    failed = len(steps) - successful

    return {
        "session": session_info,
        "summary": {
            "total_steps": len(steps),
            "successful_steps": successful,
            "failed_steps": failed,
            "signals": len(signals),
            "crashes": len(crashes),
            "total_redactions": total_redactions,
        },
        "steps": steps,
        "signals": signals,
        "sandbox_resources": resources,
        "sandbox_crashes": crashes,
    }


def render_text(data: Dict[str, Any], color: bool = False) -> str:
    """Render the human-readable terminal report matching standard cli format."""
    lines: List[str] = []
    steps = data.get("steps", [])
    signals = data.get("signals", [])
    crashes = data.get("sandbox_crashes", [])

    for d in steps:
        ok = d.get("exit_code") == 0
        mark = "ok " if ok else _paint("ERR", _RED, color)
        cmd_str = (d.get("command") or "")[:70]
        step_idx = d.get("step_index", 0)
        exit_code = d.get("exit_code", -1)
        lines.append(f"[{step_idx:>3}] {mark} exit={exit_code:<3} {cmd_str}")

    lines.append(f"\n{len(steps)} steps, {len(signals)} signals")

    for s in signals:
        line = f"  step {s['step_index']}: [{s['severity']}] {s['kind']} (x{s['count']}) - {s['hint']}"
        if s.get("severity") == "critical":
            line = _paint(line, _RED, color)
        elif s.get("severity") == "warn":
            line = _paint(line, _YELLOW, color)
        lines.append(line)

    for c in crashes:
        line = f"  step {c.get('step_index', '?')}: crash [{c.get('kind', 'unknown')}] exit={c.get('exit_code')} (oom={c.get('oom_killed')})"
        line = _paint(line, _RED, color)
        lines.append(line)

    return "\n".join(lines) + "\n"


def render_json(data: Dict[str, Any]) -> str:
    """Render report as indented JSON."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

_HTML_CSS = """
:root {
  --bg: #0d1117; --card-bg: #161b22; --border: #30363d;
  --text: #c9d1d9; --text-muted: #8b949e; --accent: #58a6ff;
  --green: #3fb950; --red: #f85149; --yellow: #d29922; --mono: monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f8fa; --card-bg: #ffffff; --border: #d0d7de;
    --text: #1f2328; --text-muted: #656d76; --accent: #0969da;
    --green: #1a7f37; --red: #cf222e; --yellow: #9a6700;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text); padding: 2rem 1rem; line-height: 1.5; }
.container { max-width: 960px; margin: 0 auto; }
header { border-bottom: 1px solid var(--border); padding-bottom: 1.5rem; margin-bottom: 2rem; }
h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
.subtitle { color: var(--text-muted); font-size: 0.9rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; }
.card-label { font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); }
.card-val { font-size: 1.5rem; font-weight: bold; margin-top: 0.25rem; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
.badge-ok { background: rgba(63,185,80,0.15); color: var(--green); }
.badge-err { background: rgba(248,81,73,0.15); color: var(--red); }
.badge-warn { background: rgba(210,153,34,0.15); color: var(--yellow); }
section { margin-bottom: 2.5rem; }
h2 { font-size: 1.25rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
.timeline { display: flex; flex-direction: column; gap: 0.75rem; }
.step-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 1rem; }
.step-header { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; }
.step-cmd { font-family: var(--mono); font-size: 0.9rem; color: var(--accent); word-break: break-all; }
.step-meta { font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; }
details { margin-top: 0.5rem; border-top: 1px dashed var(--border); padding-top: 0.5rem; font-size: 0.85rem; }
summary { cursor: pointer; color: var(--text-muted); user-select: none; }
summary:hover { color: var(--text); }
pre { font-family: var(--mono); background: rgba(0,0,0,0.15); padding: 0.5rem; border-radius: 4px; overflow-x: auto; margin-top: 0.5rem; }
.signal-box { background: rgba(210,153,34,0.1); border-left: 4px solid var(--yellow); padding: 0.75rem 1rem; margin-bottom: 0.75rem; border-radius: 0 4px 4px 0; }
.signal-box.critical { background: rgba(248,81,73,0.1); border-left-color: var(--red); }
"""


def render_html(data: Dict[str, Any]) -> str:
    """Render a single, completely self-contained HTML report with zero external assets."""
    sess = data.get("session", {})
    summary = data.get("summary", {})
    steps = data.get("steps", [])
    signals = data.get("signals", [])
    crashes = data.get("sandbox_crashes", [])

    started_str = (datetime.fromtimestamp(sess["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
                   if sess.get("started_at") else "unknown")
    iso = sess.get("isolation", {})
    backend_str = iso.get("backend", "local")
    if iso.get("image"):
        backend_str += f" ({iso.get('image')})"

    out = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>agentbox report - {html.escape(str(sess.get('session_id')))}</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="container">',
        "<header>",
        "<h1>agentbox flight report</h1>",
        f'<div class="subtitle">Session: <strong>{html.escape(str(sess.get("session_id")))}</strong> &bull; '
        f'Started: {html.escape(started_str)} &bull; '
        f'Backend: {html.escape(backend_str)}'
        f'{(" &bull; Agent: " + html.escape(str(sess["agent_id"]))) if sess.get("agent_id") else ""}</div>',
        "</header>",
        '<div class="cards">',
        f'<div class="card"><div class="card-label">Steps</div><div class="card-val">{summary.get("total_steps", 0)}</div></div>',
        f'<div class="card"><div class="card-label">Passed</div><div class="card-val">{summary.get("successful_steps", 0)}</div></div>',
        f'<div class="card"><div class="card-label">Failed</div><div class="card-val">{summary.get("failed_steps", 0)}</div></div>',
        f'<div class="card"><div class="card-label">Signals</div><div class="card-val">{summary.get("signals", 0)}</div></div>',
        f'<div class="card"><div class="card-label">Duration</div><div class="card-val">{sess.get("duration_s", 0)}s</div></div>',
        "</div>",
    ]

    if signals:
        out.append("<section><h2>Detector Signals</h2>")
        for s in signals:
            sev = s.get("severity", "warn")
            cls = "critical" if sev == "critical" else "warn"
            out.append(
                f'<div class="signal-box {cls}">'
                f'<strong>Step {s.get("step_index")}: [{html.escape(sev)}] {html.escape(s.get("kind", ""))}</strong> '
                f'(fired {s.get("count")}x)<br>'
                f'{html.escape(s.get("hint", ""))}'
                f'</div>'
            )
        out.append("</section>")

    if crashes:
        out.append("<section><h2>Sandbox Crashes</h2>")
        for c in crashes:
            out.append(
                f'<div class="signal-box critical">'
                f'<strong>Step {c.get("step_index", "?")}: Crash - {html.escape(str(c.get("kind")))}</strong> '
                f'(exit {c.get("exit_code")}, oom={c.get("oom_killed")})'
                f'</div>'
            )
        out.append("</section>")

    out.append("<section><h2>Step Timeline</h2><div class=\"timeline\">")
    for s in steps:
        ec = s.get("exit_code", 0)
        badge_cls = "badge-ok" if ec == 0 else "badge-err"
        badge_txt = "OK" if ec == 0 else f"ERR ({ec})"
        dur = f"{s.get('duration_ms', 0)}ms"
        cmd = s.get("command", "")
        out.append(
            f'<div class="step-card">'
            f'<div class="step-header">'
            f'<div><span class="badge {badge_cls}">{badge_txt}</span> '
            f'<strong>#{s.get("step_index")}</strong> '
            f'<span class="step-cmd">{html.escape(cmd)}</span></div>'
            f'<span class="step-meta">{html.escape(dur)}</span>'
            f'</div>'
        )
        details_items = []
        if s.get("thought"):
            details_items.append(f'<p><strong>Thought:</strong> {html.escape(s["thought"])}</p>')
        if s.get("tool"):
            details_items.append(f'<p><strong>Tool:</strong> {html.escape(str(s["tool"]))}</p>')
        if s.get("error_class"):
            details_items.append(f'<p><strong>Error Class:</strong> {html.escape(s["error_class"])}</p>')
        if s.get("error_signature"):
            details_items.append(f'<p><strong>Error Signature:</strong> <code>{html.escape(s["error_signature"])}</code></p>')
        stdout_text = (s.get("stdout") or {}).get("text")
        if stdout_text:
            details_items.append(f'<p><strong>stdout:</strong><pre>{html.escape(stdout_text)}</pre></p>')
        stderr_text = (s.get("stderr") or {}).get("text")
        if stderr_text:
            details_items.append(f'<p><strong>stderr:</strong><pre>{html.escape(stderr_text)}</pre></p>')
        if details_items:
            out.append(f'<details><summary>Details &amp; Output</summary>{"".join(details_items)}</details>')
        out.append("</div>")

    out.append("</div></section>")
    out.append("</div></body></html>\n")
    return "\n".join(out)
