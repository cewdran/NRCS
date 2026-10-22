"""Guarded independent treatment/time worker and auditable completion receipt."""
from hpc_guard import require_hpc
execution = require_hpc()

import argparse
from pathlib import Path
import traceback
from common import require, save, sha, utc, json_file, verify_manifest, write_manifest, workers
from source_guard import verify_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', required=True)
    parser.add_argument('--worker-id', required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    worker_id = args.worker_id
    config = json_file(run / 'CONFIG.json')
    require(worker_id in workers(config), 'Worker is not in frozen inventory.')
    receipt = json_file(run / 'submission.json')
    require(receipt.get('jobs', {}).get('worker:' + worker_id) == execution['job_id'],
            'Worker allocation differs from the immutable submitted role.')
    require(Path(receipt.get('run_directory', '')).resolve() == run,
            'Worker run path differs from its submission receipt.')
    destination = run / 'workers' / worker_id
    destination.mkdir(parents=True, exist_ok=True)
    try:
        verify_manifest(run)
        environment = json_file(run / 'audit/workers' / worker_id / 'ENVIRONMENT_STATUS.json')
        require(environment.get('status') == 'COMPUTE_RUNTIME_VERIFIED'
                and environment.get('execution', {}).get('job_id') == execution['job_id']
                and environment.get('execution', {}).get('node') == execution['node'],
                'Worker environment proof differs from this allocation.')
        checks = json_file(run / 'audit/stage/NUMERIC_SELF_CHECKS.json')
        require(checks.get('passed') is True and checks.get('numerical_checks_executed_on_puma') is True,
                'Puma numerical self-check receipt is missing or failed.')
        require(checks.get('execution', {}).get('cluster') == 'puma', 'Self-check execution was not on Puma.')
        proof = verify_sources(run, check_original=True)
        save(destination / 'SOURCE_VERIFICATION_BEFORE.json', proof)
        if worker_id.endswith('_soil'):
            from soil_worker import run_worker
        else:
            from functional_worker import run_worker
        summary = run_worker(run, worker_id)
        require(isinstance(summary, dict), 'Worker engine did not return its summary.')
        for name in ['tests.csv', 'observations.csv', 'group_summaries.csv', 'cohort_audit.csv']:
            require((destination / name).is_file(), 'Worker output missing: ' + name)
        proof = verify_sources(run, check_original=True)
        save(destination / 'SOURCE_VERIFICATION_AFTER.json', proof)
        paths = [p.relative_to(destination) for p in destination.rglob('*') if p.is_file()
                 and p.name not in ['OUTPUT_SHA256SUMS.txt', 'WORKER_STATUS.json']]
        manifest_sha = write_manifest(destination, paths)
        save(destination / 'WORKER_STATUS.json', {
            'completed': True, 'worker_id': worker_id, 'execution': execution,
            'utc': utc(), 'numerical_self_checks_passed': True,
            'self_checks_sha256': sha(run / 'audit/stage/NUMERIC_SELF_CHECKS.json'),
            'source_files_changed': False, 'package_integrity_passed': True,
            'output_manifest_sha256': manifest_sha, 'summary': summary})
        print('WORKER_COMPLETED=' + worker_id)
    except Exception as exc:
        save(destination / 'WORKER_STATUS.json', {
            'completed': False, 'worker_id': worker_id, 'execution': execution,
            'utc': utc(), 'error': str(exc), 'traceback': traceback.format_exc(),
            'source_files_changed': None, 'numerical_self_checks_passed': None})
        raise


if __name__ == '__main__':
    main()
