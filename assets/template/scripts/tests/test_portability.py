"""Project-independent target branches and host environment classification."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_dispatch as dispatch


class PortabilityTests(unittest.TestCase):
    def test_publication_uses_frozen_non_main_target_branch(self):
        state = {"head_sha": "a" * 40}
        with mock.patch.object(dispatch, "frozen", return_value=({}, {"base_branch": "trunk"})):
            self.assertEqual(dispatch.target_branch(state), "trunk")
            dispatch.check_pr({"isDraft": True, "baseRefName": "trunk", "headRefOid": "a" * 40}, state)
            with self.assertRaises(dispatch.DispatchError):
                dispatch.check_pr({"isDraft": True, "baseRefName": "main", "headRefOid": "a" * 40}, state)

    def test_exit_two_is_not_implicitly_an_environment_pause(self):
        config = {"allowed_checks": {"pytest": ["pytest"]}}
        self.assertFalse(dispatch.environment_check(["pytest"], 2, config))
        config["environment_check_commands"] = ["pytest"]
        self.assertTrue(dispatch.environment_check(["pytest"], 2, config))
        self.assertFalse(dispatch.environment_check(["pytest"], 1, config))
        self.assertFalse(dispatch.environment_check(["other-command"], 2, config))
