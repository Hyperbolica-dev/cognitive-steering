import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


REMINDER_INTERVAL = 10
MAX_CONTEXT_BYTES = 16 * 1024
SESSION_SOURCES = ("startup", "resume", "clear", "compact", "fork")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CHECKTONE_CONTEXT = (
    "依据完整指令直接给出上一轮输出或指定对象的修订。"
    "仅在用户主动询问时说明修订原因。涉及文件时遵守用户授权范围。"
)


def is_checktone(event):
    if event.get("hook_event_name") != "UserPromptSubmit":
        return False
    prompt = event.get("prompt")
    if not isinstance(prompt, str):
        return False
    command = prompt.strip().split(maxsplit=1)
    return bool(command and command[0] in ("/checktone", "checktone"))


@dataclass(frozen=True)
class ToolProgress:
    session_key: str
    completed_calls: int


DriftFeedback = Callable[[ToolProgress], bool]


def data_directory():
    configured_path = os.environ.get("PLUGIN_DATA")
    if not configured_path or not Path(configured_path).is_absolute():
        raise ValueError("PLUGIN_DATA must name an absolute directory")
    return Path(configured_path)


def record_call(directory, session_id, tool_use_id):
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    call_key = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "counters.sqlite3", timeout=3)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tool_calls ("
                "session_key TEXT NOT NULL, call_key TEXT NOT NULL, "
                "PRIMARY KEY (session_key, call_key)) WITHOUT ROWID"
            )
            inserted = connection.execute(
                "INSERT OR IGNORE INTO tool_calls VALUES (?, ?)", (session_key, call_key)
            )
            if inserted.rowcount == 0:
                return None
            connection.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                "session_key TEXT PRIMARY KEY, completed_calls INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO counters VALUES (?, 1) "
                "ON CONFLICT(session_key) DO UPDATE SET completed_calls = completed_calls + 1",
                (session_key,),
            )
            count, = connection.execute(
                "SELECT completed_calls FROM counters WHERE session_key = ?", (session_key,)
            ).fetchone()
            return ToolProgress(session_key, count)


def select_instructions(event, drift_feedback: Optional[DriftFeedback] = None):
    event_name = event.get("hook_event_name")
    if event_name == "SessionStart":
        return ("instruct.md",) if event.get("source") in SESSION_SOURCES else ()
    if event_name == "UserPromptSubmit":
        return ("instruct.md",) if is_checktone(event) else ("reminder.md",)
    if event_name != "PostToolUse":
        return ()

    session_id = event.get("session_id")
    tool_use_id = event.get("tool_use_id")
    if not all(isinstance(identifier, str) and identifier.strip() for identifier in (session_id, tool_use_id)):
        raise ValueError("PostToolUse requires session_id and tool_use_id")
    progress = record_call(data_directory(), session_id, tool_use_id)
    if progress is None:
        return ()
    if progress.completed_calls % REMINDER_INTERVAL == 0:
        return ("reminder.md", "post_tool.md")
    if drift_feedback is not None and drift_feedback(progress):
        return ("reminder.md", "post_tool.md")
    return ("post_tool.md",)


def read_instruction(filename):
    override_error = None
    if os.environ.get("PLUGIN_DATA"):
        try:
            with (data_directory() / filename).open("rb") as source:
                return decode_instruction(source), None
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            override_error = f"覆盖指令 {filename}{instruction_error(error)}。"
    try:
        with (PLUGIN_ROOT / filename).open("rb") as source:
            context = decode_instruction(source)
        if not context.strip():
            raise ValueError("为空")
    except (OSError, ValueError) as error:
        return "", f"{override_error or ''}默认指令 {filename}{instruction_error(error)}，已跳过该文件的注入。"
    return context, f"{override_error}已回退到默认指令。" if override_error else None


def instruction_error(error):
    if isinstance(error, FileNotFoundError):
        return "不存在"
    if isinstance(error, OSError):
        return "无法读取"
    return str(error)


def decode_instruction(source):
    content = source.read(MAX_CONTEXT_BYTES + 1)
    if len(content) > MAX_CONTEXT_BYTES:
        raise ValueError("超过 16 KiB")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("不是有效的 UTF-8") from None


def main():
    try:
        event = json.load(sys.stdin.buffer)
        if not isinstance(event, dict):
            raise ValueError("Expected a hook event object")
        context = ""
        notices = []
        for filename in select_instructions(event):
            instruction, notice = read_instruction(filename)
            if notice:
                notices.append(notice)
            if instruction.strip():
                context = f"{context.rstrip()}\n\n{instruction}" if context else instruction
        output = {"systemMessage": " ".join(notices)} if notices else {}
        if context:
            if is_checktone(event):
                context = f"{context.rstrip()}\n\n{CHECKTONE_CONTEXT}\n"
            output["hookSpecificOutput"] = {
                "hookEventName": event["hook_event_name"],
                "additionalContext": context,
            }
        if output:
            print(json.dumps(output))
    except (OSError, ValueError, sqlite3.Error, RecursionError):
        print("cognitive-steering: skipped an invalid event, instruction, or state update", file=sys.stderr)


if __name__ == "__main__":
    main()
