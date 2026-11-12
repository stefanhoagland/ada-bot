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
        "Ada starting (model=%s, effort=%s, allowed users=%s)",
        config.model, config.effort, sorted(config.allowed_user_ids),
    )
    AdaBot(config).build().run_polling()


if __name__ == "__main__":
    main()
