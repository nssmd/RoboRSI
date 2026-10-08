import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from native_patch_context import materialize_policy_patch, review_text
from manager_native import assert_native_candidate


class NativeRevisionTests(unittest.TestCase):
    def test_existing_skill_keeps_name_and_exact_reviewer_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'roborsi/embodied/skills/base/grasp_object/libero/policy.py'
            path.parent.mkdir(parents=True)
            path.write_text('def dispatch_runtime(state, args):\n    return 1\n')
            path.with_name('SKILL.md').write_text('existing contract')
            request = dict(kind='patch', name='diagnostic-title',
                           target_path=str(path.relative_to(root)),
                           old_string='return 1', new_string='return 2')
            proposal = materialize_policy_patch(request, root)
            self.assertEqual(proposal['name'], 'grasp_object')
            self.assertEqual(proposal['kind'], 'update')
            self.assertEqual(proposal['new_code'],
                             'def dispatch_runtime(state, args):\n    return 2\n')
            self.assertIn('return 1', path.read_text())
            self.assertEqual(request['kind'], 'patch')
            with self.assertRaises(ValueError):
                materialize_policy_patch(dict(request, old_string='missing'), root)

    def test_native_sensor_control_code_is_allowed(self):
        assert_native_candidate('def dispatch_runtime(state,args):\n'
                                ' return state.env.take_snapshot()\n')

    def test_task_predicate_is_not_a_sensor(self):
        with self.assertRaises(ValueError):
            assert_native_candidate('def dispatch_runtime(state,args):\n'
                                    ' return state.env.check_success()\n')

    def test_negative_disclaimer_does_not_whitelist_privileged_claims(self):
        self.assertNotIn('ground truth', review_text('never simulator object/site ground truth'))
        text = 'ground truth says the object is at x'
        self.assertEqual(review_text(text), text)


if __name__ == '__main__':
    unittest.main()
