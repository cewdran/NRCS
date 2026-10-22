#!/usr/bin/env python3
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
from collections import defaultdict,Counter
import csv,json,sys,datetime
from common import table as write_table,save as save_json,truth,readcsv
run=Path(sys.argv[1]).resolve()
snap=run/'source_snapshot'
out=run/'results/modeling_support'
out.mkdir(parents=True,exist_ok=False)
def table(name,rr,fields=None):write_table(out/name,rr,fields)
def save(name,obj):save_json(out/name,obj)
def rows(t):return [dict(zip(t['columns'],r)) for r in t['rows']]
def date(x):
 try:return datetime.date.fromisoformat(str(x))
 except (ValueError,TypeError):return None
import shutil
dispositions=defaultdict(list)
for r in readcsv(snap/'payload/tables/record_disposition.csv'):dispositions[r['record_id']].append(r['status'])
wb=json.loads((snap/'payload/inputs/workbook_data.json').read_text())['tables']
micro=rows(wb['16S samples']);records=rows(wb['Sample records'])
obs=readcsv(snap/'payload/tables/analysis_observations.csv')
shutil.copy2(snap/'payload/tables/record_disposition.csv',out/'source_record_disposition.csv')
issues=[];plot_groups=defaultdict(list)
for r in micro:
 for scope in ['all_positive_nonreference','primary_positive_nonreference']:
  if float(r['target_read_count'] or 0)<=0 or truth(r.get('descriptive_reference')):continue
  if scope.startswith('primary') and not truth(r.get('primary_included')):continue
  pid=r.get('biological_plot_id');system=r.get('analytic_system');dt=date(r.get('collection_date'))
  if not pid or not system or not dt:
   issues.append({'kind':'missing_microbiome_identity','sample':r.get('analysis_sample'),'scope':scope,'reason':'Missing physical plot, analytic system or ISO collection date'});continue
  key=(r['farm'],r['microbial_domain'],system,pid,scope)
  plot_groups[key].append(r)
trajectory=[]
for k,rr in sorted(plot_groups.items()):
 dates=sorted({date(r['collection_date']) for r in rr});treatments=sorted({str(r.get('treatment_label') or r.get('treatment')) for r in rr})
 trajectory.append(dict(zip(['farm','domain','system','physical_plot','scope'],k),n_distinct_visits=len(dates),first_date=str(dates[0]),last_date=str(dates[-1]),span_days=(dates[-1]-dates[0]).days,collection_dates=';'.join(map(str,dates)),treatments=';'.join(treatments),n_library_rows=len(rr),duplicate_plot_date_rows=len(rr)-len(dates),interpretation='Metadata support only; depth, design and model validity still require review'))
table('microbiome_plot_visit_support.csv',trajectory)
cohorts=defaultdict(list)
for r in trajectory:cohorts[(r['farm'],r['domain'],r['system'],r['scope'])].append(r)
summary=[]
for k,rr in sorted(cohorts.items()):
 dd=sorted({d for r in rr for d in r['collection_dates'].split(';')})
 summary.append(dict(zip(['farm','domain','system','scope'],k),n_physical_plots=len(rr),n_distinct_collection_dates=len(dd),collection_dates=';'.join(dd),plots_with_2plus_visits=sum(r['n_distinct_visits']>=2 for r in rr),plots_with_3plus_visits=sum(r['n_distinct_visits']>=3 for r in rr),treatment_switch_plots=sum(';' in r['treatments'] for r in rr),forecast_status='NOT_AUTHORIZED_BY_COUNTS_ALONE',ten_year_status='NO_VALIDATED_TEN_YEAR_FORECAST'))
table('microbiome_cohort_support.csv',summary)
# Audit already-produced HPC observations; never create a new source identity.
measured=defaultdict(list)
for r in obs:
 key=(r['farm'],r['system'],r['dataset'],r['variable'],r['unit'])
 measured[key].append(r)
