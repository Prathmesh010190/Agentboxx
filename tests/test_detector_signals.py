from agentbox.loop_detector import LoopConfig, LoopDetector


def step(cmd="c", err=None, ws="w", code=0):
    return {"command_fingerprint": cmd, "error_signature": err,
            "workspace_hash": ws, "exit_code": code}


def run(steps, cfg=None):
    d = LoopDetector(cfg)
    last = []
    for s in steps:
        last = d.observe(s)
    return last


def kinds(signals):
    return [s["kind"] for s in signals]


def find(signals, kind):
    return next(s for s in signals if s["kind"] == kind)


# ---- loop.exact -------------------------------------------------------------
def test_exact_needs_three_repeats():
    assert "loop.exact" not in kinds(run([step(), step()]))
    sig = find(run([step(), step(), step()]), "loop.exact")
    assert sig["severity"] == "warn" and sig["count"] == 3 and sig["hint"]


def test_exact_becomes_critical_at_five():
    sig = find(run([step()] * 5), "loop.exact")
    assert sig["severity"] == "critical" and sig["count"] == 5


def test_exact_ignores_repeats_when_the_workspace_changed():
    steps = [step(ws=f"w{i}") for i in range(6)]
    assert "loop.exact" not in kinds(run(steps))


def test_exact_counts_non_consecutive_repeats_inside_the_window():
    steps = [step("x"), step("y", ws="w1"), step("x"), step("z", ws="w2"), step("x")]
    # all x steps share workspace "w", the others do not
    assert find(run(steps), "loop.exact")["count"] == 3


# ---- loop.error_repeat ------------------------------------------------------
def _failing(err, i):
    return step(cmd=f"c{i}", err=err, ws=f"w{i}", code=1)


def test_error_repeat_warns_at_three_and_is_critical_at_five():
    three = run([_failing("E:1", i) for i in range(3)])
    assert find(three, "loop.error_repeat")["severity"] == "warn"
    five = run([_failing("E:1", i) for i in range(5)])
    assert find(five, "loop.error_repeat")["severity"] == "critical"


def test_different_errors_do_not_add_up():
    steps = [_failing(f"E:{i}", i) for i in range(6)]
    assert "loop.error_repeat" not in kinds(run(steps))


def test_steps_without_an_error_signature_are_ignored():
    steps = [step(cmd=f"c{i}", ws=f"w{i}", code=1) for i in range(6)]
    assert "loop.error_repeat" not in kinds(run(steps))


# ---- loop.no_progress -------------------------------------------------------
def test_no_progress_after_four_failing_steps_on_an_unchanged_workspace():
    steps = [step(cmd=f"c{i}", code=1) for i in range(4)]
    sig = find(run(steps), "loop.no_progress")
    assert sig["severity"] == "warn" and sig["count"] == 4


def test_no_progress_needs_four_steps():
    assert "loop.no_progress" not in kinds(run([step(cmd=f"c{i}", code=1) for i in range(3)]))


def test_no_progress_ignores_mostly_successful_steps():
    steps = [step("a", code=1), step("b"), step("c", code=1), step("d")]    # 2 of 4 fail
    assert "loop.no_progress" not in kinds(run(steps))


def test_no_progress_allows_one_success_in_five():
    steps = [step(cmd=f"c{i}", code=1) for i in range(4)] + [step("ok", code=0)]
    assert "loop.no_progress" in kinds(run(steps))        # 4 of 5 fail


def test_a_workspace_change_resets_no_progress():
    steps = [step(cmd=f"c{i}", code=1) for i in range(3)] + [step("edit", ws="w2", code=1)]
    assert "loop.no_progress" not in kinds(run(steps))


# ---- loop.cycle -------------------------------------------------------------
def test_two_command_cycle():
    sig = find(run([step("A", ws="1"), step("B", ws="2"), step("A", ws="3"), step("B", ws="4")]), "loop.cycle")
    assert sig["severity"] == "warn" and "2" in sig["hint"]


def test_three_command_cycle():
    seq = ["A", "B", "C", "A", "B", "C"]
    steps = [step(c, ws=str(i)) for i, c in enumerate(seq)]
    assert "loop.cycle" in kinds(run(steps))


def test_one_repeated_command_is_not_a_cycle():
    steps = [step("A", ws=str(i)) for i in range(4)]
    assert "loop.cycle" not in kinds(run(steps))


def test_distinct_commands_are_not_a_cycle():
    steps = [step(c, ws=str(i)) for i, c in enumerate("ABCD")]
    assert "loop.cycle" not in kinds(run(steps))


# ---- window edges and quiet runs ---------------------------------------------
def test_old_steps_fall_out_of_the_window():
    cfg = LoopConfig(window=4)
    steps = [step("x"), step("x")] + [step(f"y{i}", ws=f"w{i}") for i in range(4)] + [step("x")]
    assert "loop.exact" not in kinds(run(steps, cfg))


def test_a_healthy_run_is_quiet():
    steps = [step(cmd=f"c{i}", ws=f"w{i}") for i in range(12)]
    d = LoopDetector()
    assert all(d.observe(s) == [] for s in steps)
