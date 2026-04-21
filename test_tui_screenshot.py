"""Quick test: boot the TUI headless, capture SVG screenshot."""
import asyncio
from aru.tui.app import AruApp
from aru.session import Session


async def test():
    session = Session()
    app = AruApp(session=session)
    async with app.run_test(size=(120, 30)) as pilot:
        print("=== TUI Screenshot ===")

        # Check if ChatPane mounted
        from aru.tui.widgets.chat import ChatPane
        chat = app.query_one(ChatPane)
        print(f"ChatPane found: {chat is not None}")
        print(f"ChatPane children: {len(chat.children)}")

        # Check system message
        for child in chat.children:
            buf = getattr(child, "buffer", "N/A")
            cls = child.__class__.__name__
            print(f"  Widget: {cls} buffer={buf!r}")

        # Export SVG screenshot
        svg = app.export_screenshot()
        with open("tui_test.svg", "w", encoding="utf-8") as f:
            f.write(svg)
        print("Screenshot saved to tui_test.svg")


asyncio.run(test())
