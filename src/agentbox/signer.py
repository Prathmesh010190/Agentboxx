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
        self.key, self.seq, self.prev = key, 0, GENESIS

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
