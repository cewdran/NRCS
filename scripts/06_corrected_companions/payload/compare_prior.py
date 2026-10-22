#!/usr/bin/env python3
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import pandas as pd,json,hashlib
base=Path(__file__).resolve().parent
unchanged=['alpha_Shannon_tests.csv','soil_temporal_tests.csv','environment_tests.csv','PageSprings_phylum_tests.csv','matched_plot_changes.csv']
checks=[]
for prior in sorted((base/'reference_prior_HPC').glob('*.csv')):
    new=base/'tables'/prior.name
    if not new.exists():continue
    same=prior.read_bytes()==new.read_bytes()
    if prior.name in unchanged:
        pd.testing.assert_frame_equal(pd.read_csv(prior),pd.read_csv(new),check_exact=False,rtol=1e-10,atol=1e-12)
    checks.append(dict(table=prior.name,byte_identical=same,expected_unchanged=prior.name in unchanged,
        prior_sha256=hashlib.sha256(prior.read_bytes()).hexdigest(),new_sha256=hashlib.sha256(new.read_bytes()).hexdigest()))
(base.parent/'audit/comparison_to_prior_HPC.json').write_text(json.dumps(dict(execution=execution,
    prior_job_id='24054963',status='Unchanged scopes concordant; corrected scopes require review',comparisons=checks),indent=2)+'\n')
