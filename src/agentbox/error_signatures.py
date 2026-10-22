"""Language-specific error extractors for error signatures.

Each extractor returns (error_class, key_message) or None when the text does not
look like that language's error output. Only the stable part of an error is
returned (class + message), never stack frames, so the same failure produces the
same signature even when line numbers, paths or versions change.
"""
import re

# --- Go -------------------------------------------------------------------
_GO_PANIC = re.compile(r"^panic: (.+)$", re.M)
_GO_FATAL = re.compile(r"^fatal error: (.+)$", re.M)
_GO_COMPILE = re.compile(r"^\S*\.go:\d+(?::\d+)?: (.+)$", re.M)

# --- Node.js --------------------------------------------------------------
_NODE_HEADER = re.compile(
    r"^(?:Uncaught\s+)?((?:[A-Za-z_$][\w$]*)?(?:Error|Exception))(?:\s+\[([A-Z0-9_]+)\])?:\s*(.*)$",
    re.M,
)
_NODE_FRAME = re.compile(r"^\s+at \S", re.M)
_NODE_VERSION = re.compile(r"^Node\.js v\d+", re.M)


def extract_go(text: str):
    m = _GO_PANIC.search(text)
    if m:
        return "GoPanic", m.group(1)
    m = _GO_FATAL.search(text)
    if m:
        return "GoFatalError", m.group(1)
    m = _GO_COMPILE.search(text)
    if m:
        return "GoCompileError", m.group(1)
    return None


def extract_node(text: str):
    # Require Node-style stack frames or the version footer, so a bare
    # "Error: ..." line from some other tool is not misclassified.
    if not (_NODE_FRAME.search(text) or _NODE_VERSION.search(text)):
        return None
    m = _NODE_HEADER.search(text)
    if not m:
        return None
    cls, code, msg = m.groups()
    return (f"{cls}[{code}]" if code else cls), msg
