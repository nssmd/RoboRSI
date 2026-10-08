import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / 'scripts/native_bundle.py'
spec = importlib.util.spec_from_file_location('native_bundle', MODULE)
nb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nb)
sys.path.insert(0, str(MODULE.parent))
from publish_native_bundle import publication_evidence


class NativeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.primary = 'roborsi/embodied/skills/base/place_on_surface/libero/policy.py'
        self.helper = 'roborsi/embodied/skills/base/_lib/libero/reference.py'
        self.fixture = {'sim_task': 'libero_goal/8', 'seeds': [21, 22],
                        'setup': {'skill': 'grasp_object', 'args': {'object': 'bowl'}},
                        'args': [{'target': 'plate'}],
                        'pass_criteria': {'kind': 'simulator_task_success', 'min_seeds_passing': 2}}
        self.contract = {'primary_skill': 'place_on_surface', 'fixture': self.fixture}
        path = self.repo / self.primary
        path.parent.mkdir(parents=True)
        path.write_text('VALUE = 1\n')
        path.chmod(0o640)
        path.with_name('SKILL.md').write_text('---\n' + json.dumps({'metadata': {'harness': self.fixture}}) + '\n---\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.bundle = {'base_revision': self.base, 'primary_skill': 'place_on_surface',
                       'fixture': copy.deepcopy(self.fixture), 'files': [
                           {'path': self.primary, 'before_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'after': 'VALUE = 2\n'},
                           {'path': self.helper, 'before_sha256': None, 'after': 'REFERENCE = True\n'}]}

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True, stderr=subprocess.STDOUT)

    def stage(self, bundle=None):
        return nb.staged(self.repo, bundle or self.bundle, ast.parse, self.contract)

    def receipts(self):
        sha = nb.digest(self.bundle)
        return ({'bundle_sha256': sha, 'decision': 'approve'},
                {'bundle_sha256': sha, 'verdict': 'PASS', 'kind': 'simulator_task_success',
                 'pass_count': 2, 'total': 2, 'crash_count': 0, 'fixture': copy.deepcopy(self.fixture)})

    def test_complete_stage_and_exception_rollback_preserve_bytes_and_modes(self):
        with self.assertRaisesRegex(RuntimeError, 'gate failure'):
            with self.stage():
                self.assertEqual((self.repo / self.primary).read_text(), 'VALUE = 2\n')
                self.assertTrue((self.repo / self.helper).exists())
                raise RuntimeError('gate failure')
        self.assertEqual((self.repo / self.primary).read_text(), 'VALUE = 1\n')
        self.assertEqual((self.repo / self.primary).stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.repo / self.helper).exists())
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_all_hashes_validated_before_first_write(self):
        self.bundle['files'][1]['before_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'Source hash mismatch'):
            with self.stage(): pass
        self.assertEqual((self.repo / self.primary).read_text(), 'VALUE = 1\n')

    def test_cannot_edit_gate_or_escape_scope(self):
        for path in ['scripts/test_base_skill.py', '../outside.py',
                     'roborsi/embodied/skills/base/place_on_surface/robotwin/policy.py']:
            candidate = copy.deepcopy(self.bundle)
            candidate['files'][1]['path'] = path
            with self.assertRaises(ValueError):
                with self.stage(candidate): pass

    def test_changed_fixture_refused(self):
        self.bundle['fixture']['seeds'] = [21, 23]
        with self.assertRaisesRegex(ValueError, 'validation contract'):
            with self.stage(): pass

    def test_concurrent_stage_is_refused(self):
        with self.stage():
            with self.assertRaises(BlockingIOError):
                with self.stage(): pass

    def test_symlinked_helper_is_refused(self):
        directory = self.repo / 'roborsi/embodied/skills/base/_lib'
        directory.mkdir()
        (directory / 'libero').symlink_to(self.repo / 'outside', target_is_directory=True)
        # The symlink is tracked so this tests containment, not dirty-tree refusal.
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'link')
        self.bundle['base_revision'] = self.git('rev-parse', 'HEAD').strip()
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            with self.stage(): pass

    def test_failed_gate_or_different_review_cannot_publish(self):
        for change in ['failed_gate', 'wrong_review']:
            review, gate = self.receipts()
            if change == 'failed_gate':gate['verdict'] = 'FAIL'
            else:review['bundle_sha256'] = 'other'
            with self.stage() as staged:
                with self.assertRaises(ValueError):
                    nb.publish(self.repo, self.bundle, staged, review, gate, self.contract)
            self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)

    def test_post_gate_candidate_drift_blocks_commit(self):
        with self.stage() as staged:
            (self.repo / self.helper).write_text('CHANGED = 1\n')
            review, gate = self.receipts()
            with self.assertRaisesRegex(ValueError, 'changed after review'):
                nb.publish(self.repo, self.bundle, staged, review, gate, self.contract)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_atomic_commit_preserves_all_files_as_one_revision(self):
        with self.stage() as staged:
            review, gate = self.receipts()
            commit = nb.publish(self.repo, self.bundle, staged, review, gate, self.contract)
        self.assertNotEqual(commit, self.base)
        self.assertEqual(set(self.git('diff', '--name-only', self.base, commit).splitlines()),
                         {self.primary, self.helper})
        self.assertEqual((self.repo / self.primary).read_text(), 'VALUE = 2\n')
        self.assertEqual(self.git('status', '--porcelain'), '')

    def publication_receipts(self):
        raw = self.repo / '.git/synthetic-review.json'
        raw.write_text(json.dumps({'model': 'gpt-5.6-sol',
                                   'raw': json.dumps({'decision': 'approve', 'required_changes': []})}))
        gate = {'bundle_sha256': nb.digest(self.bundle), 'qualified_pass': True,
                'source_restored': True, 'state': 'gate_complete', 'fixture': self.fixture,
                'staged_file_sha256': {r['path']: hashlib.sha256(r['after'].encode()).hexdigest()
                                       for r in self.bundle['files']},
                'actual_report': {'verdict': 'PASS', 'kind': 'simulator_task_success',
                                  'skill': 'place_on_surface', 'sim_task': 'libero_goal/8',
                                  'seeds': [21, 22], 'min_required': 2, 'pass_count': 2,
                                  'total': 2, 'crash_count': 0,
                                  'results': [{'seed': seed, 'harness_simulator_success': True}
                                              for seed in [21, 22]]}}
        gate_bytes = json.dumps(gate).encode()
        review = {'purpose': 'publish_native_bundle', 'bundle_sha256': nb.digest(self.bundle),
                  'gate_record_sha256': hashlib.sha256(gate_bytes).hexdigest(),
                  'model': 'gpt-5.6-sol', 'decision': 'approve', 'required_changes': [],
                  'raw_response_path': str(raw),
                  'raw_response_sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}
        return gate, review, gate_bytes

    def test_publication_checks_real_report_not_summary_flag_alone(self):
        gate, review, blob = self.publication_receipts()
        normalized, _ = publication_evidence(self.bundle, self.contract, gate, review, blob)
        self.assertEqual(normalized['decision'], 'approve')
        gate['actual_report']['results'][1]['harness_simulator_success'] = False
        blob = json.dumps(gate).encode()
        review['gate_record_sha256'] = hashlib.sha256(blob).hexdigest()
        with self.assertRaisesRegex(ValueError, 'independent task gate'):
            publication_evidence(self.bundle, self.contract, gate, review, blob)

    def test_publication_refuses_normalized_approval_over_raw_rejection(self):
        gate, review, blob = self.publication_receipts()
        raw = Path(review['raw_response_path'])
        raw.write_text(json.dumps({'model': 'gpt-5.6-sol', 'raw': '{"decision":"reject"}'}))
        review['raw_response_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'actual Manager output'):
            publication_evidence(self.bundle, self.contract, gate, review, blob)

    def test_publication_gate_bytes_are_bound_to_parsed_evidence(self):
        gate, review, blob = self.publication_receipts()
        gate['actual_report']['pass_count'] = 1
        with self.assertRaisesRegex(ValueError, 'bytes and parsed evidence'):
            publication_evidence(self.bundle, self.contract, gate, review, blob)


if __name__ == '__main__':unittest.main()
