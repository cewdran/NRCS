"""Frozen functional treatment differences in paired changes, Puma only.

The orchestration caller owns source/environment verification and final worker
receipts. This module produces scientific tables in the caller's isolated run.
"""
from hpc_guard import require_hpc

execution = require_hpc()

from collections import Counter
from pathlib import Path
import hashlib
import json
import math

import numpy as np
import pandas as pd

from common import require, readcsv, save, table, sha
from statistics_core import omnibus, stable_seed


EXPECTED_PANEL = {
    ('EC', '1.18.6.1'), ('EC', '1.14.99.39'), ('EC', '3.5.1.5'),
    ('EC', '1.7.5.1'), ('EC', '1.7.2.2'), ('EC', '1.7.2.1'),
    ('EC', '1.7.2.5'), ('EC', '1.7.2.4'), ('EC', '3.1.3.1'),
    ('EC', '3.1.3.2'), ('METACYC', 'DENITRIFICATION-PWY'),
    ('METACYC', 'GLYCOLYSIS'), ('METACYC', 'GLYOXYLATE-BYPASS'),
    ('METACYC', 'TCA'),
}
PROFILE_FAMILIES = ('EC', 'KO', 'METACYC')
SCALAR_ESTIMAND = 'BETWEEN_TREATMENT_DIFFERENCE_IN_MEAN_FIXED_DATE_WITHIN_PLOT_RELATIVE_ABUNDANCE_CHANGE'
PROFILE_ESTIMAND = 'BETWEEN_TREATMENT_DIFFERENCE_IN_MEAN_FIXED_DATE_WITHIN_PLOT_FULL_HELLINGER_CHANGE_VECTOR'

