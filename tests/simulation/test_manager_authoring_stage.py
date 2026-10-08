from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from manager_native import request_system


class AuthoringStageTests(unittest.TestCase):
    def test_generation_request_has_authoring_not_approval_stage(self):
        text = request_system({'kind': 'code_revision_request'}, 'original rules')
        self.assertTrue(text.startswith('original rules'))
        self.assertIn('MANAGER AUTHORS A CANDIDATE', text)
        self.assertIn('decision=defer', text)
        self.assertIn('separate actual Manager review', text)
        self.assertIn('unchanged simulator gate', text)

    def test_existing_candidate_review_is_byte_identical(self):
        for kind in ['new', 'update', 'patch', 'failure_hypothesis', None]:
            self.assertEqual(request_system({'kind': kind}, 'rules\nexact'), 'rules\nexact')

    def test_request_data_and_fixture_are_unchanged(self):
        request = {'kind': 'code_revision_request', 'development_seeds': [21,22],
                   'development_pass_criteria': {'kind': 'move_completes', 'min_seeds_passing':2}}
        import copy
        before = copy.deepcopy(request)
        request_system(request, 'rules')
        self.assertEqual(request, before)


if __name__ == '__main__':
    unittest.main()
