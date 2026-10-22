#!/usr/bin/env python3
"""Stage an immutable audit run and submit guarded Puma execution plus collection."""
from pathlib import Path
import sys, os, json, hashlib, datetime, uuid, shutil, subprocess
from hpc_guard import puma_cluster

package = Path(sys.argv[1]).resolve()
try:
    puma_cluster()
except Exception as exc:
    raise SystemExit('Submit from Puma: ' + str(exc))
cfg = json.loads((package / 'CONFIG.json').read_text())
runtime = Path(cfg['runtime_package'])
python = runtime / 'runtime_env/bin/python'
if not (runtime / 'runtime_env/READY.json').is_file() or not python.is_file():
    raise SystemExit('The verified original functional Python runtime is missing: ' + str(runtime))
entries = []
for line in (package / 'SHA256SUMS.txt').read_text().splitlines():
    digest, name = line.split('  ', 1)
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise SystemExit('Unsafe manifest path')
    if hashlib.sha256((package / relative).read_bytes()).hexdigest() != digest:
        raise SystemExit('Checksum mismatch: ' + name)
    entries.append(relative)
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
    'package_revision': 'ten_year_feasibility_v2',
    'state': 'STAGED_NOT_SUBMITTED',
    'created_utc': stamp,
    'run_directory': str(run),
    'package_directory': str(package),
    'accounting_since': (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
    'environment_python': str(python),
    'account': 'dinusha1', 'partition': 'standard', 'jobs': {}, 'commands': [],
    'numerical_models_fitted': False,
    'ten_year_forecast_generated': False,
    'eligibility_review_pending': True,
}


def save():
    (run / 'submission.json').write_text(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    (package / 'LATEST_RUN.txt').write_text(str(run) + '\n')


def call(args):
    receipt['commands'].append(args)
    save()
    environment = dict(os.environ)
    for key in ['BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS', 'SBATCH_GET_USER_ENV']:
        environment.pop(key, None)
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, env=environment)
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
    resources = cfg['resources']
    main = jobid(call(common + [
        '--hold', '--job-name=nrcs_10y_audit', '--cpus-per-task=' + str(resources['cpus']),
        '--time=' + resources['time'], '--mem=' + resources['memory'],
        '--output=' + str(run / 'logs/analysis.log'), '--error=' + str(run / 'logs/analysis.log'),
        str(run / 'RUN.sh'), str(run), str(python)]))
    receipt['jobs']['analysis'] = main
    save()
    collector = jobid(call(common + [
        '--job-name=nrcs_10y_collect', '--cpus-per-task=1', '--mem=5G', '--time=00:30:00',
        '--dependency=afterany:' + main,
        '--output=' + str(run / 'logs/collector.log'), '--error=' + str(run / 'logs/collector.log'),
        str(run / 'COLLECT.sh'), str(run), main]))
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
print('Receipt status is not scheduler state. No completion is implied by submission.')
