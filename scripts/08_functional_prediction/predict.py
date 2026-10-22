#!/usr/bin/env python3
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, os, json, subprocess, datetime, traceback, shutil
import numpy as np
import pandas as pd
from common import require, save, table, readcsv

run = Path(sys.argv[1]).resolve()
cfg = json.loads((run/'CONFIG.json').read_text())
cpus = int(os.environ.get('SLURM_CPUS_PER_TASK', '1'))
summary = []
commands = []
def command(args, logfile):
    commands.append({'argv':list(map(str,args)), 'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     'log':str(logfile.relative_to(run))})
    save(run/'audit/prediction_commands.json', commands)
    print('COMMAND '+repr(args), flush=True)
    with logfile.open('w') as handle:
        subprocess.run(list(map(str,args)), stdout=handle, stderr=subprocess.STDOUT, check=True)

def numeric_table(path):
    require(path.is_file(), 'Missing output '+str(path))
    frame = pd.read_csv(path, sep='\t', index_col=0)
    require(not frame.index.has_duplicates and not frame.columns.has_duplicates, 'Duplicate output labels')
    values = frame.to_numpy(dtype=float)
    require(len(frame) > 0 and np.isfinite(values).all() and (values >= 0).all(), 'Invalid numeric profile '+str(path))
    return frame

for domain in ['bacteria','archaea']:
    output = run/'predictions'/domain
    inputs = run/'validated'/domain
    report = run/'results'/domain
    report.mkdir(parents=True, exist_ok=True)
    try:
        command(['picrust2_pipeline.py', '-s', inputs/'ASVs.fasta', '-i', inputs/'asv_by_sample.tsv',
                 '-o', output, '-p', str(cpus), '--in_traits', 'EC,KO', '--min_align', str(cfg['min_align']),
                 '--max_nsti', str(cfg['max_nsti']), '--min_reads', '1', '--min_samples', '1',
                 '-m', 'mp', '-e', '0.5', '-t', 'epa-ng', '--verbose'], run/'logs'/f'{domain}_picrust2.log')
        marker_path = output/'combined_marker_predicted_and_nsti.tsv.gz'
        require(marker_path.is_file(), 'Missing v2.6.3 marker/NSTI output')
        marker = pd.read_csv(marker_path, sep='\t', index_col=0)
        shutil.copy2(marker_path, report/marker_path.name)
        require(not marker.index.has_duplicates, 'Duplicate marker prediction ASV')
        for col in ['metadata_NSTI','16S_rRNA_Count','best_domain']:
            require(col in marker.columns, 'Missing marker column '+col)
        count = numeric_table(inputs/'asv_by_sample.tsv')
        require(set(marker.index) <= set(count.index), 'Unexpected ASVs in marker output')
        require(np.isfinite(marker[['metadata_NSTI','16S_rRNA_Count']].to_numpy(float)).all(), 'Invalid marker/NSTI values')
        require((marker['metadata_NSTI'] >= 0).all() and (marker['16S_rRNA_Count'] > 0).all(), 'Invalid marker copy number or NSTI')
        require(set(marker.best_domain) <= {'bac','arc'}, 'Unknown best-domain label')
        expected_domain = 'bac' if domain == 'bacteria' else 'arc'
        normalized = {}
        profiles = {}
        for family in ['EC','KO']:
            normalized[family] = numeric_table(output/f'{family}_metagenome_out/seqtab_norm.tsv.gz')
            require(set(normalized[family].index) <= set(count.index), 'Unexpected normalized ASV')
            require(set(normalized[family].columns) == set(count.columns), 'Normalized sample set changed')
            profiles[family] = numeric_table(output/f'{family}_metagenome_out/pred_metagenome_unstrat.tsv.gz')
            require(set(profiles[family].columns) == set(count.columns), 'Gene-family sample set changed '+family)
        profiles['METACYC'] = numeric_table(output/'pathways_out/path_abun_unstrat.tsv.gz')
        for family, profile in profiles.items():
            require(set(profile.columns) <= set(count.columns), 'Unexpected profile samples '+family)
            require(len(profile.columns) > 0, 'No output profile samples '+family)
            profile.to_csv(report/f'{family}_predicted_abundance.tsv.gz', sep='\t', index_label='function')
            command(['add_descriptions.py','-i',report/f'{family}_predicted_abundance.tsv.gz',
                     '-m',family,'-o',report/f'{family}_predicted_abundance_described.tsv.gz'],
                    run/'logs'/f'{domain}_{family}_descriptions.log')
        retained = set(normalized['EC'].index)
        require(retained == set(normalized['KO'].index), 'EC/KO ASV retention differs; review before use')
        require(retained <= set(marker.index), 'Retained ASV without marker prediction')
        require((marker.loc[sorted(retained),'metadata_NSTI'] <= cfg['max_nsti']).all(), 'NSTI cutoff inconsistency')
        meta = {r['analysis_sample']:r for r in readcsv(inputs/'sample_metadata.csv')}
        asv_rows = []
        for asv in count.index:
            has = asv in marker.index
            kept = asv in retained
            nsti = float(marker.loc[asv,'metadata_NSTI']) if has else None
            chosen = marker.loc[asv,'best_domain'] if has else ''
            asv_rows.append({'ASV_ID':asv, 'expected_domain':domain, 'predicted_reference_domain':chosen,
                'domain_discordance':bool(has and chosen != expected_domain), 'NSTI':nsti,
                'predicted_16S_copy_number':float(marker.loc[asv,'16S_rRNA_Count']) if has else None,
                'retained_for_prediction':kept,
                'disposition':'retained' if kept else 'no_marker_prediction_after_placement' if not has else
                              'above_max_NSTI' if nsti > cfg['max_nsti'] else 'other_pipeline_filter_requires_review'})
        table(report/'ASV_prediction_QC.csv',asv_rows)
        sample_rows = []
        for sid in count.columns:
            c = count[sid]
            total = float(c.sum())
            require(total > 0, 'Zero-read input sample')
            kept_reads = float(c.loc[sorted(retained)].sum())
            weights = c.loc[sorted(retained)]
            nsti = marker.loc[sorted(retained),'metadata_NSTI']
            discordant = marker.index[marker.best_domain != expected_domain].intersection(list(retained))
            discordant_reads = float(c.loc[discordant].sum())
            row = {k:meta[sid].get(k,'') for k in ['farm','analytic_system','biological_plot_id','collection_date','sample_key','treatment','primary_included']}
            row.update(domain=domain, analysis_sample=sid, input_target_reads=int(total),
                represented_target_reads=int(kept_reads), represented_read_fraction=kept_reads/total,
                retained_observed_ASVs=int((weights > 0).sum()),
                raw_read_weighted_NSTI_retained=float((weights*nsti).sum()/kept_reads) if kept_reads else None,
                discordant_reference_domain_read_fraction=discordant_reads/total,
                profile_status='PREDICTION_REVIEW_REQUIRED' if kept_reads else 'NO_REPRESENTED_READS',
                comparison_status='HELD_UNTIL_QC_AND_CONTRAST_PROTOCOL_REVIEW')
            for cutoff in [0.2,0.5,1.0,2.0]:
                eligible = marker.index[marker.metadata_NSTI <= cutoff].intersection(list(retained))
                row['read_fraction_NSTI_le_'+str(cutoff)] = float(c.loc[eligible].sum()/total)
            for family, profile in profiles.items():
                row[family+'_profile_present'] = sid in profile.columns
                row[family+'_nonzero_functions'] = int((profile[sid] > 0).sum()) if sid in profile.columns else 0
            sample_rows.append(row)
        table(report/'sample_prediction_QC.csv', sample_rows)
        for family in ['EC','KO']:
            path = output/f'{family}_metagenome_out/weighted_nsti.tsv.gz'
            require(path.is_file(), 'Missing weighted NSTI output')
            shutil.copy2(path, report/f'{family}_PICRUSt_weighted_nsti.tsv.gz')
        summary.append({'domain':domain, 'status':'PREDICTION_COMPLETED_REVIEW_REQUIRED', 'input_samples':len(count.columns),
            'ASVs_input':len(count), 'ASVs_retained':len(retained), 'samples_zero_represented_reads':sum(r['represented_target_reads']==0 for r in sample_rows),
            'profile_feature_counts':{k:len(v) for k,v in profiles.items()}, 'reference_domain_discordant_ASVs':len(discordant)})
    except Exception as exc:
        (report/'FAILURE.txt').write_text(traceback.format_exc())
        summary.append({'domain':domain,'status':'FAILED_OR_INCOMPLETE','error':str(exc)})
    save(run/'results/PREDICTION_STATUS.json',{'execution':execution,'domains':summary,
        'interpretation':'Inferred genetic potential; not measured genes, expression, process rates or plant benefits.',
        'contrast_tests_performed':False, 'forecast_performed':False})
require(all(x['status']=='PREDICTION_COMPLETED_REVIEW_REQUIRED' for x in summary), 'One or more domains failed; see domain logs and FAILURE.txt')
(run/'ANALYSES_COMPLETE.txt').write_text('Functional predictions and metadata support audit finished; scientific review remains required.\n')
