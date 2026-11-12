"""Ada's brain: the conversation with Claude and the tool-use loop with approval gates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import anthropic

from .config import Config
from .tools import TOOLS, ToolRequest, execute

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 40

SYSTEM_PROMPT = """You are Ada, a personal assistant for one person, who talks to you over Telegram. \
You run in a Docker container on their Unraid home server.

What you help with:
- Managing the homelab: checking containers, disks, logs and system health, and fixing problems.
- Writing and editing code.
- Answering questions and thinking things through.

How you work:
- You can run shell commands and read, write and edit files with your tools. The Docker CLI is \
available and talks to the host's Docker, so `docker` commands affect the real server.
- Read-only commands on a safe list run immediately. Anything that changes something is shown to \
the user with Approve and Deny buttons, together with your one-line reason, and waits for them. \
Give a clear, honest reason. If they deny an action, don't try to get around it with a different \
command; ask what they'd prefer.
- Look before you act: check the current state before changing it, and verify afterwards.
- Never run destructive commands (deleting data, formatting disks, removing volumes, `docker \
system prune`) unless the user explicitly asked for exactly that.
- If a request is ambiguous and getting it wrong would be costly, ask one short question first.

How you write:
- Your replies are read on a phone in Telegram. Keep them short and conversational. Use plain \
text; short lists are fine. Put commands and code in ``` blocks.
- Lead with the answer or the result, then the detail if it's needed.
"""

# Called with a pending action; returns True if the user approved it.
Approver = Callable[[ToolRequest], Awaitable[bool]]
# Called with a short progress note ("Running: docker ps").
Notifier = Callable[[str], Awaitable[None]]


@dataclass
class Conversation:
    messages: list = field(default_factory=list)

    def reset(self) -> None:
        self.messages.clear()


class Brain:
    def __init__(self, config: Config, client: anthropic.AsyncAnthropic | None = None):
        self.config = config
        self.client = client or anthropic.AsyncAnthropic()

    def _now_note(self) -> str:
        try:
            now = datetime.now(ZoneInfo(self.config.timezone))
        except Exception:
            now = datetime.now().astimezone()
        return now.strftime("[%A %Y-%m-%d %H:%M %Z]")

    async def _call(self, messages: list):
        return await self.client.beta.messages.create(
            model=self.config.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": self.config.effort},
            cache_control={"type": "ephemeral"},
            # If the main model declines a request, the API retries it on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    async def respond(
        self,
        convo: Conversation,
        user_text: str,
        approve: Approver,
        notify: Notifier,
    ) -> str:
        """Handle one user message end to end and return Ada's final reply."""
        start_len = len(convo.messages)
        convo.messages.append(
            {"role": "user", "content": f"{self._now_note()} {user_text}"}
        )

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await self._call(convo.messages)

                if response.stop_reason == "refusal":
                    # Roll back this turn so the conversation stays usable.
                    del convo.messages[start_len:]
                    return "I can't help with that one. Try rephrasing, or ask me something else."

                convo.messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "pause_turn":
                    continue

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason != "tool_use" or not tool_uses:
                    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                    if response.stop_reason == "max_tokens":
                        text += "\n\n(I hit my reply length limit, so this may be cut off.)"
                    return text or "Done."

                results = []
                for block in tool_uses:
                    results.append(await self._run_tool(block, approve, notify))
                convo.messages.append({"role": "user", "content": results})

            return "I stopped after a lot of steps without finishing. Tell me how you'd like to continue."
        except anthropic.APIError:
            # Leave history as it was before this message so the next one starts clean.
            del convo.messages[start_len:]
            raise

    async def _run_tool(self, block, approve: Approver, notify: Notifier) -> dict:
        req = ToolRequest(name=block.name, input=dict(block.input or {}))
        if req.needs_approval(self.config.safe_commands):
            approved = await approve(req)
            if not approved:
                return {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "The user denied this action (or didn't answer in time). It was not run.",
                    "is_error": True,
                }
        elif req.name == "run_command":
            await notify(f"Running: {req.input.get('command', '')}")

        output, is_error = await execute(
            req, self.config.workdir, self.config.command_timeout_s
        )
        log.info("tool %s error=%s", req.name, is_error)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
            "is_error": is_error,
        }
