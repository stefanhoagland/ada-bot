"""Ada's brain on a local model served by Ollama (free, runs on your own hardware)."""

from __future__ import annotations

import json
import logging

import httpx

from .brain import MAX_TOOL_ROUNDS, SYSTEM_PROMPT, Approver, BaseBrain, BrainError, Conversation, Notifier
from .config import Config
from .tools import TOOLS, ToolRequest

log = logging.getLogger(__name__)

# Ollama uses the function-calling format: {"type": "function", "function": {...}}.
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOLS
]

LOCAL_MODEL_NOTE = (
    "\nYou are running on a local model. Use one tool call at a time, wait for its result, "
    "and only claim something worked after you've seen the output."
)


class OllamaBrain(BaseBrain):
    def __init__(self, config: Config, http: httpx.AsyncClient | None = None):
        super().__init__(config)
        self.http = http or httpx.AsyncClient(
            base_url=config.ollama_url.rstrip("/"),
            # Local models can be slow, especially on the first message while the model loads.
            timeout=httpx.Timeout(config.ollama_timeout_s, connect=10.0),
        )

    async def _call(self, messages: list) -> dict:
        payload = {
            "model": self.config.ollama_model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT + LOCAL_MODEL_NOTE}, *messages],
            "tools": OLLAMA_TOOLS,
            "stream": False,
            "options": {"num_ctx": self.config.ollama_num_ctx},
        }
        try:
            resp = await self.http.post("/api/chat", json=payload)
        except httpx.ConnectError:
            raise BrainError(
                f"I couldn't reach Ollama at {self.config.ollama_url}. "
                "Is the Ollama container running, and is OLLAMA_URL right?"
            )
        except httpx.TimeoutException:
            raise BrainError("The local model took too long to answer. Try again, or use a smaller model.")
        if resp.status_code == 404:
            raise BrainError(
                f"Ollama doesn't have the model '{self.config.ollama_model}'. "
                f"Pull it on the server with: docker exec ollama ollama pull {self.config.ollama_model}"
            )
        if resp.status_code >= 400:
            detail = resp.text[:300]
            if "does not support tools" in detail:
                raise BrainError(
                    f"The model '{self.config.ollama_model}' can't use tools. "
                    "Pick one that can, such as qwen3:8b or llama3.1:8b."
                )
            raise BrainError(f"Ollama returned an error ({resp.status_code}): {detail}")
        return resp.json()

    async def respond(
        self, convo: Conversation, user_text: str, approve: Approver, notify: Notifier
    ) -> str:
        start_len = len(convo.messages)
        convo.messages.append({"role": "user", "content": f"{self._now_note()} {user_text}"})
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                data = await self._call(convo.messages)
                msg = data.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                # Keep only the fields Ollama expects back in history.
                convo.messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                })
                if not tool_calls:
                    return (msg.get("content") or "").strip() or "Done."

                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    output, is_error = await self._run_tool(ToolRequest(name, args), approve, notify)
                    convo.messages.append({
                        "role": "tool",
                        "tool_name": name,
                        "content": ("ERROR: " if is_error else "") + output,
                    })
            return "I stopped after a lot of steps without finishing. Tell me how you'd like to continue."
        except BrainError:
            del convo.messages[start_len:]
            raise
