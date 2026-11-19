# main.py — PocketPay launcher.
#   python main.py          → live campus terminal (stage interface)
#   python main.py demo     → full 21-scenario presentation run
#   python main.py build    → rebuild the dataset (campus_wallet_logs.sqlite)
#   python main.py tests    → guard-rule + parser unit tests (no LLM, fast)
# (the original ModelHouse ping demo lives on in git, commit 04b25f3)
import sys

from pocketpay import db


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ui"
    if cmd == "build":
        db.build()
    elif cmd == "demo":
        from pocketpay import demo
        demo.main()
    elif cmd == "tests":
        from pocketpay import tests
        tests.run()
    else:
        from pocketpay import ui
        ui.main()


if __name__ == "__main__":
    main()
