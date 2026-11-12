"""Start Ada: `python -m ada`."""

import logging

from .config import Config
from .telegram_bot import AdaBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Keep the HTTP client quiet; it logs every Telegram poll and would leak the bot token in URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    config = Config.from_env()
    logging.getLogger("ada").info(
        "Ada starting (brain=%s, allowed users=%s)",
        f"ollama {config.ollama_model} at {config.ollama_url}" if config.provider == "ollama"
        else f"claude {config.model} effort={config.effort}",
        sorted(config.allowed_user_ids),
    )
    # bootstrap_retries=-1: keep retrying if Telegram can't be reached at startup instead of crashing.
    AdaBot(config).build().run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
