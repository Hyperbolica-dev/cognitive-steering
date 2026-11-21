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
MAX_CONTEXT_BYTES = 8192
SESSION_SOURCES = ("startup", "resume", "clear", "compact", "fork")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


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


def select_instruction(event, drift_feedback: Optional[DriftFeedback] = None):
    event_name = event.get("hook_event_name")
    if event_name == "SessionStart":
        return "instruct.md" if event.get("source") in SESSION_SOURCES else None
    if event_name == "UserPromptSubmit":
        return "reminder.md"
    if event_name != "PostToolUse":
        return None

    session_id = event.get("session_id")
    tool_use_id = event.get("tool_use_id")
    if not all(isinstance(identifier, str) and identifier.strip() for identifier in (session_id, tool_use_id)):
        raise ValueError("PostToolUse requires session_id and tool_use_id")
    progress = record_call(data_directory(), session_id, tool_use_id)
    if progress is None:
        return None
    if progress.completed_calls % REMINDER_INTERVAL == 0:
        return "reminder.md"
    if drift_feedback is not None and drift_feedback(progress):
        return "reminder.md"
    return None


def read_instruction(filename):
    if os.environ.get("PLUGIN_DATA"):
        try:
            with (data_directory() / filename).open("rb") as source:
                return decode_instruction(source)
        except FileNotFoundError:
            pass
    with (PLUGIN_ROOT / filename).open("rb") as source:
        return decode_instruction(source)


def decode_instruction(source):
    content = source.read(MAX_CONTEXT_BYTES + 1)
    if len(content) > MAX_CONTEXT_BYTES:
        raise ValueError("Instruction file exceeds the context limit")
    return content.decode("utf-8")


def main():
    try:
        event = json.load(sys.stdin.buffer)
        if not isinstance(event, dict):
            raise ValueError("Expected a hook event object")
        filename = select_instruction(event)
        if filename is not None:
            context = read_instruction(filename)
            if context.strip():
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": event["hook_event_name"],
                        "additionalContext": context,
                    }
                }))
    except (OSError, ValueError, sqlite3.Error, RecursionError):
        print("cognitive-steering: skipped an invalid event, instruction, or state update", file=sys.stderr)


if __name__ == "__main__":
    main()
