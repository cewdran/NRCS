#!/usr/bin/env python3
"""Run metadata/support computations only inside a verified Puma allocation."""
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
from collections import defaultdict,Counter
import csv,json,sys,datetime,hashlib,shutil,gzip,os,re,platform
run=Path(sys.argv[1]).resolve()
cfg=json.loads((run/'CONFIG.json').read_text())
out=run/'results';out.mkdir(exist_ok=False)
snap=run/'source_snapshot';snap.mkdir(exist_ok=False)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def save(name,x): (out/name).write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def table(name,rows,fields=None):
 fields=fields or (list(rows[0]) if rows else ['status'])
 with (out/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def truth(x): return x is True or str(x).lower() in ('true','1','yes')
def date(x):
 try:return datetime.date.fromisoformat(str(x))
 except (ValueError,TypeError):return None
def rows(t):return [dict(zip(t['columns'],r)) for r in t['rows']]
def readcsv(p):
 with p.open(newline='') as f:return list(csv.DictReader(f))
verified=[]
for name,expected in cfg['sources'].items():
 rel=Path(name)
 if rel.is_absolute() or '..' in rel.parts:raise RuntimeError('Unsafe source path')
 src=Path(cfg['source_run'])/rel;dst=snap/rel;dst.parent.mkdir(parents=True,exist_ok=True)
 shutil.copy2(src,dst)
 actual=sha(dst)
 if actual!=expected:raise RuntimeError('Frozen-source checksum mismatch: '+name)
 verified.append({'source':str(src),'sha256':actual,'snapshot':str(dst.relative_to(run))})
status=json.loads((snap/'HPC_STATUS.json').read_text())
if status.get('analysis_job_id')!=cfg['source_analysis_job_id'] or status.get('pipeline_exit_code')!=0 or status.get('scheduler_state')!='COMPLETED':
 raise RuntimeError('Expected completed source HPC run 24054963')
save('SOURCE_VERIFICATION.json',verified)
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
 if pool and float(pool)>1:reasons.append('composite_record')
 if not date(r.get('collection_date')):reasons.append('no_exact_collection_date')
 compatible=[m for m in candidates if m['farm']==r['farm'] and m['collection_date']==r.get('collection_date')]
 if key and not compatible:reasons.append('no_exact_farm_key_date_microbiome_match')
 joinrows.append({'record_id':r['record_id'],'farm':r['farm'],'dataset':r['dataset'],'collection_date':r.get('collection_date'),'confirmed_sample_key':key,'candidate_sample_key':r.get('candidate_sample_key'),'pool_member_count':pool,'matched_domains':';'.join(sorted({m['microbial_domain'] for m in compatible})),'identity_match_status':'CONFIRMED_IDENTITY_ONLY' if not reasons else 'HELD_FOR_JOIN_OR_COMPOSITE','reasons':';'.join(reasons),'inference_note':'Identity match is not model eligibility. Laboratory duplicates remain one biological sample; preserve source disposition.'})
table('confirmed_join_audit.csv',joinrows)
# Header inventory is not inference; existing ASV names must match sequence identifiers.
count_ids={}
for domain in ['bacteria','archaea']:
 with gzip.open(snap/f'payload/inputs/{domain}_counts.tsv.gz','rt') as f:
  header=next(csv.reader(f,delimiter='\t'))
 ids=header[1:]
 if len(ids)!=len(set(ids)):raise RuntimeError('Duplicate ASV header IDs: '+domain)
 count_ids[domain]=set(ids)
save('ASV_COUNT_HEADER_AUDIT.json',{d:{'n_features':len(v),'first_identifiers':sorted(v)[:5]} for d,v in count_ids.items()})
inventory=[];scans=[]
allowed={'.rds','.rdata','.fa','.fna','.fasta','.fas','.tsv','.csv','.nwk','.tree','.tre','.biom'}
for rootstr in cfg['candidate_roots']:
 root=Path(rootstr);seen=0;truncated=False;errors=[]
 if not root.is_dir():scans.append({'root':rootstr,'status':'MISSING_ROOT'});continue
 for current,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:errors.append(str(e))):
  depth=len(Path(current).relative_to(root).parts)
  dirs[:]=sorted(d for d in dirs if d not in ['filtered','fastq','raw','tmp','__pycache__'] and not (Path(current)/d).is_symlink()) if depth<cfg['max_scan_depth'] else []
  for name in sorted(files):
   seen+=1
   if seen>cfg['max_scan_files_per_root']:truncated=True;break
   p=Path(current)/name
   if p.is_symlink():continue
   ext=p.suffix.lower();logical=Path(p.stem).suffix.lower() if ext=='.gz' else ext
   if logical not in allowed:continue
   st=p.stat();item={'path':str(p),'bytes':st.st_size,'sha256':sha(p) if st.st_size<=cfg['max_hash_bytes'] else '', 'header_preview':'','bacteria_ASV_overlap':'','archaea_ASV_overlap':'','fasta_id_count':'','status':'candidate_not_validated'}
   try:
    if logical in {'.fa','.fna','.fasta','.fas'} and st.st_size<=cfg['max_fasta_bytes']:
     op=gzip.open if ext=='.gz' else open;ids=[];uncompressed=0;complete=True
     with op(p,'rt') as f:
      for line in f:
       uncompressed+=len(line)
       if uncompressed>cfg['max_fasta_bytes']:complete=False;break
       if line.startswith('>'):ids.append(line[1:].split()[0])
     iset=set(ids);item.update(fasta_id_count=len(ids),header_preview=';'.join(ids[:4]),status='FASTA_IDS_READ' if complete else 'FASTA_READ_LIMIT_REACHED')
     for d in count_ids:item[d+'_ASV_overlap']=len(iset & count_ids[d])
     if len(ids)!=len(iset):item['status']='DUPLICATE_FASTA_IDS'
    elif logical in {'.tsv','.csv','.nwk','.tree','.tre'}:
     op=gzip.open if ext=='.gz' else open
     with op(p,'rt',errors='replace') as f:item['header_preview']=f.readline(500).rstrip()[:400]
   except Exception as exc:item['status']='READ_ERROR: '+str(exc)
   inventory.append(item)
  if truncated:break
 scans.append({'root':rootstr,'status':'FILE_LIMIT_REACHED' if truncated else 'BOUNDED_SCAN_FINISHED','files_seen':seen,'depth_limit':cfg['max_scan_depth'],'errors':errors,'note':'Only these roots, permitted extensions and bounded depth scanned; absence does not prove global absence.'})
