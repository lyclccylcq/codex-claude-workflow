"""Offline dispatcher tests: quota checkpoints, exact sessions and trusted execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_dispatch as dispatch
from test_ai_workflow import TASK, spec


class AuthAndConfigTests(unittest.TestCase):
    def auth_capabilities(self, auth):
        help_raw = b"--safe-mode --setting-sources --json-schema --permission-prompts --tools --allowedTools --session-id --resume --name --restricted --output-format --verbose --desktop"
        responses = [(0, help_raw, b""), (0, json.dumps(auth).encode(), b""), (0, b"Claude Test", b"")]
        with mock.patch.object(dispatch.shutil, "which", return_value="/tmp/fake-claude"), \
                mock.patch.object(dispatch, "managed_claude_sources", return_value=[]), \
                mock.patch.object(dispatch, "run", side_effect=responses) as run:
            result = dispatch.capabilities("/tmp/test-personal-account")
            self.auth_call = run.call_args_list[1]
            return result

    def test_subscription_oauth_is_accepted_without_guessing_quota(self):
        for method in ("claude.ai", "oauth", "oauth_token"):
            with self.subTest(method=method):
                result = self.auth_capabilities({"loggedIn": True, "authMethod": method, "apiProvider": "firstParty", "subscriptionType": "max"})
                self.assertTrue(result["logged_in"])
                self.assertIsNone(result["claude_usage_remaining_percent"])
                self.assertFalse(result["live_model_request_verified"])
                argv = self.auth_call.args[0]
                self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
                self.assertEqual(self.auth_call.kwargs["env"]["ANTHROPIC_BASE_URL"], dispatch.CLAUDE_API_URL)
                self.assertEqual(self.auth_call.kwargs["env"]["CLAUDE_CONFIG_DIR"], "/tmp/test-personal-account")

    def test_account_directory_is_explicit_and_not_inherited_from_the_shell(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/school-account"}):
            self.assertEqual(dispatch.configured_claude_dir({"claude_config_dir": "/tmp/personal-account"}), str(Path("/tmp/personal-account").resolve()))
            self.assertEqual(dispatch.claude_env("/tmp/personal-account")["CLAUDE_CONFIG_DIR"], "/tmp/personal-account")
        with self.assertRaisesRegex(dispatch.DispatchError, "absolute"):
            dispatch.configured_claude_dir({"claude_config_dir": "relative/account"})

    def test_cached_oauth_without_subscription_type_is_insufficient(self):
        for subscription in (None, "free", "team", "enterprise"):
            with self.subTest(subscription=subscription), self.assertRaisesRegex(dispatch.DispatchError, "Pro/Max"):
                self.auth_capabilities({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty", "subscriptionType": subscription})

    def test_signed_out_after_excluding_gateway_does_not_start_a_model(self):
        with self.assertRaisesRegex(dispatch.DispatchError, "settings disabled"):
            self.auth_capabilities({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})

    def test_api_or_external_provider_cannot_consume_alternative_billing(self):
        for method, provider in (("api_key", "firstParty"), ("oauth", "bedrock")):
            with self.subTest(method=method, provider=provider), \
                    self.assertRaisesRegex(dispatch.DispatchError, "subscription OAuth"):
                self.auth_capabilities({"loggedIn": True, "authMethod": method, "apiProvider": provider})

    def test_credentials_are_removed_from_child_environment(self):
        environment = {"PATH": "/bin", "HOME": "/tmp/example", "OPENAI_API_KEY": "example",
                       "ANTHROPIC_API_KEY": "example", "CLAUDE_CODE_OAUTH_TOKEN": "example",
                       "ANTHROPIC_AUTH_TOKEN": "example", "ANTHROPIC_BASE_URL": "https://gateway.invalid",
                       "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
                       "CLAUDE_CONFIG_DIR": "/tmp/alternative", "https_proxy": "https://gateway.invalid",
                       "NODE_OPTIONS": "--require example.js", "NODE_EXTRA_CA_CERTS": "/tmp/certificate",
                       "AWS_PROFILE": "example", "GIT_CONFIG_COUNT": "1", "GIT_DIR": "/tmp/elsewhere"}
        with mock.patch.dict(os.environ, environment, clear=True):
            cleaned = dispatch.clean_env()
        self.assertEqual(cleaned, {"PATH": "/bin", "HOME": "/tmp/example"})

    def test_managed_policy_requires_inspection_before_any_launch(self):
        with mock.patch.object(dispatch, "managed_claude_sources", return_value=["managed-settings.json"]), \
                self.assertRaisesRegex(dispatch.DispatchError, "do not bypass policy"):
            dispatch.claude_startup_args()

    def test_remote_managed_cache_is_detected_without_reading_its_secrets(self):
        with tempfile.TemporaryDirectory(prefix="workflow-managed-") as temporary:
            user = Path(temporary)
            cache = user / ".claude/remote-settings.json"
            cache.parent.mkdir()
            cache.write_text("not parsed or disclosed by the routing guard")
            with mock.patch.object(dispatch.Path, "home", return_value=user), \
                    mock.patch.object(dispatch.sys, "platform", "linux"):
                self.assertIn(str(cache), dispatch.managed_claude_sources())

    def test_user_settings_cannot_restore_gateway_behind_oauth_status(self):
        with tempfile.TemporaryDirectory(prefix="workflow-auth-route-") as temporary:
            user = Path(temporary)
            settings = user / ".claude/settings.json"
            settings.parent.mkdir()
            settings.write_text(json.dumps({"env": {
                "ANTHROPIC_BASE_URL": "https://school.example.invalid", "ANTHROPIC_AUTH_TOKEN": "test-only"}}))
            original = settings.read_bytes()
            observed = user / "route.json"
            fake = user / "claude"
            fake.write_text("#!" + sys.executable + "\n"
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if '--help' in args:\n"
                "    print('--safe-mode --setting-sources --json-schema --permission-prompts --tools --allowedTools --session-id --resume --name --restricted --output-format --verbose --desktop')\n"
                "elif '--version' in args:\n"
                "    print('Offline auth fake')\n"
                "else:\n"
                "    assert args[-2:] == ['auth', 'status'], 'no model calls allowed'\n"
                "    env = dict(os.environ)\n"
                "    if '--setting-sources' not in args or args[args.index('--setting-sources') + 1] != '':\n"
                "        env.update(json.loads(pathlib.Path(os.environ['HOME'], '.claude/settings.json').read_text())['env'])\n"
                "    pathlib.Path(" + repr(str(observed)) + ").write_text(json.dumps({\n"
                "        'base': env.get('ANTHROPIC_BASE_URL'), 'token_present': bool(env.get('ANTHROPIC_AUTH_TOKEN'))}))\n"
                # Deliberately report OAuth even if the injected gateway wins.
                "    print(json.dumps({'loggedIn': True, 'authMethod': 'oauth_token', 'apiProvider': 'firstParty', 'subscriptionType': 'pro'}))\n")
            fake.chmod(0o700)
            with mock.patch.object(dispatch.shutil, "which", return_value=str(fake)), \
                    mock.patch.object(dispatch, "managed_claude_sources", return_value=[]), \
                    mock.patch.dict(os.environ, {"HOME": str(user), "ANTHROPIC_AUTH_TOKEN": "inherited-test-only"}):
                self.assertEqual(dispatch.capabilities(str(settings.parent))["subscription_type"], "pro")
            self.assertEqual(json.loads(observed.read_text()), {"base": dispatch.CLAUDE_API_URL, "token_present": False})
            self.assertEqual(settings.read_bytes(), original)

    def test_isolated_login_uses_subscription_and_sends_no_prompt(self):
        with mock.patch.object(dispatch.shutil, "which", return_value="/tmp/fake-claude"), \
                mock.patch.object(dispatch, "managed_claude_sources", return_value=[]), \
                mock.patch.object(dispatch, "root", return_value=Path("/tmp")), \
                mock.patch.object(dispatch, "configured_claude_dir", return_value="/tmp/personal-account"), \
                mock.patch.object(dispatch.subprocess, "call", return_value=0) as call, \
                mock.patch.object(dispatch, "doctor", return_value={"status": "READY"}):
            dispatch.login(SimpleNamespace())
        self.assertEqual(call.call_args.args[0], ["/tmp/fake-claude", "--safe-mode", "--setting-sources", "", "auth", "login", "--claudeai"])
        self.assertEqual(call.call_args.kwargs["env"]["ANTHROPIC_BASE_URL"], dispatch.CLAUDE_API_URL)
        self.assertEqual(call.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"], "/tmp/personal-account")

    def test_frozen_spec_cannot_inject_unapproved_shell_check(self):
        with tempfile.TemporaryDirectory(prefix="workflow-check-map-") as temporary:
            location = Path(temporary)
            workspace = location / "worktree"
            target = workspace / TASK
            target.parent.mkdir(parents=True)
            value = spec()
            value["checks"][0]["command"] = "git diff --check; arbitrary-command"
            raw = json.dumps(value).encode()
            target.write_bytes(raw)
            (location / "spec.json").write_bytes(raw)
            (location / "config.json").write_text(json.dumps({"schema_version": 1,
                "allowed_checks": {"git diff --check": ["git", "diff", "--check"]}}))
            with self.assertRaisesRegex(dispatch.DispatchError, "not in trusted"):
                dispatch.frozen({"run_dir": temporary, "workspace": str(workspace), "task_path": TASK})

    def test_unrecorded_merge_authorization_is_rejected(self):
        with mock.patch.object(dispatch, "frozen", return_value=(spec(), {"completion_policy": {"merge_authorized": False}})):
            with self.assertRaisesRegex(dispatch.DispatchError, "merging is not authorized"):
                dispatch.completion_policy({})


class QuotaCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-checkpoint-")
        self.addCleanup(self.temp.cleanup)
        location = Path(self.temp.name)
        worker = location / "worktree"
        worker.mkdir()
        (worker / "unfinished.txt").write_text("preserve this work")
        self.state = {"run_dir": str(location), "workspace": str(worker), "head_sha": "a" * 40,
                      "session_id": "12345678-1234-1234-1234-123456789abc", "session_started": False,
                      "claude_path": "/tmp/fake-claude", "claude_route_policy": dispatch.CLAUDE_ROUTE_POLICY,
                      "claude_config_dir": str(location / "personal-account"), "round": 0, "max_rounds": 3,
                      "max_turns": 30, "timeout": 900, "task_path": TASK, "status": "RUNNING"}
        self.config = {"allowed_checks": {"git diff --check": ["git", "diff", "--check"]}}

    @contextlib.contextmanager
    def model_call(self, response=None, side_effect=None):
        with mock.patch.object(dispatch, "frozen", return_value=(spec(), self.config)), \
                mock.patch.object(dispatch, "managed_claude_sources", return_value=[]), \
                mock.patch.object(dispatch, "current") as current, \
                mock.patch.object(dispatch, "changed", return_value=["unfinished.txt"]) as changed, \
                mock.patch.object(dispatch, "run", return_value=response, side_effect=side_effect) as run:
            yield run, current, changed

    def test_legacy_route_cannot_resume_or_repair_with_a_new_model_call(self):
        self.state.pop("claude_route_policy")
        for status in ("RUNNING", "PAUSED_LIMIT"):
            self.state["status"] = status
            with self.subTest(status=status), self.model_call() as (run, _, _), \
                    mock.patch.object(dispatch, "capabilities") as capabilities, \
                    self.assertRaisesRegex(dispatch.DispatchError, "do not relabel historical"):
                if status == "RUNNING":
                    dispatch.implement(self.state)
                else:
                    dispatch.resume(self.state, SimpleNamespace(additional_rounds=0))
            run.assert_not_called()
            capabilities.assert_not_called()
            self.assertEqual(self.state["round"], 0)

    def test_rejected_quota_stops_after_one_call_and_preserves_checkpoint(self):
        raw = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 123}}).encode()
        with self.model_call((1, raw, b"")) as (run, _, _):
            result = dispatch.implement(self.state, "Repair all review findings")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["status"], "PAUSED_LIMIT")
        self.assertEqual(result["round"], 1)
        self.assertEqual(result["pending_feedback"], "Repair all review findings")
        self.assertEqual(result["checkpoint"]["session_id"], self.state["session_id"])
        self.assertEqual((Path(self.state["workspace"]) / "unfinished.txt").read_text(), "preserve this work")
        self.assertTrue((Path(self.temp.name) / "round-1/claude.json").is_file())
        self.assertIn("--session-id", run.call_args.args[0])
        self.assertNotIn("--continue", run.call_args.args[0])

    def test_interruption_preserves_partial_logs_instead_of_retrying(self):
        interruption = dispatch.ProcessInterrupted("timed out", b"partial output", b"partial error")
        with self.model_call(side_effect=interruption) as (run, _, _):
            result = dispatch.implement(self.state)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["status"], "PAUSED_INTERRUPTED")
        self.assertEqual((Path(self.temp.name) / "round-1/claude.json").read_bytes(), b"partial output")
        self.assertEqual((Path(self.temp.name) / "round-1/claude.stderr").read_bytes(), b"partial error")

    def test_resume_targets_same_session_and_rechecks_preserved_scope(self):
        self.state.update(status="PAUSED_LIMIT", round=1, session_started=True, pending_feedback="Repair previous findings")
        raw = json.dumps({"type": "result", "is_error": True, "error": {"type": "rate_limit_error"}}).encode()
        caps = {"claude_path": self.state["claude_path"]}
        with self.model_call((1, raw, b"")) as (run, current, changed), \
                mock.patch.object(dispatch, "capabilities", return_value=caps) as capabilities:
            result = dispatch.resume(self.state, SimpleNamespace(additional_rounds=0))
        self.assertEqual(result["status"], "PAUSED_LIMIT")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--resume") + 1], self.state["session_id"])
        self.assertNotIn("--continue", argv)
        self.assertNotIn("--session-id", argv)
        capabilities.assert_called_once_with(self.state["claude_config_dir"])
        changed.assert_called_once()
        self.assertGreaterEqual(current.call_count, 2)
        self.assertIn("Repair previous findings", result["pending_feedback"])

    def test_exhausted_attempt_budget_requires_explicit_bounded_extension(self):
        self.state.update(status="PAUSED_LIMIT", round=3, session_started=True)
        with self.model_call((1, b"", b"")) as (run, _, _), \
                mock.patch.object(dispatch, "capabilities", return_value={"claude_path": self.state["claude_path"]}):
            with self.assertRaisesRegex(dispatch.DispatchError, "attempt budget exhausted"):
                dispatch.resume(self.state, SimpleNamespace(additional_rounds=0))
            run.assert_not_called()
            with self.assertRaisesRegex(dispatch.DispatchError, "10 total"):
                dispatch.resume(self.state, SimpleNamespace(additional_rounds=8))

    def test_resume_preflight_failure_keeps_recoverable_pause(self):
        for status in ("PAUSED_LIMIT", "PAUSED_INTERRUPTED", "PAUSED_ENVIRONMENT"):
            with self.subTest(status=status):
                self.state.update(status=status, checkpoint={"session_id": self.state["session_id"]})
                checkpoint = dict(self.state["checkpoint"])
                with mock.patch.object(dispatch, "load_state", return_value=self.state), \
                        mock.patch.object(dispatch, "resume", side_effect=dispatch.DispatchError("temporary auth failure")):
                    result = dispatch.operate(SimpleNamespace(command="resume", run=self.temp.name))
                self.assertEqual(result["status"], status)
                self.assertEqual(result["checkpoint"], checkpoint)
                self.assertEqual(result["resume_error"], "temporary auth failure")

    def test_rate_limit_warning_is_not_treated_as_rejection(self):
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "utilization": 0.9}}
        self.assertIsNone(dispatch.quota_limit([event], json.dumps(event).encode()))

    def test_error_message_is_detected_in_jsonl_and_stderr(self):
        raw = b'{"type":"system","subtype":"init"}\n{"type":"result","is_error":true,"error":{"type":"rate_limit_error"}}\n'
        result, events = dispatch.decode_claude_output(raw)
        self.assertTrue(result["is_error"])
        self.assertIsNotNone(dispatch.quota_limit(events, raw))
        self.assertIsNotNone(dispatch.quota_limit([], stderr=b"You've hit your limit; resets later"))

    def test_successful_description_of_rate_limit_fix_is_not_a_quota_error(self):
        event = {"type": "result", "is_error": False,
                 "structured_output": {"summary": "Handled usage limit exceeded errors in the parser"}}
        self.assertIsNone(dispatch.quota_limit([event], json.dumps(event).encode()))


class ManualBudgetTests(unittest.TestCase):
    def test_snapshots_require_timezone_and_do_not_reset_themselves(self):
        with tempfile.TemporaryDirectory(prefix="workflow-budget-") as temporary, \
                mock.patch.object(dispatch, "root", return_value=Path(temporary)):
            args = SimpleNamespace(provider="claude", remaining_percent=0, resets_at="2000-01-01T00:00:00+11:00")
            recorded = dispatch.budget(args)
            row = recorded["providers"]["claude"]
            self.assertTrue(row["reported_reset_has_passed"])
            self.assertFalse(row["live_quota_known"])
            self.assertEqual(row["remaining_percent"], 0)
            self.assertEqual(dispatch.budget(SimpleNamespace())["providers"]["claude"]["remaining_percent"], 0)
            with self.assertRaisesRegex(dispatch.DispatchError, "timezone"):
                dispatch.budget(SimpleNamespace(provider="codex", remaining_percent=50, resets_at="2026-10-29T12:00:00"))
            with self.assertRaisesRegex(dispatch.DispatchError, "0 to 100"):
                dispatch.budget(SimpleNamespace(provider="codex", remaining_percent=101, resets_at="2026-10-29T12:00:00+11:00"))
            self.assertEqual((Path(temporary) / ".ai-workflow/budget.json").stat().st_mode & 0o777, 0o600)


class LocalPipelineTests(unittest.TestCase):
    """Exercise real temp worktrees/checks/packets with a fake executable, never a model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.location = Path(self.temp.name).resolve()
        policy = mock.patch.object(dispatch, "managed_claude_sources", return_value=[])
        policy.start()
        self.addCleanup(policy.stop)
        self.repo = self.location / "checkout"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write(".gitignore", ".ai-workflow/\nAI_WORK/\n__pycache__/\n")
        self.write("CLAUDE.md", "Implement only the scoped task; the host owns all checks.\n")
        source = Path(dispatch.__file__).resolve().parent
        for path in source.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                target = self.repo / "scripts" / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        self.write("docs/ai/templates/task.spec.json", json.dumps(spec()))
        self.write("scripts/ai_dispatch_config.json", json.dumps({
            "schema_version": 1,
            "claude_config_dir": str(self.location / "personal-account"),
            "workspace_trace": True,
            "check_helpers": ["verify_value.py"],
            "allowed_checks": {"python3 verify_value.py": [sys.executable, "verify_value.py"]},
            "completion_policy": {"merge_authorized": False},
        }))
        self.write("src/value.py", "def value():\n    return 1\n")
        self.write("verify_value.py", "import os\nfrom src.value import value\nassert value() in (2, 3)\n"
                   "print('VERIFIED_VALUE', value())\nprint('VERIFY_PYTHON', os.environ['AI_WORKFLOW_VERIFY_PYTHON'])\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Workflow baseline")
        self.base = self.git("rev-parse", "HEAD")
        task = spec()
        task["checks"][0]["command"] = "python3 verify_value.py"
        self.write(TASK, json.dumps(task))
        self.calls = self.location / "calls.jsonl"
        self.fake = self.location / "fake-claude"
        self.fake.write_text("#!" + sys.executable + "\n" +
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "assert args[args.index('--setting-sources') + 1] == ''\n"
            "assert os.environ.get('ANTHROPIC_BASE_URL') == 'https://api.anthropic.com'\n"
            "assert not os.environ.get('ANTHROPIC_AUTH_TOKEN') and not os.environ.get('CLAUDE_CODE_USE_VERTEX')\n"
            "flag = '--resume' if '--resume' in args else '--session-id'\n"
            "session = args[args.index(flag) + 1]\n"
            "assert args[args.index('--output-format') + 1] == 'stream-json' and '--verbose' in args\n"
            "assert '--restricted' in args and '--name' in args and '--no-session-persistence' not in args\n"
            "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'src/value.py', 'old_string': 'private sample'}}]}}), flush=True)\n"
            "value = 3 if flag == '--resume' else 2\n"
            "pathlib.Path('src/value.py').write_text('def value():\\n    return ' + str(value) + '\\n')\n"
            "with pathlib.Path(" + repr(str(self.calls)) + ").open('a') as output:\n"
            "    output.write(json.dumps({'args': args, 'session': session, 'account_dir': os.environ.get('CLAUDE_CONFIG_DIR')}) + '\\n')\n"
            "print(json.dumps({'type': 'result', 'session_id': session, 'is_error': False, 'structured_output': {"
            "'status': 'COMPLETED', 'summary': 'Changed the value', 'acceptance': [{"
            "'id': 'AC-1', 'status': 'PASS', 'evidence': 'src/value.py:2 returns the requested value'}], 'risks': []}}))\n")
        self.fake.chmod(0o700)
        self.caps = {"claude_path": str(self.fake), "claude_version": "Offline test fake",
                     "claude_config_dir": str(self.location / "personal-account"), "subscription_type": "pro"}

    def write(self, name, text):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=self.repo,
                              capture_output=True, text=True, check=True).stdout.strip()

    def start(self, max_rounds=3, surface=None):
        with dispatch.working(self.repo), mock.patch.object(dispatch, "capabilities", return_value=self.caps), \
                mock.patch.dict(os.environ, {"AI_WORKFLOW_VERIFY_PYTHON": sys.executable}):
            return dispatch.start(SimpleNamespace(task=TASK, base=self.base, max_rounds=max_rounds, max_turns=30, timeout=20, surface=surface))

    def desktop_assessment(self, state):
        value = {"head_sha": state["head_sha"], "round": state["round"], "summary": "Observed Desktop implementation",
                 "desktop_observation": {"title": state["desktop_session_title"], "workspace": state["workspace"],
                     "implementation_finished": True, "subscription_account_confirmed": True, "observed_at": "2026-10-29T12:00:00+11:00"},
                 "acceptance": [{"id": "AC-1", "status": "PASS", "evidence": "Observed the file edit; required host check verifies the resulting value"}], "risks": []}
        path = Path(state["run_dir"]) / ("assessment-" + str(state["round"]) + ".json")
        path.write_text(json.dumps(value))
        return path

    def test_desktop_rounds_use_visible_handoffs_and_host_checks_without_cli_inference(self):
        state = self.start(surface="desktop")
        self.assertEqual(state["status"], "AWAITING_DESKTOP", state.get("reason"))
        self.assertIsNone(state["session_id"])
        self.assertFalse(self.calls.exists())
        self.assertIn(state["workspace"], Path(state["desktop_prompt_path"]).read_text())
        self.assertIn("CLI max-turns/timeout do not control Desktop", state["desktop_limits_note"])
        worker = Path(state["workspace"])
        (worker / "src/value.py").write_text("def value():\n    return 2\n")
        with dispatch.working(self.repo):
            state = dispatch.collect_desktop(state, SimpleNamespace(assessment=str(self.desktop_assessment(state))))
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertTrue(state["desktop_ui_verified"])
        title = state["desktop_session_title"]
        state = self.review(state, "REVISE")
        self.assertEqual(state["status"], "AWAITING_DESKTOP")
        self.assertEqual(state["round"], 2)
        self.assertEqual(state["desktop_session_title"], title)
        (worker / "src/value.py").write_text("def value():\n    return 3\n")
        with dispatch.working(self.repo):
            state = dispatch.collect_desktop(state, SimpleNamespace(assessment=str(self.desktop_assessment(state))))
        self.assertEqual(self.review(state, "PASS")["status"], "READY_TO_PUBLISH")
        self.assertFalse(self.calls.exists())

    def test_desktop_collection_rejects_stale_or_wrong_workspace_observations(self):
        state = self.start(surface="desktop")
        path = self.desktop_assessment(state)
        assessment = json.loads(path.read_text())
        assessment["desktop_observation"]["workspace"] = str(self.repo)
        path.write_text(json.dumps(assessment))
        with dispatch.working(self.repo), mock.patch.object(dispatch, "finish_implementation") as finish:
            with self.assertRaisesRegex(dispatch.DispatchError, "verify the visible"):
                dispatch.collect_desktop(state, SimpleNamespace(assessment=str(path)))
            assessment["round"] = 0
            path.write_text(json.dumps(assessment))
            with self.assertRaisesRegex(dispatch.DispatchError, "stale turn"):
                dispatch.collect_desktop(state, SimpleNamespace(assessment=str(path)))
        finish.assert_not_called()
        self.assertFalse(self.calls.exists())

    def test_desktop_failed_host_check_queues_feedback_instead_of_calling_cli(self):
        state = self.start(surface="desktop")
        (Path(state["workspace"]) / "src/value.py").write_text("def value():\n    return 0\n")
        with dispatch.working(self.repo):
            state = dispatch.collect_desktop(state, SimpleNamespace(assessment=str(self.desktop_assessment(state))))
        self.assertEqual(state["status"], "AWAITING_DESKTOP")
        self.assertEqual(state["round"], 2)
        self.assertIn("Required host checks failed", state["pending_feedback"])
        self.assertFalse(self.calls.exists())

    def test_desktop_quota_pause_resumes_same_turn_without_opening_or_calling_claude(self):
        state = self.start(surface="desktop")
        prompt = state["desktop_prompt_path"]
        with dispatch.working(self.repo):
            state = dispatch.pause_desktop(state, SimpleNamespace(kind="limit", reason="Observed subscription limit in Desktop"))
            self.assertEqual(state["status"], "PAUSED_LIMIT")
            with mock.patch.object(dispatch, "capabilities") as capabilities:
                state = dispatch.resume(state, SimpleNamespace(additional_rounds=0))
        self.assertEqual(state["status"], "AWAITING_DESKTOP")
        self.assertEqual(state["desktop_prompt_path"], prompt)
        self.assertEqual(state["round"], 1)
        capabilities.assert_not_called()
        self.assertFalse(self.calls.exists())

    def test_desktop_open_only_launches_app_and_cannot_duplicate_the_conversation(self):
        state = self.start(surface="desktop")
        with dispatch.working(self.repo), mock.patch.object(dispatch, "current"), \
                mock.patch.object(dispatch.sys, "platform", "darwin"), \
                mock.patch.object(dispatch, "run", return_value=(0, b"", b"")) as launch_call:
            state = dispatch.open_desktop(state, SimpleNamespace())
            launch = launch_call.call_args.args[0]
            self.assertEqual(launch[0], "/usr/bin/open")
            link = urlparse(launch[1])
            self.assertEqual((link.scheme, link.netloc, link.path), ("claude", "code", "/new"))
            query = parse_qs(link.query)
            self.assertEqual(query["folder"], [state["workspace"]])
            self.assertEqual(query["q"], [Path(state["desktop_prompt_path"]).read_text()])
            self.assertNotIn("-p", launch)
            self.assertNotIn("--resume", launch)
            self.assertFalse(state["desktop_ui_verified"])
            with self.assertRaisesRegex(dispatch.DispatchError, "reuse the existing"):
                dispatch.open_desktop(state, SimpleNamespace())
        self.assertFalse(self.calls.exists())

    def test_desktop_deep_link_needs_no_cli_login_and_never_truncates_handoff(self):
        state = self.start(surface="desktop")
        with dispatch.working(self.repo), mock.patch.object(dispatch, "current"), \
                mock.patch.object(dispatch.sys, "platform", "darwin"), \
                mock.patch.object(dispatch, "capabilities", side_effect=AssertionError("no CLI login")), \
                mock.patch.object(dispatch, "run", return_value=(0, b"", b"")) as launch:
            dispatch.open_desktop(state, SimpleNamespace())
            state.pop("desktop_open_requested")
            prompt = Path(state["desktop_prompt_path"])
            prompt.write_text("x" * 12001)
            state["desktop_prompt_sha256"] = dispatch.workflow.digest(prompt.read_bytes())
            with self.assertRaisesRegex(dispatch.DispatchError, "deep-link budget"):
                dispatch.open_desktop(state, SimpleNamespace())
        self.assertEqual(launch.call_count, 1)

    def test_desktop_preparation_does_not_require_or_attempt_cli_login(self):
        with dispatch.working(self.repo), \
                mock.patch.object(dispatch, "capabilities", side_effect=AssertionError("must not require CLI credentials")):
            state = dispatch.start(SimpleNamespace(task=TASK, base=self.base, max_rounds=2, max_turns=30, timeout=20, surface="desktop"))
        self.assertEqual(state["status"], "AWAITING_DESKTOP")
        self.assertEqual(state["claude_route_policy"], "desktop-app-subscription-v1")
        self.assertIsNone(state["claude_subscription_type"])
        self.assertFalse(self.calls.exists())

    def test_desktop_worktree_can_live_outside_icloud_but_remains_bound_to_frozen_config(self):
        path = self.repo / "scripts/ai_dispatch_config.json"
        config = json.loads(path.read_text())
        config["desktop_worktree_root"] = str(self.location / "local-desktop-worktrees")
        path.write_text(json.dumps(config))
        self.git("add", "scripts/ai_dispatch_config.json")
        self.git("commit", "-qm", "Keep desktop worktrees on local volume")
        self.base = self.git("rev-parse", "HEAD")
        state = self.start(surface="desktop")
        expected = self.location / "local-desktop-worktrees" / Path(state["run_dir"]).name
        self.assertEqual(state["workspace"], str(expected))
        self.assertTrue((expected / "src/value.py").is_file())
        with dispatch.working(self.repo):
            self.assertEqual(dispatch.load_state(state["run_dir"])["workspace"], str(expected))
            state["workspace"] = str(self.repo)
            dispatch.save(state)
            with self.assertRaisesRegex(dispatch.DispatchError, "workspace mismatch"):
                dispatch.load_state(state["run_dir"])

    def review(self, state, verdict):
        manifest_raw = (Path(state["packet_dir"]) / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        review = {field: manifest[field] for field in ("schema_version", "task_id", "spec_version", "head_sha", "base_sha", "spec_sha256")}
        review.update(manifest_sha256=dispatch.workflow.digest(manifest_raw), verdict=verdict,
                      reviewed_by="Offline integration reviewer", acceptance=[{
                          "id": "AC-1", "status": "PASS" if verdict == "PASS" else "FAIL",
                          "evidence": "Reviewed exact implementation and host check log"}],
                      findings=[] if verdict == "PASS" else ["Change the requested return value to 3"])
        filename = Path(state["run_dir"]) / ("review-input-" + str(state["round"]) + ".json")
        filename.write_text(json.dumps(review))
        with dispatch.working(self.repo):
            return dispatch.apply_review(state, SimpleNamespace(review=str(filename)))

    def test_start_edits_only_worker_checks_and_accepts_bound_review(self):
        state = self.start()
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertEqual((self.repo / "src/value.py").read_text(), "def value():\n    return 1\n")
        self.assertTrue(state["controller_changes_excluded"])
        report = json.loads(Path(state["report_path"]).read_bytes())
        self.assertEqual(report["checks"][0]["status"], "PASS")
        log = Path(state["check_artifacts"][0]["path"]).read_text()
        self.assertIn("VERIFIED_VALUE 2", log)
        self.assertIn(sys.executable, log)
        with dispatch.working(self.repo):
            self.assertEqual(dispatch.load_state(state["run_dir"])["head_sha"], state["head_sha"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(dispatch.main(["status", "--run", state["run_dir"]]), 0)
            brief = json.loads(output.getvalue())
            self.assertEqual(brief["session_id"], state["session_id"])
            self.assertIn("state_path", brief)
            self.assertNotIn("pending_feedback", brief)
        self.assertEqual(self.review(state, "PASS")["status"], "READY_TO_PUBLISH")

    def test_cli_external_worktree_and_trace_policy_are_frozen(self):
        config_path = self.repo / "scripts/ai_dispatch_config.json"
        config = json.loads(config_path.read_text())
        config["worktree_root"] = str(self.location / "local-cli-worktrees")
        config_path.write_text(json.dumps(config))
        self.git("add", "scripts/ai_dispatch_config.json")
        self.git("commit", "-qm", "Use a local worktree for traced CLI tasks")
        self.base = self.git("rev-parse", "HEAD")
        state = self.start()
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertEqual(Path(state["workspace"]).parent, self.location / "local-cli-worktrees")
        self.assertTrue((Path(state["trace_dir"]) / "README.md").is_file())
        with dispatch.working(self.repo):
            dispatch.load_state(state["run_dir"])
            state["trace_enabled"] = False
            dispatch.save(state)
            with self.assertRaisesRegex(dispatch.DispatchError, "trace policy mismatch"):
                dispatch.load_state(state["run_dir"])

    def test_minimal_work_order_reuses_session_without_global_project_context(self):
        task = spec()
        task["checks"][0]["command"] = "python3 verify_value.py"
        task["worker_context"] = [{"path": "src/value.py", "start_line": 1, "end_line": 2}]
        task["review_mode"] = "focused"
        self.write(TASK, json.dumps(task))
        state = self.start()
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        raw = Path(state["handoff_path"]).read_text()
        self.assertIn("return 1", raw)
        self.assertNotIn("Read CLAUDE.md", raw)
        self.assertEqual(Path(state["trace_dir"], "RESULT.md").read_text().splitlines()[0], "STATUS: AWAITING_REVIEW")
        self.assertEqual(state["check_summary"], {"PASS": 1, "FAIL": 0, "BLOCKED": 0})
        state = self.review(state, "REVISE")
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertIn("return 2", Path(state["handoff_path"]).read_text())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls[0]["session"], calls[1]["session"])
        for call in calls:
            bootstrap = call["args"][call["args"].index("-p") + 1]
            self.assertIn("HANDOFF.md", bootstrap)
            self.assertNotIn("Frozen Spec", bootstrap)

    def test_worker_context_block_stops_before_host_checks_and_preserves_return(self):
        self.fake.write_text(self.fake.read_text().replace("'status': 'COMPLETED'", "'status': 'BLOCKED'"))
        state = self.start()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertNotIn("check_artifacts", state)
        returned = Path(state["run_dir"]) / "round-1/WORKER_RESULT.md"
        self.assertEqual(returned.read_text().splitlines()[0], "STATUS: BLOCKED")

    def test_required_work_order_context_stops_before_auth_or_model_without_a_handoff(self):
        config_path = self.repo / "scripts/ai_dispatch_config.json"
        config = json.loads(config_path.read_text())
        config["require_worker_handoff"] = True
        config_path.write_text(json.dumps(config))
        with dispatch.working(self.repo), mock.patch.object(dispatch, "capabilities") as caps, \
                self.assertRaisesRegex(dispatch.DispatchError, "explicit worker_context"):
            dispatch.start(SimpleNamespace(task=TASK, base=self.base, max_rounds=3, max_turns=30, timeout=20))
        caps.assert_not_called()
        self.assertFalse(self.calls.exists())

    def test_cli_history_opens_exact_finished_session_without_another_model_call(self):
        state = self.start()
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        trace = Path(state["trace_dir"])
        self.assertIn("Edit src/value.py", (trace / "activity.log").read_text())
        self.assertNotIn("private sample", (trace / "activity.log").read_text())
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "?? " + TASK)
        with mock.patch.object(dispatch.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(dispatch, "capabilities", return_value=self.caps), \
                mock.patch.object(dispatch.subprocess, "call", return_value=0) as launch:
            result = dispatch.open_history(state, SimpleNamespace())
        self.assertTrue(result["desktop_history_open_requested"])
        self.assertFalse(result["desktop_history_ui_verified"])
        self.assertEqual(launch.call_args.args[0], [str(self.fake), "--desktop", "--resume", state["session_id"]])
        self.assertEqual(launch.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"], state["claude_config_dir"])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_history_launcher_failure_preserves_finished_evidence(self):
        state = self.start()
        with dispatch.working(self.repo), mock.patch.object(dispatch.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(dispatch, "capabilities", return_value=self.caps), \
                mock.patch.object(dispatch.subprocess, "call", return_value=1):
            result = dispatch.operate(SimpleNamespace(command="open-history", run=state["run_dir"]))
        self.assertEqual(result["status"], "AWAITING_REVIEW")
        self.assertIn("could not open", result["history_open_error"])
        self.assertEqual(result["packet_dir"], state["packet_dir"])

    def test_running_session_cannot_open_in_desktop(self):
        state = self.start()
        state["status"] = "RUNNING"
        with mock.patch.object(dispatch.subprocess, "call") as launch, \
                self.assertRaisesRegex(dispatch.DispatchError, "wait until"):
            dispatch.open_history(state, SimpleNamespace())
        launch.assert_not_called()

    def test_revise_reuses_exact_session_and_rebuilds_evidence(self):
        state = self.start()
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        previous_head, previous_packet = state["head_sha"], state["packet_dir"]
        state = self.review(state, "REVISE")
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertNotEqual(state["head_sha"], previous_head)
        self.assertNotEqual(state["packet_dir"], previous_packet)
        self.assertEqual(state["round"], 2)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["session"], calls[1]["session"])
        self.assertEqual(calls[0]["account_dir"], str(self.location / "personal-account"))
        self.assertEqual(calls[0]["account_dir"], calls[1]["account_dir"])
        self.assertIn("--session-id", calls[0]["args"])
        self.assertIn("--resume", calls[1]["args"])
        self.assertNotIn("--continue", calls[1]["args"])
        self.assertEqual(self.review(state, "PASS")["status"], "READY_TO_PUBLISH")

    def test_environment_block_resumes_host_checks_without_another_model_round(self):
        marker = self.location / "host-environment-ready"
        self.write("scripts/verify_project.py", "import pathlib, sys\n"
                   "ready = pathlib.Path(" + repr(str(marker)) + ").is_file()\n"
                   "print('[PASS] Host ready' if ready else '[BLOCKED] Host dependency unavailable')\n"
                   "sys.exit(0 if ready else 2)\n")
        command = "python3 scripts/verify_project.py example-check"
        self.write("scripts/ai_dispatch_config.json", json.dumps({"schema_version": 1,
            "allowed_checks": {command: [sys.executable, "scripts/verify_project.py", "example-check"]},
            "environment_check_commands": [command],
            "completion_policy": {"merge_authorized": False}}))
        task = spec()
        task["checks"][0]["command"] = command
        self.write(TASK, json.dumps(task))
        self.git("add", ".")
        self.git("commit", "-qm", "Trusted environment-blocking verifier")
        self.base = self.git("rev-parse", "HEAD")

        state = self.start(max_rounds=1)
        self.assertEqual(state["status"], "PAUSED_ENVIRONMENT", state.get("reason"))
        self.assertEqual(state["round"], 1)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertEqual((Path(state["workspace"]) / "src/value.py").read_text(), "def value():\n    return 2\n")
        checkpoint = dict(state["checkpoint"])

        # Failed resume preflight must leave the environment checkpoint recoverable.
        with dispatch.working(self.repo), \
                mock.patch.object(dispatch, "current", side_effect=dispatch.DispatchError("host prerequisite still unavailable")):
            paused = dispatch.operate(SimpleNamespace(command="resume", run=state["run_dir"], additional_rounds=0))
        self.assertEqual(paused["status"], "PAUSED_ENVIRONMENT")
        self.assertEqual(paused["checkpoint"], checkpoint)
        self.assertIn("prerequisite", paused["resume_error"])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

        marker.touch()
        with dispatch.working(self.repo), mock.patch.object(dispatch, "capabilities") as capabilities:
            resumed = dispatch.operate(SimpleNamespace(command="resume", run=state["run_dir"], additional_rounds=0))
        self.assertEqual(resumed["status"], "AWAITING_REVIEW", resumed.get("reason"))
        self.assertEqual(resumed["round"], 1)
        self.assertEqual(resumed["max_rounds"], 1)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        capabilities.assert_not_called()
        report = json.loads(Path(resumed["report_path"]).read_bytes())
        self.assertEqual(report["checks"][0]["status"], "PASS")
        self.assertEqual(self.review(resumed, "PASS")["status"], "READY_TO_PUBLISH")

    def test_optional_environment_block_is_reported_without_pausing_or_retrying(self):
        self.write("scripts/verify_project.py", "import sys\nprint('[BLOCKED] Optional host dependency unavailable')\nsys.exit(2)\n")
        command = "python3 scripts/verify_project.py example-check"
        self.write("scripts/ai_dispatch_config.json", json.dumps({"schema_version": 1,
            "allowed_checks": {command: [sys.executable, "scripts/verify_project.py", "example-check"]},
            "environment_check_commands": [command],
            "completion_policy": {"merge_authorized": False}}))
        task = spec()
        task["checks"][0].update(command=command, required=False)
        self.write(TASK, json.dumps(task))
        self.git("add", ".")
        self.git("commit", "-qm", "Optional environment verifier")
        self.base = self.git("rev-parse", "HEAD")

        state = self.start(max_rounds=1)
        self.assertEqual(state["status"], "AWAITING_REVIEW", state.get("reason"))
        self.assertEqual(state["round"], 1)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        report = json.loads(Path(state["report_path"]).read_bytes())
        self.assertEqual(report["checks"][0]["status"], "BLOCKED")
        self.assertTrue((Path(state["packet_dir"]) / "manifest.json").is_file())

    def test_missing_baseline_stops_before_any_model_call(self):
        self.write("CLAUDE.md", "Uncommitted workflow changes\n")
        with dispatch.working(self.repo), mock.patch.object(dispatch, "capabilities") as capabilities:
            with self.assertRaisesRegex(dispatch.DispatchError, "committed baseline"):
                dispatch.start(SimpleNamespace(task=TASK, base=self.base, max_rounds=3, max_turns=30, timeout=20))
        capabilities.assert_not_called()
        self.assertFalse(self.calls.exists())

    def test_changed_check_helpers_require_reviewed_baseline(self):
        for name in ("verify_value.py", "docs/ai/templates/task.spec.json", "scripts/tests/test_ai_dispatch.py"):
            with self.subTest(path=name):
                filename = self.repo / name
                original = filename.read_bytes()
                filename.write_bytes(original + b"\n")
                try:
                    with dispatch.working(self.repo), mock.patch.object(dispatch, "capabilities") as capabilities:
                        with self.assertRaisesRegex(dispatch.DispatchError, "committed baseline"):
                            dispatch.start(SimpleNamespace(task=TASK, base=self.base, max_rounds=3, max_turns=30, timeout=20))
                    capabilities.assert_not_called()
                    self.assertFalse(self.calls.exists())
                finally:
                    filename.write_bytes(original)

    def test_verification_interpreter_falls_back_without_copying_venv(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(dispatch.verification_python(self.repo), str(Path(sys.executable).absolute()))
        with mock.patch.dict(os.environ, {"AI_WORKFLOW_VERIFY_PYTHON": str(self.repo / "missing-python")}):
            with self.assertRaisesRegex(dispatch.DispatchError, "existing absolute executable"):
                dispatch.verification_python(self.repo)


if __name__ == "__main__":
    unittest.main()
