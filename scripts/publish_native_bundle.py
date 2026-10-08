"""Publish an unchanged multi-file candidate after real gate and Manager review."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys


def publication_evidence(bundle, contract, gate, review, gate_bytes):
    """Normalize verified evidence; this function does not run or invent a gate."""
    from native_bundle import digest
    sha = digest(bundle)
    if json.loads(gate_bytes) != gate:
        raise ValueError('Gate record bytes and parsed evidence disagree')
    gate_sha = hashlib.sha256(gate_bytes).hexdigest()
    if (review.get('purpose') != 'publish_native_bundle'
            or review.get('bundle_sha256') != sha
            or review.get('gate_record_sha256') != gate_sha
            or review.get('model') != 'gpt-5.6-sol'
            or review.get('decision') != 'approve'
            or review.get('required_changes')):
        raise ValueError('Missing matching actual Manager publication approval')
    raw_path = Path(review['raw_response_path'])
    raw_bytes = raw_path.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != review['raw_response_sha256']:
        raise ValueError('Publication review raw response changed')
    raw = json.loads(raw_bytes)
    content = raw['raw'].strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
    decision = json.loads(content)
    if (raw.get('model') != review['model'] or decision.get('decision') != 'approve'
            or decision.get('required_changes')):
        raise ValueError('Publication approval disagrees with actual Manager output')
    if (gate.get('bundle_sha256') != sha or gate.get('qualified_pass') is not True
            or gate.get('source_restored') is not True
            or gate.get('state') != 'gate_complete'
            or gate.get('fixture') != contract['fixture']):
        raise ValueError('A completed, qualified and restored real gate is required')
    expected_files = {item['path']: hashlib.sha256(item['after'].encode()).hexdigest()
                      for item in bundle['files']}
    if gate.get('staged_file_sha256') != expected_files:
        raise ValueError('Gate tested a different file bundle')
    result = gate['actual_report']
    fixture = contract['fixture']
    if (result.get('verdict') != 'PASS' or result.get('kind') != 'simulator_task_success'
            or result.get('skill') != contract['primary_skill']
            or result.get('sim_task') != fixture['sim_task']
            or result.get('seeds') != fixture['seeds'] or result.get('min_required') != 2
            or result.get('pass_count') != 2 or result.get('total') != 2
            or result.get('crash_count') != 0
            or {r.get('seed') for r in result.get('results', [])
                if r.get('harness_simulator_success') is True} != set(fixture['seeds'])):
        raise ValueError('Real harness report fails the declared independent task gate')
    return ({'bundle_sha256': sha, 'decision': 'approve'},
            {'bundle_sha256': sha, 'verdict': 'PASS', 'kind': result['kind'],
             'pass_count': 2, 'total': 2, 'crash_count': 0, 'fixture': fixture})


def main():
    parser = argparse.ArgumentParser()
    for name in ['repo', 'bundle', 'contract', 'gate', 'publication-review', 'out']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    output = Path(args.out)
    if output.exists():
        raise ValueError('Publication receipt already exists; inspect instead of publishing twice')
    bundle = json.loads(Path(args.bundle).read_text())
    contract = json.loads(Path(args.contract).read_text())
    gate_bytes = Path(args.gate).read_bytes()
    gate = json.loads(gate_bytes)
    review = json.loads(Path(args.publication_review).read_text())
    sys.path[:0] = [str(repo), str(repo / 'scripts')]
    from native_bundle import staged, publish, digest
    from manager_native import assert_native_candidate
    validated_review, validated_gate = publication_evidence(bundle, contract, gate, review, gate_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'state': 'publication_intent', 'bundle_sha256': digest(bundle),
               'gate_record_sha256': hashlib.sha256(gate_bytes).hexdigest(),
               'actual_publication_review': review, 'normal_cross_task_invocation_verified': False}
    with output.open('x') as file:
        json.dump(receipt, file, indent=2)
    with staged(repo, bundle, assert_native_candidate, contract) as stage:
        commit = publish(repo, bundle, stage, validated_review, validated_gate, contract)
    receipt.update(state='published', commit=commit, changed_files=list(stage['file_sha256']),
                   file_sha256=stage['file_sha256'])
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_text(json.dumps(receipt, indent=2))
    temporary.replace(output)
    print(commit)
    return 0


if __name__ == '__main__':raise SystemExit(main())
