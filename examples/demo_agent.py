"""A fake 'agent' that gets stuck retrying a failing command."""
from agentbox import Recorder

rec = Recorder("demo", workspace="./workspace", log_dir="./runs", overwrite=True)

script = [
    ("Let me create a file first.", "echo 'print(1/0)' > main.py"),
    ("Run it.", "python3 main.py"),
    ("Try again.", "python3 main.py"),
    ("Maybe it works now?", "python3 main.py"),
    ("Same thing again.", "python3 main.py"),
]
for thought, cmd in script:
    result = rec.run(cmd, thought=thought)
    print(f"$ {cmd}  -> exit {result['observation']['exit_code']}")
    for s in result["signals"]:
        print(f"   !! {s['severity']}: {s['kind']} - {s['hint']}")
rec.close("demo finished")
