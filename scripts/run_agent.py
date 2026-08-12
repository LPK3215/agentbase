from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running without installation when src layout is used.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentbase.cli import main as cli_main


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agentbase from source tree")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--agent", default=None)
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("message")
    args = parser.parse_args()

    command = [
        "stream" if args.stream else "run",
        "--root",
        args.root,
        args.message,
    ]
    if args.agent:
        command.extend(["--agent", args.agent])
    if args.thread_id:
        command.extend(["--thread-id", args.thread_id])

    # Rebuild argv for cli parser shape: command flags message order differs.
    if args.stream:
        argv = ["stream", "--root", args.root]
        if args.agent:
            argv.extend(["--agent", args.agent])
        if args.thread_id:
            argv.extend(["--thread-id", args.thread_id])
        argv.append(args.message)
    else:
        argv = ["run", "--root", args.root]
        if args.agent:
            argv.extend(["--agent", args.agent])
        if args.thread_id:
            argv.extend(["--thread-id", args.thread_id])
        argv.append(args.message)
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