mrows=[]
for k,rr in sorted(measured.items()):
 plotdates=defaultdict(set);invalid=set();duplicates=Counter()
 for r in rr:
  dt=date(r['event'])
  if not dt:invalid.add(r['event']);continue
  if not r['plot']:continue
  plotdates[r['plot']].add(str(dt));duplicates[(r['plot'],str(dt))]+=1
 all_dates=sorted({d for dd in plotdates.values() for d in dd})
 mrows.append(dict(zip(['farm','system','dataset','variable','unit'],k),n_source_observations=len(rr),n_plots_with_dated_records=len(plotdates),n_collection_events=len(all_dates),collection_events=';'.join(all_dates),undated_or_year_only_events=';'.join(sorted(invalid)),plots_with_2plus_visits=sum(len(dd)>=2 for dd in plotdates.values()),plots_with_3plus_visits=sum(len(dd)>=3 for dd in plotdates.values()),duplicate_plot_event_rows=sum(v-1 for v in duplicates.values()),censored_rows=sum(truth(r['censored']) for r in rr),interpretation='Event strings are inherited from source model tables; verify actual collection identity before forecasting. Units/method compatibility not inferred.'))
table('measured_outcome_visit_support.csv',mrows)
# Exact, confirmed joins only. Do not promote candidate matches or use year-only joins.
meta_keys=defaultdict(list)
for r in micro:meta_keys[r['sample_key']].append(r)
joinrows=[]
for r in records:
 key=r.get('confirmed_sample_key');pool=r.get('pool_member_count')
 candidates=meta_keys.get(key,[]) if key else []
 reasons=[]
 if not key:reasons.append('no_confirmed_sample_key')
 if r.get('intentional_exclusion'):reasons.append('intentional_exclusion: '+str(r['intentional_exclusion']))
 if any('exclu' in s.lower() or 'held' in s.lower() for s in dispositions.get(r['record_id'], [])):reasons.append('source_disposition_excluded_or_held')
 if pool and float(pool)>1:reasons.append('composite_record')
 if not date(r.get('collection_date')):reasons.append('no_exact_collection_date')
 compatible=[m for m in candidates if m['farm']==r['farm'] and m['collection_date']==r.get('collection_date')]
 if key and not compatible:reasons.append('no_exact_farm_key_date_microbiome_match')
 joinrows.append({'record_id':r['record_id'],'farm':r['farm'],'dataset':r['dataset'],'collection_date':r.get('collection_date'),'confirmed_sample_key':key,'candidate_sample_key':r.get('candidate_sample_key'),'pool_member_count':pool,'matched_domains':';'.join(sorted({m['microbial_domain'] for m in compatible})),'identity_match_status':'CONFIRMED_IDENTITY_ONLY' if not reasons else 'HELD_FOR_JOIN_OR_COMPOSITE','reasons':';'.join(reasons),'inference_note':'Identity match is not model eligibility. Laboratory duplicates remain one biological sample; preserve source disposition.'})
table('confirmed_join_audit.csv',joinrows)

table('identity_issues.csv',issues,fields=['kind','sample','scope','reason'])
# Candidate scope is fixed before functional results; support counts are not approval to fit.
candidates=[]
for r in summary:
 if r['scope']!='primary_positive_nonreference':continue
 for outcome in ['Shannon','observed_ASVs','target_read_count']:
  candidates.append(dict(r,outcome=outcome,
   outcome_role='sequencing_depth_diagnostic' if outcome=='target_read_count' else 'candidate_repeated_plot_response',
   modeling_status='NO_MODEL_FITTED; exact design and depth adjustment require protocol review',
   time_axis='Actual dates within the same farm, analytic system and physical plot',
   forecast_status='No validated forecast; prospective held-out occasions must be independent',
   potential_model='Plot intercept plus collection-event and treatment terms only if estimable; no automatic interaction or slope',
   restrictions='Do not pool fields, crop systems or domains; no outcome selection by p-value'))
table('candidate_model_scopes.csv',candidates)
save('SUPPORT_COMPLETION.json',{'execution':execution,'corrected_source_job':'24067160',
 'supersedes':'Readiness 24055317 used older metadata; confirmed joins here reflect corrected run D1-D18',
 'models_fitted':False,'forecasts_generated':False,
 'limits':['Support tables do not establish identifiability, statistical power or temporal validation.',
 'Measurement IDs and units stay separate; matching labels do not establish method compatibility.',
 'Treatment and sampling date may be confounded; cohort date count is not visits per plot.',
 'NIFA/NRCS sequence or sample reuse is not resolved by this package; independent evidence claims remain held.',
 'Ten-year scenarios require external calibration, actual drivers and appropriate starting states.']})
