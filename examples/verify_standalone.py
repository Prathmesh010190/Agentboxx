#!/usr/bin/env python3
"""Verify an agentbox log using only the standard library.

An independent implementation of docs/log-format.md: it does not import agentbox.
Usage:  python verify_standalone.py LOG [--key-file PATH]     (default key: ~/.agentbox/key)
Exit codes: 0 intact, 1 tampered, 2 incomplete, 3 bad input.
"""
import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

GENESIS = "0" * 64


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mac(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--key-file", default=str(Path.home() / ".agentbox" / "key"))
    args = ap.parse_args()
    try:
        key = bytes.fromhex(Path(args.key_file).read_text().strip())
        lines = Path(args.log).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    problems, prev, expected, last = [], GENESIS, 1, {}
    for n, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
            ev = rec["event"]
            seq, etype, rhash = ev["seq"], ev["event_type"], rec["hash"]
        except (ValueError, KeyError, TypeError):
            problems.append(f"line {n}: unreadable record")
            continue
        if seq != expected:
            problems.append(f"line {n}: sequence gap (expected {expected}, got {seq})")
        expected = seq + 1
        if rec["prev_hash"] != prev:
            problems.append(f"line {n}: chain broken")
        if sha((rec["prev_hash"] + sha(canon(ev))).encode()) != rhash:
            problems.append(f"line {n}: content was modified")
        if not hmac.compare_digest(mac(key, rhash), rec["sig"]):
            problems.append(f"line {n}: bad signature")
        prev = rhash
        last = {"seq": seq, "hash": rhash, "type": etype}

    incomplete = []
    log = Path(args.log)
    manifest = log.with_name(log.stem + ".manifest.json")
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            payload = m["payload"]
            if not hmac.compare_digest(mac(key, canon(payload).decode()), m["sig"]):
                problems.append("manifest signature is invalid")
            elif last.get("seq") != payload["records"] or last.get("hash") != payload["final_hash"]:
                problems.append("log does not match its signed manifest")
        except (OSError, ValueError, KeyError, TypeError):
            problems.append("manifest is unreadable")
    elif last.get("type") != "session.end":
        incomplete.append("no session.end record and no manifest")

    if problems:
        print("TAMPERED OR CORRUPT:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    if incomplete:
        print("INCOMPLETE:")
        print("\n".join(f"  - {p}" for p in incomplete))
        return 2
    print("OK: chain, signatures and manifest are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
