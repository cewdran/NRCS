"""Fixed-visit soil comparisons with one independently labelled unit per plot.

The public entry point guards actual Puma execution before scientific imports.
Frozen, accepted plot/event values are read from corrected job 24067160. All
selected source rows remain visible; inference uses only the exact configured
complete panel. Neither repeated visits nor laboratory records become extra
independent plot replicates.
"""

from pathlib import Path
import hashlib
import json
import math

from common import json_file, readcsv, require, save, table, utc
from hpc_guard import require_hpc


TEST_FIELDS = [
    'hypothesis_id', 'worker_id', 'analysis_class', 'family', 'scope', 'farm', 'system',
    'endpoint', 'feature_name', 'estimand', 'status', 'reason', 'p_value', 'r2',
    'n_plots', 'n_visits', 'units', 'methodnote', 'assay_status', 'dates_json',
    'arms_json', 'group_n_json', 'group_mean_json', 'permutation_mode',
    'permutations_evaluated', 'permutation_space', 'seed',
    'source_record_ids_json', 'source_scope', 'statistic',
    'statistic_definition', 'interpretation'
]
AUDIT_FIELDS = [
    'worker_id', 'farm', 'system', 'endpoint', 'units', 'plot', 'treatment',
    'expected_panel', 'dates_present_json', 'missing_dates_json',
    'complete_case', 'status', 'reason', 'source_record_ids_json',
    'source_sample_keys_json'
]
SUMMARY_FIELDS = [
    'worker_id', 'farm', 'system', 'endpoint', 'units', 'population',
    'summary_kind', 'visit', 'contrast_date', 'treatment', 'n', 'mean',
    'median', 'minimum', 'maximum', 'created_utc'
]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _spec(config, worker_id):
    candidates = [s for s in config['soil_systems']
                  if s.get('worker_id', s['id'] + '_soil') == worker_id
                  or s['id'] == worker_id]
    require(len(candidates) == 1, 'Unknown or ambiguous soil worker: ' + worker_id)
    result = candidates[0]
    require(len(result['dates']) >= 2 and len(set(result['dates'])) == len(result['dates']),
            'Soil visit dates must be distinct and prespecified')
    require(result['dates'] == sorted(result['dates']), 'Soil dates must be ordered')
    require(len(set(result['treatments'])) == len(result['treatments']) >= 2,
            'Soil arms must be distinct')
    require(result['fixed_plots'] and set(result['fixed_plots'].values()) == set(result['treatments']),
            'Soil fixed panel must identify every declared arm')
    require(len({v['name'] for v in result['variables']}) == len(result['variables']),
            'Duplicate soil endpoint configuration')
    return result


def _row_reasons(row, endpoint):
    reasons = []
    if row['unit'] != endpoint['unit']:
        reasons.append('UNIT_MISMATCH')
    if str(row.get('censored', '')).strip().lower() not in ('false', '0'):
        reasons.append('CENSORED_OR_CENSORING_STATUS_UNRESOLVED')
    try:
        value = float(row['value'])
        if not math.isfinite(value):
            reasons.append('NONFINITE_VALUE')
    except (TypeError, ValueError):
        reasons.append('NONNUMERIC_VALUE')
    if row.get('sample_key', '') != row['plot'] + '|' + row['event']:
        reasons.append('PLOT_EVENT_SAMPLE_KEY_MISMATCH')
    if not row.get('record_id') or not row.get('source_id'):
        reasons.append('SOURCE_RECORD_PROVENANCE_MISSING')
    return reasons


