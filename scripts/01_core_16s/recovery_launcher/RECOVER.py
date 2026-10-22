#!/usr/bin/env python3
"""Restart NRCS bacteria with native DADA2 FASTQ streaming; preserve archaea."""
import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

DEFAULT_SOURCE = Path('/xdisk/dinusha1/dinusha1/soil_microb/16s_analyses/NRCS/NRCS_16S_results/run_20260914T201432Z_3761495')
ARCHAEA_JOB = '23875047_0'
BACTERIA_JOB = '23875047_1'
OLD_FARM_ARRAY = '23875048'
EXCLUDED = {'C108', 'C111', 'D108', 'D111'}
OLD_BLOCK = '''  dereplicated <- dada2::derepFastq(filtered_paths[retained], verbose = TRUE)
  # derepFastq returns one derep object for one filename, a list otherwise.
  if (inherits(dereplicated, "derep")) dereplicated <- list(dereplicated)
  names(dereplicated) <- ids[retained]
  denoised <- dada2::dada(dereplicated, err = err, BAND_SIZE = 32,
                         multithread = threads, pool = "pseudo")'''
NEW_BLOCK = '''  # Native filename input preserves pseudo-pooling while loading one sample
  # at a time. Supported by DADA2 >= 1.12; checked before any real processing.
  selected_fastqs <- filtered_paths[retained]
  names(selected_fastqs) <- ids[retained]
  message("Denoising ", domain, " with native FASTQ streaming and pseudo-pooling.")
  denoised <- dada2::dada(selected_fastqs, err = err, BAND_SIZE = 32,
                         multithread = threads, pool = "pseudo")'''
MEMORY_EDITS = [
    ('  if (!ncol(seqtab)) stop("No ASVs inferred after denoising.")',
     '''  if (!ncol(seqtab)) stop("No ASVs inferred after denoising.")
  denoised_totals <- rowSums(seqtab)
  saveRDS(list(err_in = denoised[[1]]$err_in, err_out = denoised[[1]]$err_out),
          file.path(out_dir, "denoising_error_models.rds"))
  writeLines(c(
    "Initial error learning: makeBinnedQualErrfun.",
    paste("DADA2 pseudo-pooling error update uses the installed dada() default:",
          paste(deparse(formals(dada2::dada)$errorEstimationFunction), collapse = " ")),
    "This behavior is retained from the corrected NIFA executable scripts."
  ), file.path(out_dir, "error_model_provenance.txt"))
  rm(denoised)
  invisible(gc())'''),
    ('  nochim <- nochim[ids[retained], , drop = FALSE]',
     '  nochim <- nochim[ids[retained], , drop = FALSE]\n  rm(seqtab)\n  invisible(gc())'),
    ('  counts[ids[retained], ] <- nochim[, unname(sequences), drop = FALSE]',
     '  counts[ids[retained], ] <- nochim[, unname(sequences), drop = FALSE]\n  rm(nochim)\n  invisible(gc())'),
    ('  denoised_counts[ids[retained]] <- rowSums(seqtab)',
     '  denoised_counts[ids[retained]] <- denoised_totals')
]


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path, delimiter):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def patch_script(text):
    require(text.count(OLD_BLOCK) == 1,
            'Unexpected DADA2 script version: exact memory-heavy block not found once. Nothing submitted.')
    patched = text.replace(OLD_BLOCK, NEW_BLOCK)
    require('dereplicated' not in patched,
            'Unexpected additional dereplicated-object references; nothing submitted.')
    for old, new in MEMORY_EDITS:
        require(patched.count(old) == 1, 'Unexpected script version for memory-release edit; nothing submitted.')
        patched = patched.replace(old, new)
    return patched


