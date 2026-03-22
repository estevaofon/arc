"""arc - A Claude Code clone built with Agno agents."""

import asyncio

from dotenv import load_dotenv

from arc.cli import run_cli


def main():
    load_dotenv()
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
