#!/usr/bin/env python3
"""
Utility: check which OpenAI models your API key can access.

Reads OPENAI_API_KEY from `config/secrets.env` (if present) and prints:
- any GPT‑5 / o‑series models visible to the key
- whether gpt-4.1-mini is callable
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    env_path = Path(__file__).resolve().parents[1] / "config" / "secrets.env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded environment variables from %s", env_path)

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()

    if not api_key and base_url:
        # Many OpenAI-compatible local providers accept any key, but the SDK expects one.
        api_key = "ollama"

    if not api_key and not base_url:
        raise SystemExit(
            "OPENAI_API_KEY not found. Set it in config/secrets.env or your shell environment "
            "(or set OPENAI_BASE_URL for an OpenAI-compatible provider)."
        )

    client = OpenAI(api_key=api_key, base_url=base_url or None)

    logger.info("Listing models visible to this API key…")
    try:
        models = client.models.list()
        model_ids = [m.id for m in models.data]
    except Exception as e:
        raise SystemExit(f"Failed to list models: {type(e).__name__}: {e}") from e

    interesting = [m for m in model_ids if ("gpt-5" in m) or m.startswith("o3") or m.startswith("o4")]
    if interesting:
        print("\nAccessible high-tier models:")
        for m in sorted(interesting):
            print(f"- {m}")
    else:
        print("\nNo GPT‑5 / o‑series models found for this key.")

    model_name = "gpt-4.1-mini"
    logger.info("Testing a small chat completion using %s…", model_name)
    try:
        client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=8,
        )
        print(f"\nSUCCESS: {model_name} is accessible.")
    except Exception as e:
        print(f"\nFAILED: {model_name} call failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()


