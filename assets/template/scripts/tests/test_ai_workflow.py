"""Offline behavioral checks for scope, evidence binding, and credential exclusion."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_workflow as workflow


TASK = "docs/ai/tasks/TASK-0001.json"


def spec():
    return {
        "schema_version": 1,
        "task_id": "TASK-0001",
        "spec_version": 1,
        "status": "READY",
        "title": "Return updated value",
        "objective": "Callers observe the new result",
        "architecture": ["Preserve the existing module interface"],
        "non_goals": ["No interface changes"],
        "allowed_paths": ["src/**"],
        "acceptance_criteria": [{"id": "AC-1", "requirement": "Return the new result",
                                 "verification": "Exercise the public function"}],
        "checks": [{"id": "CHECK-1", "command": "git diff --check", "required": True}],
        "risks": [],
        "rollback": "Revert the task commit",
        "review_budget": {"max_diff_bytes": 100000},
    }


@contextlib.contextmanager
def working(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class SpecTests(unittest.TestCase):
    def test_ready_spec_requires_finished_decisions(self):
        draft = spec()
        draft["objective"] = "TODO: decide expected output"
        with self.assertRaisesRegex(workflow.ContractError, "placeholder"):
            workflow.validate_spec(draft, TASK)
        draft["status"] = "DRAFT"
        self.assertEqual(workflow.validate_spec(draft, TASK)["status"], "DRAFT")

    def test_scope_paths_cannot_escape_repository(self):
        for path in ("../outside", "/tmp/outside", "src/../private", "src\\outside", "src//empty"):
            with self.subTest(path=path):
                value = spec()
                value["allowed_paths"] = [path]
                with self.assertRaisesRegex(workflow.ContractError, "unsafe"):
                    workflow.validate_spec(value, TASK)

    def test_task_identity_and_evidence_ids_are_unique(self):
        value = spec()
        value["acceptance_criteria"].append(copy.deepcopy(value["acceptance_criteria"][0]))
        with self.assertRaisesRegex(workflow.ContractError, "duplicate"):
            workflow.validate_spec(value, TASK)
        with self.assertRaisesRegex(workflow.ContractError, "filename"):
            workflow.validate_spec(spec(), "docs/ai/tasks/TASK-0002.json")
        with self.assertRaisesRegex(workflow.ContractError, "duplicate"):
            workflow.read_json('{"status":"READY","status":"DRAFT"}')

    def test_credentials_and_runtime_data_are_sensitive(self):
        for path in ("local.properties", "server.properties", "keystore.properties", "release.jks",
                     "nested/key.keystore", "src/.env", ".venv/secret.py",
                     "data/sample.sqlite", "data/sample.db", "data/users.json",
                     "data/invites.json", "config/local-private.json"):
            with self.subTest(path=path):
                self.assertTrue(workflow.sensitive(path), path)
        for path in ("src/db.py", "src/manifest.json", "docs/ai/README.md"):
            with self.subTest(path=path):
                self.assertFalse(workflow.sensitive(path), path)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-contract-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.write(".gitignore", ".ai-workflow/\n")
        self.write("src/value.py", "def value():\n    return 1\n")
        self.base = self.commit("Baseline")
        self.task = spec()
        self.write(TASK, json.dumps(self.task))
        self.write("src/value.py", "def value():\n    return 2\n")
        self.head = self.commit("Implement task")
        self.packet = self.repo / ".ai-workflow/packet"
        self.report = self.repo / ".ai-workflow/report.json"
        self.review = self.repo / ".ai-workflow/review.json"

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Workflow Test",
                                 "-c", "user.email=test@example.invalid", *args], cwd=self.repo,
                                capture_output=True, check=True)
        return result.stdout.decode().strip()

    def write(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def make_packet(self):
        report = {
            "schema_version": 1, "task_id": "TASK-0001", "spec_version": 1, "head_sha": self.head,
            "acceptance": [{"id": "AC-1", "status": "PASS", "evidence": "src/value.py:2 returns 2"}],
            "checks": [{"id": "CHECK-1", "command": "git diff --check", "status": "PASS",
                        "evidence": "Host ran git diff --check with exit 0"}],
            "deviations": [], "risks": [], "rollback": "Revert the task commit",
        }
        self.write(str(self.report.relative_to(self.repo)), json.dumps(report))
        with working(self.repo), contextlib.redirect_stdout(io.StringIO()):
            workflow.write_packet(SimpleNamespace(task=TASK, base=self.base, head=self.head,
                                                  report=str(self.report), output=str(self.packet)))
        manifest_raw = (self.packet / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        review = {field: manifest[field] for field in ("schema_version", "task_id", "spec_version", "head_sha", "base_sha", "spec_sha256")}
        review.update(manifest_sha256=workflow.digest(manifest_raw), verdict="PASS", reviewed_by="Offline test reviewer",
                      acceptance=report["acceptance"], findings=[])
        self.write(str(self.review.relative_to(self.repo)), json.dumps(review))

    def verify_packet(self, head=None):
        with working(self.repo), contextlib.redirect_stdout(io.StringIO()):
            return workflow.review_packet(SimpleNamespace(packet=str(self.packet), review=str(self.review),
                                                          base=self.base, head=head or self.head))

    def test_scope_accepts_only_spec_and_explicit_paths(self):
        with working(self.repo):
            result = workflow.scope(TASK, self.base, self.head)
        self.assertEqual(set(result[-1]), {TASK, "src/value.py"})
        self.write("unrelated.py", "unexpected = True\n")
        self.head = self.commit("Unrelated change")
        with working(self.repo), self.assertRaisesRegex(workflow.ContractError, "out-of-scope.*unrelated"):
            workflow.scope(TASK, self.base, self.head)

    def test_packet_pass_is_bound_to_complete_diff_and_original_report(self):
        self.make_packet()
        self.assertEqual(self.verify_packet()["verdict"], "PASS")
        patch = self.packet / "changes.diff"
        patch.write_bytes(patch.read_bytes() + b"\nforged text\n")
        with self.assertRaisesRegex(workflow.ContractError, "altered packet file"):
            self.verify_packet()

    def test_new_head_invalidates_old_packet_and_review(self):
        self.make_packet()
        self.write("src/value.py", "def value():\n    return 3\n")
        new_head = self.commit("Another implementation")
        with self.assertRaisesRegex(workflow.ContractError, "report mismatch: head_sha"):
            self.verify_packet(new_head)

    def test_manifest_tampering_cannot_retarget_review(self):
        self.make_packet()
        path = self.packet / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["head_sha"] = self.base
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(workflow.ContractError, "altered packet manifest"):
            self.verify_packet()

    def test_self_report_cannot_omit_acceptance_or_failed_required_check(self):
        self.make_packet()
        report = json.loads(self.report.read_bytes())
        report["checks"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(workflow.ContractError, "required check must PASS"):
            workflow.validate_report(report, self.task, self.head)
        report["checks"][0]["status"] = "PASS"
        report["acceptance"] = []
        with self.assertRaisesRegex(workflow.ContractError, "cover all ids"):
            workflow.validate_report(report, self.task, self.head)

    def test_sensitive_changed_file_never_enters_packet_even_if_in_scope(self):
        self.task["allowed_paths"].append("server.properties")
        self.write(TASK, json.dumps(self.task))
        self.write("server.properties", "test.value=not-a-secret\n")
        self.head = self.commit("Credential-shaped file")
        with self.assertRaisesRegex(workflow.ContractError, "sensitive path"):
            self.make_packet()
        self.assertFalse(self.packet.exists())

    def test_review_budget_rejects_instead_of_truncating_patch(self):
        self.task["review_budget"]["max_diff_bytes"] = 1
        self.write(TASK, json.dumps(self.task))
        self.head = self.commit("Tiny review budget")
        with self.assertRaisesRegex(workflow.ContractError, "diff exceeds review budget"):
            self.make_packet()
        self.assertFalse(self.packet.exists())


if __name__ == "__main__":
    unittest.main()
