"""Run an agent loop with a real Anthropic model against a Docker sandbox.

Usage:
    export ANTHROPIC_API_KEY="your-api-key"
    python examples/demo_real_model.py "Write fib.py that prints the first 10 Fibonacci numbers, run it, and report output."

Optional flags:
    --model: Model name (default: AGENTBOX_MODEL env var or claude-sonnet-5-5)
    --max-steps: Maximum steps allowed (default: 10)
    --local: Allow running commands on the local host instead of Docker.
             By default (without --local) the script exits with a clear error
             if Docker is unavailable.
"""
import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path

from agentbox import Recorder
from agentbox.agent import AnthropicModel, run_agent
from agentbox.docker_backend import DockerNotAvailable, DockerSandbox


def main():
    parser = argparse.ArgumentParser(
        description="Run an agent with a real Anthropic Claude model."
    )
    parser.add_argument(
        "goal",
        nargs="?",
        default="Write fib.py that prints the first 10 Fibonacci numbers, run it, and report the output.",
        help="The goal for the agent.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Anthropic model name (defaults to AGENTBOX_MODEL env var or claude-sonnet-5-5).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum agent loop steps (default: 10).",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Allow running commands on the local host when Docker is unavailable. "
            "Without this flag the script exits with an error if Docker cannot be started."
        ),
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Please export ANTHROPIC_API_KEY=your-key-here before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    session_id = f"real-{uuid.uuid4().hex[:8]}"
    workspace = f"./workspace-{session_id}"
    shutil.rmtree(workspace, ignore_errors=True)
    os.makedirs(workspace, exist_ok=True)
    os.makedirs("./runs", exist_ok=True)

    sandbox = None
    try:
        sandbox = DockerSandbox(workspace)
        sandbox.start()
    except DockerNotAvailable as exc:
        if args.local:
            print(f"Notice: Docker not available ({exc}). Falling back to local backend (--local flag set).")
            sandbox = None
        else:
            print(
                f"Error: Docker is not available or not running: {exc}\n"
                "Start Docker and try again, or pass --local to run commands on the host.",
                file=sys.stderr,
            )
            sys.exit(1)

    model = AnthropicModel(model=args.model)
    rec = Recorder(session_id, workspace=workspace, log_dir="./runs", sandbox=sandbox)

    print(f"=== Session: {session_id} ===")
    print(f"Model: {model.model}")
    print(f"Goal: {args.goal}")
    print(f"Sandbox: {'Docker' if sandbox else 'Local'}\n")

    try:
        out = run_agent(args.goal, model, rec, max_steps=args.max_steps, verbose=True)
    finally:
        rec.close("agent finished")

    print("\n=== Agent Result ===")
    print(f"Reason: {out.get('reason')}")
    print(f"Final answer: {out.get('final')}")
    print(f"\nInspect log:\n    agentbox report runs/{session_id}.ndjson")
    print(f"Verify log integrity:\n    agentbox verify runs/{session_id}.ndjson\n")


if __name__ == "__main__":
    main()
