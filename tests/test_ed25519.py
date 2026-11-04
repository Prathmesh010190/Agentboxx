"""Tests for Ed25519 public-key run receipts and third-party verification."""
import json
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from agentbox.cli import main as cli_main
from agentbox.recorder import Recorder
from agentbox.signer import (
    generate_ed25519_keypair,
    load_ed25519_private_key,
    load_ed25519_public_key,
    receipt_path,
    verify_receipt,
)


@pytest.fixture
def keypair(tmp_path):
    key_path = tmp_path / "ed25519.key"
    pub_path = tmp_path / "ed25519.pub"
    generate_ed25519_keypair(key_path=key_path, pub_path=pub_path)
    return key_path, pub_path


def test_keygen_and_loading(keypair):
    key_path, pub_path = keypair
    assert key_path.exists() and pub_path.exists()

    priv = load_ed25519_private_key(key_path)
    assert isinstance(priv, ed25519.Ed25519PrivateKey)

    pub_from_file = load_ed25519_public_key(pub_path)
    assert isinstance(pub_from_file, ed25519.Ed25519PublicKey)

    pub_hex = pub_path.read_text().strip()
    pub_from_hex = load_ed25519_public_key(pub_hex)
    assert isinstance(pub_from_hex, ed25519.Ed25519PublicKey)

    raw_bytes = bytes.fromhex(pub_hex)
    pub_from_bytes = load_ed25519_public_key(raw_bytes)
    assert isinstance(pub_from_bytes, ed25519.Ed25519PublicKey)


def test_recorder_generates_valid_receipt(tmp_path, keypair):
    key_path, pub_path = keypair
    rec = Recorder(
        "session-ed25519",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo hello", thought="say hello")
    rec.close("test completed")

    rp = receipt_path(rec.log_path)
    assert rp.exists(), "receipt file was not generated"

    receipt = json.loads(rp.read_text(encoding="utf-8"))
    payload = receipt["payload"]
    assert payload["version"] == 1
    assert payload["session_id"] == "session-ed25519"
    assert payload["record_count"] == 3  # session.start, step, session.end
    assert len(payload["final_hash"]) == 64
    assert "public_key" in payload
    assert "key_id" in payload

    problems, verified_payload = verify_receipt(rec.log_path, rp, pub_path)
    assert problems == [], f"verification failed: {problems}"
    assert verified_payload["final_hash"] == payload["final_hash"]


def test_tampered_record_detected_by_receipt(tmp_path, keypair):
    key_path, pub_path = keypair
    rec = Recorder(
        "session-tamper",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo clean", thought="clean run")
    rec.close()

    rp = receipt_path(rec.log_path)
    lines = rec.log_path.read_text(encoding="utf-8").splitlines()
    step_record = json.loads(lines[1])
    step_record["event"]["data"]["thought"] = "MALICIOUS INJECTION"
    lines[1] = json.dumps(step_record)
    rec.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    problems, _ = verify_receipt(rec.log_path, rp, pub_path)
    assert any("content was modified" in p for p in problems)


def test_truncated_log_detected_by_receipt(tmp_path, keypair):
    key_path, pub_path = keypair
    rec = Recorder(
        "session-truncate",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo step 1", thought="step 1")
    rec.run("echo step 2", thought="step 2")
    rec.close()

    rp = receipt_path(rec.log_path)
    lines = rec.log_path.read_text(encoding="utf-8").splitlines()
    rec.log_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    problems, _ = verify_receipt(rec.log_path, rp, pub_path)
    assert any("log does not match signed receipt" in p for p in problems)


def test_tampered_receipt_payload_detected(tmp_path, keypair):
    key_path, pub_path = keypair
    rec = Recorder(
        "session-tamper-receipt",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo ok")
    rec.close()

    rp = receipt_path(rec.log_path)
    receipt = json.loads(rp.read_text(encoding="utf-8"))
    receipt["payload"]["final_hash"] = "0" * 64
    rp.write_text(json.dumps(receipt), encoding="utf-8")

    problems, _ = verify_receipt(rec.log_path, rp, pub_path)
    assert any("receipt signature is invalid" in p for p in problems)


def test_wrong_public_key_fails_verification(tmp_path, keypair):
    key_path, _ = keypair
    rec = Recorder(
        "session-wrong-key",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo ok")
    rec.close()

    other_priv = ed25519.Ed25519PrivateKey.generate()
    other_pub = other_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()

    rp = receipt_path(rec.log_path)
    problems, _ = verify_receipt(rec.log_path, rp, other_pub)
    assert any("signature is invalid" in p or "does not match" in p for p in problems)


def test_cli_keygen_and_verify(tmp_path, monkeypatch, capsys):
    key_dir = tmp_path / "keys"
    code = cli_main(["keygen", "--dir", str(key_dir)])
    assert code == 0
    assert (key_dir / "ed25519.key").exists()
    assert (key_dir / "ed25519.pub").exists()

    rec = Recorder(
        "session-cli",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_dir / "ed25519.key",
    )
    rec.run("echo cli test")
    rec.close()

    rp = receipt_path(rec.log_path)
    code = cli_main([
        "verify",
        str(rec.log_path),
        "--receipt",
        str(rp),
        "--pubkey",
        str(key_dir / "ed25519.pub"),
    ])
    assert code == 0

    lines = rec.log_path.read_text(encoding="utf-8").splitlines()
    rec.log_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    code = cli_main([
        "verify",
        str(rec.log_path),
        "--receipt",
        str(rp),
        "--pubkey",
        str(key_dir / "ed25519.pub"),
    ])
    assert code == 1


def test_standalone_verifier_agrees_with_agentbox(tmp_path, keypair):
    key_path, pub_path = keypair
    rec = Recorder(
        "session-standalone",
        workspace=str(tmp_path / "ws"),
        log_dir=str(tmp_path / "runs"),
        ed25519_key_path=key_path,
    )
    rec.run("echo standalone")
    rec.close()

    rp = receipt_path(rec.log_path)
    res = subprocess.run(
        [
            sys.executable,
            "examples/verify_standalone.py",
            str(rec.log_path),
            "--receipt",
            str(rp),
            "--pubkey",
            str(pub_path),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "OK: Ed25519 receipt and log hash chain are valid" in res.stdout

    # Tamper with log
    lines = rec.log_path.read_text(encoding="utf-8").splitlines()
    rec.log_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    res_tamper = subprocess.run(
        [
            sys.executable,
            "examples/verify_standalone.py",
            str(rec.log_path),
            "--receipt",
            str(rp),
            "--pubkey",
            str(pub_path),
        ],
        capture_output=True,
        text=True,
    )
    assert res_tamper.returncode == 1
    assert "TAMPERED OR CORRUPT" in res_tamper.stdout