def inspect_source(source):
    require((source / 'inputs/PREFLIGHT_COMPLETE').is_file(), 'Original preflight is not complete.')
    require((source / 'dada2/archaea').is_dir(), 'Original archaea output directory is missing.')
    require(not (source / 'dada2/bacteria/COMPLETE').exists(),
            'Original bacteria already completed; do not start a duplicate recovery.')
    for domain in ('archaea', 'bacteria'):
        rows = read_rows(source / f'inputs/{domain}.tsv', '\t')
        ids = [row['sample_id'] for row in rows]
        require(len(ids) == 171 and len(set(ids)) == 171, f'Expected 171 unique {domain} inputs.')
        require(not (set(ids) & EXCLUDED), f'Excluded samples occur in {domain} inputs.')
        prefix = 'AC' if domain == 'archaea' else 'BD'
        require(all(re.fullmatch(f'[{prefix}][0-9]{{3}}', sid) for sid in ids), 'Unexpected sample identifiers.')
        require(all(Path(row['fastq']).is_file() for row in rows), f'Missing {domain} FASTQ input.')
        md = read_rows(source / f'metadata/NRCS_{domain}_metadata.csv', ',')
        mids = [row[f'{domain}_sample'] for row in md]
        require(len(mids) == 171 and len(set(mids)) == 171 and set(mids) == set(ids),
                f'{domain} metadata do not match the validated inputs.')
    config = {}
    for line in (source / 'package/config.sh').read_text().splitlines():
        match = re.match(r'^(SLURM_ACCOUNT|SLURM_PARTITION|PROCESS_CPUS|PROCESS_MEMORY|PROCESS_TIME|FARM_CPUS|FARM_MEMORY|FARM_TIME)=(.*)$', line)
        if match:
            parts = shlex.split(match[2], comments=True)
            require(len(parts) == 1, 'Unsupported configuration value: ' + match[1])
            config[match[1]] = parts[0]
    require(config.get('SLURM_ACCOUNT') == 'dinusha1', 'Expected the dinusha1 account.')
    for key in ('SLURM_PARTITION', 'PROCESS_CPUS', 'PROCESS_MEMORY', 'PROCESS_TIME', 'FARM_CPUS', 'FARM_MEMORY', 'FARM_TIME'):
        require(key in config and re.fullmatch(r'[A-Za-z0-9_:.+-]+', config[key]), 'Invalid or missing ' + key)
    return config


def run_command(args):
    result = subprocess.run(args, text=True, capture_output=True)
    require(result.returncode == 0, shlex.join(args) + '\n' + result.stderr.strip())
    return result.stdout.strip()


def archaea_dependency(source):
    state_output = run_command(['sacct', '-n', '-P', '-X', '-S', '2026-09-14',
                                '-j', ARCHAEA_JOB, '--format=JobID%32,State%32'])
    states = [line.split('|')[1].strip().split()[0].rstrip('+')
              for line in state_output.splitlines()
              if len(line.split('|')) >= 2 and line.split('|')[0].strip() == ARCHAEA_JOB]
    require(len(states) == 1, 'Cannot establish the original archaea job state. No new jobs submitted.')
    state = states[0]
    if state == 'COMPLETED':
        require((source / 'dada2/archaea/COMPLETE').is_file(), 'Archaea job completed but its COMPLETE marker is missing.')
        return None, state
    require(state in {'RUNNING', 'PENDING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED'},
            'Original archaea job is ' + state + '; review it before submitting this recovery.')
    return ARCHAEA_JOB, state


