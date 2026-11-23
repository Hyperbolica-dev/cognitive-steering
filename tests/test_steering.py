import hashlib
import json
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
MARKETPLACE = json.loads((REPO / ".agents/plugins/marketplace.json").read_text("utf-8"))
PACKAGE = REPO / MARKETPLACE["plugins"][0]["source"]["path"]
MANIFEST = json.loads((PACKAGE / ".codex-plugin/plugin.json").read_text("utf-8"))
HOOKS = json.loads((PACKAGE / MANIFEST["hooks"]).read_text("utf-8"))["hooks"]
STEERING = runpy.run_path(str(PACKAGE / "hooks/steering.py"))


class SteeringTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = Path(directory)
        self.plugin = self.workspace / "installed plugin"
        shutil.copytree(PACKAGE, self.plugin)
        self.data = self.workspace / "plugin data"
        self.database = self.data / "counters.sqlite3"
        self.environment = {**os.environ, "PLUGIN_ROOT": str(self.plugin), "PLUGIN_DATA": str(self.data)}

    def invoke(self, event, fields=None, raw=None, environment=None):
        payload = {"session_id": "session-a", "hook_event_name": event, **(fields or {})}
        command = HOOKS[event][0]["hooks"][0]["command"]
        return subprocess.run(
            ["sh", "-c", command],
            input=json.dumps(payload) if raw is None else raw,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=self.workspace,
            env=self.environment if environment is None else environment,
            timeout=10,
            check=True,
        )

    def assert_context(self, result, event, *filenames):
        self.assertEqual(result.stderr, "")
        contents = [(self.plugin / filename).read_text("utf-8") for filename in filenames]
        expected = "\n\n".join([part.rstrip() for part in contents[:-1]] + contents[-1:])
        self.assertEqual(json.loads(result.stdout), {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": expected,
            }
        })

    def test_package_wires_only_the_three_requested_hooks(self):
        self.assertEqual(MARKETPLACE["name"], "hyperbolica-dev")
        self.assertEqual(MANIFEST["name"], MARKETPLACE["plugins"][0]["name"])
        self.assertEqual(MANIFEST["author"]["name"], MARKETPLACE["name"])
        self.assertEqual(set(HOOKS), {"SessionStart", "UserPromptSubmit", "PostToolUse"})
        self.assertNotIn("matcher", HOOKS["PostToolUse"][0])

    def test_session_start_sources(self):
        matcher = HOOKS["SessionStart"][0]["matcher"]
        for source in ("startup", "resume", "clear", "compact", "fork"):
            with self.subTest(source=source):
                self.assertIsNotNone(re.search(matcher, source))
                result = self.invoke("SessionStart", {"source": source})
                self.assert_context(result, "SessionStart", "instruct.md")
        self.assertEqual(self.invoke("SessionStart", {"source": "other"}).stdout, "")
        self.assertFalse(self.data.exists())

    def test_prompt_uses_live_overrides_and_preserves_workspace_instructions(self):
        agents = self.workspace / "AGENTS.md"
        agents.write_text("Existing project instructions", encoding="utf-8")
        self.assert_context(self.invoke("UserPromptSubmit"), "UserPromptSubmit", "reminder.md")
        self.data.mkdir()
        for filename, event, fields in (
            ("reminder.md", "UserPromptSubmit", {"prompt": "PRIVATE_PROMPT"}),
            ("instruct.md", "SessionStart", {"source": "resume"}),
        ):
            override = self.data / filename
            for content in ("第一版\n", "直接使用更新的指令。\n"):
                override.write_text(content, encoding="utf-8")
                result = self.invoke(event, fields)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"], content)
            override.unlink()
            self.assert_context(self.invoke(event, fields), event, filename)
        self.assertEqual(agents.read_text("utf-8"), "Existing project instructions")
        self.assertFalse(self.database.exists())

    def test_explicit_feedback_reloads_full_instructions(self):
        self.data.mkdir()
        instruction = self.data / "instruct.md"
        instruction.write_text("Updated principles\n", encoding="utf-8")
        for prompt in ("/checktone", "  checktone README.md ", "/checktone previous reply"):
            with self.subTest(prompt=prompt):
                result = self.invoke("UserPromptSubmit", {"prompt": prompt})
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"], {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "Updated principles\n\n" + STEERING["CHECKTONE_CONTEXT"] + "\n",
                })
        for prompt in ("Explain /checktone", "/checktoner", None, 123):
            with self.subTest(prompt=prompt):
                self.assert_context(self.invoke("UserPromptSubmit", {"prompt": prompt}), "UserPromptSubmit", "reminder.md")
        instruction.write_text("\n", encoding="utf-8")
        result = self.invoke("UserPromptSubmit", {"prompt": "/checktone"})
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertFalse(self.database.exists())

    def test_every_tenth_tool_call_across_turns_and_lifecycle_events(self):
        for index in range(1, 22):
            if index == 6:
                self.invoke("UserPromptSubmit", {"turn_id": "turn-2"})
            if index == 8:
                result = self.invoke("UserPromptSubmit", {"turn_id": "turn-2", "prompt": "checktone"})
                self.assertIn(STEERING["CHECKTONE_CONTEXT"], json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
            if index in (9, 15):
                self.invoke("SessionStart", {"source": "compact" if index == 9 else "resume"})
            result = self.invoke("PostToolUse", {
                "tool_use_id": f"call-{index}",
                "turn_id": "turn-1" if index < 6 else "turn-2",
            })
            if index in (10, 20):
                self.assert_context(result, "PostToolUse", "reminder.md", "post_tool.md")
            else:
                self.assert_context(result, "PostToolUse", "post_tool.md")

    def test_concurrent_deliveries_are_isolated_and_deduplicated(self):
        jobs = [("session-a", index) for index in range(25)]
        jobs += [("session-b", index) for index in range(15)]
        jobs += [("session-a", index) for index in range(8)]

        def submit(job):
            session_id, call_id = job
            result = self.invoke("PostToolUse", {
                "session_id": session_id,
                "tool_use_id": f"call-{call_id}",
                "tool_input": {"content": "PRIVATE_TOOL_INPUT"},
                "tool_response": {"content": "PRIVATE_TOOL_RESPONSE"},
            })
            self.assertEqual(result.stderr, "")
            if not result.stdout:
                return session_id, False, False
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            periodic = context.startswith((self.plugin / "reminder.md").read_text("utf-8").rstrip())
            files = ("reminder.md", "post_tool.md") if periodic else ("post_tool.md",)
            self.assert_context(result, "PostToolUse", *files)
            return session_id, True, periodic

        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(submit, jobs))
        for session_id, calls, reminders in (("session-a", 25, 2), ("session-b", 15, 1)):
            outcomes_for_session = [(injected, periodic) for session, injected, periodic in outcomes
                                    if session == session_id]
            self.assertEqual(sum(injected for injected, _ in outcomes_for_session), calls)
            self.assertEqual(sum(periodic for _, periodic in outcomes_for_session), reminders)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(dict(connection.execute("SELECT * FROM counters")), {
                hashlib.sha256(b"session-a").hexdigest(): 25,
                hashlib.sha256(b"session-b").hexdigest(): 15,
            })
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone(), (40,))
        db_bytes = self.database.read_bytes()
        for token in (b"PRIVATE_TOOL_INPUT", b"PRIVATE_TOOL_RESPONSE", b"session-a", b"call-0"):
            self.assertNotIn(token, db_bytes)

    def test_invalid_payload_and_missing_identifiers_fail_open(self):
        for raw in ("", "{SENSITIVE_INVALID_DATA", "[]", '{"hook_event_name":"PostToolUse"}'):
            with self.subTest(raw=raw):
                result = self.invoke("PostToolUse", raw=raw)
                self.assertEqual(result.stdout, "")
                self.assertIn("cognitive-steering:", result.stderr)
                self.assertNotIn("SENSITIVE_INVALID_DATA", result.stderr)
        self.assertFalse(self.data.exists())
        unknown = self.invoke("PostToolUse", {"hook_event_name": "Unknown"})
        self.assertEqual((unknown.stdout, unknown.stderr), ("", ""))

    def test_instruction_errors_and_empty_override(self):
        self.data.mkdir()
        override = self.data / "reminder.md"
        override.write_bytes(b" \n")
        empty = self.invoke("UserPromptSubmit")
        self.assertEqual((empty.stdout, empty.stderr), ("", ""))
        limit = 16 * 1024
        override.write_bytes(b"x" * limit)
        at_limit = self.invoke("UserPromptSubmit")
        self.assertEqual(json.loads(at_limit.stdout)["hookSpecificOutput"]["additionalContext"], "x" * limit)
        for invalid, reason in ((b"\xff", "不是有效的 UTF-8"), (b"x" * (limit + 1), "超过 16 KiB")):
            with self.subTest(reason=reason):
                override.write_bytes(invalid)
                result = self.invoke("UserPromptSubmit")
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), {
                    "systemMessage": f"覆盖指令 reminder.md{reason}。已回退到默认指令。",
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": (self.plugin / "reminder.md").read_text("utf-8"),
                    },
                })

    def test_invalid_default_instruction_reports_error_without_context(self):
        instruction = self.plugin / "instruct.md"
        for invalid, reason in ((b"\xff", "不是有效的 UTF-8"), (b" \n", "为空"), (None, "不存在")):
            with self.subTest(reason=reason):
                if invalid is None:
                    instruction.unlink()
                else:
                    instruction.write_bytes(invalid)
                result = self.invoke("SessionStart", {"source": "resume"})
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), {
                    "systemMessage": f"默认指令 instruct.md{reason}，已跳过该文件的注入。",
                })

    def test_post_tool_override_and_periodic_reminder_are_independent(self):
        self.data.mkdir()
        override = self.data / "post_tool.md"
        custom = "回到用户的问题。\n"
        override.write_text(custom, encoding="utf-8")
        first = self.invoke("PostToolUse", {"tool_use_id": "call-1"})
        self.assertEqual(json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"], custom)

        override.write_bytes(b"\xff")
        second = self.invoke("PostToolUse", {"tool_use_id": "call-2"})
        fallback = json.loads(second.stdout)
        self.assertIn("post_tool.md不是有效的 UTF-8。已回退到默认指令。", fallback["systemMessage"])
        self.assertEqual(fallback["hookSpecificOutput"]["additionalContext"], (self.plugin / "post_tool.md").read_text("utf-8"))

        override.write_text("\n", encoding="utf-8")
        for index in range(3, 10):
            result = self.invoke("PostToolUse", {"tool_use_id": f"call-{index}"})
            self.assertEqual((result.stdout, result.stderr), ("", ""))
        tenth = self.invoke("PostToolUse", {"tool_use_id": "call-10"})
        self.assert_context(tenth, "PostToolUse", "reminder.md")

    def test_unavailable_state_fails_open_without_fallback_writes(self):
        env = self.environment.copy()
        for path in (None, "relative-state"):
            if path is None:
                env.pop("PLUGIN_DATA")
            else:
                env["PLUGIN_DATA"] = path
            result = self.invoke("PostToolUse", {"tool_use_id": "call-1"}, environment=env)
            self.assertEqual(result.stdout, "")
            self.assertIn("cognitive-steering:", result.stderr)
        self.assertFalse((self.workspace / "relative-state").exists())
        self.data.mkdir()
        self.database.write_bytes(b"invalid database content")
        corrupt = self.invoke("PostToolUse", {"tool_use_id": "call-1"})
        self.assertEqual(corrupt.stdout, "")
        self.assertIn("cognitive-steering:", corrupt.stderr)
        self.assertEqual(self.database.read_bytes(), b"invalid database content")

    def test_drift_feedback_extension_is_opt_in(self):
        received = []

        def report_drift(progress):
            received.append(progress)
            return True

        def event(call_id):
            return {"hook_event_name": "PostToolUse", "session_id": "feedback-session", "tool_use_id": call_id}

        select = STEERING["select_instructions"]
        with patch.dict(os.environ, {"PLUGIN_DATA": str(self.data)}):
            self.assertEqual(select(event("a")), ("post_tool.md",))
            self.assertEqual(select(event("b"), drift_feedback=report_drift), ("reminder.md", "post_tool.md"))
            self.assertEqual(select(event("b"), drift_feedback=report_drift), ())
        self.assertEqual(received, [STEERING["ToolProgress"](
            hashlib.sha256(b"feedback-session").hexdigest(), 2
        )])
