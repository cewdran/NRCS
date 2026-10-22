"""One Puma plotting job; consume the exact v2 return archive, retain its tables."""
from pathlib import Path
import csv,datetime,hashlib,importlib.metadata,json,os,shutil,sys,traceback,zipfile
from hpc_guard import require_hpc

BASE=Path(__file__).resolve().parent

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def js(path,record):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')

def read(path):
    with Path(path).open(newline='') as f:return list(csv.DictReader(f))

def require(condition,message):
    if not condition:raise RuntimeError(message)

def manifest(directory):
    return {str(p.relative_to(directory)):sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}

def save_table(path,rows):
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main(run):
    run=Path(run).resolve();run.mkdir(parents=True,exist_ok=True)
    out=run/'payload';out.mkdir(exist_ok=True)
    c=json.loads((BASE/'CONFIG.json').read_text())
    status={'state':'INCOMPLETE_OR_FAILED','visual_review_complete':False,'manuscripts_edited':False,
            'source_values_changed':False,'descriptive_display_medians_computed':True,'new_inferential_tests':False,'FDR_recalculated':False}
    error=None
    try:
        execution=require_hpc()
        require(sys.version_info[:3]==(3,11,4),'Pinned Python version differs')
        require(Path(sys.executable).absolute()==Path(c['runtime_python']).absolute(),'Wrong runtime interpreter')
        require(sha(c['runtime_ready'])==c['runtime_ready_sha256'],'Runtime READY changed')
        versions={k:importlib.metadata.version(k) for k in c['versions']}
        require(versions==c['versions'],'Runtime package versions differ')
        for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
            h,p=line.split('  ',1);require(sha(BASE/p)==h,'Refresh package changed: '+p)
        js(out/'audit/refresh_environment.json',{'execution':execution,'versions':versions,'python':sys.version})
        archive=Path(c['source_archive'])
        require(archive.is_file(),'Original v2 results ZIP is missing: '+str(archive))
        require(sha(archive)==c['source_archive_sha256'],'Original v2 results ZIP checksum differs')
        with zipfile.ZipFile(archive) as z:
            require(z.testzip() is None,'Original v2 ZIP CRC failure')
            for entry in z.infolist():
                p=Path(entry.filename)
                require(not p.is_absolute() and '..' not in p.parts,'Unsafe source ZIP member')
                require((entry.external_attr>>16)&0o170000 != 0o120000,'Source ZIP symlink rejected')
            z.extractall(run/'source')
        src=run/'source'/c['source_run']
        for line in (src/'RETURN_SHA256SUMS.txt').read_text().splitlines():
            h,p=line.split(maxsplit=1);require(sha(src/p.lstrip('*'))==h,'Source member checksum differs: '+p)
        original=json.loads((src/'FINAL_STATUS.json').read_text())
        require(original['state']=='HPC_OUTPUTS_CREATED_REVIEW_PENDING' and original['figure_count']==38,
                'Source is not the complete expected v2 run')
        shutil.copytree(src,out,dirs_exist_ok=True)
        (out/'RETURN_SHA256SUMS.txt').unlink()
        (out/'FINAL_STATUS.json').rename(out/'audit/source_v2_FINAL_STATUS.json')
        audit_package=out/'audit/refresh_package';audit_package.mkdir(parents=True)
        for p in BASE.iterdir():
            if p.is_file():shutil.copy2(p,audit_package/p.name)
        before_tables={p:h for p,h in manifest(out/'results').items() if p.endswith(('.csv','.csv.gz'))}
        from plot_layouts import community,associations,grenache_soil,whipstone_soil,inspect_layout,truth,plt
        from PIL import Image
        results=out/'results'; changed=[]; registry=json.loads((results/'FIGURE_REGISTRY.json').read_text())
        def finish(fig,stem,caption):
            path=results/stem;path.parent.mkdir(parents=True,exist_ok=True)
            qc=inspect_layout(fig);js(str(path)+'_LAYOUT_QC.json',qc)
            if qc['text_outside_canvas'] or qc['protected_region_overlaps'] or qc['clipped_marker_footprints']:
                fig.savefig(str(path)+'_DIAGNOSTIC.png',dpi=150)
                raise RuntimeError('Layout validation failed: '+stem+'; inspect its LAYOUT_QC.json')
            for ext in ('pdf','svg','eps','png','tif'):
                args={'dpi':600 if ext=='tif' else 300,'facecolor':'white'}
                if ext=='tif':args['pil_kwargs']={'compression':'tiff_lzw'}
                fig.savefig(str(path)+'.'+ext,**args)
            tif=Path(str(path)+'.tif')
            with Image.open(tif) as im:rgb=im.convert('RGB');rgb.save(tif,compression='tiff_lzw',dpi=(600,600))
            Path(str(path)+'_caption.txt').write_text(caption+'\n');plt.close(fig);changed.append(stem)
        for spec in c['systems']:
            folder=results/'communities'/spec['farm'].replace(' ','')
            for kind in ('Shannon','depth','PCoA'):
                rows=read(folder/(spec['slug']+'_'+kind+'_source_values.csv'))
                if kind=='depth':
                    # Include excluded source records if present; do not infer additional samples.
                    oldkeys={(r['microbial_domain'],r['analysis_sample']) for r in rows}
                    for domain in ('bacteria','archaea'):
                        for r in read(folder/(domain+'_complete_sample_inventory.csv')):
                            if r['analytic_system']==spec['system'] and r['collection_date'] in spec['dates'] and not truth(r['figure_primary']):
                                require((domain,r['analysis_sample']) not in oldkeys,'Excluded/primary identity collision')
                                r['display_code']=spec['treatments'][r['treatment_label']];rows.append(r)
                require(all(r['analytic_system']==spec['system'] for r in rows),'Wrong community system')
                eigen={}
                if kind=='PCoA':
                    for domain in ('bacteria','archaea'):
                        for d in spec['dates']:
                            vals=[float(r['after']) for r in read(folder/(spec['slug']+'_'+domain+'_'+d+'_eigenvalues.csv'))]
                            tol=c['pcoa_eigen_relative_tolerance']*max(1,max(abs(v) for v in vals));positive=[v for v in vals if v>tol]
                            eigen[(domain,d)]=[v/sum(positive) for v in positive]
                stem='communities/'+spec['farm'].replace(' ','')+'/'+spec['slug']+'_'+kind
                caption=Path(str(results/stem)+'_caption.txt').read_text().strip()
                caption+=' Layout refresh: dates form rows and domains form columns. Numerical source values and membership are unchanged.'
                finish(community(spec,kind,rows,eigen),stem,caption)
        pairs=read(results/'extras/all_selected_exact_CLR_soil_pairs.csv')
        saved=read(results/'extras/all_selected_association_saved_rows.csv');groups=[]
        for farm in ('Page Springs','Whipstone'):
            rr=[r for r in saved if r['farm']==farm]
            for i in range(0,len(rr),4):groups.append((farm,rr[i:i+4],farm.replace(' ','')+'_soil_associations_'+str(i//4+1)))
        ids=('2589580486ce284278bc7716','18ec6fb6ac0eb34ca401c8ee','14f70d1002747fd6ae04a4e7')
        groups.append(('Synthesis',[r for r in saved if r['test_id'] in ids],'Synthesis_predicted_function_soil_examples'))
        for farm,group,name in groups:
            for row in group:
                pp=[r for r in pairs if r['selected_test_id']==row['test_id']]
                require(len(pp)==int(row['n']) and len({r['biological_plot_id'] for r in pp})==len(pp),'Association membership changed')
                require(float(row['q_by_global'])==1 and all(r['saved_rho']==row['rho'] for r in pp),'Saved association statistic changed')
                if row['variable']=='soil_haney_H2O_Total_Organic_C':require({r['unit'] for r in pp}=={'ppm C'},'Unexpected Haney unit')
            stem='extras/'+farm.replace(' ','')+'/'+name
            caption=Path(str(results/stem)+'_caption.txt').read_text().strip()
            caption+=' Treatment legends are panel-specific. Haney H2O total organic C retains its reported ppm C unit; no additional extraction-method claim is made.'
            finish(associations(farm,group,pairs),stem,caption)
        obs=read(results/'source_tables/inputs/analyses/18_treatment_time/results/all_observations.csv')
        soil=[r for r in obs if r['worker_id']=='grenache_soil' and truth(r['comparison_complete_case']) and truth(r['fixed_visit'])]
        require(len(soil)==32 and all(r['row_status']=='VALID_SOURCE_VALUE' for r in soil),'Unexpected Grenache soil population')
        tests=[r for r in read(results/'source_tables/inputs/analyses/18_treatment_time/results/tests_with_multiplicity.csv') if r['worker_id']=='grenache_soil']
        require(len(tests)==4 and all(r['status']=='TESTED' and r['n_plots']=='8' for r in tests),'Unexpected Grenache tests')
        stem='measured_soil/PageSprings/Grenache_measured_soil_trajectories'
        finish(grenache_soil(soil,tests),stem,
            'Measured Olsen P and reported total N in the eight-plot fixed panel, with two physical plots per treatment. Lines join the same plot on 20 October 2023 and 5 August 2024. Values, reported assays, units and treatment codes are copied from the saved complete-case soil follow-up. Printed p values compare treatments for average levels or temporal changes and retain the complete planned ten-test soil Holm family. None passes 0.05. These are not the larger historical event cohorts. Total-N method was unspecified; no method equivalence beyond the source reports is asserted. T1, fertigation control; T7, fresh pomace; T8, composted pomace plus horse manure; T9, composted horse manure.')
        save_table(results/(stem+'_source_values.csv'),soil);save_table(results/(stem+'_saved_tests.csv'),tests)
        wh=[r for r in read(results/'source_tables/inputs/analyses/06_corrected_companions/payload/tables/Whipstone_environment_matched_values.csv')
            if r['domain']=='bacteria' and r['minimum_reads']=='0' and r['event']=='2023-11-16' and r['predictor'] in ('1:1 Soil pH','Organic Carbon, % C')]
        require(len(wh)==56,'Unexpected Whipstone soil population')
        require(len({(r['system'],r['sample_key'],r['predictor']) for r in wh})==56,'Duplicate soil plot/endpoint')
        stem='measured_soil/Whipstone/Whipstone_measured_soil_context'
        finish(whipstone_soil(wh),stem,
            'Measured 1:1 soil pH and organic carbon in the source-confirmed 16 November 2023 Whipstone panel. Annual crops, Chester and Natchez remain separate. Points are physical plots; short lines are descriptive medians. The bacterial minimum-zero matched table is used once per plot to prevent duplicate counting across microbial domains or sensitivity branches. Annual groups contain three plots per treatment and blackberry cultivar groups contain two. These measured soil endpoints do not represent predicted function; organic carbon is distinct from loss-on-ignition organic matter. No new test is fitted and the display does not establish pairwise treatment effects.')
        save_table(results/(stem+'_source_values.csv'),wh)
        for stem in changed[-2:]:registry.append({'stem':stem,'formats':['pdf','svg','eps','png','tif'],'manual_visual_review_complete':False})
        require(len(changed)==23 and len(registry)==40,'Unexpected refresh figure count')
        for item in registry:
            item['manual_visual_review_complete']=False
            item['refresh_action']='redrawn_from_saved_values' if item['stem'] in changed else 'v2_formats_reused_byte_for_byte'
            for ext in item['formats']:
                new=results/(item['stem']+'.'+ext);require(new.is_file(),'Missing publication format')
                if item['stem'] not in changed:require(sha(new)==sha(src/'results'/(item['stem']+'.'+ext)),'Unchanged figure modified')
        after=manifest(results)
        require(all(after[p]==h for p,h in before_tables.items()),'A pre-existing scientific CSV changed')
        js(results/'FIGURE_REGISTRY.json',registry)
        js(out/'audit/refresh_integrity.json',{'original_scientific_tables_verified_unchanged':len(before_tables),
            'source_archive_sha256':c['source_archive_sha256'],'refreshed_stems':changed,'reused_figure_count':17,
            'distances_refitted':False,'coordinates_refitted':False,'CLR_recomputed':False,
            'hypothesis_tests_recomputed':False,'multiple_testing_recomputed':False})
        status.update(state='HPC_FIGURES_REFRESHED_REVIEW_PENDING',figure_count=40,figures_redrawn=23,
            figures_reused=17,scientific_execution_completed=True,automated_layout_checks_passed=True)
    except BaseException as exc:
        error=exc;status['error']=str(exc);(out/'FAILURE_TRACEBACK.txt').write_text(traceback.format_exc())
    status['collector_terminal_state_verified']=False
    js(out/'FINAL_STATUS.json',status)
    entries=manifest(out);(out/'RETURN_SHA256SUMS.txt').write_text(''.join(h+'  '+p+'\n' for p,h in entries.items()))
    suffix='results' if error is None else 'DIAGNOSTIC_INCOMPLETE'
    dest=Path(c['source_archive']).parent/(c['package']+'_'+run.name+'_'+suffix+'.zip')
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():z.write(p,run.name+'/'+str(p.relative_to(out)))
        for p in sorted(run.glob('*.log')):
            z.write(p,run.name+'/refresh_logs/'+p.name)
    checksum=Path(str(dest)+'.sha256.txt');checksum.write_text(sha(dest)+'  '+dest.name+'\n')
    js(run/'completion.json',{'state':status['state'],'archive':str(dest),'checksum':str(checksum),
        'sha256':sha(dest),'requires_terminal_scheduler_check':True})
    print('RETURN_ZIP='+str(dest));print('RETURN_CHECKSUM='+str(checksum))
    if error is not None:raise SystemExit(1)

if __name__=='__main__':main(sys.argv[1])
