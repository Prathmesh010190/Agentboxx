"""Tamper-evident event log: hash-chained records with HMAC and Ed25519 receipts."""
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

GENESIS = "0" * 64
DEFAULT_ED25519_KEY_PATH = Path.home() / ".agentbox" / "ed25519.key"
DEFAULT_ED25519_PUB_PATH = Path.home() / ".agentbox" / "ed25519.pub"


def canon(obj) -> bytes:
    """Canonical JSON encoding for deterministic hashing and signing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_or_create_key(path=None) -> bytes:
    """Load or generate the 32-byte symmetric HMAC key."""
    path = Path(path or Path.home() / ".agentbox" / "key")
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    path.write_text(key.hex())
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


# --- Ed25519 Key Management ---

def generate_ed25519_keypair(key_path=None, pub_path=None) -> tuple[Path, Path]:
    """Generate an Ed25519 keypair and write hex-encoded keys to disk."""
    key_path = Path(key_path or DEFAULT_ED25519_KEY_PATH)
    pub_path = Path(pub_path or DEFAULT_ED25519_PUB_PATH)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.parent.mkdir(parents=True, exist_ok=True)

    priv = ed25519.Ed25519PrivateKey.generate()
    raw_priv = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    key_path.write_text(raw_priv.hex() + "\n", encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    pub_path.write_text(raw_pub.hex() + "\n", encoding="utf-8")
    return key_path, pub_path


def load_ed25519_private_key(path=None):
    """Load an Ed25519 private key from path, env, or default location.

    Returns ed25519.Ed25519PrivateKey or None if not found and path is None.
    """
    if path is None:
        env_path = os.environ.get("AGENTBOX_ED25519_KEY")
        path = Path(env_path) if env_path else DEFAULT_ED25519_KEY_PATH
        if not path.exists():
            return None
    else:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Ed25519 private key not found: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("-----BEGIN"):
        return serialization.load_pem_private_key(text.encode(), password=None)
    try:
        return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text))
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid Ed25519 private key in {path}: {e}")


def load_ed25519_public_key(source):
    """Load an Ed25519 public key from a file path, hex string, or raw bytes."""
    if isinstance(source, ed25519.Ed25519PublicKey):
        return source
    if isinstance(source, bytes):
        if len(source) == 32:
            return ed25519.Ed25519PublicKey.from_public_bytes(source)
        source = source.decode("utf-8").strip()
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8").strip()
        else:
            text = str(source).strip()
        if text.startswith("-----BEGIN"):
            return serialization.load_pem_public_key(text.encode())
        try:
            return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(text))
        except ValueError as e:
            raise ValueError(f"Cannot parse Ed25519 public key: {e}")
    raise TypeError(f"Unsupported public key type: {type(source)}")


# --- ChainWriter ---

class ChainWriter:
    def __init__(self, path, key: bytes, overwrite: bool = False, ed25519_key=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > 0:
            if not overwrite:
                raise FileExistsError(
                    f"Log already exists: {self.path}. Use a new session id or pass overwrite=True.")
            self.path.write_text("")
        manifest_path(self.path).unlink(missing_ok=True)   # never keep a stale manifest
        receipt_path(self.path).unlink(missing_ok=True)    # never keep a stale receipt
        self.key, self.seq, self.prev = key, 0, GENESIS
        self.ed25519_key = ed25519_key
        self.started_at = None

    def write_manifest(self):
        """Write the signed closing manifest. Call after the final record."""
        write_manifest(self.path, self.key, self.seq, self.prev)

    def write_receipt(self, private_key=None, session_id: str | None = None) -> dict | None:
        """Write the Ed25519 run receipt if a private key is provided or configured."""
        pk = private_key if private_key is not None else self.ed25519_key
        if not pk:
            return None
        return write_receipt(
            self.path,
            pk,
            records=self.seq,
            final_hash=self.prev,
            session_id=session_id,
            started_at=self.started_at,
        )

    def append(self, event_type: str, data: dict) -> dict:
        self.seq += 1
        ts = time.time()
        if self.started_at is None:
            self.started_at = ts
        event = {
            "schema_version": "0.1",
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "ts": ts,
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


# --- Manifest & Receipt Paths ---

def manifest_path(log_path) -> Path:
    p = Path(log_path)
    return p.with_name(p.stem + ".manifest.json")


def receipt_path(log_path) -> Path:
    p = Path(log_path)
    return p.with_name(p.stem + ".receipt.json")


# --- Manifest (HMAC) ---

def write_manifest(log_path, key: bytes, records: int, final_hash: str):
    """Signed summary written when a session closes: record count and last hash."""
    payload = {"log": Path(log_path).name, "records": records, "final_hash": final_hash}
    sig = hmac.new(key, canon(payload), hashlib.sha256).hexdigest()
    manifest_path(log_path).write_text(
        json.dumps({"payload": payload, "sig": sig}, indent=2), encoding="utf-8")


# --- Receipt (Ed25519) ---

def write_receipt(
    log_path,
    private_key,
    records: int,
    final_hash: str,
    session_id: str | None = None,
    started_at: float | None = None,
    closed_at: float | None = None,
) -> dict:
    """Sign and write an Ed25519 run receipt for third-party verification."""
    pub = private_key.public_key()
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_hex = pub_bytes.hex()
    key_id = f"sha256:{hashlib.sha256(pub_bytes).hexdigest()[:16]}"
    now = time.time()
    payload = {
        "version": 1,
        "session_id": session_id or Path(log_path).stem,
        "log": Path(log_path).name,
        "record_count": records,
        "final_hash": final_hash,
        "key_id": key_id,
        "public_key": pub_hex,
        "started_at": started_at if started_at is not None else now,
        "closed_at": closed_at if closed_at is not None else now,
    }
    sig = private_key.sign(canon(payload)).hex()
    receipt = {"payload": payload, "signature": sig}
    receipt_path(log_path).write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    return receipt


# --- Verification ---

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


def verify_receipt(log_path, receipt_file, pubkey) -> tuple[list[str], dict | None]:
    """Verify an Ed25519 run receipt against a log file using ONLY the public key.

    Returns (problems, receipt_payload). Empty problems = intact and untampered.
    """
    problems = []
    rp = Path(receipt_file)
    if not rp.exists():
        return [f"receipt file not found: {receipt_file}"], None

    try:
        receipt = json.loads(rp.read_text(encoding="utf-8"))
        payload = receipt["payload"]
        sig_bytes = bytes.fromhex(receipt["signature"])
    except Exception as e:
        return [f"receipt is unreadable or malformed: {e}"], None

    try:
        public_key = load_ed25519_public_key(pubkey)
    except Exception as e:
        return [f"invalid public key: {e}"], None

    # 1. Verify Ed25519 signature
    try:
        public_key.verify(sig_bytes, canon(payload))
    except InvalidSignature:
        problems.append("receipt signature is invalid (wrong key or payload modified)")
    except Exception as e:
        problems.append(f"signature verification error: {e}")

    # Check key ID if present
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if payload.get("public_key") and payload["public_key"].lower() != pub_bytes.hex().lower():
        problems.append("public key does not match receipt's designated public key")

    # 2. Verify hash chain without HMAC secret
    lp = Path(log_path)
    if not lp.exists():
        problems.append(f"log file not found: {log_path}")
        return problems, payload

    records = 0
    prev_hash = GENESIS
    with open(lp, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            try:
                rec = json.loads(line)
                ev = rec["event"]
            except (json.JSONDecodeError, KeyError):
                problems.append(f"line {n}: unreadable record")
                continue
            records += 1
            if ev["seq"] != records:
                problems.append(f"line {n}: sequence gap (expected {records}, got {ev['seq']})")
            if rec["prev_hash"] != prev_hash:
                problems.append(f"line {n}: chain broken")
            good = _sha((rec["prev_hash"] + _sha(canon(ev))).encode())
            if good != rec["hash"]:
                problems.append(f"line {n}: content was modified")
            prev_hash = rec["hash"]

    # 3. Verify match against signed receipt
    if payload.get("record_count") != records or payload.get("final_hash") != prev_hash:
        problems.append(
            f"log does not match signed receipt (expected {payload.get('record_count')} "
            f"records ending in {str(payload.get('final_hash'))[:12]}, but log has "
            f"{records} records ending in {prev_hash[:12]}): truncated or modified"
        )

    return problems, payload


def verify_log(path, key: bytes):
    """Full check using symmetric HMAC key. Returns (problems, incomplete)."""
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
    else:
        if last.get("type") != "session.end":
            incomplete.append("no session.end record and no manifest: the session crashed, "
                              "is still running, or the end of the log was removed")
        else:
            incomplete.append("session is closed but its manifest is missing: "
                              "the manifest may have been deleted")
    return problems, incomplete
