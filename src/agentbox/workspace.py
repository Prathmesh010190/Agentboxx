"""Hash a workspace directory so the loop detector can tell whether files changed.

Properties: symlinks are recorded but never followed, special files (pipes, sockets)
are never opened, big files are hashed by size and mtime instead of content, and
unchanged files are not re-read between steps. Not thread-safe (module-level cache).
"""
import hashlib
import os
import stat
import time

DEFAULT_IGNORE = frozenset({".git", "__pycache__", "node_modules", ".venv",
                            ".pytest_cache", ".mypy_cache", ".ruff_cache"})
MAX_HASH_BYTES = 5 * 1024 * 1024        # larger files are hashed by size + mtime
_RACY_NS = 2_000_000_000                # trust a cached digest only for files older than 2 s
_CACHE: dict = {}                       # full path -> (size, mtime_ns, cached_at_ns, digest)


def _sha_file(path: str):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _entry(full: str, rel: str, max_file_bytes: int, use_cache: bool, now: int, seen: dict) -> bytes:
    try:
        st = os.lstat(full)
    except OSError:
        return f"X:{rel}\n".encode()
    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(full)
        except OSError:
            target = "?"
        return f"L:{rel}:{target}\n".encode()
    if not stat.S_ISREG(st.st_mode):
        return f"S:{rel}\n".encode()    # never open FIFOs, sockets or devices: open() could block
    if st.st_size > max_file_bytes:
        return f"B:{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode()
    cached = _CACHE.get(full) if use_cache else None
    if (cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns
            and cached[1] + _RACY_NS <= cached[2]):
        entry = cached
    else:
        digest = _sha_file(full)
        if digest is None:
            return f"X:{rel}\n".encode()
        entry = (st.st_size, st.st_mtime_ns, now, digest)
    seen[full] = entry
    return f"F:{rel}:{entry[3]}\n".encode()


def workspace_hash(root: str, ignore=None, max_file_bytes: int = MAX_HASH_BYTES,
                   use_cache: bool = True) -> str:
    """Short hash of the workspace. Changes when files are added, removed or modified."""
    ignore = DEFAULT_IGNORE if ignore is None else frozenset(ignore)
    root = os.path.abspath(root)
    now = time.time_ns()
    seen: dict = {}
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in ignore)
        for d in list(dirnames):            # symlinked directories are listed but never entered
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                dirnames.remove(d)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                h.update(_entry(full, rel, max_file_bytes, use_cache, now, seen))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            h.update(_entry(full, rel, max_file_bytes, use_cache, now, seen))
    if use_cache:
        prefix = root + os.sep
        for k in [k for k in _CACHE if k.startswith(prefix) and k not in seen]:
            del _CACHE[k]
        _CACHE.update(seen)
    return h.hexdigest()[:16]
