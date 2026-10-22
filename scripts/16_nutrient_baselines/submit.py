#!/usr/bin/env python3
"""Stage an immutable Whipstone OM and nitrate diagnostic run and register afterany collection before releasing it."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from hpc_guard import puma_cluster

package = Path(sys.argv[1]).resolve()
try:
    puma_cluster()
except Exception as exc:
    raise SystemExit('Submit from Puma: ' + str(exc))
cfg = json.loads((package / 'CONFIG.json').read_text())
if cfg.get('package') != 'NRCS_Whipstone_OM_NO3_baselines_HPC_v1':
    raise SystemExit('Unexpected Whipstone OM and nitrate diagnostic package configuration')
runtime = Path(cfg['runtime_package']).resolve()
if runtime != Path('/xdisk/dinusha1/dinusha1/NRCS_corrected_rerun_HPC_v3'):
    raise SystemExit('Use the existing corrected-analysis runtime for this Whipstone OM and nitrate diagnostic stage.')
python = runtime / 'runtime_env/bin/python'
ready = runtime / 'runtime_env/READY.json'
if not ready.is_file():
    raise SystemExit('Corrected runtime READY.json is missing or is not a file: ' + str(ready)
                     + '\nNo jobs were submitted by this attempt.')
interpreter_target_is_file = python.is_file()
interpreter_entry_is_symlink = python.is_symlink()
if not (interpreter_target_is_file or interpreter_entry_is_symlink):
    raise SystemExit('Corrected runtime interpreter entry is missing or is not a file/symlink: ' + str(python)
                     + '\nNo jobs were submitted by this attempt.')
runtime_preflight = {
    'ready_record_path': str(ready),
    'interpreter_entry_path': str(python),
    'interpreter_entry_is_symlink': interpreter_entry_is_symlink,
    'login_node_interpreter_target_is_file': interpreter_target_is_file,
    'compute_node_verification': 'PENDING_MODULE_READY_PYTHON_BUILD_AND_PINNED_VERSION_CHECKS',
    'scientific_checks_performed_on_login_node': False,
}
if interpreter_entry_is_symlink and not interpreter_target_is_file:
    print('RUNTIME_LOGIN_PREFLIGHT=READY record and interpreter symlink entry exist; target is unresolved on this login node.')
else:
    print('RUNTIME_LOGIN_PREFLIGHT=READY record and interpreter entry exist.')
print('COMPUTE_RUNTIME_VERIFICATION=PENDING; the allocated node must pass the existing module, READY, Python-build and pinned-version checks before scientific stages.')
for alias, source in cfg['source_runs'].items():
    path = Path(source['path'])
    if not path.is_absolute() or not path.is_dir():
        raise SystemExit('Required source run is missing: ' + alias + ': ' + str(path))
print('SOURCE_VERIFICATION=PENDING_COMPUTE_NODE; original source runs and historical review statuses stay unchanged.')
resources = cfg['resources']
if resources != {'cpus': 1, 'memory': '5G', 'time': '01:00:00'}:
    raise SystemExit('Unexpected reviewed Whipstone OM and nitrate diagnostic resources')
entries = []
for line in (package / 'SHA256SUMS.txt').read_text().splitlines():
    digest, name = line.split('  ', 1)
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or relative in entries:
        raise SystemExit('Unsafe or duplicate manifest path: ' + name)
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise SystemExit('Invalid package SHA256: ' + name)
    if hashlib.sha256((package / relative).read_bytes()).hexdigest() != digest:
        raise SystemExit('Checksum mismatch: ' + name)
    entries.append(relative)
necessary = {'CONFIG.json', 'RUN.sh', 'COLLECT.sh', 'stage_source.py',
             'analyze_nutrients.py', 'nutrient_self_checks.py',
             'render_nutrient_report.py', 'environment_check.py',
             'record_runtime.py', 'hpc_guard.py', 'common.py', 'collect.py',
             'PROTOCOL.txt', 'RESPONSE_SPEC.json', 'OUTPUT_CONTRACT.json', 'nutrient_core.py'}
if not necessary.issubset({str(path) for path in entries}):
    raise SystemExit('Package manifest omits a necessary Whipstone OM and nitrate diagnostic stage')
download_directory = Path((package / 'DOWNLOAD_DIRECTORY.txt').read_text().strip()).resolve()
if not download_directory.is_dir():
    raise SystemExit('Download directory is missing: ' + str(download_directory))
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
run = package / 'runs' / ('run_' + stamp + '_' + uuid.uuid4().hex[:8])
run.mkdir(parents=True)
for relative in entries:
    destination = run / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package / relative, destination)
shutil.copy2(package / 'SHA256SUMS.txt', run / 'PACKAGE_SHA256SUMS.txt')
(run / 'audit').mkdir()
(run / 'logs').mkdir()
receipt = {
    'download_directory': str(download_directory),
    'package_revision': cfg.get('package_revision', 'whipstone_annual_om_no3_baselines_v1'),
    'state': 'STAGED_NOT_SUBMITTED',
    'created_utc': stamp,
    'run_directory': str(run),
    'package_directory': str(package),
    'accounting_since': (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
    'environment_python': str(python),
    'runtime_preflight': runtime_preflight,
    'account': 'dinusha1', 'partition': 'standard', 'jobs': {}, 'commands': [],
    'nutrient_baseline_status': 'NOT_YET_EXECUTED',
    'scope_flags_describe': 'PREPARED_NOT_EXECUTED; completed engine flags are validated separately',
    'accepted_measured_inventory_source_job_id': '24147988',
    'baseline_parameters_computed': False, 'baseline_predictions_computed': False,
    'baseline_models_fitted': False, 'complex_models_fitted': False,
    'accepted_validation_source_job_id': '24128032',
    'models_fitted': False,
    'statistical_tests_run': False,
    'forecasts_generated': False,
    'numerical_ten_year_forecast': False,
    'ten_year_forecast_generated': False,
    'predictive_validation_established': False,
    'scientific_review_pending': True,
}


def save():
    temporary = run / 'submission.json.tmp'
    temporary.write_text(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    temporary.replace(run / 'submission.json')
    (package / 'LATEST_RUN.txt').write_text(str(run) + '\n')


def call(args):
    receipt['commands'].append(args)
    save()
    environment = dict(os.environ)
    for key in ['BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'SBATCH_GET_USER_ENV']:
        environment.pop(key, None)
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=environment)
    if process.returncode:
        raise RuntimeError('Command failed: ' + repr(args) + '\n' + process.stderr)
    return process.stdout.strip()


def jobid(reply):
    value = reply.split(';')[0]
    if not value.isdigit():
        raise RuntimeError('Unexpected sbatch reply: ' + reply)
    return value


main = None
try:
    common = ['sbatch', '--parsable', '--account=dinusha1', '--partition=standard',
              '--nodes=1', '--ntasks=1', '--export=ALL', '--chdir=' + str(run)]
    main = jobid(call(common + [
        '--hold', '--job-name=nrcs_om_no3', '--cpus-per-task=1',
        '--time=' + resources['time'], '--mem=' + resources['memory'],
        '--output=' + str(run / 'logs/analysis.log'), '--error=' + str(run / 'logs/analysis.log'),
        str(run / 'RUN.sh'), str(run), str(python)]))
    receipt['jobs']['analysis'] = main
    save()
    collector = jobid(call(common + [
        '--job-name=nrcs_nutr_collect', '--cpus-per-task=1', '--mem=5G', '--time=00:30:00',
        '--dependency=afterany:' + main,
        '--output=' + str(run / 'logs/collector.log'), '--error=' + str(run / 'logs/collector.log'),
        str(run / 'COLLECT.sh'), str(run), main, str(python)]))
    receipt['jobs']['collector'] = collector
    receipt['state'] = 'SUBMITTED_NOT_YET_COMPLETE'
    save()
    call(['scontrol', 'release', main])
    save()
except Exception as exc:
    receipt['state'] = 'SUBMISSION_FAILED'
    receipt['error'] = str(exc)
    save()
    if main:
        try:
            call(['scancel', main])
        except Exception:
            pass
    raise SystemExit(str(exc))
print('ANALYSIS_JOB_ID=' + main + '\nCOLLECTOR_JOB_ID=' + collector + '\nRESULT_DIRECTORY=' + str(run))
print('CHECK_COMMAND=bash ' + str(package / 'CHECK.sh'))
print('Receipt status is not scheduler state. No computation or validation is implied by submission.')
