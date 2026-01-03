"""AutoTrader trader entrypoint.

This file intentionally stays small. The trader orchestration logic lives in
`src/trader/runner.py` so it can be maintained and tested more easily.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def _load_local_secrets() -> None:
    """Load local secrets for development runs (ignored by git)."""
    env_path = Path(__file__).resolve().parent / "config" / "secrets.env"
    if env_path.exists():
        load_dotenv(env_path)


def main() -> None:
    _load_local_secrets()

    from src.trader.runner import main as runner_main

    runner_main()


if __name__ == "__main__":
    main()
