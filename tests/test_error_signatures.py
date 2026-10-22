from agentbox.executor import error_signature

NODE_A = (
    "/tmp/a/app.js:10\n    const x = obj.name.first;\n                       ^\n\n"
    "TypeError: Cannot read properties of undefined (reading 'first')\n"
    "    at Object.<anonymous> (/tmp/a/app.js:10:24)\n"
    "    at Module._compile (node:internal/modules/cjs/loader:1256:14)\n\n"
    "Node.js v20.11.0\n"
)
NODE_A_MOVED = (
    "/tmp/b/app.js:42\n    const y = obj.name.first;\n\n"
    "TypeError: Cannot read properties of undefined (reading 'first')\n"
    "    at Object.<anonymous> (/tmp/b/app.js:42:7)\n"
    "    at Module._compile (node:internal/modules/cjs/loader:1500:2)\n\n"
    "Node.js v22.1.0\n"
)
NODE_OTHER = (
    "ReferenceError: foo is not defined\n"
    "    at Object.<anonymous> (/tmp/a/app.js:3:1)\n\nNode.js v20.11.0\n"
)


def test_node_same_error_same_signature_despite_lines_and_paths():
    s1, c1 = error_signature(NODE_A, 1)
    s2, c2 = error_signature(NODE_A_MOVED, 1)
    assert c1 == c2 == "TypeError"
    assert s1 == s2


def test_node_different_errors_differ():
    assert error_signature(NODE_A, 1)[0] != error_signature(NODE_OTHER, 1)[0]


def test_node_error_code_is_part_of_class():
    text = ("Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/tmp/a/foo.js'\n"
            "    at new NodeError (node:internal/errors:405:5)\n")
    assert error_signature(text, 1)[1] == "Error[ERR_MODULE_NOT_FOUND]"


GO_PANIC_1 = ("panic: runtime error: index out of range [5] with length 3\n\n"
              "goroutine 1 [running]:\nmain.main()\n\t/tmp/x/main.go:8 +0x1d\nexit status 2\n")
GO_PANIC_2 = ("panic: runtime error: index out of range [7] with length 4\n\n"
              "goroutine 1 [running]:\nmain.main()\n\t/tmp/y/main.go:21 +0x2f\nexit status 2\n")


def test_go_panic_numbers_normalized():
    s1, c1 = error_signature(GO_PANIC_1, 2)
    s2, c2 = error_signature(GO_PANIC_2, 2)
    assert c1 == c2 == "GoPanic"
    assert s1 == s2


def test_go_compile_error_ignores_line_numbers():
    a = "# command-line-arguments\n./main.go:5:2: undefined: foo\n"
    b = "# command-line-arguments\n./main.go:12:9: undefined: foo\n"
    sa, ca = error_signature(a, 1)
    assert ca == "GoCompileError"
    assert sa == error_signature(b, 1)[0]
    assert sa != error_signature("./main.go:5:2: undefined: bar\n", 1)[0]


def test_go_fatal_error():
    text = "fatal error: all goroutines are asleep - deadlock!\n\ngoroutine 1 [chan receive]:\n"
    assert error_signature(text, 2)[1] == "GoFatalError"


def test_python_still_works():
    text = ('Traceback (most recent call last):\n  File "main.py", line 1, in <module>\n'
            "    print(1/0)\nZeroDivisionError: division by zero\n")
    sig, cls = error_signature(text, 1)
    assert cls == "ZeroDivisionError" and sig.startswith("ZeroDivisionError:")


def test_success_has_no_signature():
    assert error_signature("warning: something", 0) == (None, None)
