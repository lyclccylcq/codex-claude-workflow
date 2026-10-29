"""Minimal worker context and status-first receipts, without any model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_handoff
import ai_trace
import ai_workflow as workflow
from test_ai_workflow import spec


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workflow-handoff-')
        self.addCleanup(temp.cleanup)
        self.location = Path(temp.name).resolve()
        self.worker = self.location / 'worker'
        (self.worker / 'src').mkdir(parents=True)
        (self.worker / 'src/value.py').write_text('def value():\n    return 1\n# UNRELATED_CONTEXT\n')
        self.run = self.location / 'TASK-0001-abcdef1234'
        self.folder = self.run / 'round-1'
        self.state = {'workspace': str(self.worker), 'run_dir': str(self.run), 'branch': 'codex/task-0001-test',
                      'head_sha': 'a' * 40, 'round': 1, 'status': 'RUNNING'}
        self.spec = spec()
        self.spec.update(worker_context=[{'path': 'src/value.py', 'start_line': 1, 'end_line': 2}], review_mode='focused')

    def prepare(self, limit=24000):
        return ai_handoff.prepare(self.state, self.spec, self.folder, '', limit)

    def test_handoff_has_exact_assignment_and_only_selected_code_ranges(self):
        bootstrap = self.prepare()
        content = Path(self.state['handoff_path']).read_text()
        self.assertIn(str(self.worker), content)
        self.assertIn(self.state['branch'], content)
        self.assertIn('src/value.py:1-2', content)
        self.assertIn('return 1', content)
        self.assertNotIn('UNRELATED_CONTEXT', content)
        self.assertNotIn('return 1', bootstrap)
        self.assertNotIn(self.spec['objective'], bootstrap)
        self.assertNotIn('Read CLAUDE.md', content)
        self.assertIn('return BLOCKED with exact', content)
        self.assertIn('HANDOFF.md', bootstrap)
        ai_handoff.verify(self.state)

    def test_budget_failure_keeps_large_context_out_of_worker_handoff(self):
        with self.assertRaisesRegex(workflow.ContractError, 'context budget'):
            self.prepare(limit=1000)
        self.assertFalse((self.folder / 'HANDOFF.md').exists())
        self.assertNotIn('handoff_path', self.state)

    def test_spec_rejects_sensitive_or_unbounded_context(self):
        for item in ({'path': '../outside', 'start_line': 1, 'end_line': 2},
                     {'path': '.env', 'start_line': 1, 'end_line': 2},
                     {'path': 'src/TODO.py', 'start_line': 1, 'end_line': 2},
                     {'path': 'src/*.py', 'start_line': 1, 'end_line': 2},
                     {'path': 'src/value.py'},
                     {'path': 'src/value.py', 'start_line': 2, 'end_line': 1}):
            self.spec['worker_context'] = [item]
            with self.subTest(item=item), self.assertRaises(workflow.ContractError):
                workflow.validate_spec(self.spec)

    def test_context_link_cannot_read_outside_worktree(self):
        outside = self.location / 'outside.py'
        outside.write_text('OUTSIDE_CONTENT\n')
        (self.worker / 'src/value.py').unlink()
        (self.worker / 'src/value.py').symlink_to(outside)
        with self.assertRaisesRegex(workflow.ContractError, 'escapes workspace'):
            self.prepare()
        self.assertFalse((self.folder / 'HANDOFF.md').exists())

    def test_modified_handoff_invalidates_worker_evidence(self):
        self.prepare()
        Path(self.state['handoff_path']).write_text('Changed acceptance criteria')
        with self.assertRaisesRegex(workflow.ContractError, 'handoff changed'):
            ai_handoff.verify(self.state)

    def test_brief_is_bounded_and_worker_completion_does_not_become_review_pass(self):
        self.state.update(status='AWAITING_REVIEW', summary='x' * 10000, pending_feedback='PRIVATE_LONG_LOG')
        brief = ai_trace.brief(self.state)
        self.assertEqual(next(iter(brief)), 'status')
        self.assertEqual(brief['status'], 'AWAITING_REVIEW')
        self.assertEqual(brief['next_action'], 'controller_review')
        self.assertLess(len(brief['summary']), 450)
        self.assertNotIn('PRIVATE_LONG_LOG', json.dumps(brief))
        ai_handoff.worker_result(self.state, self.folder, {'status': 'COMPLETED', 'summary': 'Edited the scoped file'})
        self.assertEqual((self.folder / 'WORKER_RESULT.md').read_text().splitlines()[0], 'STATUS: COMPLETED')
        self.assertIn('host checks and controller review pending', (self.folder / 'WORKER_RESULT.md').read_text())
