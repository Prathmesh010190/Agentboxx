"""Run an agent loop with a real Anthropic model against a sandbox.

Usage:
    export ANTHROPIC_API_KEY="your-api-key"
    python examples/demo_real_model.py "Write fib.py that prints the first 10 Fibonacci numbers, run it, and report output."

Optional flags:
    --model: Model name (default: AGENTBOX_MODEL env var or claude-3-5-sonnet-latest)
    --max-steps: Maximum steps allowed (default: 10)
    --local: Force local execution backend instead of Docker sandbox
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
    parser = argparse.ArgumentParser(description="Run an agent with a real Anthropic Claude model.")
    parser.add_argument(
        "goal",
        nargs="?",
        default="Write fib.py that prints the first 10 Fibonacci numbers, run it, and report the output.",
        help="The goal for the agent.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Anthropic model name (defaults to AGENTBOX_MODEL or claude-3-5-sonnet-latest).",
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
        help="Run commands locally without a Docker container.",
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
    if not args.local:
        try:
            sandbox = DockerSandbox(workspace)
            sandbox.start()
        except DockerNotAvailable:
            print("Notice: Docker not available or not running. Falling back to local backend.")
            sandbox = None

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
