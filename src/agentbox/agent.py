"""A minimal ReAct-style agent loop wired to the recorder.

Loop: model proposes a command -> sandbox runs it -> recorder logs it ->
the observation (plus any loop-detector hint) goes back to the model.
"""
import json

SYSTEM_PROMPT = (
    "You are a coding agent working in a sandbox with a bash tool. "
    "Reply with ONLY one JSON object, no other text. "
    'To run a command: {"thought": "why", "command": "bash command"}. '
    'When the goal is done: {"thought": "why", "final": "short answer"}. '
    "If you receive an [agentbox hint], change your approach."
)


class ScriptedModel:
    """A fake model for demos and tests. Retries a failing command, then
    changes approach once it receives an [agentbox hint]."""

    def __init__(self):
        self.plan = [
            ("Create a small script.", "printf 'x = 0\\nprint(10 / x)\\n' > main.py"),
            ("Run it.", "python3 main.py"),
        ]
        self.recovery = [
            ("The hint is right: x = 0 causes the crash. Fix the value.", "sed -i 's/x = 0/x = 2/' main.py"),
            ("Run it again.", "python3 main.py"),
            ("DONE", None),
        ]
        self.i, self.recovering = 0, False

    def next_action(self, messages):
        if not self.recovering and "[agentbox hint]" in messages[-1]["content"]:
            self.recovering, self.i = True, 0
        steps = self.recovery if self.recovering else self.plan
        thought, cmd = steps[min(self.i, len(steps) - 1)]
        self.i += 1
        if cmd is None:
            return {"thought": "The script runs now.", "final": "Fixed the divide-by-zero; script prints 5.0."}
        return {"thought": thought, "command": cmd}


class AnthropicModel:
    """Real model via the Anthropic API. Needs `pip install anthropic` and
    the ANTHROPIC_API_KEY environment variable."""

    def __init__(self, model="claude-sonnet-5-5"):
        import anthropic
        self.client, self.model = anthropic.Anthropic(), model

    def next_action(self, messages):
        r = self.client.messages.create(model=self.model, max_tokens=1000,
                                        system=SYSTEM_PROMPT, messages=messages)
        text = r.content[0].text.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"thought": "unparseable reply", "final": text[:500]}


def _format_observation(obs: dict) -> str:
    return (f"exit_code={obs['exit_code']}\n"
            f"stdout:\n{obs['stdout']['text'][-1500:]}\n"
            f"stderr:\n{obs['stderr']['text'][-1500:]}")


def run_agent(goal, model, recorder, max_steps=15, verbose=True) -> dict:
    messages = [{"role": "user", "content": f"Goal: {goal}"}]
    reason, final = "max_steps", None
    for _ in range(max_steps):
        action = model.next_action(messages)
        messages.append({"role": "assistant", "content": json.dumps(action)})
        if action.get("final") is not None:
            reason, final = "final_answer", action["final"]
            break
        cmd = action.get("command")
        if not cmd:
            messages.append({"role": "user", "content": "Reply with a command or a final answer."})
            continue
        result = recorder.run(cmd, thought=action.get("thought", ""))
        obs, signals = result["observation"], result["signals"]
        if verbose:
            print(f"[{recorder.step_index}] {action.get('thought', '')}\n    $ {cmd}  -> exit {obs['exit_code']}")
            for s in signals:
                print(f"    !! {s['severity']}: {s['kind']}")
        critical = [s for s in signals if s["severity"] == "critical"]
        if critical:
            reason = f"terminated: {critical[0]['kind']}"
            break
        text = _format_observation(obs)
        if signals:
            text += "\n\n[agentbox hint] " + " ".join(sorted({s["hint"] for s in signals}))
        messages.append({"role": "user", "content": text})
    return {"reason": reason, "final": final}
