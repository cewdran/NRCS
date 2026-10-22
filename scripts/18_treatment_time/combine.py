"""Join complete Puma workers, adjust planned test families, and render review outputs.

Scientific libraries are imported only after an actual Puma allocation is verified.
No worker is omitted and no incomplete run is presented as a completed analysis.
"""
from pathlib import Path
import argparse
import json
from common import (require, save, json_file, readcsv, table, sha, utc, workers,
                    verify_manifest, write_manifest, scheduler_rows, verify_runtime)
from hpc_guard import require_hpc


EXPECTED_WORKERS = {
    'oatman_scalar', 'petitesirah_scalar', 'annual_scalar', 'chester_scalar',
    'oatman_profile', 'petitesirah_profile', 'annual_profile', 'chester_profile',
    'oatman_soil', 'grenache_soil',
}
EXPECTED_FAMILIES = {
    ('scalar', 'primary'): 56,
    ('scalar', 'depth_sensitivity'): 56,
    ('profile', 'primary'): 12,
    ('profile', 'depth_sensitivity'): 12,
    ('soil', 'soil_primary'): 10,
}
MIN_TEST_FIELDS = {
    'hypothesis_id', 'analysis_class', 'scope', 'farm', 'system', 'endpoint',
    'feature_name', 'family', 'estimand', 'status', 'reason', 'p_value', 'r2',
}
TABLE_NAMES = ('tests.csv', 'observations.csv', 'group_summaries.csv', 'cohort_audit.csv')
SCALAR_ESTIMAND = 'BETWEEN_TREATMENT_DIFFERENCE_IN_MEAN_FIXED_DATE_WITHIN_PLOT_RELATIVE_ABUNDANCE_CHANGE'
PROFILE_ESTIMAND = 'BETWEEN_TREATMENT_DIFFERENCE_IN_MEAN_FIXED_DATE_WITHIN_PLOT_FULL_HELLINGER_CHANGE_VECTOR'
# Fixed from the earlier reviewed temporal findings, before this follow-up runs.
FEATURED_SCALARS = {
    'oatman_scalar': [('METACYC', 'GLYCOLYSIS'), ('EC', '1.7.2.1')],
    'petitesirah_scalar': [('METACYC', 'DENITRIFICATION-PWY'), ('METACYC', 'GLYCOLYSIS'), ('EC', '1.7.2.5')],
    'annual_scalar': [('METACYC', 'DENITRIFICATION-PWY'), ('METACYC', 'GLYCOLYSIS'), ('METACYC', 'GLYOXYLATE-BYPASS')],
    'chester_scalar': [('METACYC', 'GLYCOLYSIS')],
}


def is_tested(row):
    return row['status'].upper() == 'TESTED'


def union_fields(rows, preferred=()):
    result = list(preferred)
    for row in rows:
        for key in row:
            if key not in result:
                result.append(key)
    return result


def export_union(path, rows, preferred=()):
    fields = union_fields(rows, preferred)
    table(path, [{key: row.get(key, '') for key in fields} for row in rows], fields)


def optional_number(value, label, math):
    if value is None or str(value).strip() == '':
        return None
    result = float(value)
    require(math.isfinite(result), label + ': non-finite number')
    return result


def verify_current_audit(run, execution):
    """The combine job is still RUNNING; its final END is checked by collector."""
    directory = run / 'audit' / 'combine'
    start = json_file(directory / 'EXECUTION_START.json')
    environment = json_file(directory / 'ENVIRONMENT_STATUS.json')
    for label, value in [('START', start), ('ENV', environment)]:
        actual = value.get('execution', {})
        require(all(actual.get(key) == execution[key] for key in ('job_id', 'cluster', 'node')),
                'Combine ' + label + ' execution identity differs')
    require(start.get('event') == 'start', 'Combine START event is missing')
    require(environment.get('status') == 'COMPUTE_RUNTIME_VERIFIED'
            and environment.get('package_integrity_passed') is True
            and environment.get('ready_sha256') == '6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30',
            'Combine corrected runtime not verified')


