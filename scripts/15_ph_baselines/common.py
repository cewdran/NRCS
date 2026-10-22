"""Standard-library byte/I/O helpers; scientific entry points guard first."""
from pathlib import Path
import csv
import hashlib
import json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def table(path, rows, fields=None):
    path = Path(path)
    rows = list(rows)
    columns = list(fields or ())
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    require(columns, 'An empty table requires an explicit schema: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def readcsv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def truth(value):
    return value is True or str(value).strip().lower() in ('true', '1', 'yes')


def wbrows(table_value):
    return [dict(zip(table_value['columns'], row)) for row in table_value['rows']]


def safe_file(base, name):
    """Resolve an existing regular file without allowing escape from its root."""
    relative = Path(name)
    require(not relative.is_absolute() and '..' not in relative.parts,
            'Unsafe relative evidence path: ' + str(name))
    base = Path(base).resolve(strict=True)
    target = (base / relative).resolve(strict=True)
    require(target.is_file() and target.is_relative_to(base),
            'Evidence file escapes its verified root: ' + str(name))
    return target


def verify_acceptance_notes(run, cfg):
    """Close accepted review-note references using frozen package path maps."""
    rows = []
    for decision_path, mapping_key, nested in (
        (cfg['accepted_review_path'], 'inventory_acceptance_note_links', False),
        ('review/ACCEPTED_RETURNED_REVIEW_24128032.json', 'validation_acceptance_note_links', True)):
        decision = json.loads(safe_file(run, decision_path).read_text())
        declared = decision['review_records']
        if nested:
            flattened = {}
            for record in declared.values():
                for kind in ('json', 'text'):
                    name, digest = record[kind + '_path'], record[kind + '_sha256']
                    require(name not in flattened, 'Duplicate accepted companion note: ' + name)
                    flattened[name] = digest
            declared = flattened
        links = cfg[mapping_key]
        require(set(links) == set(declared), 'Accepted companion-note map is incomplete: ' + mapping_key)
        for name, digest in sorted(declared.items()):
            link = links[name]
            relative = link['package_path']
            require(link['sha256'] == digest == cfg['review_locks'].get(relative)
                    == sha(safe_file(run, relative)), 'Accepted companion-note bytes differ: ' + name)
            rows.append({'source': mapping_key, 'relative_path': relative,
                         'expected_sha256': digest, 'original_sha256': digest,
                         'snapshot_sha256': digest, 'status': 'UNCHANGED'})
    return rows


def verify_staged_sources(run, cfg):
    """Byte-only original/snapshot/review preservation; callers guard first.

    The source manifest contains cfg.sources only. The accepted validation
    output set is an additional original-byte proof, never a source of a new
    scientific calculation or a demand to rewrite historical review receipts.
    """
    run = Path(run).resolve(strict=True)
    rows = verify_acceptance_notes(run, cfg)
    require(set(cfg['sources']) == set(cfg['source_runs']), 'Source aliases do not match source runs.')
    snapshot_root = run / 'source_snapshot'
    require(snapshot_root.is_dir() and not snapshot_root.is_symlink(), 'Missing or linked source snapshot root.')
    expected_files = {str(Path(alias) / relative) for alias, files in cfg['sources'].items() for relative in files}
    actual_files = set()
    for target in snapshot_root.rglob('*'):
        require(not target.is_symlink(), 'Source snapshots must not contain symlink entries: ' + str(target))
        require(target.is_dir() or target.is_file(), 'Nonregular source snapshot entry: ' + str(target))
        if target.is_file():
            actual_files.add(str(target.relative_to(snapshot_root)))
    require(actual_files == expected_files, 'Exact staged source file set differs from the frozen source locks.')
    for alias in sorted(cfg['sources']):
        root = Path(cfg['source_runs'][alias]['path'])
        for relative, expected in sorted(cfg['sources'][alias].items()):
            original = safe_file(root, relative)
            snapshot = safe_file(run / 'source_snapshot' / alias, relative)
            original_hash, snapshot_hash = sha(original), sha(snapshot)
            require(expected == original_hash == snapshot_hash,
                    'Immutable source or snapshot changed: ' + alias + '/' + relative)
            rows.append({'source': alias, 'relative_path': relative,
                         'expected_sha256': expected, 'original_sha256': original_hash,
                         'snapshot_sha256': snapshot_hash, 'status': 'UNCHANGED'})
    for relative, expected in sorted(cfg['review_locks'].items()):
        actual = sha(safe_file(run, relative))
        require(actual == expected, 'Accepted review or source-report asset changed: ' + relative)
        rows.append({'source': 'package_review', 'relative_path': relative,
                     'expected_sha256': expected, 'original_sha256': actual,
                     'snapshot_sha256': actual, 'status': 'UNCHANGED'})
    validation = Path(cfg['source_runs']['validation']['path'])
    for relative, expected in sorted(cfg['validation_output_locks'].items()):
        actual = sha(safe_file(validation, relative))
        require(actual == expected, 'Accepted validation output changed: ' + relative)
        rows.append({'source': 'validation_original_output', 'relative_path': relative,
                     'expected_sha256': expected, 'original_sha256': actual,
                     'snapshot_sha256': '', 'status': 'UNCHANGED'})
    # The accepted inventory output manifests are themselves fixed source pins.
    # Check all original listed output bytes, including those not needed as
    # snapshots for this narrow pH stage. No scientific values are recalculated.
    measured_alias = 'measured_inventory'
    if measured_alias in cfg['sources']:
        original_root = Path(cfg['source_runs'][measured_alias]['path'])
        measured_cfg = json.loads(safe_file(snapshot_root / measured_alias, 'CONFIG.json').read_text())
        seen = set()
        for manifest_name, expected_names in (
            ('results/ENGINE_OUTPUT_MANIFEST.csv', measured_cfg['engine_output_files']),
            ('results/report_output_manifest.csv',
             ['results/NRCS_measured_outcomes_inventory_review.pdf', 'audit/report_layout_receipt.json'])):
            manifest = safe_file(snapshot_root / measured_alias, manifest_name)
            require(sha(manifest) == cfg['sources'][measured_alias][manifest_name],
                    'Accepted measured-output manifest changed: ' + manifest_name)
            entries = readcsv(manifest)
            names = [r['relative_path'] for r in entries]
            require(len(names) == len(set(names)) and set(names) == set(expected_names),
                    'Accepted measured output set differs: ' + manifest_name)
            for row in entries:
                name = row['relative_path']
                require(name not in seen, 'Duplicate measured output proof: ' + name)
                seen.add(name)
                target = safe_file(original_root, name)
                actual = sha(target)
                require(actual == row['sha256'] and target.stat().st_size == int(row['size_bytes']),
                        'Accepted measured output changed: ' + name)
                rows.append({'source': 'measured_inventory_original_output', 'relative_path': name,
                             'expected_sha256': row['sha256'], 'original_sha256': actual,
                             'snapshot_sha256': '', 'status': 'UNCHANGED'})
    return rows
