"""Real project installation and preservation checks; no model or network requests."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/bootstrap.py"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.location = Path(self.temp.name).resolve()
        self.project = self.location / "Project Orion"

    def bootstrap(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(BOOTSTRAP), "--project", str(self.project), "--init-git", *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_preview_creates_no_project_or_git(self):
        result = self.bootstrap()
        self.assertEqual(result["status"], "PLANNED")
        self.assertFalse(self.project.exists())

    def test_existing_instructions_and_custom_checks_survive_idempotent_install(self):
        self.project.mkdir()
        original = "# Team rules\nKeep our domain invariants.\n"
        (self.project / "AGENTS.md").write_text(original)
        (self.project / "CLAUDE.md").write_text("Keep our language convention.\n")
        (self.project / ".gitignore").write_text("/personal-cache/\n")
        result = self.bootstrap("--apply", "--base-branch", "trunk", "--name", "Orion")
        self.assertEqual(result["status"], "INSTALLED")
        self.assertTrue((self.project / "AGENTS.md").read_text().startswith(original))
        self.assertIn("/personal-cache/", (self.project / ".gitignore").read_text())
        config_path = self.project / "scripts/ai_dispatch_config.json"
        config = json.loads(config_path.read_text())
        self.assertEqual(config["project_name"], "Orion")
        self.assertEqual(config["base_branch"], "trunk")
        self.assertFalse(config["completion_policy"]["merge_authorized"])
        config["allowed_checks"]["team-tests"] = ["python3", "-m", "unittest", "discover", "-s", "team_tests"]
        config_path.write_text(json.dumps(config))
        self.bootstrap("--apply")
        updated = json.loads(config_path.read_text())
        self.assertIn("team-tests", updated["allowed_checks"])
        self.assertEqual(updated["project_name"], "Orion")
        self.assertIn("# Orion 工单调度", (self.project / "docs/ai/automation.md").read_text())
        again = self.bootstrap("--apply")
        self.assertEqual(again["status"], "UNCHANGED")
        self.assertEqual(again["changed_files"], [])
        staged = subprocess.run(["git", "-C", str(self.project), "diff", "--cached", "--name-only"], capture_output=True, text=True, check=True)
        self.assertEqual(staged.stdout, "")

    def test_existing_file_conflict_has_no_partial_install_or_git_init(self):
        (self.project / "scripts").mkdir(parents=True)
        target = self.project / "scripts/ai_dispatch.py"
        target.write_text("# Existing project dispatcher\n")
        result = self.bootstrap("--apply", expected=2)
        self.assertEqual(result["status"], "CONFLICT")
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertFalse((self.project / ".git").exists())
        self.assertEqual(target.read_text(), "# Existing project dispatcher\n")

    def test_managed_code_edits_are_preserved_on_upgrade(self):
        self.bootstrap("--apply")
        target = self.project / "scripts/ai_dispatch.py"
        target.write_text(target.read_text() + "\n# Local project customization\n")
        previous = target.read_bytes()
        self.bootstrap("--apply", expected=2)
        self.assertEqual(target.read_bytes(), previous)

    def test_output_symlink_cannot_redirect_install(self):
        self.project.mkdir()
        outside = self.location / "outside"
        outside.mkdir()
        (self.project / "scripts").symlink_to(outside, target_is_directory=True)
        result = self.bootstrap("--apply", expected=1)
        self.assertIn("symlink", result["reason"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_local_skill_installs_and_preserves_modified_skill(self):
        target = self.location / "skills/codex-claude-workflow"
        command = [sys.executable, str(ROOT / "scripts/install_skill.py"), "--dest", str(target)]
        first = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(first.stdout)["status"], "INSTALLED")
        subprocess.run(command, capture_output=True, check=True)
        self.assertTrue((target / "assets/template/scripts/ai_handoff.py").is_file())
        skill = target / "SKILL.md"
        skill.write_text(skill.read_text() + "\nLocal preference\n")
        before = hashlib.sha256(skill.read_bytes()).hexdigest()
        blocked = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(hashlib.sha256(skill.read_bytes()).hexdigest(), before)

    def test_generated_project_offline_contract_and_pipeline(self):
        self.bootstrap("--apply", "--base-branch", "trunk")
        for argv in ([sys.executable, "scripts/ai_workflow.py", "validate"],
                     [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-q"]):
            result = subprocess.run(argv, cwd=self.project, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