table('sequence_taxonomy_tree_candidates.csv',inventory,fields=['path','bytes','sha256','header_preview','bacteria_ASV_overlap','archaea_ASV_overlap','fasta_id_count','status'])
save('SEARCH_SCOPE.json',scans);table('identity_issues.csv',issues,fields=['kind','sample','scope','reason'])
save('AUDIT_COMPLETION.json',{'status':'READINESS_AUDIT_COMPLETED_REVIEW_REQUIRED','execution':execution,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version,'platform':platform.platform(),'model_fitting_performed':False,'functional_prediction_performed':False,'ten_year_forecast_performed':False,'seed':'not applicable: deterministic metadata audit','next_step':'Review support tables and ASV candidates, then freeze model/functional input plans. Candidate files are not validated functional inputs.'})
(out/'READ_ME_FIRST.txt').write_text('NRCS READINESS AUDIT - REVIEW REQUIRED\n\nThe audit contains new metadata summaries computed on Puma. It does not fit models or run PICRUSt2.\nSource snapshots and hashes identify the completed companion run reused here. No historical source files were modified.\nVisit counts do not establish statistical power or validate a ten-year horizon.\nAll-positive and primary-included microbiome scopes are reported separately; domain rows are not independent physical plots.\nSource laboratory duplicates are not extra biological replicates. Candidate sample keys remain held.\nSequence candidates and partial-search limits must be reviewed before selecting files. No sequences were fabricated from ASV names.\nMissing external drivers, uncertainty and data overlap remain downstream design requirements.\n')
print(json.dumps({'status':'READINESS_AUDIT_COMPLETED_REVIEW_REQUIRED','results':str(out)},indent=2))