def verify_prerequisites(run, config, execution):
    ids = workers(config)
    require(set(ids) == EXPECTED_WORKERS and len(ids) == 10, 'Unexpected ten-worker plan')
    receipt = json_file(run / 'submission.json')
    jobs = receipt['jobs']
    require(set(receipt['worker_ids']) == set(ids), 'Submission worker inventory differs')
    require(str(jobs['combine']) == execution['job_id'], 'Current allocation is not the submitted combine job')
    roles = ['stage'] + ['worker:' + worker_id for worker_id in ids]
    rows, raw = scheduler_rows([jobs[role] for role in roles])
    verified = {}
    for role in roles:
        job = str(jobs[role])
        require(job in rows, 'No final scheduler record for ' + role)
        verified[role] = verify_runtime(run, role, job, rows[job])
    self_checks = json_file(run / 'audit' / 'stage' / 'NUMERIC_SELF_CHECKS.json')
    require(self_checks.get('passed') is True and self_checks.get('numerical_checks_executed_on_puma') is True,
            'Stage numerical self-checks did not pass on Puma')
    stage_identity = verified['stage']
    for name in ['NUMERIC_SELF_CHECKS.json', 'STAGE_STATUS.json']:
        stage_status = json_file(run / 'audit' / 'stage' / name)
        identity = stage_status.get('execution', {})
        require(identity.get('job_id') == stage_identity['job_id']
                and identity.get('cluster') == 'puma' and identity.get('node') == stage_identity['node'],
                'Stage ' + name + ' execution identity differs')
    require(stage_status.get('completed') is True and stage_status.get('source_files_changed') is False
            and stage_status.get('package_integrity_passed') is True, 'Stage completion proof is missing')
    stage_sources = json_file(run / 'audit' / 'stage' / 'SOURCE_VERIFICATION.json')
    require(stage_sources.get('passed') is True and stage_sources.get('source_files_changed') is False
            and stage_sources.get('originals_checked') is True
            and stage_sources.get('source_manifest_sha256') == sha(run / 'SOURCE_MANIFEST.json'),
            'Stage source preservation proof differs')
    verify_current_audit(run, execution)
    statuses = {}
    for worker_id in ids:
        directory = run / 'workers' / worker_id
        status = json_file(directory / 'WORKER_STATUS.json')
        expected = verified['worker:' + worker_id]
        require(status.get('completed') is True, worker_id + ': worker incomplete')
        require(status.get('numerical_self_checks_passed') is True,
                worker_id + ': numerical self-check receipt missing')
        require(status.get('self_checks_sha256') == sha(run / 'audit' / 'stage' / 'NUMERIC_SELF_CHECKS.json'),
                worker_id + ': stage self-check identity differs')
        require(status.get('source_files_changed') is False,
                worker_id + ': source preservation not established')
        require(status.get('package_integrity_passed') is True,
                worker_id + ': package integrity not established')
        actual = status.get('execution', {})
        require(actual.get('job_id') == expected['job_id']
                and actual.get('cluster') == 'puma' and actual.get('node') == expected['node'],
                worker_id + ': scientific status execution differs')
        manifest_path = directory / 'OUTPUT_SHA256SUMS.txt'
        require(status.get('output_manifest_sha256') == sha(manifest_path),
                worker_id + ': output manifest identity differs')
        entries = {str(path) for path in verify_manifest(directory, 'OUTPUT_SHA256SUMS.txt')}
        require(set(TABLE_NAMES).issubset(entries), worker_id + ': incomplete manifested table inventory')
        statuses[worker_id] = status
    return ids, verified, statuses, raw


