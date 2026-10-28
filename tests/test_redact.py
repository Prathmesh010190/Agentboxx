import json

import pytest

from agentbox import Recorder, verify_log
from agentbox.executor import run_command
from agentbox.redact import add_pattern, redact
from agentbox.signer import load_or_create_key

# Fake secrets are assembled at runtime so secret scanners do not flag this file.
GH = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
AWS = "AKI" + "A" + "ABCDEFGHIJKLMNOP"
SK = "sk" + "-ant-" + "api03-" + "x" * 30
SLACK = "xox" + "b-" + "1234567890-abcdefghij"
GOOGLE = "AIza" + "SyA" + "x" * 32
JWT = ("eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0"
       + "." + "abcdefghijklmnop")
PEM = ("-----BEGIN RSA PRIVATE" + " KEY-----\nMIIEfakefakefake\n"
       "-----END RSA PRIVATE" + " KEY-----")

CASES = [
    ("github_token", f"token is {GH} ok"),
    ("aws_key", f"id={AWS}"),
    ("api_key", f"key {SK} used"),
    ("slack_token", f"hook {SLACK}"),
    ("google_key", f"maps {GOOGLE}"),
    ("jwt", f"session {JWT}"),
    ("private_key", f"before\n{PEM}\nafter"),
    ("auth_header", "curl -H 'Authorization: Bearer abcdef123456' https://x"),
    ("url_password", "git clone https://bob:hunter2pass@example.com/r.git"),
    ("secret_assignment", "export DB_PASSWORD=correcthorse"),
]


@pytest.mark.parametrize("name,text", CASES)
def test_pattern_redacts(name, text):
    out, counts = redact(text)
    assert counts.get(name) == 1, (counts, out)
    assert f"[REDACTED:{name}]" in out


def test_plain_text_is_unchanged():
    text = "exit code 0, total=12345, max_tokens=1000, done"
    assert redact(text) == (text, {})


def test_no_double_counting():
    assert redact(f"Authorization: Bearer {GH}")[1] == {"github_token": 1}
    assert redact(f"GITHUB_TOKEN={GH}")[1] == {"github_token": 1}


def test_custom_pattern():
    add_pattern("ticket", r"INT-[0-9]{8}")
    out, counts = redact("see INT-12345678")
    assert counts == {"ticket": 1} and "INT-12345678" not in out


def test_same_command_with_different_secrets_has_same_fingerprint(tmp_path):
    a = run_command("echo API_TOKEN=abcdef123456", str(tmp_path))
    b = run_command("echo API_TOKEN=zyxwvu987654", str(tmp_path))
    assert a["command_fingerprint"] == b["command_fingerprint"]
    assert "abcdef123456" not in json.dumps(a)
    assert "abcdef123456" not in a["stdout"]["text"]


def test_command_runs_with_the_real_value(tmp_path):
    run_command("echo API_TOKEN=abcdef123456 > f.txt", str(tmp_path))
    assert "abcdef123456" in (tmp_path / "f.txt").read_text()


def test_secret_never_reaches_signed_log(tmp_path):
    rec = Recorder("s", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "key")
    rec.run("echo db_password=supersecret99", thought="using password=otherSecret77")
    rec.close("done")
    text = rec.log_path.read_text()
    assert "supersecret99" not in text and "otherSecret77" not in text
    step = [json.loads(ln)["event"] for ln in text.splitlines()
            if '"event_type": "step"' in ln][0]["data"]
    assert step["redactions"]["secret_assignment"] >= 2
    assert verify_log(rec.log_path, load_or_create_key(tmp_path / "key")) == ([], [])
