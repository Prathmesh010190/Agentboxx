"""Tamper-evident event log: every record is hash-chained and HMAC-signed."""
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

GENESIS = "0" * 64


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_or_create_key(path=None) -> bytes:
    path = Path(path or Path.home() / ".agentbox" / "key")
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    path.write_text(key.hex())
    path.chmod(0o600)
    return key


class ChainWriter:
    def __init__(self, path, key: bytes, overwrite: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > 0:
            if not overwrite:
                raise FileExistsError(
                    f"Log already exists: {self.path}. Use a new session id or pass overwrite=True.")
            self.path.write_text("")
        manifest_path(self.path).unlink(missing_ok=True)   # never keep a stale manifest
        self.key, self.seq, self.prev = key, 0, GENESIS

    def write_manifest(self):
        """Write the signed closing manifest. Call after the final record."""
        write_manifest(self.path, self.key, self.seq, self.prev)

    def append(self, event_type: str, data: dict) -> dict:
        self.seq += 1
        event = {
            "schema_version": "0.1",
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "ts": time.time(),
            "seq": self.seq,
            "data": data,
        }
        chain_hash = _sha((self.prev + _sha(canon(event))).encode())
        sig = hmac.new(self.key, chain_hash.encode(), hashlib.sha256).hexdigest()
        record = {"event": event, "prev_hash": self.prev, "hash": chain_hash, "sig": sig}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.prev = chain_hash
        return record


def verify_file(path, key: bytes) -> list[str]:
    """Return a list of problems. Empty list = log is intact."""
    problems, prev, expected = [], GENESIS, 1
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            try:
                rec = json.loads(line)
                ev = rec["event"]
            except (json.JSONDecodeError, KeyError):
                problems.append(f"line {n}: unreadable record")
                continue
            if ev["seq"] != expected:
                problems.append(f"line {n}: sequence gap (expected {expected}, got {ev['seq']})")
            expected = ev["seq"] + 1
            if rec["prev_hash"] != prev:
                problems.append(f"line {n}: chain broken")
            good = _sha((rec["prev_hash"] + _sha(canon(ev))).encode())
            if good != rec["hash"]:
                problems.append(f"line {n}: content was modified")
            want = hmac.new(key, rec["hash"].encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, rec["sig"]):
                problems.append(f"line {n}: bad signature")
            prev = rec["hash"]
    return problems


def manifest_path(log_path) -> Path:
    p = Path(log_path)
    return p.with_name(p.stem + ".manifest.json")


def write_manifest(log_path, key: bytes, records: int, final_hash: str):
    """Signed summary written when a session closes: record count and last hash."""
    payload = {"log": Path(log_path).name, "records": records, "final_hash": final_hash}
    sig = hmac.new(key, canon(payload), hashlib.sha256).hexdigest()
    manifest_path(log_path).write_text(
        json.dumps({"payload": payload, "sig": sig}, indent=2), encoding="utf-8")


def verify_log(path, key: bytes):
    """Full check. Returns (problems, incomplete).

    problems   = evidence of tampering (bad chain or signature, or the log does
                 not match its signed manifest, e.g. the end was cut off).
    incomplete = the log is intact so far but was never closed, or its end is
                 missing: a crash, a running session, or a removed tail.
    """
    problems = verify_file(path, key)
    last = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                last = {"seq": rec["event"]["seq"], "hash": rec["hash"],
                        "type": rec["event"]["event_type"]}
            except (ValueError, KeyError, TypeError):
                continue
    incomplete = []
    mp = manifest_path(path)
    if mp.exists():
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
            payload, sig = m["payload"], m["sig"]
            want = hmac.new(key, canon(payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, sig):
                problems.append("manifest signature is invalid")
            elif last.get("seq") != payload["records"] or last.get("hash") != payload["final_hash"]:
                problems.append(
                    f"log does not match its signed manifest (expected {payload['records']} "
                    f"records ending in {payload['final_hash'][:12]}): truncated or modified")
        except (OSError, ValueError, KeyError, TypeError):
            problems.append("manifest is unreadable")
    elif last.get("type") != "session.end":
        incomplete.append("no session.end record and no manifest: the session crashed, "
                          "is still running, or the end of the log was removed")
    return problems, incomplete