TEST_FIELDS = [
    'worker_id', 'hypothesis_id', 'analysis_class', 'scope', 'farm', 'system',
    'family', 'endpoint', 'feature_name', 'estimand', 'domain', 'variant',
    'early_date', 'late_date', 'n_independent_pairs', 'group_sizes_json',
    'pairing_sha256', 'ordered_pairs_json', 'status', 'testable', 'reason',
    'skip_reason', 'p_value', 'r2', 'r_squared', 'between_ss', 'within_ss',
    'total_ss', 'statistic', 'assignment_space', 'permutation_mode',
    'permutations_evaluated', 'permutation_tail_count', 'seed',
    'dimensions', 'design_rank_json', 'gates_json', 'effect_units',
    'mean_change', 'mean_change_norm', 'feature_in_original_table',
]
OBSERVATION_FIELDS = [
    'worker_id', 'hypothesis_id', 'analysis_class', 'scope', 'farm', 'system',
    'family', 'endpoint', 'feature_name', 'domain', 'variant',
    'biological_plot_id', 'treatment', 'early_date', 'late_date',
    'early_sample', 'late_sample', 'early_value', 'late_value', 'change',
    'feature_in_original_table', 'Hellinger_distance',
    'Bray_Curtis_on_unit_sum_profiles',
]
GROUP_FIELDS = [
    'worker_id', 'hypothesis_id', 'analysis_class', 'scope', 'farm', 'system',
    'family', 'endpoint', 'feature_name', 'domain', 'variant',
    'treatment', 'early_date', 'late_date', 'n_independent_pairs',
    'mean_early_relative_abundance', 'mean_late_relative_abundance',
    'mean_change', 'min_change', 'max_change', 'mean_change_norm',
    'mean_individual_Hellinger_distance',
    'mean_individual_Bray_Curtis_on_unit_sum_profiles',
    'feature_in_original_table', 'interpretation',
]
AUDIT_FIELDS = [
    'worker_id', 'analysis_class', 'scope', 'farm', 'system', 'family',
    'biological_plot_id', 'early_date', 'late_date', 'early_sample',
    'late_sample', 'early_treatment', 'late_treatment', 'included',
    'reason', 'group_sizes_json', 'all_declared_arms_minimum_pairs',
    'minimum_pairs_per_arm', 'pairing_sha256', 'seed',
]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _flag(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    require(text in {'true', 'false', '1', '0', 'yes', 'no'},
            'Unrecognized source boolean: ' + repr(value))
    return text in {'true', '1', 'yes'}


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        require(math.isfinite(float(value)), 'Nonfinite value in functional output')
        return float(value)
    return value


def _write(path, rows, required_fields):
    fields = list(required_fields)
    flat = []
    for row in rows:
        current = _clean(row)
        for key in current:
            if key not in fields:
                fields.append(key)
        flat.append({key: _json(value) if isinstance(value, (dict, list)) else value
                     for key, value in current.items()})
    table(path, flat, fieldnames=fields)


def _context(worker_id, analysis_class, scope, spec, family, endpoint, name):
    identity = [analysis_class, scope, spec['farm'], spec['system'], family,
                endpoint, SCALAR_ESTIMAND if analysis_class == 'scalar' else PROFILE_ESTIMAND,
                list(spec['paired_dates'])]
    return {
        'worker_id': worker_id,
        'hypothesis_id': hashlib.sha256(_json(identity).encode('utf-8')).hexdigest(),
        'analysis_class': analysis_class, 'scope': scope,
        'farm': spec['farm'], 'system': spec['system'], 'family': family,
        'endpoint': endpoint, 'feature_name': name,
        'estimand': SCALAR_ESTIMAND if analysis_class == 'scalar' else PROFILE_ESTIMAND,
        'domain': 'bacteria', 'variant': 'original',
        'early_date': spec['paired_dates'][0], 'late_date': spec['paired_dates'][1],
    }


def _index_sources(snapshot, panel):
    metadata_rows = readcsv(snapshot / 'prediction/validated/bacteria/sample_metadata.csv')
    metadata = {}
    plot_events = set()
    for row in metadata_rows:
        sid = row['analysis_sample']
        key = (row['farm'], row['analytic_system'], row['biological_plot_id'],
               row['collection_date'])
        require(sid and sid not in metadata, 'Duplicate or empty prediction sample ID')
        require(row['biological_plot_id'] and key not in plot_events,
                'Duplicate or empty physical plot/event identity')
        metadata[sid] = row
        plot_events.add(key)
        for field in ('primary_included', 'depth_sensitivity_included'):
            _flag(row[field])
    require(metadata, 'Empty prediction metadata')

    eligibility = {}
    for row in readcsv(snapshot / 'comparisons/results/sample_eligibility.csv'):
        if row['domain'] != 'bacteria' or row['variant'] != 'original':
            continue
        key = (row['family'], row['analysis_sample'])
        require(key not in eligibility, 'Duplicate original bacterial eligibility row')
        require(row['analysis_sample'] in metadata, 'Eligibility sample absent from metadata')
        original = metadata[row['analysis_sample']]
        for field in ('farm', 'analytic_system', 'biological_plot_id',
                      'collection_date', 'treatment'):
            require(row[field] == original[field], 'Eligibility metadata mismatch: ' + field)
        for field in ('primary_included', 'depth_sensitivity_included'):
            require(_flag(row[field]) == _flag(original[field]),
                    'Eligibility inclusion mismatch: ' + field)
        _flag(row['nonzero_profile'])
        eligibility[key] = row
    for family in PROFILE_FAMILIES:
        require({sample for fam, sample in eligibility if fam == family} == set(metadata),
                'Eligibility sample inventory differs for ' + family)

    values = {}
    panel_keys = {(row['family'], row['function']) for row in panel}
    for row in readcsv(snapshot / 'comparisons/results/feature_sample_values.csv'):
        if row['domain'] != 'bacteria' or row['variant'] != 'original':
            continue
        require((row['family'], row['function']) in panel_keys,
                'Unexpected feature in frozen bacterial sample values')
        sid = row['analysis_sample']
        require(sid in metadata, 'Feature-value sample absent from prediction metadata')
        original = metadata[sid]
        for field in ('farm', 'analytic_system', 'biological_plot_id',
                      'collection_date', 'treatment'):
            require(row[field] == original[field], 'Feature-value metadata mismatch: ' + field)
        require(row['scope'] in {'primary', 'depth_sensitivity'}, 'Unknown source scope')
        eligible = eligibility[(row['family'], sid)]
        require(_flag(eligible['primary_included']) and _flag(eligible['nonzero_profile']),
                'Ineligible sample present in frozen feature values')
        if row['scope'] == 'depth_sensitivity':
            require(_flag(eligible['depth_sensitivity_included']),
                    'Depth-excluded sample present in frozen sensitivity values')
        key = (row['scope'], row['family'], row['function'], sid)
        require(key not in values, 'Duplicate frozen functional sample-value row')
        value = float(row['relative_abundance'])
        require(math.isfinite(value) and 0.0 <= value <= 1.0,
                'Invalid frozen relative abundance')
        _flag(row['in_original_table'])
        values[key] = row
    return metadata, eligibility, values


def _paired_cohort(metadata, eligibility, spec, scope, family, cfg,
                   worker_id, analysis_class):
    dates = list(spec['paired_dates'])
    arms = list(spec['treatments'])
    require(len(dates) == 2 and dates[0] < dates[1], 'Invalid fixed paired dates')
    require(len(arms) >= 2 and len(set(arms)) == len(arms), 'Invalid declared arms')
    by_plot = {}
    for row in metadata.values():
        if (row['farm'] != spec['farm'] or row['analytic_system'] != spec['system']
                or row['collection_date'] not in dates):
            continue
        require(row['treatment'] in arms, 'Undeclared treatment in selected functional system')
        visits = by_plot.setdefault(row['biological_plot_id'], {})
        require(row['collection_date'] not in visits, 'Duplicate endpoint for physical plot')
        visits[row['collection_date']] = row
    pairs = []
    audit = []
    for plot, visits in sorted(by_plot.items()):
        early, late = visits.get(dates[0]), visits.get(dates[1])
        reasons = []
        if early is None:
            reasons.append('MISSING_FIXED_EARLY_ENDPOINT')
        if late is None:
            reasons.append('MISSING_FIXED_LATE_ENDPOINT')
        if early is not None and late is not None and early['treatment'] != late['treatment']:
            reasons.append('TREATMENT_CHANGED_WITHIN_PHYSICAL_PLOT')
        for point, row in (('EARLY', early), ('LATE', late)):
            if row is None:
                continue
            e = eligibility[(family, row['analysis_sample'])]
            if not _flag(e['primary_included']):
                reasons.append(point + '_NOT_PRIMARY_INCLUDED')
            if scope == 'depth_sensitivity' and not _flag(e['depth_sensitivity_included']):
                reasons.append(point + '_NOT_DEPTH_SENSITIVITY_INCLUDED')
            if not _flag(e['nonzero_profile']):
                reasons.append(point + '_ZERO_FULL_FUNCTIONAL_PROFILE')
        included = not reasons
        if included:
            pairs.append((early, late))
        audit.append({
            'worker_id': worker_id, 'analysis_class': analysis_class, 'scope': scope,
            'farm': spec['farm'], 'system': spec['system'], 'family': family,
            'biological_plot_id': plot, 'early_date': dates[0], 'late_date': dates[1],
            'early_sample': early['analysis_sample'] if early else '',
            'late_sample': late['analysis_sample'] if late else '',
            'early_treatment': early['treatment'] if early else '',
            'late_treatment': late['treatment'] if late else '',
            'included': included, 'reason': ';'.join(reasons),
        })
    ordered = [dict(biological_plot_id=a['biological_plot_id'],
                    treatment=a['treatment'], early_sample=a['analysis_sample'],
                    late_sample=b['analysis_sample']) for a, b in pairs]
    payload = {'farm': spec['farm'], 'system': spec['system'], 'scope': scope,
               'paired_dates': dates, 'declared_arms': arms, 'ordered_pairs': ordered}
    digest = hashlib.sha256(_json(payload).encode('utf-8')).hexdigest()
    seed = stable_seed(cfg.get('seed', 20261016), 'functional_fixed_date_treatment_change', payload)
    sizes = {arm: sum(a['treatment'] == arm for a, b in pairs) for arm in arms}
    minimum = int(cfg.get('min_per_arm', cfg.get('min_plots_per_arm', 2)))
    require(minimum >= 2, 'Cannot lower the minimum independent paired plots per arm')
    enough = all(size >= minimum for size in sizes.values())
    for row in audit:
        row.update(group_sizes_json=_json(sizes),
                   all_declared_arms_minimum_pairs=enough,
                   minimum_pairs_per_arm=minimum, pairing_sha256=digest, seed=seed)
    return {'pairs': pairs, 'ordered': ordered, 'labels': [a['treatment'] for a, b in pairs],
            'arms': arms, 'sizes': sizes, 'seed': seed, 'digest': digest,
            'minimum': minimum, 'enough': enough, 'audit': audit}


def _test_context(ctx, cohort):
    return dict(ctx, n_independent_pairs=len(cohort['pairs']),
                group_sizes_json=_json(cohort['sizes']), pairing_sha256=cohort['digest'],
                ordered_pairs_json=_json(cohort['ordered']), seed=cohort['seed'])


def _call_core(y, cohort, cfg):
    return omnibus(y, cohort['labels'], cohort['arms'], cohort['seed'],
                   exact_limit=int(cfg.get('exact_limit', 5000)),
                   permutations=int(cfg.get('permutations', 19999)),
                   min_per_arm=cohort['minimum'])


def _flatten_core(result):
    excluded = {'group_mean', 'mean_change_vector_norm'}
    answer = {}
    for key, value in result.items():
        if key in excluded:
            continue
        if isinstance(value, dict):
            answer[key + '_json'] = _json(value)
        elif isinstance(value, list):
            answer[key + '_json'] = _json(value)
        else:
            answer[key] = value
    answer['reason'] = result.get('skip_reason') or ''
    return answer


def _absent_result(cohort):
    return {'status': 'skipped', 'testable': False,
            'skip_reason': 'PLANNED_FEATURE_ID_ABSENT_FROM_ORIGINAL_FULL_PROFILE',
            'reason': 'PLANNED_FEATURE_ID_ABSENT_FROM_ORIGINAL_FULL_PROFILE',
            'p_value': None, 'r2': None, 'r_squared': None,
            'seed': cohort['seed'], 'permutations_evaluated': 0}


def _scalar_rows(ctx, cohort, source_values, feature, cfg):
    family, fid = feature['family'], feature['function']
    scope = ctx['scope']
    endpoint_rows = []
    availability = set()
    for early, late in cohort['pairs']:
        first = source_values.get((scope, family, fid, early['analysis_sample']))
        last = source_values.get((scope, family, fid, late['analysis_sample']))
        require(first is not None and last is not None,
                'Frozen feature sample values are missing for a matched endpoint')
        availability.update((_flag(first['in_original_table']), _flag(last['in_original_table'])))
        endpoint_rows.append((early, late, first, last))
    if not endpoint_rows:
        # Determine profile presence from another saved sample in this system;
        # a zero-pair cohort must not turn a missing panel ID into observed zero.
        availability.update(_flag(row['in_original_table'])
                            for (s, fam, function, sample), row in source_values.items()
                            if s == scope and fam == family and function == fid
                            and row['farm'] == ctx['farm']
                            and row['analytic_system'] == ctx['system'])
    require(len(availability) <= 1, 'Inconsistent planned-feature presence across source samples')
    # Membership in the full matrix is invariant across systems/scopes; if this
    # cohort has no source rows, the frozen global export still identifies it.
    if not availability:
        availability.update(_flag(row['in_original_table'])
                            for (s, fam, function, sample), row in source_values.items()
                            if fam == family and function == fid)
    require(len(availability) == 1, 'Cannot establish planned-feature source presence')
    present = next(iter(availability))
    observations = []
    early_values, late_values, changes = [], [], []
    for early, late, first, last in endpoint_rows:
        a = float(first['relative_abundance']) if present else None
        b = float(last['relative_abundance']) if present else None
        delta = b - a if present else None
        if present:
            early_values.append(a)
            late_values.append(b)
            changes.append(delta)
        observations.append(dict(ctx,
            biological_plot_id=early['biological_plot_id'], treatment=early['treatment'],
            early_sample=early['analysis_sample'], late_sample=late['analysis_sample'],
            early_value=a, late_value=b, change=delta,
            feature_in_original_table=present))
    y = np.asarray(changes, dtype=float)
    result = _call_core(y, cohort, cfg) if present else _absent_result(cohort)
    test = _test_context(ctx, cohort)
    test.update(_flatten_core(result), feature_in_original_table=present,
                effect_units='relative proportion of the complete ' + family + ' profile',
                mean_change=float(y.mean()) if present and len(y) else None)
    groups = []
    labels = np.asarray(cohort['labels'], dtype=str)
    for arm in cohort['arms']:
        mask = labels == arm
        n = cohort['sizes'][arm]
        group = dict(ctx, treatment=arm, n_independent_pairs=n,
                     feature_in_original_table=present,
                     interpretation='DESCRIPTIVE_PAIRED_GROUP_RELATIVE_ABUNDANCE_CHANGE')
        if present and n:
            selected = y[mask]
            group.update(mean_early_relative_abundance=float(np.asarray(early_values)[mask].mean()),
                         mean_late_relative_abundance=float(np.asarray(late_values)[mask].mean()),
                         mean_change=float(selected.mean()), min_change=float(selected.min()),
                         max_change=float(selected.max()))
        groups.append(group)
    return test, observations, groups


def _profile_frame(path, metadata, eligibility, family):
    frame = pd.read_csv(path, sep='\t', index_col=0)
    frame.index = frame.index.astype(str)
    frame.columns = frame.columns.astype(str)
    require(not frame.index.has_duplicates and not frame.columns.has_duplicates,
            'Duplicate raw profile feature/sample identifiers')
    require(set(frame.columns) == set(metadata), 'Full profile and metadata sample sets differ')
    matrix = frame.to_numpy(dtype=float)
    require(np.isfinite(matrix).all() and (matrix >= 0).all(), 'Invalid full prediction abundances')
    totals = frame.sum(axis=0)
    require(np.isfinite(totals.to_numpy(dtype=float)).all(), 'Nonfinite full profile totals')
    for sample in metadata:
        require(bool(totals[sample] > 0) == _flag(eligibility[(family, sample)]['nonzero_profile']),
                'Frozen zero-profile eligibility differs from full prediction table')
    return frame, frame.divide(totals.replace(0, np.nan), axis=1)


def _reproduce(family, frame, relative, values, spec):
    checks = []
    for (scope, fam, fid, sample), row in values.items():
        if fam != family or row['farm'] != spec['farm'] or row['analytic_system'] != spec['system']:
            continue
        present = fid in frame.index
        require(_flag(row['in_original_table']) == present,
                'Frozen panel presence differs from full profile: ' + fid)
        expected = float(row['relative_abundance'])
        actual = float(relative.at[fid, sample]) if present else 0.0
        require(math.isfinite(actual), 'Nonfinite normalized matched profile value')
        passed = bool(np.isclose(actual, expected, rtol=1e-10, atol=1e-12))
        require(passed, 'Frozen full-profile relative abundance reproduction failed: ' + fid)
        checks.append({'scope': scope, 'family': family, 'function': fid,
                       'analysis_sample': sample, 'in_original_table': present,
                       'absolute_difference': abs(actual - expected), 'passed': True})
    require(checks, 'No frozen sample values were available for profile reproduction')
    return {'family': family, 'scope': 'all saved original bacterial rows for this system',
            'rtol': 1e-10, 'atol': 1e-12, 'checks_performed': len(checks),
            'maximum_absolute_difference': max(row['absolute_difference'] for row in checks),
            'passed': True, 'checks': checks}


def _profile_rows(ctx, cohort, relative, cfg):
    dimensions = len(relative.index)
    if cohort['pairs']:
        first = relative[[a['analysis_sample'] for a, b in cohort['pairs']]].T.to_numpy(dtype=float)
        last = relative[[b['analysis_sample'] for a, b in cohort['pairs']]].T.to_numpy(dtype=float)
        require(np.isfinite(first).all() and np.isfinite(last).all(),
                'Nonfinite normalized profile in paired cohort')
        delta = np.sqrt(last) - np.sqrt(first)
        distances = np.linalg.norm(delta, axis=1)
        bray = np.abs(last - first).sum(axis=1) / 2.0
    else:
        delta = np.empty((0, dimensions), dtype=float)
        distances = np.empty(0, dtype=float)
        bray = np.empty(0, dtype=float)
    result = _call_core(delta, cohort, cfg)
    test = _test_context(ctx, cohort)
    test.update(_flatten_core(result),
                effect_units='complete-profile Hellinger change coordinates',
                mean_change_norm=float(np.linalg.norm(delta.mean(axis=0))) if len(delta) else None)
    observations = []
    for index, (early, late) in enumerate(cohort['pairs']):
        observations.append(dict(ctx, biological_plot_id=early['biological_plot_id'],
            treatment=early['treatment'], early_sample=early['analysis_sample'],
            late_sample=late['analysis_sample'], Hellinger_distance=float(distances[index]),
            Bray_Curtis_on_unit_sum_profiles=float(bray[index])))
    groups = []
    labels = np.asarray(cohort['labels'], dtype=str)
    for arm in cohort['arms']:
        mask = labels == arm
        n = cohort['sizes'][arm]
        group = dict(ctx, treatment=arm, n_independent_pairs=n,
                     interpretation='DESCRIPTIVE_FULL_PROFILE_MEAN_VECTOR_SHIFT_AND_INDIVIDUAL_TURNOVER')
        if n:
            group.update(mean_change_norm=float(np.linalg.norm(delta[mask].mean(axis=0))),
                         mean_individual_Hellinger_distance=float(distances[mask].mean()),
                         mean_individual_Bray_Curtis_on_unit_sum_profiles=float(bray[mask].mean()))
        groups.append(group)
    return test, observations, groups


def run_worker(run: Path, worker_id: str) -> dict:
    """Write all planned functional worker rows; caller writes final receipts."""
    run = Path(run).resolve()
    cfg = json.loads((run / 'CONFIG.json').read_text())
    systems = {row['id']: row for row in cfg['functional_systems']}
    require(len(systems) == len(cfg['functional_systems']), 'Duplicate functional system ID')
    require(worker_id.endswith('_scalar') or worker_id.endswith('_profile'),
            'Unknown functional worker class')
    system_id, analysis_class = worker_id.rsplit('_', 1)
    require(system_id in systems, 'Unfrozen functional worker system')
    spec = systems[system_id]
    scopes = list(cfg['scopes'])
    require(scopes == ['primary', 'depth_sensitivity'], 'Unexpected functional analysis scopes')
    snapshot = run / 'source_snapshot'
    panel_path = snapshot / 'comparisons/results/FEATURE_PANEL.csv'
    panel = readcsv(panel_path)
    require(len(panel) == len(EXPECTED_PANEL)
            and {(r['family'], r['function']) for r in panel} == EXPECTED_PANEL,
            'Frozen complete fourteen-feature panel changed')
    metadata, eligibility, values = _index_sources(snapshot, panel)
    out = run / 'workers' / worker_id
    out.mkdir(parents=True, exist_ok=True)
    tests, observations, summaries, audits = [], [], [], []
    reproductions = []
    for family in (('EC', 'METACYC') if analysis_class == 'scalar' else PROFILE_FAMILIES):
        if analysis_class == 'profile':
            raw_path = snapshot / ('prediction/results/bacteria/' + family + '_predicted_abundance.tsv.gz')
            frame, relative = _profile_frame(raw_path, metadata, eligibility, family)
            reproduction = _reproduce(family, frame, relative, values, spec) if family != 'KO' else {
                'family': family, 'passed': True,
                'validation': 'complete raw profile validity, sample inventory and frozen zero-profile eligibility',
                'targeted_panel_reproduction': 'NOT_APPLICABLE_NO_KO_IN_FROZEN_TARGETED_PANEL',
            }
            reproduction.update(source_path=str(raw_path.relative_to(run)), source_sha256=sha(raw_path))
            reproductions.append(reproduction)
        for scope in scopes:
            cohort = _paired_cohort(metadata, eligibility, spec, scope, family,
                                    cfg, worker_id, analysis_class)
            audits.extend(cohort['audit'])
            endpoints = [p for p in panel if p['family'] == family] if analysis_class == 'scalar' else [
                {'family': family, 'function': family, 'feature_name': 'Complete ' + family + ' profile'}]
            for feature in endpoints:
                ctx = _context(worker_id, analysis_class, scope, spec, family,
                               feature['function'], feature['feature_name'])
                if analysis_class == 'scalar':
                    result = _scalar_rows(ctx, cohort, values, feature, cfg)
                else:
                    result = _profile_rows(ctx, cohort, relative, cfg)
                test, current_observations, current_summaries = result
                tests.append(test)
                observations.extend(current_observations)
                summaries.extend(current_summaries)
        if analysis_class == 'profile':
            del frame, relative
    require(len(tests) == len(scopes) * (len(panel) if analysis_class == 'scalar' else len(PROFILE_FAMILIES)),
            'Functional planned hypothesis count changed')
    require(len({row['hypothesis_id'] for row in tests}) == len(tests),
            'Duplicate planned functional hypothesis ID')
    files = ['tests.csv', 'observations.csv', 'group_summaries.csv', 'cohort_audit.csv']
    for name, rows, fields in (
            ('tests.csv', tests, TEST_FIELDS), ('observations.csv', observations, OBSERVATION_FIELDS),
            ('group_summaries.csv', summaries, GROUP_FIELDS), ('cohort_audit.csv', audits, AUDIT_FIELDS)):
        _write(out / name, rows, fields)
    if analysis_class == 'profile':
        save(out / 'REPRODUCTION.json', _clean({
            'status': 'PASSED', 'worker_id': worker_id, 'farm': spec['farm'],
            'system': spec['system'], 'panel_sha256': sha(panel_path),
            'scientific_computation_location': 'verified Puma compute allocation',
            'families': reproductions, 'passed': True,
        }))
        files.append('REPRODUCTION.json')
    counts = Counter(row['status'] for row in tests)
    return {
        'worker_id': worker_id, 'analysis_class': analysis_class,
        'farm': spec['farm'], 'system': spec['system'], 'scopes': scopes,
        'planned_hypotheses': len(tests), 'tested_hypotheses': counts.get('tested', 0),
        'skipped_hypotheses': counts.get('skipped', 0), 'tables_written': files,
        'reproduction_status': 'PASSED' if analysis_class == 'profile' else 'FROZEN_SAVED_RELATIVE_VALUES',
        'all_planned_hypotheses_returned': True,
        'interpretation': 'EXPLORATORY_TREATMENT_DIFFERENCES_IN_FIXED_DATE_CHANGES',
        'predictive_validation_established': False, 'ten_year_forecast_generated': False,
    }
