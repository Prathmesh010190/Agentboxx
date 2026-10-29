"""Command-line interface: verify, report, list and demo."""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .signer import load_or_create_key, verify_log
from .report import build_report_data, render_text, render_json, render_html

EXIT_OK = 0
EXIT_TAMPERED = 1
EXIT_INCOMPLETE = 2
EXIT_ERROR = 3          # missing, empty or unreadable input (not a verdict on the log)

_RED, _YELLOW, _RESET = "\033[31m", "\033[33m", "\033[0m"


class CliError(Exception):
    """A problem with the input that should be shown as a message, not a traceback."""


def _use_color(stream=None) -> bool:
    """Color only on a terminal, and never when NO_COLOR is set (https://no-color.org)."""
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{_RESET}" if enabled else text


def _check_readable(path) -> None:
    p = Path(path)
    if not p.exists():
        raise CliError(f"file not found: {path}")
    if p.is_dir():
        raise CliError(f"{path} is a directory, not a log file")
    try:
        size = p.stat().st_size
    except OSError as e:
        raise CliError(f"cannot read {path}: {e.strerror or e}")
    if size == 0:
        raise CliError(f"log file is empty: {path}")


def _run_verify(path, key=None):
    """Return (problems, incomplete). Raises CliError for missing, empty or unreadable files."""
    _check_readable(path)                      # before the key lookup, so a typo creates no key
    key = key if key is not None else load_or_create_key()
    try:
        return verify_log(path, key)
    except (OSError, UnicodeDecodeError) as e:
        raise CliError(f"cannot read {path}: {getattr(e, 'strerror', None) or e}")
    except (KeyError, TypeError, AttributeError):
        return ["a record is malformed (not a valid agentbox record)"], []


def _verify_and_print(path, key=None) -> int:
    problems, incomplete = _run_verify(path, key)
    if problems:
        print("LOG HAS BEEN TAMPERED WITH OR IS CORRUPT:")
        for p in problems:
            print("  -", p)
        return EXIT_TAMPERED
    if incomplete:
        print("INCOMPLETE LOG (chain and signatures are valid so far):")
        for p in incomplete:
            print("  -", p)
        return EXIT_INCOMPLETE
    print("OK: log is intact (chain and signatures valid).")
    return EXIT_OK


def cmd_verify(args) -> int:
    return _verify_and_print(args.logfile)


def _read_events(path) -> list:
    _check_readable(path)
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)["event"]
                    ev["event_type"]
                except (ValueError, KeyError, TypeError):
                    raise CliError(f"{path}: line {n} is not a valid agentbox record")
                events.append(ev)
    except (OSError, UnicodeDecodeError) as e:
        raise CliError(f"cannot read {path}: {getattr(e, 'strerror', None) or e}")
    return events


def cmd_report(args) -> int:
    events = _read_events(args.logfile)
    fmt = getattr(args, "format", "text") or "text"
    out_path = getattr(args, "output", None)
    try:
        data = build_report_data(events)
        if fmt == "json":
            rendered = render_json(data)
        elif fmt == "html":
            rendered = render_html(data)
        else:
            color = _use_color(stream=sys.stdout if not out_path else None)
            rendered = render_text(data, color=color)
    except (KeyError, TypeError) as e:
        raise CliError(f"{args.logfile}: a record is missing the field {e}")

    if out_path and out_path != "-":
        try:
            Path(out_path).write_text(rendered, encoding="utf-8")
        except OSError as e:
            raise CliError(f"cannot write to {out_path}: {getattr(e, 'strerror', None) or e}")
    else:
        print(rendered, end="")
    return EXIT_OK


def _peek(path):
    """Cheap, tolerant read for `list`: (session_id, start timestamp, step count)."""
    session, started, steps = Path(path).stem, None, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)["event"]
                etype = ev["event_type"]
            except (ValueError, KeyError, TypeError):
                continue
            if started is None and isinstance(ev.get("ts"), (int, float)):
                started = ev["ts"]
            data = ev.get("data")
            if etype == "session.start" and isinstance(data, dict):
                session = str(data.get("session_id", session))
            elif etype == "step":
                steps += 1
    return session, started, steps


def cmd_list(args) -> int:
    root = Path(args.directory)
    if not root.is_dir():
        raise CliError(f"directory not found: {args.directory}")
    logs = sorted(root.glob("*.ndjson"))
    if not logs:
        print(f"No logs found in {args.directory}")
        return EXIT_OK
    key = load_or_create_key()
    rows = []
    for path in logs:
        try:
            session, started, steps = _peek(path)
            problems, incomplete = _run_verify(path, key)
            status = "TAMPERED" if problems else "INCOMPLETE" if incomplete else "OK"
        except CliError:
            session, started, steps = path.stem, None, 0
            status = "EMPTY" if path.stat().st_size == 0 else "UNREADABLE"
        except OSError:
            session, started, steps, status = path.stem, None, 0, "UNREADABLE"
        when = (datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
                if started is not None else "-")
        rows.append((started if started is not None else float("inf"), session, when, str(steps), status))
    rows.sort(key=lambda r: (r[0], r[1]))
    table = [("SESSION", "STARTED", "STEPS", "VERIFIES")] + [r[1:] for r in rows]
    widths = [max(len(r[i]) for r in table) for i in range(4)]
    for r in table:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
    return EXIT_OK


def cmd_demo(args) -> int:
    """Run the scripted stuck-agent scenario in a temp directory (local backend, no Docker)."""
    from .agent import ScriptedModel, run_agent
    from .recorder import Recorder

    missing = [t for t in ("bash", "python3") if shutil.which(t) is None]
    if missing:
        raise CliError("the demo needs " + " and ".join(missing) + " on your PATH")
    with tempfile.TemporaryDirectory(prefix="agentbox-demo-") as tmp:
        tmp = Path(tmp)
        key_path = tmp / "key"              # throwaway key: the demo never touches ~/.agentbox
        rec = Recorder("demo", workspace=str(tmp / "workspace"), log_dir=str(tmp / "runs"),
                       key_path=key_path)
        print("agentbox demo: a scripted agent repeats a failing command, gets a hint, recovers.\n")
        try:
            out = run_agent("Make main.py run without errors.", ScriptedModel(), rec)
        finally:
            rec.close("demo finished")      # every way a run stops is written to the log
        print(f"\nResult: {out}\n")
        print("--- agentbox report ---")
        cmd_report(argparse.Namespace(logfile=rec.log_path))
        print("\n--- agentbox verify ---")
        code = _verify_and_print(rec.log_path, load_or_create_key(key_path))
        print("\n(This ran in a temporary directory that is deleted when the command exits.)")
    return code if code != EXIT_OK else (EXIT_OK if out["reason"] == "final_answer" else 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentbox")
    p.add_argument("--version", action="version", version=f"agentbox {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a log for tampering")
    v.add_argument("logfile")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("report", help="print a readable summary")
    r.add_argument("logfile")
    r.add_argument(
        "--format", "-f",
        choices=["text", "json", "html"],
        default="text",
        help="output format (text, json, html; default: text)",
    )
    r.add_argument(
        "--output", "-o",
        default=None,
        help="write report to a file instead of stdout",
    )
    r.set_defaults(fn=cmd_report)
    ls = sub.add_parser("list", help="list the logs in a directory (default: ./runs)")
    ls.add_argument("directory", nargs="?", default="runs")
    ls.set_defaults(fn=cmd_list)
    d = sub.add_parser("demo", help="run a self-contained demo (no Docker, no API key)")
    d.set_defaults(fn=cmd_demo)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except CliError as e:
        print(f"agentbox: error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
