"""Ada's tools: what she can do on the server, and which actions need your approval.

Reading is free. Anything that changes something (writing files, or running a
command that isn't on the safe list) goes to you as an Approve/Deny prompt first.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_CHARS = 20_000

# Characters that let one command smuggle in another (chaining, pipes, redirects, subshells).
_SHELL_META = re.compile(r"[;&|<>`$\\\n]|\$\(")

TOOLS = [
    {
        "name": "run_command",
        "description": (
            "Run a shell command on the server (bash, inside Ada's container, which can manage "
            "Docker on the host). Read-only commands on the safe list run right away; anything "
            "else is sent to the user for approval first, along with your reason. Prefer one "
            "clear command per call so the user can judge it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The exact command to run."},
                "reason": {
                    "type": "string",
                    "description": "One short sentence, shown to the user, saying why you want to run it.",
                },
            },
            "required": ["command", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file. Runs without approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path, or relative to the work folder."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_dir",
        "description": "List the files in a folder. Runs without approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path, or relative to the work folder."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content. Needs the user's approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "reason": {"type": "string", "description": "One short sentence on why."},
            },
            "required": ["path", "content", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace one exact snippet of text in a file with new text. old_text must appear "
            "exactly once. Needs the user's approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "reason": {"type": "string", "description": "One short sentence on why."},
            },
            "required": ["path", "old_text", "new_text", "reason"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class ToolRequest:
    name: str
    input: dict

    def needs_approval(self, safe_commands: list[str]) -> bool:
        if self.name in ("read_file", "list_dir"):
            return False
        if self.name == "run_command":
            return not is_safe_command(self.input.get("command", ""), safe_commands)
        return True

    def describe(self) -> str:
        """Human-readable summary for the approval prompt."""
        reason = self.input.get("reason", "")
        if self.name == "run_command":
            body = f"Run command:\n{self.input.get('command', '')}"
        elif self.name == "write_file":
            content = self.input.get("content", "")
            preview = content if len(content) <= 1500 else content[:1500] + "\n…"
            body = f"Write file {self.input.get('path')} ({len(content)} chars):\n{preview}"
        elif self.name == "edit_file":
            body = (
                f"Edit file {self.input.get('path')}\n"
                f"Replace:\n{_clip(self.input.get('old_text', ''), 700)}\n"
                f"With:\n{_clip(self.input.get('new_text', ''), 700)}"
            )
        else:
            body = f"{self.name}: {self.input}"
        return f"{reason}\n\n{body}" if reason else body


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


def is_safe_command(command: str, safe_commands: list[str]) -> bool:
    """True if the command is a single read-only command that starts with a safe-list entry."""
    cmd = command.strip()
    if not cmd or _SHELL_META.search(cmd):
        return False
    for prefix in safe_commands:
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False


def _resolve(path: str, workdir: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(workdir) / p
    return p


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + f"\n\n[… {len(text) - MAX_OUTPUT_CHARS} chars cut …]\n\n" + text[-half:]


async def execute(req: ToolRequest, workdir: str, command_timeout_s: int) -> tuple[str, bool]:
    """Run a tool. Returns (output, is_error)."""
    try:
        if req.name == "run_command":
            return await _run_command(req.input["command"], workdir, command_timeout_s)
        if req.name == "read_file":
            p = _resolve(req.input["path"], workdir)
            return _truncate(p.read_text(errors="replace")), False
        if req.name == "list_dir":
            p = _resolve(req.input["path"], workdir)
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            lines = [f"{e.name}/" if e.is_dir() else e.name for e in entries[:500]]
            if len(entries) > 500:
                lines.append(f"… and {len(entries) - 500} more")
            return "\n".join(lines) or "(empty folder)", False
        if req.name == "write_file":
            p = _resolve(req.input["path"], workdir)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req.input["content"])
            return f"Wrote {len(req.input['content'])} chars to {p}", False
        if req.name == "edit_file":
            p = _resolve(req.input["path"], workdir)
            text = p.read_text()
            count = text.count(req.input["old_text"])
            if count != 1:
                return f"old_text found {count} times in {p}; it must appear exactly once.", True
            p.write_text(text.replace(req.input["old_text"], req.input["new_text"], 1))
            return f"Edited {p}", False
        return f"Unknown tool: {req.name}", True
    except KeyError as e:
        return f"Missing input field: {e}", True
    except OSError as e:
        return f"{type(e).__name__}: {e}", True


async def _run_command(command: str, workdir: str, timeout_s: int) -> tuple[str, bool]:
    cwd = workdir if os.path.isdir(workdir) else None
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Command timed out after {timeout_s}s and was stopped.", True
    text = out.decode(errors="replace")
    result = _truncate(text) if text else "(no output)"
    return f"exit code {proc.returncode}\n{result}", proc.returncode != 0
