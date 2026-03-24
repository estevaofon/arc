"""arc - A Claude Code clone built with Agno agents."""

import asyncio
import sys

from dotenv import load_dotenv

from arc.cli import run_cli


def main():
    load_dotenv()
    skip_permissions = "--dangerously-skip-permissions" in sys.argv
    try:
        asyncio.run(run_cli(skip_permissions=skip_permissions))
    except (KeyboardInterrupt, asyncio.CancelledError, SystemExit):
        pass  # Handled by cli.main() or run_cli's own exit logic


if __name__ == "__main__":
    main()
