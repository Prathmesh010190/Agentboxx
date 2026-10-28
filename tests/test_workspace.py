import os
import threading
import time

import pytest

import agentbox.workspace as ws
from agentbox.workspace import workspace_hash

posix_only = pytest.mark.skipif(os.name != "posix", reason="needs POSIX symlinks and FIFOs")


def _age(path, seconds=10):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_same_content_gives_same_hash(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    assert workspace_hash(str(tmp_path)) == workspace_hash(str(tmp_path))


def test_modified_file_changes_the_hash_even_with_same_size_and_instantly(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("aaaa")
    first = workspace_hash(str(tmp_path))
    f.write_text("bbbb")                      # same size, rewritten within the same moment
    assert workspace_hash(str(tmp_path)) != first


def test_added_and_removed_files_change_the_hash(tmp_path):
    base = workspace_hash(str(tmp_path))
    f = tmp_path / "new.txt"
    f.write_text("x")
    added = workspace_hash(str(tmp_path))
    assert added != base
    f.unlink()
    assert workspace_hash(str(tmp_path)) == base


def test_ignored_directories_do_not_affect_the_hash(tmp_path):
    base = workspace_hash(str(tmp_path))
    for d in (".git", "node_modules", "__pycache__", ".venv"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x").write_text("noise")
    assert workspace_hash(str(tmp_path)) == base


def test_ignore_list_is_configurable(tmp_path):
    (tmp_path / "cache").mkdir()
    base = workspace_hash(str(tmp_path), ignore={"cache"})
    (tmp_path / "cache" / "x").write_text("noise")
    assert workspace_hash(str(tmp_path), ignore={"cache"}) == base
    assert workspace_hash(str(tmp_path), ignore=set()) != base


@posix_only
def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("one")
    work = tmp_path / "work"
    work.mkdir()
    (work / "link").symlink_to(secret)
    (work / "dirlink").symlink_to(outside)
    first = workspace_hash(str(work))
    secret.write_text("two")                  # changes only OUTSIDE the workspace
    assert workspace_hash(str(work)) == first
    (work / "link").unlink()
    (work / "link").symlink_to(outside)       # now points somewhere else
    assert workspace_hash(str(work)) != first


def test_large_files_are_hashed_by_size_and_mtime_not_content(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"a" * 100)
    _age(f)
    first = workspace_hash(str(tmp_path), max_file_bytes=10)
    mtime = os.stat(f).st_mtime
    f.write_bytes(b"b" * 100)                 # same size, different content
    os.utime(f, (mtime, mtime))               # same mtime
    assert workspace_hash(str(tmp_path), max_file_bytes=10) == first      # never read
    f.write_bytes(b"b" * 101)
    assert workspace_hash(str(tmp_path), max_file_bytes=10) != first


def test_unchanged_old_files_are_not_read_again(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    _age(f)
    first = workspace_hash(str(tmp_path))
    calls = []
    real = ws._sha_file
    monkeypatch.setattr(ws, "_sha_file", lambda p: calls.append(p) or real(p))
    assert workspace_hash(str(tmp_path)) == first
    assert calls == []                        # served from the cache


def test_cache_does_not_change_the_result(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text(str(i))
        _age(tmp_path / f"f{i}.txt")
    assert workspace_hash(str(tmp_path), use_cache=True) == workspace_hash(str(tmp_path), use_cache=False)


def test_deleted_files_leave_the_cache(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    _age(f)
    workspace_hash(str(tmp_path))
    assert str(f) in ws._CACHE
    f.unlink()
    workspace_hash(str(tmp_path))
    assert str(f) not in ws._CACHE


@posix_only
def test_a_named_pipe_does_not_hang_the_hash(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    done = []
    t = threading.Thread(target=lambda: done.append(workspace_hash(str(tmp_path))), daemon=True)
    t.start()
    t.join(timeout=5)
    assert done, "hashing blocked on a FIFO"
