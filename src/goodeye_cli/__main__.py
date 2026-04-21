"""Module entrypoint.

Enables ``python -m goodeye_cli`` and the ``goodeye`` console script.
"""

from __future__ import annotations

from goodeye_cli.app import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