def require_bacteria_stopped():
    output = run_command(['sacct', '-n', '-P', '-X', '-S', '2026-09-14',
                          '-j', BACTERIA_JOB, '--format=JobID%32,State%32'])
    states = [line.split('|')[1].strip().split()[0].rstrip('+')
              for line in output.splitlines()
              if len(line.split('|')) >= 2 and line.split('|')[0].strip() == BACTERIA_JOB]
    require(len(states) == 1 and states[0] in {'OUT_OF_MEMORY', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL'},
            'Original bacteria is not confirmed stopped in a failed/cancelled state. No recovery submitted.')
    return states[0]


def source_receipt_path(source):
    return source.parent / ('bacteria_recovery_' + source.name + '.json')


def prepare(source, target, package_dir):
    patched = patch_script((source / 'package/scripts/02_dada2.R').read_text())
    target.mkdir()
    for folder in ('package', 'inputs', 'metadata'):
        shutil.copytree(source / folder, target / folder)
    (target / 'logs').mkdir()
    (target / 'dada2').mkdir()
    (target / 'dada2/archaea').symlink_to(source / 'dada2/archaea', target_is_directory=True)
    (target / 'package/scripts/02_dada2.R').write_text(patched)
    shutil.copy2(package_dir / 'run_recovery_stage.sh', target / 'package/run_recovery_stage.sh')
    shutil.copy2(package_dir / 'validate_recovery_runtime.R', target / 'package/validate_recovery_runtime.R')
    (target / 'SOURCE_RUN.txt').write_text(str(source) + '\n')
    checks = {}
    for folder in ('inputs', 'metadata'):
        for original in sorted((source / folder).rglob('*')):
            if original.is_file():
                relative = original.relative_to(source)
                digest = sha(original)
                require(digest == sha(target / relative), 'Copy differs from original: ' + str(relative))
                checks[str(relative)] = digest
    return checks


def save_receipt(path, receipt):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    os.replace(temporary, path)
    if receipt.get('recovery_run'):
        mirror = Path(receipt['recovery_run']) / 'RECOVERY_RECEIPT.json'
        mirror.write_text(json.dumps(receipt, indent=2) + '\n')
    if receipt.get('launcher_receipt'):
        Path(receipt['launcher_receipt']).write_text(json.dumps(receipt, indent=2) + '\n')


def submit(args):
    output = run_command(args)
    job = output.split(';')[0].strip()
    require(bool(re.fullmatch(r'[0-9]+', job)), 'Unexpected sbatch receipt; inspect scheduler: ' + output)
    return job


def retire_old_pending_farms(receipt):
    # Only the obsolete farm array may be cancelled, only after replacements
    # were submitted, and only when every visible task belongs to dinusha1
    # and is still pending. The archaea job is never cancelled or requeued.
    try:
        output = run_command(['squeue', '-h', '-r', '-j', OLD_FARM_ARRAY, '-o', '%i|%T|%u|%j'])
        rows = [line.strip().split('|') for line in output.splitlines() if line.strip()]
        safe = rows and all(len(r) == 4 and re.fullmatch(OLD_FARM_ARRAY + r'_[012]', r[0])
                            and r[1] == 'PENDING' and r[2] == 'dinusha1'
                            and r[3] == 'nrcs_farms' for r in rows)
        if safe:
            run_command(['scancel', '--state=PENDING', OLD_FARM_ARRAY])
            receipt['old_farm_array'] = 'obsolete pending array cancelled after replacements submitted'
        else:
            receipt['old_farm_array'] = 'left unchanged (visible tasks did not meet pending-task checks)'
    except RuntimeError as error:
        receipt['old_farm_array'] = 'left unchanged: ' + str(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    package_dir = Path(__file__).resolve().parent
    source = args.source_run.resolve(strict=True)
    require(source == DEFAULT_SOURCE, 'This recovery is tied to the September 14 run and job IDs.')
    receipt_path = source_receipt_path(source)
    with receipt_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not receipt_path.exists() and not (package_dir / 'RECOVERY_RECEIPT.json').exists(),
                'A recovery receipt already exists. Run CHECK_PROGRESS.py; no duplicate jobs submitted.')
        config = inspect_source(source)
        bacteria_state = require_bacteria_stopped()
        dependency, archaea_state = archaea_dependency(source)
        require((source / 'dada2/bacteria/error_model.rds').is_file(),
                'Original bacterial error model missing; needed for the small runtime comparison.')
        require((source / 'dada2/bacteria/filtering_counts.tsv').is_file(),
                'Original bacterial filtering counts missing; needed for the runtime comparison.')
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = source.parent / ('run_' + stamp + '_bacteria_memory_recovery_' + uuid.uuid4().hex[:8])
        checks = prepare(source, target, package_dir)
        receipt = {'status': 'PREPARED', 'created_utc': stamp, 'source_run': str(source),
                   'recovery_run': str(target), 'archaea_job': ARCHAEA_JOB,
                   'revision': 2, 'original_bacteria_state': bacteria_state,
                   'launcher_receipt': str(package_dir / 'RECOVERY_RECEIPT.json'),
                   'archaea_state_at_preparation': archaea_state, 'jobs': {},
                   'excluded_samples': sorted(EXCLUDED), 'unchanged_input_metadata_sha256': checks,
                   'original_dada2_script_sha256': sha(source / 'package/scripts/02_dada2.R'),
                   'recovery_dada2_script_sha256': sha(target / 'package/scripts/02_dada2.R'),
                   'method_change': 'Native FASTQ filename input, early release of intermediate objects, error-model provenance; scientific parameters preserved.',
                   'restart_scope': 'Bacteria from filtering onward; original archaea output linked without rerunning.',
                   'resources': config}
        save_receipt(receipt_path, receipt)
        common = ['sbatch', '--parsable', '--account=dinusha1', '--partition=' + config['SLURM_PARTITION'],
                  '--nodes=1', '--ntasks=1']
        stage = str(target / 'package/run_recovery_stage.sh')
        try:
            bacteria = submit(common + ['--job-name=nrcs_bac_fix', '--cpus-per-task=' + config['PROCESS_CPUS'],
                              '--mem=' + config['PROCESS_MEMORY'], '--time=' + config['PROCESS_TIME'],
                              '--output=' + str(target / 'logs/bacteria_recovery_%j.log'),
                              stage, 'bacteria', str(target)])
            print('Submitted bacteria recovery job:', bacteria, flush=True)
            receipt['jobs']['bacteria'] = bacteria
            receipt['status'] = 'BACTERIA_SUBMITTED'
            save_receipt(receipt_path, receipt)
            dependencies = [bacteria] + ([dependency] if dependency else [])
            farms = submit(common + ['--job-name=nrcs_farms', '--array=0-2',
                           '--dependency=afterok:' + ':'.join(dependencies), '--kill-on-invalid-dep=yes',
                           '--cpus-per-task=' + config['FARM_CPUS'], '--mem=' + config['FARM_MEMORY'],
                           '--time=' + config['FARM_TIME'], '--output=' + str(target / 'logs/farms_%A_%a.log'),
                           stage, 'farm', str(target)])
            print('Submitted replacement farm array:', farms, flush=True)
            receipt['jobs']['farms'] = farms
            receipt['status'] = 'SUBMITTED_NOT_YET_COMPLETE'
            save_receipt(receipt_path, receipt)
            retire_old_pending_farms(receipt)
            save_receipt(receipt_path, receipt)
        except (RuntimeError, OSError) as error:
            receipt['submission_error'] = str(error)
            try:
                save_receipt(receipt_path, receipt)
            except OSError as save_error:
                print('Could not save receipt; submitted job IDs:', json.dumps(receipt['jobs']), file=sys.stderr)
                print('Receipt save error:', save_error, file=sys.stderr)
            raise
        print('Recovery run:', target)
        print('Original archaea retained:', ARCHAEA_JOB)
        print('Bacteria recovery job:', bacteria)
        print('Replacement farm array:', farms)
        print('Monitor: python3', str(package_dir / 'CHECK_PROGRESS.py'))
        print('Original FASTQs, metadata, scripts, and output directories were preserved.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        print('RECOVERY STOPPED:', error, file=sys.stderr)
        sys.exit(1)