def verify_planned_identity(run, config, tests):
    """Compare logical endpoints and fixed visits, rather than just row counts."""
    panel = readcsv(run / 'source_snapshot' / 'comparisons' / 'results' / 'FEATURE_PANEL.csv')
    require(len(panel) == 14 and len({(r['family'], r['function']) for r in panel}) == 14,
            'Frozen functional panel is not fourteen unique endpoints')
    require(config['scopes'] == ['primary', 'depth_sensitivity'], 'Functional scopes differ from plan')
    require(set(config['families']) == {'EC', 'KO', 'METACYC'}, 'Full-profile families differ from plan')
    require(len(config['functional_systems']) == 4 and len(config['soil_systems']) == 2,
            'Frozen system inventory differs')
    expected = set()
    for spec in config['functional_systems']:
        for analysis_class in ('scalar', 'profile'):
            worker_id = spec['id'] + '_' + analysis_class
            endpoints = panel if analysis_class == 'scalar' else [
                {'family': family, 'function': family, 'feature_name': 'Complete ' + family + ' profile'}
                for family in config['families']]
            for scope in config['scopes']:
                for feature in endpoints:
                    expected.add((worker_id, analysis_class, scope, spec['farm'], spec['system'],
                                  feature['family'], feature['function'], feature['feature_name'],
                                  SCALAR_ESTIMAND if analysis_class == 'scalar' else PROFILE_ESTIMAND,
                                  spec['paired_dates'][0], spec['paired_dates'][1]))
    for spec in config['soil_systems']:
        for endpoint in spec['variables']:
            for estimand in ('average_level', 'temporal_change'):
                expected.add((spec['worker_id'], 'soil', 'soil_primary', spec['farm'], spec['system'],
                              'soil', endpoint['name'], endpoint['name'], estimand, endpoint['unit'],
                              tuple(spec['dates'])))
    actual = []
    for row in tests:
        prefix = tuple(row[key] for key in ['worker_id', 'analysis_class', 'scope', 'farm', 'system',
                                            'family', 'endpoint', 'feature_name', 'estimand'])
        if row['analysis_class'] == 'soil':
            actual.append(prefix + (row['units'], tuple(json.loads(row['dates_json']))))
        else:
            actual.append(prefix + (row['early_date'], row['late_date']))
    require(len(expected) == sum(EXPECTED_FAMILIES.values()), 'Frozen plan has duplicate logical endpoints')
    require(len(actual) == len(set(actual)), 'Duplicate logical endpoint has distinct hypothesis IDs')
    require(set(actual) == expected, 'Returned endpoint/system/estimand/visit inventory differs from frozen plan')


def join_tables(run, config, ids, math):
    outputs = {name: [] for name in TABLE_NAMES}
    expected_by_worker = {}
    for worker_id in ids:
        for name in TABLE_NAMES:
            values = readcsv(run / 'workers' / worker_id / name)
            for value in values:
                if value.get('worker_id'):
                    require(value['worker_id'] == worker_id, 'Foreign worker row in ' + worker_id + '/' + name)
                value['worker_id'] = worker_id
                outputs[name].append(value)
        if worker_id.endswith('_scalar'):
            expected_by_worker[worker_id] = {('scalar', 'primary'): 14, ('scalar', 'depth_sensitivity'): 14}
        elif worker_id.endswith('_profile'):
            expected_by_worker[worker_id] = {('profile', 'primary'): 3, ('profile', 'depth_sensitivity'): 3}
        else:
            expected_by_worker[worker_id] = {('soil', 'soil_primary'): 6 if worker_id == 'oatman_soil' else 4}
    tests = outputs['tests.csv']
    require(len(tests) == sum(EXPECTED_FAMILIES.values()), 'Planned hypothesis count differs')
    seen = set()
    counts = {}
    worker_counts = {}
    for row in tests:
        require(MIN_TEST_FIELDS.issubset(row), 'Test schema misses required fields')
        hypothesis = row['hypothesis_id']
        require(hypothesis and hypothesis not in seen, 'Empty or duplicate hypothesis_id: ' + hypothesis)
        seen.add(hypothesis)
        family = (row['analysis_class'], row['scope'])
        require(family in EXPECTED_FAMILIES, 'Unexpected multiplicity family: ' + str(family))
        counts[family] = counts.get(family, 0) + 1
        worker_family = (row['worker_id'], family)
        worker_counts[worker_family] = worker_counts.get(worker_family, 0) + 1
        value = optional_number(row['p_value'], hypothesis + ' p', math)
        r2 = optional_number(row['r2'], hypothesis + ' R2', math)
        require(value is None or 0 <= value <= 1, 'Raw p-value outside [0,1]')
        require(r2 is None or -1e-12 <= r2 <= 1 + 1e-12, 'R2 outside [0,1]')
        require(row['status'].upper() in ('TESTED', 'SKIPPED', 'NOT_TESTED'), 'Unknown test status')
        if is_tested(row):
            require(value is not None, 'TESTED hypothesis has no raw p-value')
        else:
            require(value is None and row['reason'].strip(), 'Untested hypothesis needs a reason and no p-value')
        row['_p_numeric'] = value
        row['_r2_numeric'] = r2
    require(counts == EXPECTED_FAMILIES, 'Multiplicity family sizes differ from the complete plan')
    for worker_id, families in expected_by_worker.items():
        actual = {family: count for (worker, family), count in worker_counts.items() if worker == worker_id}
        require(actual == families, worker_id + ': planned hypothesis inventory differs')
    verify_planned_identity(run, config, tests)
    return outputs