def run_worker(run: Path, worker_id: str) -> dict:
    """Compute two planned exact whole-plot tests per frozen soil endpoint.

    Called by the parent worker dispatcher, which verifies source/package
    hashes, records final status and writes output manifests. This function
    additionally verifies actual Puma execution before importing NumPy or the
    statistical core. Existing source files are never modified.
    """
    execution = require_hpc()
    import numpy as np
    from statistics_core import omnibus, stable_seed

    run = Path(run)
    config = json_file(run / 'CONFIG.json')
    spec = _spec(config, worker_id)
    source = run / 'source_snapshot/corrected/payload/tables/analysis_observations.csv'
    source_rows = readcsv(source)
    required = {'farm', 'system', 'dataset', 'event', 'plot', 'treatment',
                'variable', 'value', 'unit', 'sample_key', 'source_id',
                'record_id', 'censored'}
    require(source_rows and required.issubset(source_rows[0]),
            'Corrected soil observation schema differs')
    selected = [dict(r) for r in source_rows
                if r['farm'] == spec['farm'] and r['system'] == spec['system']
                and r['dataset'] in spec['datasets']
                and r['variable'] in {v['name'] for v in spec['variables']}]
    out = run / 'workers' / worker_id
    out.mkdir(parents=True, exist_ok=True)
    require(not (out / 'tests.csv').exists(), 'Refusing to overwrite soil worker results')
    tests, observations, audit, summaries = [], [], [], []
    created = utc()

    def summarize(values, endpoint, arm, population, kind, visit='', contrast=''):
        if not values:
            return
        vector = np.asarray(values, dtype=float)
        require(np.isfinite(vector).all(), 'Nonfinite soil summary input')
        summaries.append(dict(
            worker_id=worker_id, farm=spec['farm'], system=spec['system'],
            endpoint=endpoint['name'], units=endpoint['unit'],
            population=population, summary_kind=kind, visit=visit,
            contrast_date=contrast, treatment=arm, n=len(vector),
            mean=float(np.mean(vector)), median=float(np.median(vector)),
            minimum=float(np.min(vector)), maximum=float(np.max(vector)),
            created_utc=created))

    for endpoint in spec['variables']:
        rows = [r for r in selected if r['variable'] == endpoint['name']]
        indexed = {}
        for row in rows:
            indexed.setdefault((row['plot'], row['event']), []).append(row)
        row_reasons = {}
        for row in rows:
            reasons = _row_reasons(row, endpoint)
            if len(indexed[(row['plot'], row['event'])]) != 1:
                reasons.append('DUPLICATE_PLOT_EVENT')
            if row['treatment'] not in spec['treatments']:
                reasons.append('UNDECLARED_TREATMENT')
            if row['plot'] in spec['fixed_plots'] and row['treatment'] != spec['fixed_plots'][row['plot']]:
                reasons.append('FROZEN_PLOT_TREATMENT_MISMATCH')
            row_reasons[id(row)] = reasons

        observed_plots = {r['plot'] for r in rows}
        panel = []
        for plot in sorted(observed_plots | set(spec['fixed_plots'])):
            own = [r for r in rows if r['plot'] == plot]
            treatments = sorted({r['treatment'] for r in own})
            is_expected = plot in spec['fixed_plots']
            reasons = []
            missing = [d for d in spec['dates'] if (plot, d) not in indexed]
            if not is_expected:
                reasons.append('OUTSIDE_FROZEN_COMPLETE_PANEL')
            if missing:
                reasons.append('MISSING_FIXED_VISIT')
            if len(treatments) != 1:
                reasons.append('TREATMENT_NOT_STABLE_OR_UNOBSERVED')
            for date in spec['dates']:
                pair_rows = indexed.get((plot, date), [])
                for row in pair_rows:
                    reasons.extend(row_reasons[id(row)])
            reasons = sorted(set(reasons))
            complete = not reasons
            if complete:
                panel.append(plot)
            audit.append(dict(
                worker_id=worker_id, farm=spec['farm'], system=spec['system'],
                endpoint=endpoint['name'], units=endpoint['unit'], plot=plot,
                treatment=spec['fixed_plots'].get(plot, ';'.join(treatments)),
                expected_panel=is_expected,
                dates_present_json=_json(sorted({r['event'] for r in own})),
                missing_dates_json=_json(missing), complete_case=complete,
                status='COMPLETE_FIXED_PANEL_PLOT' if complete else 'DESCRIPTIVE_SOURCE_PLOT',
                reason=';'.join(reasons),
                source_record_ids_json=_json([r['record_id'] for r in own]),
                source_sample_keys_json=_json([r['sample_key'] for r in own])))

        for row in rows:
            reasons = row_reasons[id(row)]
            if row['event'] not in spec['dates']:
                reasons = reasons + ['OUTSIDE_FIXED_VISITS']
            if row['plot'] not in spec['fixed_plots']:
                reasons = reasons + ['OUTSIDE_FROZEN_COMPLETE_PANEL']
            observations.append(dict(
                **row, worker_id=worker_id, endpoint=endpoint['name'],
                fixed_visit=row['event'] in spec['dates'],
                in_expected_panel=row['plot'] in spec['fixed_plots'],
                comparison_complete_case=row['plot'] in panel and row['event'] in spec['dates'],
                row_status='VALID_SOURCE_VALUE' if not row_reasons[id(row)] else 'RETAINED_INVALID_FOR_SUMMARY',
                row_reason=';'.join(sorted(set(reasons))),
                assay_status=endpoint['assay_status'], methodnote=endpoint['methodnote']))

        # Every valid observed visit is summarized independently; fixed-panel
        # summaries below explicitly identify their different population.
        for visit in sorted({r['event'] for r in rows}):
            for arm in spec['treatments']:
                values = [float(r['value']) for r in rows
                          if r['event'] == visit and r['treatment'] == arm
                          and not row_reasons[id(r)]]
                summarize(values, endpoint, arm, 'observed_plot_event', 'visit', visit)

        matrix = np.asarray([[float(indexed[(p, d)][0]['value']) for d in spec['dates']]
                             for p in panel], dtype=float)
        if not panel:
            matrix = np.empty((0, len(spec['dates'])), dtype=float)
        labels = [spec['fixed_plots'][p] for p in panel]
        complete_records = [indexed[(p, d)][0]['record_id'] for p in panel for d in spec['dates']]
        fixed_panel_ok = set(panel) == set(spec['fixed_plots'])
        panel_reason = '' if fixed_panel_ok else 'FROZEN_COMPLETE_PANEL_NOT_ESTABLISHED'
        if fixed_panel_ok:
            require(all(labels.count(a) >= 2 for a in spec['treatments']),
                    'Frozen soil arm has fewer than two independent plots')
        summary_population = ('fixed_complete_panel' if fixed_panel_ok
                              else 'complete_plots_within_frozen_panel')
        for arm in spec['treatments']:
            positions = [i for i, label in enumerate(labels) if label == arm]
            for j, visit in enumerate(spec['dates']):
                summarize([matrix[i, j] for i in positions], endpoint, arm,
                          summary_population, 'visit', visit)
            summarize([float(np.mean(matrix[i, :])) for i in positions], endpoint,
                      arm, summary_population, 'average_level')
            for j, later in enumerate(spec['dates'][1:], start=1):
                summarize([float(matrix[i, j] - matrix[i, 0]) for i in positions],
                          endpoint, arm, summary_population, 'temporal_change',
                          contrast=spec['dates'][0] + ' to ' + later)

        for estimand in ('average_level', 'temporal_change'):
            context = dict(worker_id=worker_id, analysis_class='soil', scope='soil_primary',
                           farm=spec['farm'], system=spec['system'], endpoint=endpoint['name'],
                           units=endpoint['unit'], estimand=estimand, dates=spec['dates'],
                           arms=spec['treatments'], fixed_plots=spec['fixed_plots'],
                           included_plots=panel)
            canonical = _json(context)
            hypothesis = hashlib.sha256(canonical.encode()).hexdigest()[:24]
            seed = stable_seed(canonical)
            base = dict(
                hypothesis_id=hypothesis, worker_id=worker_id, analysis_class='soil', family='soil',
                scope='soil_primary', farm=spec['farm'], system=spec['system'],
                endpoint=endpoint['name'], feature_name=endpoint['name'],
                estimand=estimand, status='NOT_TESTED', reason=panel_reason,
                p_value=None, r2=None, n_plots=len(panel), n_visits=len(spec['dates']),
                units=endpoint['unit'], methodnote=endpoint['methodnote'],
                assay_status=endpoint['assay_status'], dates_json=_json(spec['dates']),
                arms_json=_json(spec['treatments']), group_n_json=_json({a: labels.count(a) for a in spec['treatments']}),
                group_mean_json='{}', permutation_mode='', permutations_evaluated=None,
                permutation_space=None, seed=seed, source_record_ids_json=_json(complete_records),
                source_scope='corrected_24067160', statistic=None,
                statistic_definition='Between-treatment centroid SS divided by total centered SS over complete independent plots',
                interpretation=('Exploratory observational difference in average as-reported levels over the fixed visits'
                                if estimand == 'average_level' else
                                'Exploratory observational difference in within-plot changes from the first observed visit'))
            if fixed_panel_ok:
                response = (np.mean(matrix, axis=1)[:, None] if estimand == 'average_level'
                            else matrix[:, 1:] - matrix[:, [0]])
                if np.all(response == response[0, :]):
                    base['reason'] = 'CONSTANT_RESPONSE_ACROSS_COMPLETE_PLOTS'
                else:
                    result = omnibus(response, labels, spec['treatments'], seed,
                                     exact_limit=5000, permutations=19999, min_per_arm=2)
                    # The core's finite result is authoritative; all gates and
                    # permutation metadata are retained beside this CSV.
                    save(out / (hypothesis + '_core.json'), result)
                    p_value = result.get('p_value', result.get('p'))
                    if p_value is not None:
                        require(math.isfinite(float(p_value)) and 0 < float(p_value) <= 1,
                                'Invalid soil permutation p-value')
                        base.update(status='TESTED', reason='', p_value=float(p_value),
                                    r2=result.get('r2'),
                                    group_mean_json=_json(result.get('group_mean', {})),
                                    permutation_mode=result.get('permutation_mode', ''),
                                    permutations_evaluated=result.get('permutations_evaluated'),
                                    permutation_space=result.get('assignment_space'),
                                    statistic=result.get('r2'))
                    else:
                        base['reason'] = str(result.get('skip_reason') or 'STATISTICAL_CORE_DESIGN_GATE')
            tests.append(base)

    require(len(tests) == 2 * len(spec['variables']), 'Soil planned hypothesis slots missing')
    table(out / 'tests.csv', tests, TEST_FIELDS)
    observation_fields = list(source_rows[0]) + [
        'worker_id', 'endpoint', 'fixed_visit', 'in_expected_panel',
        'comparison_complete_case', 'row_status', 'row_reason', 'assay_status', 'methodnote']
    table(out / 'observations.csv', observations, observation_fields)
    table(out / 'group_summaries.csv', summaries, SUMMARY_FIELDS)
    table(out / 'cohort_audit.csv', audit, AUDIT_FIELDS)
    return dict(worker_id=worker_id, analysis_class='soil', scope='soil_primary',
                completed=True, execution=execution, planned_hypotheses=len(tests),
                tested_hypotheses=sum(t['status'] == 'TESTED' for t in tests),
                independent_unit='EXACT_CONFIRMED_PHYSICAL_PLOT',
                fixed_dates=spec['dates'], declared_arms=spec['treatments'],
                inference='WHOLE_PLOT_FIXED_COUNT_LABEL_PERMUTATION',
                visit_rows_permuted_independently=False,
                missing_values_imputed=False, source_files_changed=False,
                method_scope='EXPLORATORY_COMPARISON_OF_AS_REPORTED_SOIL_VALUES',
                requires_combined_Holm_family='ALL_TEN_PLANNED_SOIL_LEVEL_AND_CHANGE_HYPOTHESES',
                completed_utc=utc())
