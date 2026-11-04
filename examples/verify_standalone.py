#!/usr/bin/env python3
"""Verify an agentbox log using only the standard library (or cryptography if installed).

An independent implementation of docs/log-format.md: it does not import agentbox.
Usage:
  HMAC mode:     python verify_standalone.py LOG [--key-file PATH]
  Receipt mode:  python verify_standalone.py LOG --pubkey <KEY_OR_FILE> [--receipt PATH]

Exit codes: 0 intact, 1 tampered, 2 incomplete, 3 bad input.
"""
import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

GENESIS = "0" * 64

# RFC 8032 Ed25519 Constants for pure-Python fallback
_P = 2**255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)
_L = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


def _edwards_add(p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p1
    x2, y2 = p2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _D * x1 * x2 * y1 * y2) % _P
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _D * x1 * x2 * y1 * y2) % _P
    return (x3, y3)


def _scalarmult(p: tuple[int, int], e: int) -> tuple[int, int]:
    if e == 0:
        return (0, 1)
    q = _scalarmult(p, e // 2)
    q = _edwards_add(q, q)
    if e & 1:
        q = _edwards_add(q, p)
    return q


_By = 4 * _inv(5) % _P
_Bx = _xrecover(_By)
if _Bx % 2 != 0:
    _Bx = _P - _Bx
_B = (_Bx, _By)


def _decodepoint(s: bytes) -> tuple[int, int]:
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _P - x
    return (x, y)


def _verify_ed25519(pub_bytes: bytes, sig_bytes: bytes, msg_bytes: bytes) -> bool:
    """Verify an Ed25519 signature via cryptography if available, else pure Python."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.exceptions import InvalidSignature
        key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        try:
            key.verify(sig_bytes, msg_bytes)
            return True
        except InvalidSignature:
            return False
    except ImportError:
        pass

    # Pure Python RFC 8032 fallback
    if len(pub_bytes) != 32 or len(sig_bytes) != 64:
        return False
    r_bytes, s_bytes = sig_bytes[:32], sig_bytes[32:]
    s = int.from_bytes(s_bytes, "little")
    if s >= _L:
        return False
    try:
        a = _decodepoint(pub_bytes)
        r = _decodepoint(r_bytes)
    except Exception:
        return False
    k = int.from_bytes(hashlib.sha512(r_bytes + pub_bytes + msg_bytes).digest(), "little")
    return _scalarmult(_B, s) == _edwards_add(r, _scalarmult(a, k))


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mac(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def _load_pubkey(source: str) -> bytes:
    p = Path(source)
    if p.exists() and p.is_file():
        text = p.read_text(encoding="utf-8").strip()
    else:
        text = source.strip()
    if text.startswith("-----BEGIN"):
        try:
            from cryptography.hazmat.primitives import serialization
            key = serialization.load_pem_public_key(text.encode())
            return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        except ImportError:
            raise ValueError("PEM public keys require cryptography. Use raw 64-hex char key instead.")
    return bytes.fromhex(text)


def verify_receipt_mode(log_path: Path, receipt_path: Path, pubkey_source: str) -> int:
    try:
        pub_bytes = _load_pubkey(pubkey_source)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload = receipt["payload"]
        sig_bytes = bytes.fromhex(receipt["signature"])
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    problems = []

    # 1. Verify Ed25519 signature
    if not _verify_ed25519(pub_bytes, sig_bytes, canon(payload)):
        problems.append("receipt signature is invalid (wrong public key or payload modified)")

    if payload.get("public_key") and payload["public_key"].lower() != pub_bytes.hex().lower():
        problems.append("public key does not match receipt's designated public key")

    # 2. Verify hash chain in log
    records = 0
    prev = GENESIS
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            ev = rec["event"]
            seq, rhash = ev["seq"], rec["hash"]
        except (ValueError, KeyError, TypeError):
            problems.append(f"line {n}: unreadable record")
            continue
        records += 1
        if seq != records:
            problems.append(f"line {n}: sequence gap (expected {records}, got {seq})")
        if rec["prev_hash"] != prev:
            problems.append(f"line {n}: chain broken")
        if sha((rec["prev_hash"] + sha(canon(ev))).encode()) != rhash:
            problems.append(f"line {n}: content was modified")
        prev = rhash

    # 3. Match against receipt
    if payload.get("record_count") != records or payload.get("final_hash") != prev:
        problems.append(
            f"log does not match receipt (expected {payload.get('record_count')} records "
            f"ending in {str(payload.get('final_hash'))[:12]}, got {records} records ending in {prev[:12]})"
        )

    if problems:
        print("TAMPERED OR CORRUPT:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    key_id = payload.get("key_id", "unknown")
    print(f"OK: Ed25519 receipt and log hash chain are valid (key ID: {key_id})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--key-file", default=None, help="path to HMAC secret key")
    ap.add_argument("--receipt", default=None, help="path to .receipt.json")
    ap.add_argument("--pubkey", default=None, help="Ed25519 public key file or hex string")
    args = ap.parse_args()

    log = Path(args.log)
    if args.pubkey or args.receipt:
        rcpt = Path(args.receipt) if args.receipt else log.with_name(log.stem + ".receipt.json")
        pubkey = args.pubkey or str(Path.home() / ".agentbox" / "ed25519.pub")
        return verify_receipt_mode(log, rcpt, pubkey)

    key_file = args.key_file or str(Path.home() / ".agentbox" / "key")
    try:
        key = bytes.fromhex(Path(key_file).read_text().strip())
        lines = log.read_text(encoding="utf-8").splitlines()
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