def adjust_tests(tests, holm_adjust, math, alpha):
    audit = []
    for family, planned in EXPECTED_FAMILIES.items():
        selected = [row for row in tests if (row['analysis_class'], row['scope']) == family]
        values = [1.0 if row['_p_numeric'] is None else row['_p_numeric'] for row in selected]
        adjusted = list(holm_adjust(values))
        require(len(adjusted) == planned, 'Holm result has wrong family size')
        family_id = '__'.join(family)
        tested = 0
        supported = 0
        for row, value in zip(selected, adjusted):
            value = float(value)
            require(math.isfinite(value) and 0 <= value <= 1, 'Invalid Holm-adjusted p-value')
            present = row['_p_numeric'] is not None
            support = present and value <= alpha
            row.update(multiplicity_family=family_id, planned_family_size=planned,
                       holm_p_value=value if present else '',
                       adjusted_support=bool(support), alpha=alpha,
                       multiplicity_method='Holm; untested planned hypotheses contribute p=1')
            tested += int(present)
            supported += int(support)
        audit.append(dict(multiplicity_family=family_id, analysis_class=family[0], scope=family[1],
                          planned_hypotheses=planned, tested_hypotheses=tested,
                          skipped_hypotheses=planned-tested, adjusted_supported_hypotheses=supported,
                          alpha=alpha, adjustment='Holm',
                          missing_handling='Untested planned hypotheses count as p=1; display remains missing',
                          family_interpretation='Global across the planned systems/endpoints for this analysis class and scope'))
    return audit


def number_label(value, math):
    if value is None or value == '':
        return '—'
    number = float(value)
    if not math.isfinite(number):
        return '—'
    return ('{:.3g}'.format(number) if number else '0')


def row_label(row):
    feature = row.get('feature_name') or row.get('endpoint') or row.get('family', '')
    if row['analysis_class'] == 'profile':
        return row.get('family') or feature
    return feature


