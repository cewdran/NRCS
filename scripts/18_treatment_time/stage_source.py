"""Stage immutable source snapshots on a verified Puma compute node."""
from hpc_guard import require_hpc
execution = require_hpc()

import argparse
from pathlib import Path
import shutil
from common import require, sha, save, json_file, safe_relative, utc, verify_manifest
from source_guard import verify_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    verify_manifest(run)
    receipt = json_file(run / 'submission.json')
    require(receipt.get('jobs', {}).get('stage') == execution['job_id'],
            'Stage allocation differs from the immutable submission role.')
    require(Path(receipt.get('run_directory', '')).resolve() == run,
            'Stage run path differs from its submission receipt.')
    require(not (run / 'source_snapshot').exists(), 'Private source destination already exists.')
    specification = json_file(run / 'SOURCE_MANIFEST.json')
    for source in specification['sources']:
        original = Path(source['root'])
        require(original.is_absolute() and original.is_dir() and not original.is_symlink(),
                'Frozen source root must be an absolute regular directory.')
        require(not run.is_relative_to(original) and not original.is_relative_to(run),
                'New run and frozen source root must be isolated.')
        for item in source['files']:
            relative = safe_relative(item['path'])
            source_file = original / relative
            require(source_file.is_file() and not source_file.is_symlink(),
                    'Frozen source missing or symlinked: ' + str(source_file))
            require(sha(source_file) == item['sha256'], 'Frozen source hash changed: ' + str(source_file))
            target = run / 'source_snapshot' / safe_relative(source['id']) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            require(sha(target) == item['sha256'], 'Source copy did not preserve bytes: ' + str(target))
            target.chmod(0o444)
    proof = verify_sources(run, check_original=True)
    save(run / 'audit/stage/SOURCE_VERIFICATION.json', proof)
    save(run / 'audit/stage/STAGE_STATUS.json', {
        'completed': True, 'utc': utc(), 'execution': execution,
        'package_integrity_passed': True, 'source_files_changed': False,
        'snapshot_file_count': len(proof['files']),
        'numerical_self_checks': 'Separate guarded stage command must pass before workers release.'})
    print('SOURCE_STAGE_COMPLETED=true')


if __name__ == '__main__':
    main()
