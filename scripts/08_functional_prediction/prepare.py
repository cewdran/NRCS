#!/usr/bin/env python3
"""Verify immutable sources and build domain-specific PICRUSt2 input on Puma."""
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
from collections import Counter
import sys, json, shutil, csv, hashlib
from common import require, sha, save, table, wbrows, truth, counts, fasta

run = Path(sys.argv[1]).resolve()
cfg = json.loads((run/'CONFIG.json').read_text())
snap = run/'source_snapshot'
snap.mkdir(exist_ok=False)
(run/'results').mkdir(exist_ok=False)
verified = []
def copy_checked(src, dst, expected):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    actual = sha(dst)
    require(actual == expected, 'Frozen input checksum mismatch: '+str(src))
    verified.append({'source':str(src), 'snapshot':str(dst.relative_to(run)), 'sha256':actual})

for name, expected in cfg['sources'].items():
    rel = Path(name)
    require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe source path')
    copy_checked(Path(cfg['source_run'])/rel, snap/rel, expected)
status = json.loads((snap/'HPC_STATUS.json').read_text())
require(status.get('analysis_job_id') == cfg['source_analysis_job_id'] and status.get('pipeline_exit_code') == 0
        and status.get('scheduler_state') == 'COMPLETED', 'Corrected source run is not the expected successful run')
wb = json.loads((snap/'payload/inputs/workbook_data.json').read_text())['tables']
micro = wbrows(wb['16S samples'])
audit, dispositions = [], []
for domain in ['bacteria', 'archaea']:
    folder = snap/'candidates'/domain
    for fn, info in cfg['candidates'][domain].items():
        copy_checked(Path(info['path']), folder/fn, info['sha256'])
    ids, frozen_counts = counts(snap/f'payload/inputs/{domain}_counts.tsv.gz')
    original_ids, original_counts = counts(folder/'asv_counts_target.tsv')
    require(ids == original_ids and frozen_counts == original_counts,
            domain+': original target counts differ from corrected run input; no remapping permitted')
    seqs = fasta(folder/'ASVs_target.fasta')
    require(set(seqs) == set(ids), domain+': target FASTA and count ASV sets differ')
    catalogue = {}
    with (folder/'ASV_catalog.tsv').open() as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            if row['asv_id'] in seqs:
                require(row['asv_id'] not in catalogue, 'Duplicate catalogue ASV')
                require(row['sequence'].upper() == seqs[row['asv_id']], 'Catalogue/FASTA sequence mismatch '+row['asv_id'])
                require(int(row['length']) == len(seqs[row['asv_id']]), 'Catalogue sequence length mismatch')
                catalogue[row['asv_id']] = row
    require(set(catalogue) == set(ids), 'Catalogue missing target ASVs')
    taxonomy = {}
    with (folder/'taxonomy.tsv').open() as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            if row['asv_id'] in seqs:
                require(row['asv_id'] not in taxonomy, 'Duplicate taxonomy ASV')
                tax_domain = row['Kingdom'].strip().lower().removeprefix('d__')
                require(tax_domain == domain, 'Unexpected target taxonomy domain: '+str(row))
                taxonomy[row['asv_id']] = row
    require(set(taxonomy) == set(ids), 'Taxonomy missing target ASVs')
    meta = [r for r in micro if r['microbial_domain'] == domain]
    require(len({r['analysis_sample'] for r in meta}) == len(meta), 'Duplicate domain/sample metadata rows')
    selected = []
    for row in meta:
        sid = row['analysis_sample']
        expected = float(row['target_read_count'] or 0)
        require(sid in frozen_counts or expected == 0, 'Missing positive-read sample '+sid)
        observed = sum(frozen_counts.get(sid, []))
        require(observed == expected, 'Read-total mismatch '+sid)
        include = observed > 0 and not truth(row.get('descriptive_reference'))
        reason = 'retained_current_experimental_metadata' if include else 'zero_target_reads_or_descriptive_reference'
        dispositions.append({'domain':domain, 'analysis_sample':sid, 'sample_key':row['sample_key'],
            'target_reads':observed, 'prediction_included':include, 'primary_included':row['primary_included'],
            'reason':reason})
        if include:
            require(bool(row.get('biological_plot_id')) and bool(row.get('analytic_system')), 'Missing plot/system identity')
            selected.append(row)
    for sid in sorted(set(frozen_counts)-{r['analysis_sample'] for r in meta}):
        dispositions.append({'domain':domain, 'analysis_sample':sid, 'sample_key':'',
            'target_reads':sum(frozen_counts[sid]), 'prediction_included':False, 'primary_included':'',
            'reason':'not_in_corrected_metadata; historical/excluded/control library; no automatic reintroduction'})
    require(bool(selected), 'No eligible samples for '+domain)
    keys = [(r['farm'],r['analytic_system'],r['biological_plot_id'],r['collection_date']) for r in selected]
    require(len(keys) == len(set(keys)), 'Duplicate biological plot/date; do not average automatically')
    sids = [r['analysis_sample'] for r in selected]
    active = [j for j in range(len(ids)) if any(frozen_counts[s][j] > 0 for s in sids)]
    dest = run/'validated'/domain
    dest.mkdir(parents=True)
    with (dest/'asv_by_sample.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['ASV_ID']+sids)
        for j in active:
            writer.writerow([ids[j]]+[frozen_counts[s][j] for s in sids])
    with (dest/'ASVs.fasta').open('w') as handle:
        for j in active:
            handle.write('>'+ids[j]+'\n'+seqs[ids[j]]+'\n')
    table(dest/'sample_metadata.csv', selected)
    sequence_multiplicity = Counter(seqs.values())
    table(run/'results'/f'{domain}_sequence_identity.csv', [
        {'ASV_ID':ids[j], 'length':len(seqs[ids[j]]), 'sequence_sha256':hashlib.sha256(seqs[ids[j]].encode()).hexdigest(),
         'catalogue_identical':True, 'taxonomy_domain':taxonomy[ids[j]]['Kingdom'],
         'duplicate_sequence_count':sequence_multiplicity[seqs[ids[j]]]} for j in active])
    audit.append({'domain':domain, 'source_ASVs_verified':len(ids), 'prediction_ASVs':len(active),
        'prediction_samples':len(selected), 'source_counts_identical':True, 'fasta_catalogue_identical':True,
        'counts_transposed_on_HPC':True, 'technical_replicates':'Preserve corrected source library identities and merged counts; never split them',
        'unobserved_features_removed':len(ids)-len(active)})
save(run/'audit/SOURCE_VERIFICATION.json', verified)
save(run/'results/INPUT_VALIDATION.json', {'execution':execution, 'domains':audit})
table(run/'results/sample_disposition.csv', dispositions)
print(json.dumps(audit, indent=2), flush=True)