def result_figures(results, tests, group_summaries, plt, math):
    """Plot saved test statistics; no additional inferential statistic is fitted."""
    directory = results / 'figures'
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    figure_rows = []
    descriptions = []
    import textwrap
    for analysis_class, scope, title in [
        ('scalar', 'primary', 'Predicted bacterial functions: treatment differences in paired change'),
        ('profile', 'primary', 'Whole bacterial functional profiles: treatment differences in mean change'),
        ('soil', 'soil_primary', 'Soil chemistry: treatment differences in level and change'),
    ]:
        selected = [row for row in tests if row['analysis_class'] == analysis_class
                    and row['scope'] == scope and is_tested(row)
                    and row['_r2_numeric'] is not None]
        if not selected:
            continue
        panels = list(dict.fromkeys((row['farm'], row['system']) for row in selected))
        # One system per figure keeps all endpoint labels readable in the PDF.
        for panel in panels:
            values = [row for row in selected if (row['farm'], row['system']) == panel]
            height = 6.4 if analysis_class in ('scalar', 'soil') else 4.4
            figure, axis = plt.subplots(figsize=(10, height))
            labels = [textwrap.fill(row_label(row), 43)
                      + (' · ' + row['estimand'].replace('_', ' ') if analysis_class == 'soil' else '')
                      for row in values]
            # Each point is the exact R2 already written by a worker.
            x = [row['_r2_numeric'] for row in values]
            y = list(range(len(values)))
            point_colors = ['#007F79' if row['adjusted_support'] else '#6B7280' for row in values]
            axis.scatter(x, y, color=point_colors, s=48, zorder=3)
            for index, row in enumerate(values):
                axis.annotate('Holm p=' + number_label(row['holm_p_value'], math),
                              (row['_r2_numeric'], index), xytext=(7, 0), textcoords='offset points',
                              va='center', fontsize=10)
                figure_rows.append({key: value for key, value in row.items() if not key.startswith('_')})
            axis.set_yticks(y, labels=labels, fontsize=10)
            axis.invert_yaxis()
            axis.set_xlim(-0.02, 1.36)
            axis.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
            axis.set_xlabel('R² for the specified treatment comparison', fontsize=11)
            axis.tick_params(axis='x', labelsize=10)
            axis.grid(axis='x', alpha=0.2)
            axis.spines[['top', 'right']].set_visible(False)
            figure_title = ' / '.join(panel) + ': ' + title
            figure.suptitle(textwrap.fill(figure_title, 95), x=0.02, ha='left', fontsize=13, weight='bold')
            figure.text(0.02, 0.012,
                        'Teal: Holm p ≤ 0.05 in the complete planned family. Gray: other tested comparisons.\n'
                        'R² is model-specific; predicted functions describe genomic potential.', fontsize=9)
            figure.tight_layout(rect=(0, 0.07, 1, 0.91))
            stem = values[0]['worker_id'] + '_primary_treatment_effects'
            for extension in ('png', 'svg'):
                output = directory / (stem + '.' + extension)
                figure.savefig(output, dpi=300, bbox_inches='tight', facecolor='white')
                paths.append(output.relative_to(results))
            plt.close(figure)
            descriptions.append(dict(stem=stem, title=figure_title, analysis_class=analysis_class, scope=scope,
                                     plotted_test_count=len(values), source='tests_with_multiplicity.csv',
                                     interpretation='Saved worker R2 values; no new effect estimator is fitted by the figure',
                                     caption=(
                                         'The plotted R² compares treatment groups’ mean full-profile Hellinger '
                                         'change vectors, including their directions. It is not a test of mean '
                                         'individual turnover distance. Holm p-values use all twelve planned '
                                         'primary whole-profile comparisons. Predicted functions describe genomic potential.'
                                         if analysis_class == 'profile' else
                                         'Each point is an existing worker R² for the stated comparison. '
                                         'Holm p-values use the complete planned correction family. '
                                         'R² is model-specific; it is not a common measurement of biological activity.')))
    if figure_rows:
        export_union(directory / 'figure_source_tests.csv', figure_rows)
        paths.append(Path('figures/figure_source_tests.csv'))
    # Show exact worker-saved paired means for the previously highlighted endpoints.
    # These are descriptive treatment trajectories, not new treatment-pair tests.
    source_groups = []
    display_names = {
        'oatman_scalar': 'Oatman Flats', 'petitesirah_scalar': 'Page Springs Petite Sirah',
        'annual_scalar': 'Whipstone Annual', 'chester_scalar': 'Whipstone Chester blackberry',
    }
    for worker_id, endpoints in FEATURED_SCALARS.items():
        selected_tests = [row for row in tests if row['worker_id'] == worker_id
                          and row['scope'] == 'primary' and (row['family'], row['endpoint']) in endpoints]
        usable = []
        for family, endpoint in endpoints:
            matches = [row for row in group_summaries if row['worker_id'] == worker_id
                       and row.get('scope') == 'primary' and row.get('family') == family
                       and row.get('endpoint') == endpoint
                       and row.get('mean_change') not in (None, '')]
            if matches:
                usable.append((family, endpoint, matches))
        if not usable:
            continue
        figure, axes = plt.subplots(1, len(usable), figsize=(max(7, 5.3 * len(usable)), 5.7), squeeze=False)
        for axis, (family, endpoint, matches) in zip(axes.flat, usable):
            test = next(row for row in selected_tests if row['family'] == family and row['endpoint'] == endpoint)
            x = list(range(len(matches)))
            y = [optional_number(row['mean_change'], 'Saved group mean change', math) for row in matches]
            axis.scatter(x, y, s=58, color='#276C78', zorder=3)
            axis.axhline(0, color='#7C858B', linewidth=.8)
            axis.grid(axis='y', alpha=.2)
            labels = [textwrap.fill(row['treatment'].replace('_', ' '), 12)
                      + '\n(n=' + str(row['n_independent_pairs']) + ')' for row in matches]
            axis.set_xticks(x, labels=labels, fontsize=11)
            axis.set_xlim(-.5, len(matches) - .5)
            axis.set_ylabel('Mean paired change in predicted relative proportion', fontsize=11)
            axis.tick_params(axis='y', labelsize=11)
            axis.ticklabel_format(axis='y', style='sci', scilimits=(-3, 3))
            subtitle = ('Overall Holm p=' + number_label(test['holm_p_value'], math)
                        if is_tested(test) else 'Descriptive group means; overall test not estimated')
            axis.set_title(textwrap.fill(test['feature_name'], 36) + '\n' + textwrap.fill(subtitle, 44), fontsize=12)
            axis.spines[['top', 'right']].set_visible(False)
            source_groups.extend(matches)
        figure.suptitle(display_names[worker_id] + ': bacterial function changes by treatment',
                        x=.02, ha='left', fontsize=14, weight='bold')
        figure.text(.02, .018,
                    'Dots are saved paired-group means. Positive = higher at the later visit; negative = lower. '
                    'Treatment-pair significance is not inferred from these descriptive dots.', fontsize=8)
        figure.tight_layout(rect=(0, .075, 1, .91), w_pad=2.5)
        stem = worker_id.replace('_scalar', '') + '_featured_paired_group_changes'
        for extension in ('png', 'svg'):
            output = directory / (stem + '.' + extension)
            figure.savefig(output, dpi=300, bbox_inches='tight', facecolor='white')
            paths.append(output.relative_to(results))
        plt.close(figure)
        descriptions.append(dict(stem=stem, title=display_names[worker_id] + ': bacterial function changes by treatment',
             analysis_class='scalar', scope='primary', source='all_group_summaries.csv',
             endpoint_count=len(usable),
             interpretation='Exact saved paired-group mean changes for a subset fixed from earlier temporal findings',
             caption='Dots show exact worker-saved paired mean changes, with the paired group sizes in the labels. '
                     'A positive value means a higher predicted relative proportion at the later visit. '
                     'The overall Holm-adjusted test compares all declared groups; the dots do not establish '
                     'statistically different treatment pairs. Predicted functions describe genomic potential.'))
    if source_groups:
        export_union(directory / 'figure_source_group_summaries.csv', source_groups)
        paths.append(Path('figures/figure_source_group_summaries.csv'))
    save(directory / 'FIGURE_STATUS.json', dict(created_utc=utc(), figure_count=len(descriptions),
         figures=descriptions, execution_scope='Puma compute allocation only',
         numerical_ten_year_forecast=False))
    paths.append(Path('figures/FIGURE_STATUS.json'))
    return paths, descriptions


