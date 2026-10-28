import io
import shutil
import sys

import pytest

import agentbox
from agentbox import Recorder
from agentbox import cli
from agentbox.signer import load_or_create_key, manifest_path


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Tests never touch the real ~/.agentbox/key and never inherit NO_COLOR."""
    key = load_or_create_key(tmp_path / "test-key")
    real = cli.load_or_create_key
    # Only the default key (~/.agentbox/key) is replaced; an explicit path still works.
    monkeypatch.setattr(cli, "load_or_create_key", lambda path=None: key if path is None else real(path))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)


def _session(tmp_path, name="s", close=True, fail=False):
    rec = Recorder(name, workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "test-key")
    rec.run("false" if fail else "echo hi")
    rec.run("echo there")
    if close:
        rec.close("done")
    return rec.log_path


# --version

def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"agentbox {agentbox.__version__}"


# verify / report: missing, empty, bad files

@pytest.mark.parametrize("command", ["verify", "report"])
def test_missing_file_is_a_clear_error(command, tmp_path, capsys):
    code = cli.main([command, str(tmp_path / "nope.ndjson")])
    err = capsys.readouterr().err
    assert code == cli.EXIT_ERROR
    assert "file not found" in err and "Traceback" not in err


@pytest.mark.parametrize("command", ["verify", "report"])
def test_empty_file_is_a_clear_error(command, tmp_path, capsys):
    p = tmp_path / "empty.ndjson"
    p.write_text("")
    code = cli.main([command, str(p)])
    assert code == cli.EXIT_ERROR
    assert "empty" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["verify", "report"])
def test_directory_is_a_clear_error(command, tmp_path, capsys):
    assert cli.main([command, str(tmp_path)]) == cli.EXIT_ERROR
    assert "directory" in capsys.readouterr().err


def test_report_on_non_log_file_is_a_clear_error(tmp_path, capsys):
    p = tmp_path / "x.ndjson"
    p.write_text("this is not json\n")
    assert cli.main(["report", str(p)]) == cli.EXIT_ERROR
    assert "line 1" in capsys.readouterr().err


def test_verify_on_binary_file_does_not_traceback(tmp_path, capsys):
    p = tmp_path / "x.ndjson"
    p.write_bytes(b"\xff\xfe\x00\x01" * 10)
    assert cli.main(["verify", str(p)]) == cli.EXIT_ERROR
    assert "Traceback" not in capsys.readouterr().err


def test_verify_on_structurally_bad_record_reports_corruption(tmp_path, capsys):
    p = tmp_path / "x.ndjson"
    p.write_text('{"event": {"seq": 1, "event_type": "step"}}\n')   # no prev_hash, hash, sig
    assert cli.main(["verify", str(p)]) == cli.EXIT_TAMPERED
    assert "CORRUPT" in capsys.readouterr().out


def test_typo_in_path_does_not_create_a_key(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(cli, "load_or_create_key", lambda path=None: created.append(1))
    cli.main(["verify", str(tmp_path / "nope.ndjson")])
    assert created == []


# verify keeps its documented exit codes

def test_verify_exit_codes(tmp_path, capsys):
    path = _session(tmp_path)
    assert cli.main(["verify", str(path)]) == 0
    path.write_text(path.read_text().replace("echo hi", "echo HI", 1))
    assert cli.main(["verify", str(path)]) == 1
    open_log = _session(tmp_path, name="open", close=False)
    assert cli.main(["verify", str(open_log)]) == 2


# color

def test_color_rules(monkeypatch):
    assert cli._use_color(Tty()) is True
    assert cli._use_color(io.StringIO()) is False          # not a terminal
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._use_color(Tty()) is False                  # NO_COLOR wins
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert cli._use_color(Tty()) is False


def test_report_colors_errors_on_a_terminal(tmp_path, monkeypatch):
    path = _session(tmp_path, fail=True)
    out = Tty()
    monkeypatch.setattr(sys, "stdout", out)
    cli.main(["report", str(path)])
    assert "\033[31mERR\033[0m" in out.getvalue()


def test_report_has_no_color_when_not_a_terminal(tmp_path, capsys):
    path = _session(tmp_path, fail=True)
    cli.main(["report", str(path)])
    out = capsys.readouterr().out
    assert "ERR" in out and "\033[" not in out


def test_report_respects_no_color(tmp_path, monkeypatch):
    path = _session(tmp_path, fail=True)
    out = Tty()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("NO_COLOR", "1")
    cli.main(["report", str(path)])
    assert "ERR" in out.getvalue() and "\033[" not in out.getvalue()


def test_report_colors_warnings_yellow(tmp_path, monkeypatch):
    rec = Recorder("loop", workspace=str(tmp_path / "ws"), log_dir=str(tmp_path / "runs"),
                   key_path=tmp_path / "test-key")
    for _ in range(4):
        rec.run("false")
    rec.close("done")
    out = Tty()
    monkeypatch.setattr(sys, "stdout", out)
    cli.main(["report", str(rec.log_path)])
    assert "\033[33m" in out.getvalue()


# list

def test_list_shows_session_steps_and_status(tmp_path, capsys):
    _session(tmp_path, name="good")
    _session(tmp_path, name="open", close=False)
    bad = _session(tmp_path, name="bad")
    bad.write_text(bad.read_text().replace("echo hi", "echo HI", 1))
    assert cli.main(["list", str(tmp_path / "runs")]) == 0
    rows = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines()[1:]}
    assert rows["good"].split()[-1] == "OK" and " 2 " in rows["good"]
    assert rows["open"].split()[-1] == "INCOMPLETE"
    assert rows["bad"].split()[-1] == "TAMPERED"


def test_list_ignores_manifest_files_and_handles_empty_logs(tmp_path, capsys):
    _session(tmp_path, name="good")
    assert manifest_path(tmp_path / "runs" / "good.ndjson").exists()
    (tmp_path / "runs" / "blank.ndjson").write_text("")
    assert cli.main(["list", str(tmp_path / "runs")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3                                  # header + two logs
    assert any(ln.startswith("blank") and ln.endswith("EMPTY") for ln in lines)


def test_list_empty_directory(tmp_path, capsys):
    assert cli.main(["list", str(tmp_path)]) == 0
    assert "No logs found" in capsys.readouterr().out


def test_list_missing_directory(tmp_path, capsys):
    assert cli.main(["list", str(tmp_path / "nope")]) == cli.EXIT_ERROR
    assert "directory not found" in capsys.readouterr().err


# demo

needs_tools = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None,
                                 reason="the demo needs bash and python3")


@needs_tools
def test_demo_succeeds_and_leaves_no_files(tmp_path, monkeypatch, capsys):
    cwd, home = tmp_path / "cwd", tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "loop.exact" in out and "OK: log is intact" in out
    assert list(cwd.iterdir()) == []                        # nothing left in the working directory
    assert list(home.iterdir()) == []                       # the real signing key is untouched


def test_demo_without_bash_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert cli.main(["demo"]) == cli.EXIT_ERROR
    assert "PATH" in capsys.readouterr().err