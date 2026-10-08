"""Run the real declared task harness on an approved isolated native bundle.

This command never publishes. A later actual Manager publication decision must
refer to the resulting bundle/gate hashes before native_bundle.publish is used.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--review', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    bundle = json.loads(Path(args.bundle).read_text())
    contract = json.loads(Path(args.contract).read_text())
    review = json.loads(Path(args.review).read_text())
    output = Path(args.out)
    if output.exists():
        raise ValueError('Existing gate attempt: inspect it; do not retry unchanged')
    sys.path[:0] = [str(repo), str(repo / 'scripts')]
    from native_bundle import digest, staged
    from manager_native import assert_native_candidate
    from manager_cycle import parse
    from scripts_lib_harness_gate import _invoke_harness
    expected = digest(bundle)
    if (review.get('bundle_sha256') != expected or review.get('decision') != 'approve'
            or review.get('model') != 'gpt-5.6-sol' or review.get('required_changes')):
        raise ValueError('A matching actual Manager trial approval is required')
    raw = Path(review['raw_response_path'])
    if hashlib.sha256(raw.read_bytes()).hexdigest() != review['raw_response_sha256']:
        raise ValueError('Actual Manager review response changed')
    raw_record = json.loads(raw.read_text())
    actual_decision = parse(raw_record['raw'])
    if (raw_record.get('model') != review['model'] or actual_decision.get('decision') != 'approve'
            or actual_decision.get('required_changes')):
        raise ValueError('Approval receipt disagrees with the actual Manager response')
    fixture = contract['fixture']
    if (fixture.get('pass_criteria') != {'kind': 'simulator_task_success', 'min_seeds_passing': 2}
            or len(fixture.get('seeds', [])) != 2 or len(set(fixture['seeds'])) != 2):
        raise ValueError('This bundle runner preserves the declared two-seed task gate')
    if os.environ.get('ROBORSI_HARNESS_NAMESPACE') != 'libero':
        raise ValueError('Configure the established LIBERO runtime and GPU admission first')
    output.mkdir(parents=True)
    record = {'utc': time.time(), 'bundle_sha256': expected, 'repo': str(repo),
              'fixture': fixture, 'actual_review': review, 'state': 'staging',
              'publication': False}
    (output / 'state.json').write_text(json.dumps(record, indent=2))
    try:
        with staged(repo, bundle, assert_native_candidate, contract) as stage:
            record.update(state='real_harness', staged_file_sha256=stage['file_sha256'])
            (output / 'state.json').write_text(json.dumps(record, indent=2))
            report, stdout, stderr = _invoke_harness(bundle['primary_skill'], timeout_s=900)
            (output / 'stdout.log').write_text(stdout)
            (output / 'stderr.log').write_text(stderr)
            actual = report.get('results', [])
            passed = (report.get('verdict') == 'PASS'
                      and report.get('kind') == 'simulator_task_success'
                      and report.get('skill') == bundle['primary_skill']
                      and report.get('sim_task') == fixture['sim_task']
                      and report.get('seeds') == fixture['seeds']
                      and report.get('min_required') == 2
                      and report.get('pass_count') == 2 and report.get('total') == 2
                      and report.get('crash_count') == 0
                      and {r.get('seed') for r in actual if r.get('harness_simulator_success') is True}
                      == set(fixture['seeds']))
            record.update(state='gate_complete', verdict='PASS' if passed else report.get('verdict', 'ERROR'),
                          qualified_pass=passed, actual_report=report)
            if record['verdict'] == 'PASS' and not passed:
                record['verdict'] = 'ERROR'
            # Roll back even a passing trial. Publication is a separate decision.
        record['source_restored'] = True
    except Exception as exc:
        record.update(state='error', error=repr(exc))
        raise
    finally:
        (output / 'state.json').write_text(json.dumps(record, indent=2))
    return 0 if record.get('qualified_pass') else 1


if __name__ == '__main__':
    raise SystemExit(main())