def render_pdf(path, run, config, tests, audit, descriptions, math):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, PageBreak, Image, KeepTogether)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SmallCell', fontName='Helvetica', fontSize=7.3,
                              leading=9.2, alignment=TA_LEFT, spaceAfter=0))
    styles.add(ParagraphStyle(name='CellHeader', parent=styles['SmallCell'], fontName='Helvetica-Bold',
                              textColor=colors.white))
    styles['BodyText'].fontSize = 10
    styles['BodyText'].leading = 14
    story = []
    width, height = landscape(A4)
    available = width - 84

    def paragraph(text, style='BodyText'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])

    def heading(text):
        story.append(paragraph(text, 'Heading2'))

    def add_table(headers, body, weights):
        total = sum(weights)
        widths = [available * weight / total for weight in weights]
        data = [[paragraph(x, 'CellHeader') for x in headers]]
        data.extend([[paragraph(x, 'SmallCell') for x in row] for row in body])
        item = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        item.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#264B55')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4F4')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor('#264B55')),
        ]))
        story.append(item)
        story.append(Spacer(1, 10))

    story.append(paragraph('NRCS treatment and time follow-up', 'Title'))
    story.append(paragraph('Farm-specific comparisons of predicted bacterial functions and soil chemistry', 'Heading2'))
    story.append(paragraph('Generated on Puma: ' + utc()))
    story.append(paragraph('Run: ' + run.name))
    story.append(Spacer(1, 10))
    story.append(paragraph(
        'The bacterial functional analyses compare changes between two visits in the same physical plots. '
        'Each treatment comparison asks whether the average change differs among the declared treatment groups. '
        'The whole-profile test compares the treatment groups’ mean change vectors; it does not rank treatments '
        'using the average distance traveled by individual plots. Functions are inferred genomic potential, '
        'rather than measured enzyme activity or nutrient flux.'))
    story.append(paragraph(
        'The soil analyses distinguish average measured levels across the fixed visits from changes between visits. '
        'A treatment difference in average level can include differences already present at the first visit. '
        'A nonsignificant difference in temporal change does not establish equal treatment levels or persistence.'))
    story.append(paragraph(
        'These are exploratory follow-up comparisons. Holm adjustment is applied across every planned hypothesis '
        'within each analysis class and scope, including planned tests that were not estimable. '
        'The primary functional and read-depth sensitivity branches have separate correction families. '
        'The results describe associations among treatments and observed changes; they do not establish causation.'))
    heading('Planned comparison families')
    add_table(['Family', 'Planned', 'Tested', 'Not tested', 'Holm p ≤ 0.05'], [
        [row['multiplicity_family'], row['planned_hypotheses'], row['tested_hypotheses'],
         row['skipped_hypotheses'], row['adjusted_supported_hypotheses']] for row in audit], [4, 1, 1, 1.2, 1.6])

    story.append(PageBreak())
    heading('Adjusted-supported comparisons')
    supported = [row for row in tests if row['adjusted_support']]
    if supported:
        add_table(['Farm / system', 'Endpoint', 'Estimand / scope', 'Paired plots', 'R²', 'Raw p', 'Holm p'], [
            [' / '.join((row['farm'], row['system'])), row_label(row),
             row['estimand'].replace('_', ' ') + ' / ' + row['scope'],
             row.get('n_independent_pairs') or row.get('n_plots', ''),
             number_label(row['_r2_numeric'], math), number_label(row['_p_numeric'], math),
             number_label(row['holm_p_value'], math)] for row in supported], [2, 3.2, 3, 1.2, 0.8, 1, 1])
    else:
        story.append(paragraph('No planned comparison met Holm p ≤ 0.05 in its complete correction family.'))
    story.append(paragraph(
        'A supported multi-group test indicates that at least one group differs. It does not identify a particular '
        'pair of treatments as statistically different. Group summaries and individual plot values are descriptive '
        'companions to the specified overall test.'))

    for item in descriptions:
        story.append(PageBreak())
        heading(item['title'])
        image = Image(str(path.parent / 'figures' / (item['stem'] + '.png')))
        scale = min(available / image.imageWidth, (height - 150) / image.imageHeight)
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        story.append(image)
        story.append(paragraph(item.get('caption',
            'Each point is an existing worker R² for the stated comparison. The displayed Holm p-value uses '
            'the complete planned correction family. R² values belong to their stated models and are not '
            'a common measurement of biological activity across endpoints.')))

    for family in EXPECTED_FAMILIES:
        story.append(PageBreak())
        heading('Complete test inventory: ' + '__'.join(family))
        selected = [row for row in tests if (row['analysis_class'], row['scope']) == family]
        add_table(['Farm / system', 'Endpoint', 'Estimand', 'Plots', 'R²', 'Raw p', 'Holm p', 'Status / reason'], [
            [' / '.join((row['farm'], row['system'])), row_label(row), row['estimand'].replace('_', ' '),
             row.get('n_independent_pairs') or row.get('n_plots', ''),
             number_label(row['_r2_numeric'], math), number_label(row['_p_numeric'], math),
             number_label(row['holm_p_value'], math),
             row['status'] + (': ' + row['reason'] if row['reason'] else '')] for row in selected],
             [1.9, 2.7, 2.0, .65, .65, .75, .75, 3.2])

    story.append(PageBreak())
    heading('Reading the companion tables')
    for text in [
        'tests_with_multiplicity.csv contains every planned hypothesis, raw p-values, Holm-adjusted p-values, '
        'test statistics, sample counts, and explicit reasons for any untested hypothesis.',
        'all_group_summaries.csv contains worker-generated treatment summaries. Functional profile mean-vector '
        'change norms and mean individual turnover distances are different quantities and retain separate labels.',
        'all_observations.csv contains the saved plot-level observations; all_cohort_audit.csv records the '
        'fixed-pair or fixed-visit inclusion decisions and source identities.',
        'multiplicity_family_audit.csv records the complete planned denominators. Untested hypotheses contribute '
        'p=1 to adjustment but retain missing displayed p-values.',
        'These returned outputs require scientific and visual review before manuscript acceptance. '
        'This package produces treatment/time follow-up comparisons, rather than a numerical ten-year forecast.',
    ]:
        story.append(paragraph(text))
        story.append(Spacer(1, 8))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#526169'))
        canvas.drawString(42, 22, 'NRCS exploratory treatment/time follow-up | ' + run.name)
        canvas.drawRightString(width - 42, 22, str(document.page))
        canvas.restoreState()

    document = SimpleDocTemplate(str(path), pagesize=(width, height),
                                 leftMargin=42, rightMargin=42, topMargin=34, bottomMargin=38,
                                 title='NRCS treatment and time follow-up', author='NRCS analysis team')
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    require(path.is_file() and path.stat().st_size > 1000, 'PDF output was not created')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', required=True)
    args = parser.parse_args()
    execution = require_hpc()
    # Guard above deliberately precedes every scientific/report import.
    import math
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from statistics_core import holm_adjust
    from source_guard import verify_sources

    run = Path(args.run_root).resolve()
    config = json_file(run / 'CONFIG.json')
    verify_manifest(run)
    results = run / 'results'
    results.mkdir(parents=True, exist_ok=True)
    require(not (results / 'COMBINE_STATUS.json').exists(), 'A completed combine cannot be overwritten')
    alpha = float(config.get('alpha', 0.05))
    require(alpha == 0.05, 'Unplanned alpha threshold')
    ids, verified, statuses, scheduler_raw = verify_prerequisites(run, config, execution)
    source_before = verify_sources(run)
    require(source_before.get('passed') is True and source_before.get('source_files_changed') is False,
            'Combine source verification did not pass')
    save(run / 'audit' / 'combine' / 'SOURCE_VERIFICATION_BEFORE.json', source_before)
    outputs = join_tables(run, config, ids, math)
    tests = outputs['tests.csv']
    audit = adjust_tests(tests, holm_adjust, math, alpha)
    public_tests = [{key: value for key, value in row.items() if not key.startswith('_')} for row in tests]
    export_union(results / 'tests_with_multiplicity.csv', public_tests,
                 ['hypothesis_id', 'worker_id', 'analysis_class', 'scope', 'farm', 'system', 'endpoint'])
    table(results / 'multiplicity_family_audit.csv', audit)
    export_union(results / 'all_group_summaries.csv', outputs['group_summaries.csv'], ['worker_id'])
    export_union(results / 'all_cohort_audit.csv', outputs['cohort_audit.csv'], ['worker_id'])
    export_union(results / 'all_observations.csv', outputs['observations.csv'], ['worker_id'])
    summaries = []
    for worker_id in ids:
        subset = [row for row in tests if row['worker_id'] == worker_id]
        summaries.append(dict(worker_id=worker_id, farm=subset[0]['farm'], system=subset[0]['system'],
                              analysis_class=subset[0]['analysis_class'], planned_hypotheses=len(subset),
                              tested_hypotheses=sum(is_tested(row) for row in subset),
                              skipped_hypotheses=sum(not is_tested(row) for row in subset),
                              adjusted_supported_hypotheses=sum(row['adjusted_support'] for row in subset),
                              scientific_review_pending=True))
    table(results / 'analysis_summary.csv', summaries)
    (results / 'PREREQUISITE_SCHEDULER.txt').write_text(scheduler_raw)
    figure_paths, figures = result_figures(results, tests, outputs['group_summaries.csv'], plt, math)
    render_pdf(results / 'NRCS_treatment_time_review.pdf', run, config, tests, audit, figures, math)
    # Recheck source bytes after all numerical work and rendering.
    source_after = verify_sources(run)
    require(source_after.get('passed') is True and source_after.get('source_files_changed') is False,
            'Combine final source verification did not pass')
    save(run / 'audit' / 'combine' / 'SOURCE_VERIFICATION_AFTER.json', source_after)
    report_status = dict(created=True, completed=True, created_utc=utc(), execution=execution,
                         report='NRCS_treatment_time_review.pdf', figure_count=len(figures),
                         planned_hypotheses=len(tests), scientific_review_pending=True,
                         visual_review_pending=True, numerical_ten_year_forecast=False,
                         ten_year_forecast_generated=False, source_files_changed=False)
    save(results / 'REPORT_STATUS.json', report_status)
    paths = [Path(name) for name in ['tests_with_multiplicity.csv', 'multiplicity_family_audit.csv',
             'all_group_summaries.csv', 'all_cohort_audit.csv', 'all_observations.csv',
             'analysis_summary.csv', 'PREREQUISITE_SCHEDULER.txt', 'NRCS_treatment_time_review.pdf',
             'REPORT_STATUS.json']] + figure_paths
    manifest_sha = write_manifest(results, paths)
    save(results / 'COMBINE_STATUS.json', dict(completed=True, created_utc=utc(), execution=execution,
         status='HPC_TREATMENT_TIME_COMPLETED_SCIENTIFIC_AND_VISUAL_REVIEW_PENDING',
         scientific_scripts_completed=True, all_workers_verified=True, worker_count=len(ids),
         worker_ids=ids, planned_hypotheses=len(tests), expected_family_sizes={
             '__'.join(key): value for key, value in EXPECTED_FAMILIES.items()},
         numerical_self_checks_passed=True, source_files_changed=False, package_integrity_passed=True,
         output_manifest_sha256=manifest_sha, report_created=True, figure_count=len(figures),
         scientific_review_pending=True, visual_review_pending=True,
         predictive_validation_established=False, numerical_ten_year_forecast=False,
         ten_year_forecast_generated=False))
    print('COMBINE_COMPLETED=true')
    print('REPORT_CREATED=' + str(results / 'NRCS_treatment_time_review.pdf'))
    print('PLANNED_HYPOTHESES=' + str(len(tests)))


if __name__ == '__main__':
    main()
