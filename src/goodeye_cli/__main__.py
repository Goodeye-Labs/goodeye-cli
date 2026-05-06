"""Module entrypoint.

Enables ``python -m goodeye_cli`` and the ``goodeye`` console script.
"""

from __future__ import annotations

from goodeye_cli.app import main as _app_main


def main() -> None:
    _app_main()


if __name__ == "__main__":
    main()
