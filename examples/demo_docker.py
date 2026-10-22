"""Agent commands run inside Docker. Shows what the sandbox blocks.
Run: python examples/demo_docker.py   then: agentbox report runs/docker-demo.ndjson
"""
from pathlib import Path

from agentbox import Recorder
from agentbox.docker_backend import DockerSandbox

Path("runs/docker-demo.ndjson").unlink(missing_ok=True)   # fresh log each run

sandbox = DockerSandbox("./workspace-docker")
sandbox.start()
rec = Recorder("docker-demo", workspace="./workspace-docker", log_dir="./runs", sandbox=sandbox)

attempts = [
    ("Write a file in my workspace.", "echo hello > hello.txt && cat hello.txt"),
    ("Who am I in here?", "id"),
    ("Look for the signing key.", "cat ~/.agentbox/key; ls -la /root /home 2>&1 | head -5"),
    ("Modify a system file.", "touch /etc/pwned"),
    ("Call the internet.", "python3 -c \"import urllib.request; urllib.request.urlopen('http://example.com', timeout=5)\""),
]
try:
    for thought, cmd in attempts:
        r = rec.run(cmd, thought=thought)
        o = r["observation"]
        print(f"$ {cmd[:60]}\n   exit={o['exit_code']}  {(o['stdout']['text'] or o['stderr']['text']).strip()[-100:]}")
finally:
    rec.close("demo finished")
