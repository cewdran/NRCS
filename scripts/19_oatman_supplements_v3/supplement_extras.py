"""Puma-only S5/S6 and Tables S4-S7 from immutable completed NRCS outputs.

Importing this module performs no numerical work and imports only stdlib modules.
No models, inferential tests, normalizations or multiplicity corrections are run.
"""
from pathlib import Path
import csv
import datetime
import gzip
import hashlib
import json
import math
import re
import traceback

BASE = Path(__file__).resolve().parent
FARM = 'Oatman Flats'
SYSTEM = 'OFR_2023_2024_Field'
EARLY, LATE = '2023-11-20', '2024-12-06'
COLORS = {'Control': '#667785', 'Cover_crop_only': '#16877d',
          'Cover_crop_and_sheep': '#ad6822'}
LABELS = {'Control': 'Control', 'Cover_crop_only': 'CC',
          'Cover_crop_and_sheep': 'CCS'}


def _require(value, message):
    if not value:
        raise RuntimeError(message)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def run(run, cfg):
    # Guard is deliberately the first executable operation; no numerical import
    # or data-dependent helper is reachable before the live allocation check.
    import hpc_guard
    execution = hpc_guard.require_hpc()
    run = Path(run)
    out = run / 'results' / 'extras'
    out.mkdir(parents=True, exist_ok=False)
    try:
        contract = json.loads((BASE / 'inputs_extra' / 'EXTRA_INPUTS.json').read_text())
        stage = json.loads((run / 'STAGE_STATUS.json').read_text())
        _require(stage.get('status') == 'VERIFIED', 'Common source stage is not verified')
        staged = {rec['role']: rec for rec in stage['sources']}
        # Whole packaged inputs were copied without local scientific processing.
        sources, receipts = {}, []
        for spec in contract['local_files']:
            path = BASE / 'inputs_extra' / spec['packaged_file']
            _require(path.is_file() and path.stat().st_size == spec['bytes']
                     and _sha(path) == spec['sha256'], 'Packaged frozen input changed: ' + str(path))
            key = spec.get('original_HPC_path') or spec.get('archive_member')
            _require(key not in sources, 'Ambiguous frozen source identity: ' + str(key))
            sources[key] = path
            receipts.append(dict(spec, used_path=str(path)))
        remote = {}
        for spec in contract['remote_CLR_files']:
            role = spec['stage_role']
            _require(role in staged, 'CLR source was not verified before parallel workers: ' + role)
            rec = staged[role]
            path = Path(rec['snapshot'])
            _require(rec['sha256'] == spec['sha256'] and _sha(path) == spec['sha256'],
                     'Frozen complete CLR matrix changed: ' + role)
            if spec.get('bytes') is not None:
                _require(path.stat().st_size == spec['bytes'], 'CLR byte count changed')
            remote[role] = path
            receipts.append(dict(spec, used_path=str(path), actual_bytes=path.stat().st_size))
        _json(out / 'SOURCE_PROVENANCE.json', {'execution': execution, 'sources': receipts,
              'whole_files_only': True, 'new_inferential_tests': False})

        # Scientific imports occur only after live Puma and immutable-source checks.
        import numpy as np
        import pandas as pd
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D

        def source(stage_name, suffix):
            found = [sources[s.get('original_HPC_path') or s.get('archive_member')]
                     for s in contract['local_files']
                     if s['stage'] == stage_name and
                     str(s.get('original_HPC_path') or s.get('archive_member')).endswith(suffix)]
            _require(len(found) == 1, 'Missing/ambiguous source: ' + stage_name + ' ' + suffix)
            return found[0]

        def frame(path):
            return pd.read_csv(path, dtype=str, keep_default_na=False)

        def farm_rows(data):
            _require('farm' in data, 'No explicit farm field for Oatman export')
            return data.loc[data['farm'].eq(FARM)].copy()

        def export(data, filename, description):
            path = out / filename
            data.to_csv(path, index=False)
            table_inventory.append({'file': filename, 'rows': len(data),
                                    'columns': list(data.columns), 'description': description})

        def close_enough(a, b):
            return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-14)

        def one(data, mask, description):
            rows = data.loc[mask]
            _require(len(rows) == 1, 'Expected exactly one saved row: ' + description)
            return rows.iloc[0]

        plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8,
                             'axes.labelsize': 8, 'axes.titlesize': 8.5,
                             'xtick.labelsize': 8, 'ytick.labelsize': 8,
                             'legend.fontsize': 8, 'pdf.fonttype': 42,
                             'ps.fonttype': 42, 'svg.fonttype': 'none'})

        def finish(fig, stem, legend):
            for ext in ('pdf', 'svg', 'png', 'tif', 'eps'):
                options = {'dpi': 600 if ext == 'tif' else 300}
                if ext == 'tif':
                    options['pil_kwargs'] = {'compression': 'tiff_lzw'}
                fig.savefig(out / (stem + '.' + ext), **options)
            plt.close(fig)
            (out / (stem + '_legend.txt')).write_text(legend + '\n')
            figures.append(stem)

        table_inventory, figures, qc = [], [], []
        memberships = {}
        # Separate keyed membership columns preserve distinct functional scopes
        # and selected-correlation cohorts, rather than reusing a generic flag.
        for domain in ('bacteria', 'archaea'):
            for _, sample in frame(BASE / 'inputs' / (domain + '_sample_metadata.csv.gz')).iterrows():
                memberships[(domain, sample.analysis_sample)] = {
                    'domain': domain, 'analysis_sample': sample.analysis_sample,
                    'biological_plot_id': sample.biological_plot_id,
                    'functional_primary_pair': False, 'functional_depth_sensitivity_pair': False,
                    'functional_pair_endpoint_keys': [], 'selected_correlation_test_ids': []}
        seed = json.loads((BASE / 'inputs_extra' / 'TableS1_seed_components.json').read_text())
        _require(len(seed['rows']) == 13 and all(len(row) == 2 for row in seed['rows']),
                 'Editorial seed table changed')
        export(pd.DataFrame(seed['rows'], columns=seed['columns']),
               'TableS1_cover_crop_commercial_components.csv', 'Editorial copy of reviewed component/rate strings; no arithmetic.')
        (out / 'TableS1_title_and_note.txt').write_text(seed['title'] + '\n\n' + seed['note'] + '\n')
        observations = frame(source('followup', '/results/all_observations.csv'))
        followup = frame(source('followup', '/results/tests_with_multiplicity.csv'))
        temporal = frame(source('functions', '/results/targeted_tests.csv'))
        robust = frame(source('functions', '/results/robustness.csv'))
        values = frame(source('functions', '/results/feature_sample_values.csv'))
        features = [('METACYC', 'GLYCOLYSIS', 'Glycolysis I'),
                    ('EC', '1.7.2.1', 'NO-forming nitrite reductase')]
        expected_holm = {('GLYCOLYSIS', 'primary'): .015625,
                         ('GLYCOLYSIS', 'depth_sensitivity'): .03125,
                         ('1.7.2.1', 'primary'): .0390625,
                         ('1.7.2.1', 'depth_sensitivity'): .078125}
        pair_data, pair_stats = [], []
        fig, axes = plt.subplots(2, 2, figsize=(6.5, 6.8))
        fig.subplots_adjust(left=.115, right=.975, bottom=.23, top=.92,
                            wspace=.38, hspace=.80)
        for row_i, (family, endpoint, title) in enumerate(features):
            for col_i, scope in enumerate(('primary', 'depth_sensitivity')):
                ax = axes[row_i, col_i]
                mask = (observations.worker_id.eq('oatman_scalar')
                        & observations.analysis_class.eq('scalar')
                        & observations.farm.eq(FARM) & observations.system.eq(SYSTEM)
                        & observations.domain.eq('bacteria') & observations.variant.eq('original')
                        & observations.scope.eq(scope) & observations.family.eq(family)
                        & observations.endpoint.eq(endpoint))
                obs = observations.loc[mask].sort_values('biological_plot_id').copy()
                expected_n = 9 if scope == 'primary' else 8
                _require(len(obs) == expected_n and not obs.biological_plot_id.duplicated().any(),
                         'Paired biological units changed: ' + endpoint + ' ' + scope)
                _require(obs.early_date.eq(EARLY).all() and obs.late_date.eq(LATE).all(),
                         'Wrong paired dates or field')
                sizes = obs.groupby('treatment').size().to_dict()
                _require(sizes == {'Control': 3, 'Cover_crop_only': 3,
                                  'Cover_crop_and_sheep': 3 if scope == 'primary' else 2},
                         'Paired treatment membership changed')
                test = one(followup, followup.worker_id.eq('oatman_scalar')
                           & followup.scope.eq(scope) & followup.family.eq(family)
                           & followup.endpoint.eq(endpoint), 'treatment-change result')
                ordered = json.loads(test.ordered_pairs_json)
                cols = ['biological_plot_id', 'treatment', 'early_sample', 'late_sample']
                _require(sorted(ordered, key=lambda x: x['biological_plot_id']) ==
                         obs[cols].to_dict('records'), 'Saved exact pair key does not match observations')
                _require(int(float(test.n_independent_pairs)) == expected_n
                         and int(float(test.planned_family_size)) == 56,
                         'Saved treatment-change n/family changed')
                _require(close_enough(test.holm_p_value, 1), 'Saved treatment-change adjustment changed')
                pooled = one(temporal, temporal.farm.eq(FARM) & temporal.analytic_system.eq(SYSTEM)
                             & temporal.domain.eq('bacteria') & temporal.variant.eq('original')
                             & temporal.scope.eq(scope) & temporal.family.eq(family)
                             & temporal['function'].eq(endpoint)
                             & temporal.test_type.eq('paired_shared_date_change'), 'pooled temporal result')
                adjusted = one(robust, robust.hypothesis_id.eq(pooled.hypothesis_id),
                               'saved targeted temporal correction')
                _require(int(float(pooled.n_independent_pairs)) == expected_n and
                         close_enough(adjusted.holm_p_conservative, expected_holm[(endpoint, scope)]),
                         'Saved pooled temporal n/correction changed')
                for _, record in obs.iterrows():
                    early = float(record.early_value)
                    late = float(record.late_value)
                    _require(all(math.isfinite(x) and x >= 0 for x in (early, late)),
                             'Invalid saved relative-profile value')
                    _require(close_enough(late - early, record.change), 'Saved pair delta changed')
                    for sample, date, value in [(record.early_sample, EARLY, early),
                                                (record.late_sample, LATE, late)]:
                        _require(('bacteria', sample) in memberships, 'Paired sample absent from accepted metadata')
                        member_row = memberships[('bacteria', sample)]
                        member_row['functional_' + scope + '_pair'] = True
                        member_row['functional_pair_endpoint_keys'].append(scope + '|' + endpoint + '|' + date)
                        saved = one(values, values.domain.eq('bacteria') & values.variant.eq('original')
                                    & values.scope.eq(scope) & values.farm.eq(FARM)
                                    & values.analytic_system.eq(SYSTEM) & values.family.eq(family)
                                    & values['function'].eq(endpoint) & values.analysis_sample.eq(sample),
                                    'original full-profile normalized endpoint')
                        _require(saved.biological_plot_id == record.biological_plot_id and
                                 saved.collection_date == date and saved.treatment == record.treatment and
                                 close_enough(saved.relative_abundance, value), 'Functional source-value mismatch')
                    ax.plot([0, 1], [early, late], color=COLORS[record.treatment],
                            marker='o', markersize=3.5, linewidth=.8, alpha=.85)
                panel = chr(65 + row_i * 2 + col_i)
                scope_label = 'primary' if scope == 'primary' else 'depth sensitivity'
                ax.set_title(panel + '  ' + title + '\n' + scope_label + '; n = ' + str(expected_n), loc='left')
                ax.set_xticks([0, 1], ['20 Nov 2023', '6 Dec 2024'])
                ax.set_xlim(-.12, 1.12)
                ax.set_ylabel('Predicted relative proportion')
                ax.ticklabel_format(axis='y', style='sci', scilimits=(-3, 3), useOffset=False)
                ax.yaxis.get_offset_text().set_fontsize(8)
                ax.spines[['top', 'right']].set_visible(False)
                ax.text(0, -.29, 'Pooled temporal Holm p = ' + format(float(adjusted.holm_p_conservative), '.5g')
                        + '\nTreatment-change Holm p = ' + format(float(test.holm_p_value), '.4g'),
                        transform=ax.transAxes, fontsize=8, va='top', clip_on=False)
                pair_data.append(obs)
                pair_stats.append({'panel': panel, 'scope': scope, 'endpoint': endpoint,
                                   'n': expected_n, 'pairing_sha256': test.pairing_sha256,
                                   'pooled_hypothesis_id': pooled.hypothesis_id,
                                   'pooled_raw_p_saved': pooled.p_value,
                                   'pooled_targeted_holm_saved': adjusted.holm_p_conservative,
                                   'treatment_change_hypothesis_id': test.hypothesis_id,
                                   'treatment_change_raw_p_saved': test.p_value,
                                   'treatment_change_complete_family_holm_saved': test.holm_p_value})
        handles = [Line2D([0], [0], color=COLORS[t], marker='o', label=LABELS[t]) for t in COLORS]
        fig.legend(handles=handles, ncol=3, loc='lower center', bbox_to_anchor=(.5, .04), frameon=False)
        fig.text(.115, .015, 'Lines join the same physical plot; primary and sensitivity are overlapping populations.', fontsize=8)
        stem = 'FigS5_Oatman_Flats_Ranch_individual_paired_function_trajectories'
        finish(fig, stem, 'Individual paired trajectories of predicted bacterial functions in the shared 2023–2024 field. '
               'Panels A and B show glycolysis I, and panels C and D show NO-forming nitrite reductase (EC 1.7.2.1). '
               'The left panels contain nine primary plot pairs (three control, three CC and three CCS); the right panels '
               'contain the saved eight-pair depth-sensitivity population (three control, three CC and two CCS). Each line '
               'connects values from 20 November 2023 and 6 December 2024 for the same physical plot. Values are the saved '
               'proportions normalized to each complete original MetaCyc or EC profile; no normalization restricted to the '
               'selected features was used. Displayed pooled temporal Holm probabilities retain their original targeted '
               'families. Treatment-change Holm probabilities retain the full 56-test scalar family and address differences '
               'in change among treatments. The primary and sensitivity panels overlap biologically and are not independent '
               'replications. Predictions do not measure genes, expression, enzyme activity or process rates. CC, cover crop; '
               'CCS, cover crop plus sheep.')
        export(pd.concat(pair_data, ignore_index=True), 'TableS6_S5_exact_paired_source_values.csv',
               'Exact selected saved early/late values and physical pair identities; no normalization added.')
        export(pd.DataFrame(pair_stats), 'TableS6_S5_saved_inference_key.csv',
               'Saved pooled and treatment-change hypotheses, probabilities and pairing hashes.')

        # S6 reads complete frozen CLR matrices, never recalculates a four-feature CLR.
        inventory = json.loads((BASE / 'inputs_extra' / contract['inventory_file']).read_text())
        display = frame(source('correlations', '/results/association_display_rows.csv'))
        scatter_specs = inventory['four_scatterplots']
        _require(len(scatter_specs) == 4 and {s['selected_saved_result']['test_id'] for s in scatter_specs} ==
                 {'bc3d183cdeda0bdc36f779f6', '3e3faf1f32311ca0f04b8975',
                  '91955582a2e3f5be050d5993', '2589580486ce284278bc7716'},
                 'Frozen four-example selection changed')
        pair_exports, selected_stats = [], []
        fig, axes = plt.subplots(2, 2, figsize=(6.5, 6.8))
        fig.subplots_adjust(left=.12, right=.97, bottom=.23, top=.92, wspace=.40, hspace=.80)
        for i, (spec, ax) in enumerate(zip(scatter_specs, axes.flat)):
            saved = spec['selected_saved_result']
            actual = one(display, display.test_id.eq(saved['test_id']), 'selected association')
            _require(all(str(actual[key]) == str(value) for key, value in saved.items()),
                     'Saved association row differs from frozen selection contract')
            _require(saved['scenario'] == 'PRIMARY__PRIMARY__PRIMARY' and
                     saved['analytic_system'] == SYSTEM and float(saved['q_by_global']) == 1,
                     'Unexpected association scope/field/correction')
            shard, klass = saved['shard_id'], saved['feature_class']
            prefix = '/workers/' + shard + '/'
            cohorts = frame(source('correlations', prefix + 'cohorts.csv'))
            scenario = frame(source('correlations', prefix + 'scenario_cohorts.csv'))
            cohort = one(cohorts, cohorts.cohort_id.eq(saved['cohort_id']), 'saved exact endpoint cohort')
            member = one(scenario, scenario.cohort_id.eq(saved['cohort_id'])
                         & scenario.feature_class.eq(klass) & scenario.scenario.eq(saved['scenario']),
                         'saved primary feature-class cohort')
            ids = json.loads(member.sample_ids_json)
            _require(len(ids) == int(saved['n']) and len(ids) == len(set(ids)), 'Saved correlation n/IDs changed')
            _require(ids == json.loads(cohort.primary_sample_ids_json), 'Primary cohort membership mismatch')
            endpoints = frame(source('correlations', prefix + 'matched_endpoints.csv.gz'))
            traits = json.loads(cohort.traits_json)
            selected = endpoints.copy()
            for key, value in traits.items():
                field = 'collection_date' if key == 'event' else key
                _require(field in selected.columns, 'Endpoint cohort trait field missing: ' + field)
                selected = selected.loc[selected[field].eq(str(value))]
            selected = selected.loc[selected.analysis_sample.isin(ids)].copy()
            _require(len(selected) == len(ids) and not selected.analysis_sample.duplicated().any(),
                     'Exact source endpoint rows are missing or ambiguous')
            selected = selected.set_index('analysis_sample').loc[ids]
            metadata = frame(source('correlations', prefix + 'sample_metadata.csv.gz'))
            _require(not metadata.analysis_sample.duplicated().any(), 'Duplicate source metadata')
            metadata = metadata.set_index('analysis_sample').loc[ids]
            _require(len(set(selected.biological_plot_id)) == len(ids) and
                     selected.biological_plot_id.tolist() == metadata.biological_plot_id.tolist() and
                     selected.treatment.tolist() == metadata.treatment.tolist() and
                     selected.collection_date.eq(saved['event']).all() and
                     metadata.collection_date.eq(saved['event']).all(), 'Source plot/date/treatment mismatch')
            _require(selected.analytic_system.eq(SYSTEM).all() and metadata.analytic_system.eq(SYSTEM).all(),
                     'Different fields entered a selected correlation')
            role = 'extra_CLR_' + shard + '_' + klass
            clr = pd.read_csv(remote[role], index_col=0, dtype=str, keep_default_na=False)
            _require(not clr.index.duplicated().any() and saved['feature_id'] in clr.columns
                     and set(ids).issubset(clr.index), 'Frozen full-reference CLR cache lacks selected feature/IDs')
            x = pd.to_numeric(selected.value).to_numpy(float)
            y = pd.to_numeric(clr.loc[ids, saved['feature_id']]).to_numpy(float)
            _require(np.isfinite(x).all() and np.isfinite(y).all(), 'Nonfinite plotted coordinate')
            # Reproduce the descriptive rho as a coordinate integrity check only.
            # There is no p-value/permutation/FDR recomputation or regression fit.
            xr = pd.Series(x).rank(method='average').to_numpy(float)
            yr = pd.Series(y).rank(method='average').to_numpy(float)
            observed_rho = float(np.corrcoef(xr, yr)[0, 1])
            _require(close_enough(observed_rho, saved['rho']), 'Saved coordinates do not reproduce recorded rho')
            _require(sorted(selected.biological_plot_id.tolist()) == sorted(json.loads(member.plot_ids_json)),
                     'Saved physical plot set differs from plotted set')
            for treatment, group in selected.groupby('treatment', sort=False):
                _require(treatment in COLORS, 'Unknown treatment label')
                positions = [ids.index(sid) for sid in group.index]
                ax.scatter(x[positions], y[positions], s=24, color=COLORS[treatment],
                           edgecolors='white', linewidths=.4, zorder=3)
            short = {'Paenibacillaceae': 'Paenibacillaceae',
                     'Candidatus Nitrosocosmicus': 'Ca. Nitrosocosmicus', 'ko:K21393': 'Predicted KO K21393'}[saved['feature_label']]
            soil_label = ('Soluble salts' if 'Soluble_Salt' in saved['variable'] else
                          'H3A total phosphorus' if 'Phosphorus' in saved['variable'] else 'Water-extractable organic C')
            ax.set_title(chr(65 + i) + '  ' + short + '\n' + saved['event'], loc='left')
            ax.set_xlabel(soil_label + ' (' + saved['unit'] + ')')
            ax.set_ylabel('Full-reference ' + klass + ' CLR')
            ax.spines[['top', 'right']].set_visible(False)
            ax.text(0, -.29, 'n = ' + saved['n'] + '; rho = ' + format(float(saved['rho']), '.3f')
                    + '\nGlobal BY q = 1.0', transform=ax.transAxes, fontsize=8, va='top', clip_on=False)
            joined = selected.reset_index().copy()
            joined.insert(0, 'selected_test_id', saved['test_id'])
            joined['feature_id'] = saved['feature_id']
            joined['feature_class'] = klass
            joined['feature_label'] = saved['feature_label']
            joined['saved_full_reference_CLR'] = clr.loc[ids, saved['feature_id']].to_numpy()
            joined['saved_rho'] = saved['rho']
            joined['saved_global_BY_q'] = saved['q_by_global']
            joined['cohort_id'] = saved['cohort_id']
            pair_exports.append(joined)
            selected_stats.append(actual.to_dict())
            for sid in ids:
                member_key = (saved['domain'], sid)
                _require(member_key in memberships, 'Correlation sample absent from accepted metadata')
                memberships[member_key]['selected_correlation_test_ids'].append(saved['test_id'])
            qc.append({'test_id': saved['test_id'], 'cohort_id': saved['cohort_id'],
                       'n': len(ids), 'sample_ids': ids, 'rho_coordinate_check': observed_rho,
                       'saved_rho': saved['rho'], 'saved_global_BY_q': saved['q_by_global'],
                       'p_values_recomputed': False, 'CLR_recomputed': False})
        fig.legend(handles=handles, ncol=3, loc='lower center', bbox_to_anchor=(.5, .04), frameon=False)
        fig.text(.12, .015, 'Selected marginal associations across treatments; every saved global BY q = 1.0.', fontsize=8)
        stem = 'FigS6_Oatman_Flats_Ranch_selected_CLR_soil_associations'
        finish(fig, stem, 'Four selected descriptive feature–soil associations in the shared 2023–2024 Oatman field. '
               'Panels A and B show Paenibacillaceae family CLR against soluble salts and H3A total phosphorus, respectively, '
               'on 20 November 2023 (nine matched physical plots in each panel). Panels C and D show Candidatus Nitrosocosmicus '
               'genus CLR and predicted bacterial KO K21393 CLR, respectively, against water-extractable organic carbon '
               'on 6 June 2024 (eight matched plots in each panel). '
               'CLR coordinates are read from the complete frozen primary reference matrices; no selected-feature CLR '
               'or new normalization is calculated. Colors identify treatment for context. Each point is one source-matched '
               'physical plot. Saved rho and primary global BY q = 1.0 are printed for every panel; all remain descriptive. '
               'These are marginal across-treatment associations, not treatment-adjusted or within-treatment tests. '
               'No regression line, new inferential test or selected-example multiplicity correction was fitted. '
               'Family/genus relative composition and predicted KO representation do not measure absolute abundance or '
               'activity and do not establish an organism–function link. CC, cover crop; CCS, cover crop plus sheep.')
        export(pd.concat(pair_exports, ignore_index=True), 'TableS7_four_selected_exact_source_pairs.csv',
               'Exact plotted source endpoints, frozen CLR coordinates, sample/plot IDs and original provenance columns.')
        export(pd.DataFrame(selected_stats), 'TableS7_four_selected_saved_test_rows.csv',
               'Four complete original test rows; global correction families/p/q unchanged.')
        _json(out / 'S5_S6_COORDINATE_QC.json', {'paired': pair_stats, 'associations': qc,
              'new_inferential_tests': False, 'multiplicity_recomputed': False})
        for member in memberships.values():
            for key in ('functional_pair_endpoint_keys', 'selected_correlation_test_ids'):
                member[key] = json.dumps(sorted(set(member[key])), separators=(',', ':'))
        export(pd.DataFrame(memberships.values()), 'TableS2_functional_and_selected_association_membership.csv',
               'Keyed by domain and analysis_sample; separate exact functional scopes and selected-test memberships. '
               'Join to the core complete sample inventory; this file does not replace its exclusions/source flags.')

        # Full Oatman table exports are made on Puma only, preserving source fields.
        _require('corrected_workbook' in staged, 'Full corrected workbook required for exact held-record farm membership')
        workbook = json.loads(Path(staged['corrected_workbook']['snapshot']).read_text())
        def sheet(name):
            _require(name in workbook['tables'], 'Missing corrected workbook sheet: ' + name)
            table = workbook['tables'][name]
            rows, headers = table['rows'], table['columns']
            _require(len(headers) == len(set(headers)), 'Duplicate workbook field')
            return pd.DataFrame(rows, columns=headers).fillna('')
        records = sheet('Sample records')
        _require({'farm', 'record_id'}.issubset(records), 'Corrected workbook record schema changed')
        records = farm_rows(records)
        _require(not records.record_id.duplicated().any(), 'Ambiguous workbook physical record ID')
        record_ids = set(records.record_id.astype(str))
        measurements = sheet('Measurements')
        _require('record_id' in measurements, 'Workbook measurement record ID missing')
        measurements = measurements.loc[measurements.record_id.astype(str).isin(record_ids)].copy()
        record_export = records.copy()
        export(record_export, 'TableS4_complete_Oatman_workbook_sample_records.csv', 'All source workbook records; no microbiome eligibility filter.')
        export(measurements, 'TableS4_complete_Oatman_workbook_measurements.csv', 'All source measurement fields for exact Oatman record IDs, including held/censored records.')
        for name in ('environment_tests', 'confirmed_soil_microbiome_tests', 'confirmed_soil_microbiome_matches'):
            data = farm_rows(frame(source('corrected', '/payload/tables/' + name + '.csv')))
            export(data, 'Table_S3_' + name + '.csv',
                   'Complete saved Oatman adjusted-environment/confirmed-association records with source cohort gates and original correction fields.')
        # This saved value table has no farm column. Determine ownership from
        # the exact corrected sample/date/key tuple, never a name prefix.
        meta = farm_rows(sheet('16S samples'))
        key_fields = ['microbial_domain', 'analysis_sample', 'collection_date', 'sample_key']
        _require(set(key_fields + ['treatment', 'treatment_label', 'treatment_code']).issubset(meta),
                 'Corrected16S metadata identity schema changed')
        sample_keys = {}
        for _, item in meta.iterrows():
            key = tuple(str(item[field]) for field in key_fields)
            _require(key not in sample_keys, 'Duplicate corrected environmental sample key')
            sample_keys[key] = {str(item[field]) for field in ('treatment', 'treatment_label', 'treatment_code')}
        data = frame(source('corrected', '/payload/tables/environment_matched_values.csv'))
        keep = []
        for _, item in data.iterrows():
            key = (item.domain, item.analysis_sample, item.event, item.sample_key)
            selected = key in sample_keys
            if selected:
                _require(item.treatment in sample_keys[key], 'Saved environmental treatment differs from source metadata')
            keep.append(selected)
        export(data.loc[keep], 'Table_S3_environment_matched_values.csv',
               'Exact domain/sample/date/sample_key ownership join to corrected Oatman16S metadata; source treatment identity verified.')
        for name in ('retained_measurements', 'analysis_observations', 'soil_crop_tests',
                     'soil_crop_descriptive', 'soil_crop_control_differences', 'soil_temporal_tests',
                     'soil_temporal_descriptive', 'soil_temporal_changes', 'soil_temporal_control_differences'):
            data = farm_rows(frame(source('corrected', '/payload/tables/' + name + '.csv')))
            _require('dataset' in data, 'Dataset field missing from corrected table: ' + name)
            grain = data.dataset.str.contains('grain', case=False, regex=False)
            export(data.loc[~grain], 'TableS4_' + name + '.csv', 'Complete saved Oatman non-grain rows; original units, eligibility and inference preserved.')
            export(data.loc[grain], 'TableS5_' + name + '.csv', 'Complete saved Oatman grain rows; raw qualifiers/limits/censor and saved test status preserved where present.')
        for name in ('record_disposition', 'held_records'):
            data = frame(source('corrected', '/payload/tables/' + name + '.csv'))
            selected = data.loc[data.record_id.isin(record_ids)].copy()
            export(selected, 'TableS4_' + name + '.csv', 'Exact record-ID join to all Oatman workbook records; no inferred farm labels.')
            grain_ids = set(records.loc[records.dataset.astype(str).str.contains('grain', case=False, regex=False), 'record_id'])
            export(selected.loc[selected.record_id.isin(grain_ids)], 'TableS5_' + name + '.csv', 'Grain held/disposition rows joined by exact recorded ID.')
        grain_ids = set(records.loc[records.dataset.astype(str).str.contains('grain', case=False, regex=False), 'record_id'])
        export(records.loc[records.record_id.isin(grain_ids)], 'TableS5_complete_grain_source_records.csv', 'All Oatman grain source records and original fields.')
        export(measurements.loc[measurements.record_id.isin(grain_ids)], 'TableS5_complete_grain_source_measurements.csv', 'All elemental/protein fields actually present; no protein inference from element data.')
        for name in ('targeted_tests', 'global_tests', 'robustness', 'feature_sample_values',
                     'feature_summaries', 'sample_eligibility', 'event_design_audit',
                     'dispersion_diagnostics', 'paired_profile_changes'):
            data = farm_rows(frame(source('functions', '/results/' + name + '.csv')))
            export(data, 'TableS6_' + name + '.csv', 'Complete saved Oatman functional rows across represented domains/scopes/versions; p/q families unchanged.')
        export(frame(source('functions', '/results/multiplicity_family_audit.csv')),
               'TableS6_GLOBAL_original_multiplicity_family_audit.csv', 'Original complete global/targeted correction-family context; not an Oatman-only recalculation.')
        export(farm_rows(frame(source('sensitivity', '/sample_sensitivity.csv'))),
               'TableS6_archaeal_original_filtered_sample_sensitivity.csv',
               'Saved Oatman original/filtered prediction diagnostics; no new filtering or prediction.')
        export(frame(source('sensitivity', '/function_sensitivity.csv')),
               'TableS6_GLOBAL_original_function_sensitivity_audit.csv',
               'Original all-sample functional sensitivity audit; explicitly global context, not an Oatman-only summary.')
        pathways = frame(source('sensitivity', '/pathway_changes.csv'))
        archaea_ids = set(meta.loc[meta.microbial_domain.eq('archaea'), 'analysis_sample'].astype(str))
        export(pathways.loc[pathways.analysis_sample.isin(archaea_ids)],
               'TableS6_archaeal_saved_pathway_sensitivity.csv',
               'Exact Oatman archaeal sample-ID subset of saved before/after pathway values.')
        for name in ('tests_with_multiplicity', 'all_observations', 'all_group_summaries', 'all_cohort_audit'):
            data = farm_rows(frame(source('followup', '/results/' + name + '.csv')))
            _require('worker_id' in data, 'Follow-up worker identity missing')
            soil = data.worker_id.eq('oatman_soil')
            export(data.loc[soil], 'TableS4_repeated_soil_' + name + '.csv', 'Complete saved repeated-soil rows; all27soil plot-events retained by original workflow.')
            export(data.loc[~soil], 'TableS6_followup_' + name + '.csv', 'Complete saved functional scalar/profile follow-ups; all planned untestable rows and family columns retained.')
        export(frame(source('followup', '/results/multiplicity_family_audit.csv')),
               'TableS6_GLOBAL_followup_multiplicity_family_audit.csv', 'Original full10soil/56scalar/12profile families by scope; not recalculated.')
        for name in ('association_display_rows', 'association_summary', 'primary_top_associations'):
            data = farm_rows(frame(source('correlations', '/results/' + name + '.csv')))
            export(data, 'TableS7_AVAILABLE_COMPACT_' + name + '.csv',
                   'Available compact-review summary only; not the complete association screen.')
        export(frame(source('correlations', '/results/multiplicity_family_audit.csv')),
               'TableS7_GLOBAL_original_multiplicity_family_audit.csv', 'Unmodified global BY/BH family audit; no selected-four correction.')
        completeness = {'association_screen': 'FOUR_SELECTED_EXAMPLES_PLUS_AVAILABLE_COMPACT_REVIEW_SUMMARIES',
                        'complete_screen_claimed': False, 'full_global_matrices_recovered': False,
                        'selected_test_count': 4, 'all_selected_global_BY_q': 1.0,
                        'legacy_beta_sources': 'Provenance only; not exported as current beta tests or new ordination geometry.',
                        'omitted_remote_full_matrices': [x for x in inventory['supplementary_tables']
                            if x.get('category') == 'complete_association_results'],
                        'scientific_tests_recomputed': False}
        _json(out / 'TABLE_COMPLETENESS.json', completeness)
        _json(out / 'SUPPLEMENTAL_TABLE_INVENTORY.json', table_inventory)
        (out / 'TABLES_README.txt').write_text(
            'TablesS4–S7 are machine-readable CSV sets with preserved source columns.\n'
            'Oatman-only exports retain saved global correction-family labels/counts and raw/adjusted probabilities.\n'
            'Global audit files explicitly describe the original correction context, not new Oatman-only corrections.\n'
            'TableS5 includes all grain source fields actually present, MDL/reporting-limit/qualifier and saved censor/status fields. '
            'No substitution, detected-only testing, area conversion or protein inference is performed.\n'
            'The2025field remains distinct and side-confounded. Unreported laboratory fields remain unreported.\n'
            'TableS7 contains four selected examples and available COMPACT summaries; it is NOT the complete association screen.\n'
            'Complete frozen CLR matrices were read unchanged, with exact saved strata and cohorts. Coordinate rho checks are QC only; '
            'no p-value, model, regression or multiple-testing adjustment was recomputed.\n')
        # Source integrity is rechecked after all scientific display/export work.
        for spec in contract['local_files']:
            _require(_sha(BASE / 'inputs_extra' / spec['packaged_file']) == spec['sha256'], 'Frozen packaged source changed during worker')
        for rec in stage['sources']:
            _require(_sha(rec['snapshot']) == rec['sha256'], 'Common staged source changed during extras worker')
            original = rec.get('original')
            if original:
                _require(_sha(original) == rec['sha256'], 'Pinned Puma source changed during extras worker')
        files = [p for p in out.iterdir() if p.is_file()]
        (out / 'SHA256SUMS.txt').write_text(''.join(_sha(p) + '  ' + p.name + '\n' for p in sorted(files)))
        _json(out / 'EXTRA_STATUS.json', {'status': 'EXTRA_OUTPUTS_CREATED_SCIENTIFIC_AND_VISUAL_REVIEW_PENDING',
              'execution': execution, 'figures': figures, 'table_files': len(table_inventory),
              'selected_association_n': [int(x['selected_saved_result']['n']) for x in scatter_specs],
              'source_checks_passed': True, 'pair_and_coordinate_checks_passed': True,
              'CLR_recomputed': False, 'new_inferential_tests': False,
              'multiplicity_recomputed': False, 'visual_review_complete': False,
              'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    except BaseException as exc:
        _json(out / 'EXTRA_STATUS.json', {'status': 'FAILED', 'error': str(exc),
              'type': type(exc).__name__, 'traceback': traceback.format_exc(),
              'execution': execution, 'new_inferential_tests': False})
        raise
