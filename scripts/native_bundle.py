"""Content-addressed native LIBERO bundles, staged and rolled back as a unit."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import ast
import fcntl
import hashlib
import json
import os
import re
import subprocess


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _allowed(relative):
    parts = PurePosixPath(relative).parts
    if not relative or relative.startswith('/') or any(p in ('.', '..') for p in parts):
        return False
    if str(PurePosixPath(relative)) != relative:
        return False
    prefix = ('roborsi', 'embodied', 'skills', 'base')
    if parts[:4] != prefix:
        return False
    if len(parts) >= 7 and parts[4:6] == ('_lib', 'libero'):
        return relative.endswith('.py')
    return (len(parts) == 7 and re.fullmatch('[a-z][a-z0-9_]{1,63}', parts[4]) is not None
            and parts[5] == 'libero' and parts[6] in ('policy.py', 'SKILL.md'))


def _path(repo, relative):
    if not _allowed(relative):
        raise ValueError('Outside declared native LIBERO code scope: ' + str(relative))
    path = repo / relative
    cursor = path
    while cursor != repo:
        if cursor.is_symlink():
            raise ValueError('Symlink in bundle path: ' + relative)
        cursor = cursor.parent
    path.resolve().relative_to(repo)
    return path


def validate(repo, bundle, inspect_python, contract):
    """Check every base hash and candidate before writing any file."""
    repo = Path(repo).resolve()
    if bundle.get('primary_skill') != contract['primary_skill'] or bundle.get('fixture') != contract['fixture']:
        raise ValueError('Bundle changed the separately declared validation contract')
    current = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if current != bundle['base_revision']:
        raise ValueError('Bundle base revision changed')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip():
        raise ValueError('Use a clean isolated worktree for native bundles')
    files = bundle.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('Empty native bundle')
    seen = set()
    for item in files:
        relative = item['path']
        if relative in seen:
            raise ValueError('Duplicate bundle path: ' + relative)
        seen.add(relative)
        path = _path(repo, relative)
        before = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if before != item['before_sha256']:
            raise ValueError('Source hash mismatch: ' + relative)
        content = item['after']
        if not isinstance(content, str):
            raise ValueError('Bundle contents must be UTF-8 text')
        if path.is_file() and content.encode() == path.read_bytes():
            raise ValueError('Unchanged file is not a repair: ' + relative)
        if relative.endswith('.py'):
            ast.parse(content)
            inspect_python(content)
    skill = bundle['primary_skill']
    if not re.fullmatch('[a-z][a-z0-9_]{1,63}', skill):
        raise ValueError('Invalid primary skill')
    if 'roborsi/embodied/skills/base/' + skill + '/libero/policy.py' not in seen:
        raise ValueError('Primary skill implementation must be in the bundle')
    import yaml
    md_path = 'roborsi/embodied/skills/base/' + skill + '/libero/SKILL.md'
    replacement = next((item['after'] for item in files if item['path'] == md_path), None)
    document = replacement if replacement is not None else _path(repo, md_path).read_text()
    frontmatter = yaml.safe_load(document.split('---', 2)[1])
    fixture = (frontmatter.get('metadata') or {}).get('harness') or frontmatter.get('harness')
    if fixture != bundle['fixture']:
        raise ValueError('Primary harness differs from the declared fixture')
    return {item['path']: hashlib.sha256(item['after'].encode()).hexdigest() for item in files}


@contextmanager
def staged(repo, bundle, inspect_python, contract):
    """Exclusive per-worktree staging; no partial bundle is admitted to a gate."""
    repo = Path(repo).resolve()
    lock_path = Path(subprocess.check_output(
        ['git', 'rev-parse', '--git-path', 'native-bundle.lock'], cwd=repo, text=True).strip())
    if not lock_path.is_absolute():
        lock_path = repo / lock_path
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with _staged_unlocked(repo, bundle, inspect_python, contract) as state:
            yield state


@contextmanager
def _staged_unlocked(repo, bundle, inspect_python, contract):
    """Expose all validated files together and restore them on every failed exit."""
    repo = Path(repo).resolve()
    hashes = validate(repo, bundle, inspect_python, contract)
    backups = {}
    created_dirs = []
    temporary_paths = []
    committed = [False]
    try:
        for item in bundle['files']:
            path = _path(repo, item['path'])
            backups[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
            absent = []
            parent = path.parent
            while not parent.exists():
                absent.append(parent); parent = parent.parent
            for directory in reversed(absent):
                directory.mkdir(); created_dirs.append(directory)
            temporary = path.with_name(path.name + '.bundle-tmp')
            temporary_paths.append(temporary)
            temporary.write_text(item['after'])
            if backups[path] is not None:
                temporary.chmod(backups[path][1])
            os.replace(temporary, path)
        yield {'bundle_sha256': digest(bundle), 'file_sha256': hashes, 'committed': committed}
    finally:
        if not committed[0]:
            for temporary in temporary_paths:
                temporary.unlink(missing_ok=True)
            for path, before in reversed(list(backups.items())):
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(before[0]); path.chmod(before[1])
            for directory in reversed(created_dirs):
                try: directory.rmdir()
                except OSError: pass


def publish(repo, bundle, stage, review, gate, contract):
    """Commit only an unchanged bundle with matching real review/gate receipts."""
    repo = Path(repo).resolve()
    expected = digest(bundle)
    expected_files = {item['path']: hashlib.sha256(item['after'].encode()).hexdigest() for item in bundle['files']}
    if (stage.get('bundle_sha256') != expected or stage.get('file_sha256') != expected_files
            or bundle.get('fixture') != contract['fixture']
            or bundle.get('primary_skill') != contract['primary_skill']):
        raise ValueError('Staging receipt or validation contract does not match bundle')
    if review.get('bundle_sha256') != expected or review.get('decision') != 'approve':
        raise ValueError('Missing matching actual Manager approval')
    if (gate.get('bundle_sha256') != expected or gate.get('verdict') != 'PASS'
            or gate.get('kind') != 'simulator_task_success'
            or gate.get('pass_count') != 2 or gate.get('total') != 2
            or gate.get('crash_count') != 0 or gate.get('fixture') != bundle['fixture']):
        raise ValueError('Missing unchanged two-seed real task gate')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip() != bundle['base_revision']:
        raise ValueError('Source revision changed during gate')
    for relative, sha in stage['file_sha256'].items():
        if hashlib.sha256(_path(repo, relative).read_bytes()).hexdigest() != sha:
            raise ValueError('Candidate changed after review or gate: ' + relative)
    paths = list(stage['file_sha256'])
    status = subprocess.check_output(['git', 'status', '--porcelain', '-z', '--untracked-files=all'], cwd=repo)
    changed = {entry[3:].decode() for entry in status.split(b'\0') if entry}
    if changed != set(paths):
        raise ValueError('Unexpected working-tree change during native gate')
    subprocess.run(['git', 'add', '--', *paths], cwd=repo, check=True, capture_output=True)
    result = subprocess.run(['git', '-c', 'user.name=RoboRSI Manager', '-c',
                             'user.email=manager@localhost', 'commit', '--only', '-m',
                             'evolve: publish reviewed native bundle ' + expected[:12], '--', *paths],
                            cwd=repo, capture_output=True, text=True)
    if result.returncode:
        subprocess.run(['git', 'reset', '--', *paths], cwd=repo, check=True, capture_output=True)
        raise RuntimeError(result.stderr)
    stage['committed'][0] = True
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
