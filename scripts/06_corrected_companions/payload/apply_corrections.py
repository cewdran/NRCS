#!/usr/bin/env python3
"""Apply audited original-report values and researcher decisions on Puma only."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import csv, json, shutil, re
BASE = Path(__file__).resolve().parent
P = BASE/'inputs/workbook_data.json'
shutil.copy2(P, BASE/'inputs/workbook_data_before_revision.json')
d = json.loads(P.read_text()); tables = d['tables']
def rows(name):
    t = tables[name]
    return [dict(zip(t['columns'], r)) for r in t['rows']]
def put(name, rr):
    cols = list(dict.fromkeys(k for r in rr for k in r))
    tables[name] = {'columns': cols, 'rows': [[r.get(k) for k in cols] for r in rr]}
def read(name):
    with (BASE/'corrections'/name).open() as f: return list(csv.DictReader(f))
records = rows('Sample records'); byid = {r['record_id']:r for r in records}
measurements = rows('Measurements'); meta = rows('16S samples')
bykey = {r['sample_key']:r for r in meta if r['microbial_domain']=='bacteria'}
excluded = {}
def exclude(rid, reason):
    assert rid in byid, rid
    excluded[rid] = reason
for rid in ['R0075','R0076','R0077','R0206','R0302']:
    exclude(rid, 'Researcher intentional exclusion; see D3/D4/D8')
for i in list(range(290,302))+list(range(272,278)):
    exclude(f'R{i:04}', 'Preliminary OFR-JB or Baseline IAS excluded by researcher D5/D12')
for row in read('Intentional_exclusions_without_16S.csv'):
    exclude(row['record_id'], 'No matching 16S; researcher year-specific exclusion D18')
labs = read('laboratory_records.csv'); lm = read('laboratory_measurements.csv')
cross = read('legacy_record_crosswalk.csv')
source_to_legacy = {x['source_record_key']:x['legacy_record_id'] for x in cross}
for lab in labs:
    rid = source_to_legacy.get(lab['source_record_key'])
    if not rid: continue  # T5 original report pages preserved in corrections; physical T5 excluded below.
    r = byid[rid]
    if lab['suffix']=='S2': exclude(rid, 'Page Springs 2023 S2 excluded; retain S1 only D9')
    r.update(preferred_pdf_source_id=lab['source_id'], preferred_pdf_page=int(lab['pdf_page']),
             original_lab_id=lab['lab_id'], original_report_label=lab['report_label'],
             collection_date=lab['collection_date'] or None,
             confirmed_sample_key=lab['matched_16S_sample_key'] or None,
             link_status=lab['soil_16S_link_status'])
for r in records:
    if r['farm']=='Page Springs' and re.search(r'(?:^|[-_])T5(?:[-_]|$)', r['original_sample_label']):
        exclude(r['record_id'], 'T5 excluded by researcher D1; do not merge with T4')
replaced = set(source_to_legacy.values())
measurements = [m for m in measurements if m['record_id'] not in replaced]
for m in lm:
    rid = source_to_legacy.get(m['source_record_key'])
    if not rid: continue
    r = byid[rid]; value = m['value_as_reported']
    try: number = float(value)
    except ValueError: number = None
    measurements.append(dict(sample_name=r['sample_name'], variable=m['report_heading'], value=number,
        unit=m['unit'], text_value=value if number is None else None, record_id=rid, farm=r['farm'],
        system=r['system'], year=r['year'], period=r['collection_date'], dataset=r['dataset'],
        source_value=value, qualifier=None, reporting_limit=None, method=m['method'],
        source_id=r['preferred_pdf_source_id'], source_location=m['source_file']+' page '+m['pdf_page'],
        source_field=m['report_heading'], notes='Original PDF preferred; superseded workbook preserved',
        csv_value=None, value_basis='PDF preferred', preferred_pdf_source_id=r['preferred_pdf_source_id'],
        preferred_pdf_page=int(m['pdf_page'])))
for r in records:
    rid=r['record_id']
    if (206<=int(rid[1:])<=244) and rid not in excluded:
        key=r['candidate_sample_key']
        assert key in bykey, (rid,key)
        r.update(confirmed_sample_key=key, collection_date=bykey[key]['collection_date'],
                 link_status='Researcher-confirmed same soil sample D6/D7')
    if rid=='R0206': r['collection_date']=None; r['confirmed_sample_key']=None
    r['intentional_exclusion']=excluded.get(rid,'')
    r['field_depth_protocol']='Surface layer excluded; full remaining column to 15–20 cm terminal depth; researcher D14'
for x in read('Oatman_porewater_Control_PDF_reconciliation.csv'):
    rr=byid[x['legacy_record_id']];rr['collection_date']='2023-08-03'
    for m in measurements:
        if m['record_id']==x['legacy_record_id'] and m['variable']==x['legacy_heading']:
            m.update(variable=x['corrected_analyte'],value=float(x['corrected_value_as_reported']),
                     unit=x['corrected_unit_as_reported'] or 'dimensionless',value_basis='PDF preferred',
                     preferred_pdf_source_id='S38',preferred_pdf_page=int(x['pdf_page']))
# Porewater remains descriptive only; unverified CC report is a hold, not invented data.
for r in records:
    if 278<=int(r['record_id'][1:])<=289:
        r.update(collection_date='2023-11-20', biomass_basis='fresh shoot weight',
                 sampling_area_ft2=8, subsamples_per_plot=2, separation_ft=10)
        for m in measurements:
            if m['record_id']==r['record_id']: m['unit']='g fresh weight'
put('Sample records', records); put('Measurements',measurements)
d['source']='Corrected revision v3: original PDFs and researcher decisions D1–D18 applied on Puma'
P.write_text(json.dumps(d,ensure_ascii=False,allow_nan=False)+'\n')
(BASE.parent/'audit/revision_decisions.json').write_text(json.dumps(dict(execution=execution,
    excluded_records=excluded, replaced_legacy_records=sorted(replaced),
    scope='No raw reads reprocessed; established ASV counts retained. All derived analyses regenerated.'),indent=2)+'\n')
print('Applied correction sources and researcher decisions; exclusions:',len(excluded))
