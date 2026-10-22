import argparse
import json
import sys

from .signer import load_or_create_key, verify_file


def cmd_verify(args):
    problems = verify_file(args.logfile, load_or_create_key())
    if problems:
        print("LOG HAS BEEN TAMPERED WITH OR IS CORRUPT:")
        for p in problems:
            print("  -", p)
        return 1
    print("OK: log is intact (chain and signatures valid).")
    return 0


def cmd_report(args):
    steps, signals = 0, []
    with open(args.logfile, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)["event"]
            if ev["event_type"] == "step":
                steps += 1
                d = ev["data"]
                mark = "ok " if d["exit_code"] == 0 else "ERR"
                print(f"[{d['step_index']:>3}] {mark} exit={d['exit_code']:<3} {d['command'][:70]}")
            elif ev["event_type"] == "analysis.signal":
                signals.append(ev["data"])
    print(f"\n{steps} steps, {len(signals)} signals")
    for s in signals:
        print(f"  step {s['step_index']}: [{s['severity']}] {s['kind']} (x{s['count']}) - {s['hint']}")
    return 0


def main():
    p = argparse.ArgumentParser(prog="agentbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a log for tampering")
    v.add_argument("logfile")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("report", help="print a readable summary")
    r.add_argument("logfile")
    r.set_defaults(fn=cmd_report)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
