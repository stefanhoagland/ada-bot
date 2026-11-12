"""Settings for Ada, read from environment variables (set them in Unraid's container settings)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SAFE_COMMANDS = [
    "docker ps",
    "docker stats --no-stream",
    "docker logs",
    "docker inspect",
    "df",
    "du",
    "free",
    "uptime",
    "uname",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "date",
    "whoami",
    "hostname",
    "git status",
    "git log",
    "git diff",
]


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    telegram_token: str
    allowed_user_ids: set[int]
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    workdir: str = "/workspace"
    safe_commands: list[str] = field(default_factory=lambda: list(DEFAULT_SAFE_COMMANDS))
    approval_timeout_s: int = 600
    command_timeout_s: int = 300
    max_history_messages: int = 200
    timezone: str = "America/New_York"
    provider: str = "claude"  # "claude" or "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_num_ctx: int = 16384
    ollama_timeout_s: int = 600

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        # Default: Claude if there's an API key, otherwise the free local model.
        provider = os.environ.get("ADA_PROVIDER", "").strip().lower() or ("claude" if has_key else "ollama")
        if provider not in ("claude", "ollama"):
            raise SystemExit("ADA_PROVIDER must be 'claude' or 'ollama'.")
        if provider == "claude" and not has_key:
            raise SystemExit("ADA_PROVIDER is 'claude' but ANTHROPIC_API_KEY is not set.")
        ids_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        if not ids_raw:
            raise SystemExit(
                "TELEGRAM_ALLOWED_USER_IDS is not set. Ada refuses to start without it, "
                "so strangers can't control your server. Message @userinfobot on Telegram to get your ID."
            )
        allowed = {int(x) for x in ids_raw.split(",") if x.strip()}
        return cls(
            telegram_token=token,
            allowed_user_ids=allowed,
            model=os.environ.get("ADA_MODEL", "claude-opus-5-5"),
            effort=os.environ.get("ADA_EFFORT", "medium"),
            workdir=os.environ.get("ADA_WORKDIR", "/workspace"),
            safe_commands=_list("ADA_SAFE_COMMANDS", list(DEFAULT_SAFE_COMMANDS)),
            approval_timeout_s=int(os.environ.get("ADA_APPROVAL_TIMEOUT", "600")),
            command_timeout_s=int(os.environ.get("ADA_COMMAND_TIMEOUT", "300")),
            max_history_messages=int(os.environ.get("ADA_MAX_HISTORY", "200")),
            timezone=os.environ.get("TZ", "America/New_York"),
            provider=provider,
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "16384")),
            ollama_timeout_s=int(os.environ.get("OLLAMA_TIMEOUT", "600")),
        )
