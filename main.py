"""arc - A Claude Code clone built with Agno agents."""

import asyncio
import sys

from dotenv import load_dotenv

from arc.cli import run_cli


def main():
    load_dotenv()
    skip_permissions = "--dangerously-skip-permissions" in sys.argv
    asyncio.run(run_cli(skip_permissions=skip_permissions))


if __name__ == "__main__":
    main()
