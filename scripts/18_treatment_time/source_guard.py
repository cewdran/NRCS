"""Byte integrity of frozen sources and their private snapshots (stdlib only)."""
from pathlib import Path
from common import require, sha, json_file, safe_relative, utc


def verify_sources(run, check_original=True):
    run = Path(run)
    specification = json_file(run / 'SOURCE_MANIFEST.json')
    verified = []
    for source in specification['sources']:
        original = Path(source['root'])
        destination = run / 'source_snapshot' / safe_relative(source['id'])
        for item in source['files']:
            relative = safe_relative(item['path'])
            expected = item['sha256']
            snapshot_file = destination / relative
            require(snapshot_file.is_file() and not snapshot_file.is_symlink(),
                    'Missing or symlinked source snapshot: ' + str(snapshot_file))
            require(sha(snapshot_file) == expected, 'Private source bytes differ: ' + str(snapshot_file))
            if check_original:
                original_file = original / relative
                require(original_file.is_file() and not original_file.is_symlink(),
                        'Missing or symlinked frozen source: ' + str(original_file))
                require(sha(original_file) == expected, 'Original source bytes differ: ' + str(original_file))
            verified.append({'source': source['id'], 'path': str(relative),
                             'expected_sha256': expected, 'snapshot_verified': True,
                             'original_verified': bool(check_original)})
        status = json_file(destination / 'HPC_STATUS.json')
        require(str(status.get('analysis_job_id')) == str(source['job_id']),
                'Frozen source receipt job identity differs: ' + source['id'])
        require(status.get('scheduler_state') == 'COMPLETED'
                and status.get('scheduler_exit_code') == '0:0'
                and status.get('pipeline_exit_code') == 0
                and status.get('scientific_scripts_completed') is True,
                'Frozen source receipt does not establish successful computation: ' + source['id'])
    return {'passed': True, 'source_files_changed': False, 'utc': utc(),
            'originals_checked': bool(check_original), 'files': verified,
            'source_manifest_sha256': sha(run / 'SOURCE_MANIFEST.json')}
