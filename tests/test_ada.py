import asyncio
from types import SimpleNamespace

from ada.brain import Brain, Conversation
from ada.config import DEFAULT_SAFE_COMMANDS, Config
from ada.telegram_bot import split_message
from ada.tools import ToolRequest, execute, is_safe_command


def cfg(tmp_path):
    return Config(telegram_token="t", allowed_user_ids={1}, workdir=str(tmp_path))


# ---- safe-command rules --------------------------------------------------

def test_safe_commands_run_without_approval():
    assert is_safe_command("docker ps", DEFAULT_SAFE_COMMANDS)
    assert is_safe_command("docker ps -a", DEFAULT_SAFE_COMMANDS)
    assert is_safe_command("df -h", DEFAULT_SAFE_COMMANDS)


def test_changes_and_chaining_need_approval():
    for cmd in [
        "docker restart plex",
        "rm -rf /mnt/user",
        "docker ps; rm -rf /",
        "ls && reboot",
        "cat x | sh",
        "ls > /etc/passwd",
        "echo $(reboot)",
        "ls `reboot`",
        "dfx",  # prefix must be a whole word
        "",
    ]:
        assert not is_safe_command(cmd, DEFAULT_SAFE_COMMANDS), cmd


def test_tool_approval_classification():
    safe = DEFAULT_SAFE_COMMANDS
    assert not ToolRequest("read_file", {"path": "x"}).needs_approval(safe)
    assert not ToolRequest("list_dir", {"path": "x"}).needs_approval(safe)
    assert ToolRequest("write_file", {"path": "x", "content": ""}).needs_approval(safe)
    assert ToolRequest("edit_file", {}).needs_approval(safe)
    assert ToolRequest("run_command", {"command": "docker stop plex"}).needs_approval(safe)


# ---- tools ---------------------------------------------------------------

def test_file_tools(tmp_path):
    async def go():
        out, err = await execute(ToolRequest("write_file", {"path": "a/b.txt", "content": "hello world"}), str(tmp_path), 10)
        assert not err and (tmp_path / "a/b.txt").read_text() == "hello world"
        out, err = await execute(ToolRequest("edit_file", {"path": "a/b.txt", "old_text": "world", "new_text": "Ada"}), str(tmp_path), 10)
        assert not err and (tmp_path / "a/b.txt").read_text() == "hello Ada"
        out, err = await execute(ToolRequest("edit_file", {"path": "a/b.txt", "old_text": "nope", "new_text": "x"}), str(tmp_path), 10)
        assert err
        out, err = await execute(ToolRequest("list_dir", {"path": "a"}), str(tmp_path), 10)
        assert out == "b.txt"
        out, err = await execute(ToolRequest("read_file", {"path": "missing"}), str(tmp_path), 10)
        assert err
    asyncio.run(go())


def test_run_command(tmp_path):
    async def go():
        out, err = await execute(ToolRequest("run_command", {"command": "echo hi"}), str(tmp_path), 10)
        assert not err and "hi" in out
        out, err = await execute(ToolRequest("run_command", {"command": "exit 3"}), str(tmp_path), 10)
        assert err and "exit code 3" in out
        out, err = await execute(ToolRequest("run_command", {"command": "sleep 5"}), str(tmp_path), 1)
        assert err and "timed out" in out
    asyncio.run(go())


def test_split_message():
    text = "\n".join(f"line {i}" for i in range(2000))
    chunks = split_message(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


# ---- brain loop with a fake Claude --------------------------------------

def _tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _text(t):
    return SimpleNamespace(type="text", text=t)


class FakeClient:
    """Plays back scripted responses and records what Ada sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_brain_asks_approval_and_respects_deny(tmp_path):
    target = tmp_path / "note.txt"
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[
            _tool_use("t1", "run_command", {"command": "docker ps", "reason": "check"}),
            _tool_use("t2", "write_file", {"path": str(target), "content": "x", "reason": "save"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("All done.")]),
    ])
    brain = Brain(cfg(tmp_path), client=client)
    asked, notes = [], []

    async def deny(req):
        asked.append(req.name)
        return False

    async def note(n):
        notes.append(n)

    reply = asyncio.run(brain.respond(Conversation(), "hi", deny, note))
    assert reply == "All done."
    assert asked == ["write_file"]          # docker ps is on the safe list
    assert notes == ["Running: docker ps"]
    assert not target.exists()              # denied, so never written
    results = client.calls[1]["messages"][2]["content"]  # list is shared, so index from the start
    assert results[1]["is_error"] and "denied" in results[1]["content"]
    assert client.calls[0]["model"] == "claude-opus-5-5"


def test_brain_rolls_back_on_refusal(tmp_path):
    client = FakeClient([SimpleNamespace(stop_reason="refusal", content=[])])
    brain = Brain(cfg(tmp_path), client=client)
    convo = Conversation()

    async def never(req):
        raise AssertionError

    async def note(n):
        pass

    reply = asyncio.run(brain.respond(convo, "bad", never, note))
    assert "can't help" in reply
    assert convo.messages == []
