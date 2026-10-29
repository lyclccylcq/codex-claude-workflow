"""Offline checks for live trace capture, partial recovery and safe visible mirrors."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_dispatch as dispatch
import ai_trace


class StreamTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workflow-trace-")
        self.addCleanup(temporary.cleanup)
        self.location = Path(temporary.name).resolve()
        controller = self.location / "controller"
        controller.mkdir()
        worker = self.location / "worker"
        worker.mkdir()
        (worker / ".git").write_text("offline test marker")
        run = controller / ".ai-workflow/runs/TASK-9999-abcdef1234"
        run.mkdir(parents=True)
        (run / "spec.json").write_text('{"task_id":"TASK-9999"}')
        self.state = {"controller": str(controller), "workspace": str(worker), "run_dir": str(run),
                      "trace_enabled": True, "status": "RUNNING", "round": 1,
                      "task_id": "TASK-9999", "session_id": "12345678-1234-1234-1234-123456789abc",
                      "claude_config_dir": str(self.location / "account"), "session_name": "Trace test"}

    def test_tool_trace_is_visible_before_exit_and_both_pipes_are_drained(self):
        ai_trace.sync(self.state)
        stdout = Path(self.state["run_dir"]) / "round-1/claude.json"
        stderr = stdout.with_name("claude.stderr")
        gate = self.location / "continue"
        item = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/example.py", "new_string": "PRIVATE_PAYLOAD"}}]}}
        script = ("import pathlib, sys, time\n"
                  "print(" + repr(json.dumps(item)) + ", flush=True)\n"
                  "sys.stderr.write('x' * 200000); sys.stderr.flush()\n"
                  "while not pathlib.Path(" + repr(str(gate)) + ").exists(): time.sleep(.01)\n"
                  "print('{\"type\":\"result\"}', flush=True)\n")
        seen = []

        def observe(line):
            ai_trace.stream_event(self.state, line)
            if b"PRIVATE_PAYLOAD" in line:
                # The child cannot exit before this callback reads its flushed trace.
                activity = (ai_trace.worker_dir(self.state) / "activity.log").read_text()
                self.assertIn("Edit src/example.py", activity)
                self.assertNotIn("PRIVATE_PAYLOAD", activity)
                self.assertIn(b"PRIVATE_PAYLOAD", stdout.read_bytes())
                seen.append(True)
                gate.touch()

        code, out, err = dispatch.run([sys.executable, "-c", script], self.location, timeout=4,
            stream={"stdout_path": stdout, "stderr_path": stderr, "on_line": observe})
        self.assertEqual(code, 0)
        self.assertEqual(seen, [True])
        self.assertEqual(stdout.read_bytes(), out)
        self.assertEqual(stderr.read_bytes(), err)
        self.assertEqual(len(err), 200000)
        self.assertEqual(stdout.stat().st_mode & 0o777, 0o600)

    def test_timeout_retains_partial_output_and_stops_process_group(self):
        stdout = self.location / "partial.json"
        stderr = self.location / "partial.stderr"
        script = "import time; print('saved partial', flush=True); time.sleep(20)"
        with self.assertRaises(dispatch.ProcessInterrupted) as raised:
            dispatch.run([sys.executable, "-c", script], self.location, timeout=.3,
                stream={"stdout_path": stdout, "stderr_path": stderr, "on_line": lambda line: None})
        self.assertEqual(raised.exception.stdout, b"saved partial\n")
        self.assertEqual(stdout.read_bytes(), raised.exception.stdout)

    def test_visible_mirrors_retain_session_and_distinguish_host_review(self):
        ai_trace.sync(self.state)
        self.state.update(status="AWAITING_REVIEW", summary="Implementation returned")
        ai_trace.sync(self.state)
        trace = ai_trace.worker_dir(self.state)
        self.assertIn("AWAITING_REVIEW", (trace / "README.md").read_text())
        self.assertEqual((trace / "RESULT.md").read_text().splitlines()[0], "STATUS: AWAITING_REVIEW")
        self.assertIn(self.state["session_id"], (trace / "SESSION.md").read_text())
        self.assertIn("open-history", (trace / "SESSION.md").read_text())
        self.assertTrue((Path(self.state["controller"]) / "AI_WORK/README.md").is_file())
        events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
        self.assertEqual([row["message"] for row in events], ["RUNNING", "AWAITING_REVIEW"])

    def test_trace_directory_symlink_cannot_redirect_host_writes(self):
        elsewhere = self.location / "elsewhere"
        elsewhere.mkdir()
        (Path(self.state["workspace"]) / "AI_WORK").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(OSError):
            ai_trace.sync(self.state)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_stream_never_echoes_tool_results_or_reasoning(self):
        ai_trace.sync(self.state)
        for item in (
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "PRIVATE_REASONING"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "PRIVATE_RESULT"}]}},
        ):
            ai_trace.stream_event(self.state, json.dumps(item))
        self.assertNotIn("PRIVATE", (ai_trace.worker_dir(self.state) / "activity.log").read_text())
