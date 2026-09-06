"""Check every board against its plan, once, and print what changed.

    uv run python -m scripts.check_boards

The same check the API runs on a timer. Having it as a command means it can be
run the moment something is suspected rather than at the next cycle, and it can
be handed to Windows Task Scheduler for a machine that does not keep the API up.
"""

from __future__ import annotations

import logging
import sys

from app.taskmanager.watch import check_all_boards

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    report = check_all_boards()

    print(f"Checked {report.checked} board(s).")
    for message in report.details:
        print(f"  - {message}")
    if not report.details:
        print("  Nothing new; the boards match their plans.")

    for error in report.errors:
        print(f"  ! {error}", file=sys.stderr)

    # A board that could not be read is not the same as a board with nothing
    # wrong, and a scheduler needs to be able to tell them apart.
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
