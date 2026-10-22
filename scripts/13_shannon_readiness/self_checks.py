#!/usr/bin/env python3
"""Meaningful synthetic scientific checks, permitted only on real Puma."""
from hpc_guard import require_hpc
execution = require_hpc()  # Refuse locally before fixtures or scientific imports.

from pathlib import Path
import json
import sys
from baseline_core import (ARM_SKIP, fit_baselines, select_training, predict,
                           compare_fixed_errors, require, stable_digest)


def main():
    run = Path(sys.argv[1]).resolve(strict=True)
    arms = ('A', 'B', 'C', 'D')
    dates = ('2023-11-16', '2024-07-06', '2024-08-14')
    cutoff = '2025-01-01'
    checks = []
    def row(plot, arm, when, value, depth=True):
        return dict(plot=plot, treatment=arm, date=when, Shannon=value,
            analysis_sample=plot+'_'+when, sample_key=plot+'|'+when,
            primary_included=True, descriptive_reference=False,
            target_read_count=100, depth_sensitivity_included=depth)
    uneven = [row('P1','A',dates[0],0.0), row('P1','A',dates[1],4.0),
              row('P2','B',dates[0],10.0)]
    fit = fit_baselines(select_training(uneven,'PRIMARY',dates,cutoff),arms,dates,cutoff)
    require(fit['global_training_mean'] == 6.0,
            'Unequal visits weighted plots by visits instead of one mean per plot.')
    require(fit['method_status']['EQUAL_PLOT_TRAINING_ARM_MEAN'] == ARM_SKIP,
            'Thin/unbalanced arms were silently estimated.')
    checks.append(dict(check='equal_plot_mean_with_unequal_visits',status='PASS',
        detail='P1 mean 2 and P2 mean 10 receive equal plot weight: global mean 6, not row mean.'))
    balanced = uneven + [row('P4','B',dates[0],2.0),row('P5','C',dates[0],3.0),
        row('P6','C',dates[0],5.0),row('P7','D',dates[0],4.0),row('P8','D',dates[0],6.0),
        row('P9','A',dates[0],10.0)]
    arm_fit=fit_baselines(select_training(balanced,'PRIMARY',dates,cutoff),arms,dates,cutoff)
    require(arm_fit['arm_plot_counts']=={arm:2 for arm in arms}
            and arm_fit['arm_training_means']['A']==6.0,
            'Balanced arm mean did not give each training plot one mean/weight.')
    checks.append(dict(check='balanced_arm_mean_equal_plot_weighting',status='PASS',
        detail='Four arms with two plots each; unequal A visits retain equal-plot arm mean 6.'))
    future = row('P3','C','2025-08-17',1000.0)
    history = uneven + [future]
    selected = select_training(history,'PRIMARY',dates,cutoff)
    require(stable_digest(selected) == stable_digest(select_training(uneven,'PRIMARY',dates,cutoff)),
            '2025 event changed selected training rows.')
    try:
        fit_baselines([dict(plot='P3',treatment='C',date='2025-08-17',Shannon=1000.0)],
                      arms,dates,cutoff)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('Core fitting accepted a held-out event as training.')
    checks.append(dict(check='whole_2025_event_never_training',status='PASS',
        detail='Date partition excludes future rows and fitting independently rejects a 2025 row.'))
    sensitivity = [row('P1','A',dates[0],2.0,True),row('P1','A',dates[1],8.0,False),
                   row('P1','A','2025-08-17',9.0,True)]
    before = fit_baselines(select_training(sensitivity,'DEPTH_SENSITIVITY',dates,cutoff),arms,dates,cutoff)
    sensitivity[-1]['depth_sensitivity_included'] = False
    sensitivity[-1]['Shannon'] = 1234.0
    after = fit_baselines(select_training(sensitivity,'DEPTH_SENSITIVITY',dates,cutoff),arms,dates,cutoff)
    require(before == after and after['plot_summaries']['P1']['last_prior_date'] == dates[0],
            'Future sensitivity flag/response changed historical fit or older eligible origin.')
    older=predict(after,[dict(plot='P1',treatment='A',sample_key='P1|2025-08-17',
                             analysis_sample='P1_2025-08-17')],'2025-08-17',cutoff)
    persistence=[r for r in older if r['method']=='PERSISTENCE']
    require(len(persistence)==1 and persistence[0]['predicted_Shannon']==2.0
            and persistence[0]['last_prior_date']==dates[0],
            'Persistence borrowed an excluded newer observation.')
    checks.append(dict(check='sensitivity_training_independent_of_2025_flags',status='PASS',
        detail='Changing held-out depth and outcome leaves training membership/fit unchanged; older prior retained.'))
    left=[dict(biological_plot_id='P1',held_out_date='2025-08-17',observed_Shannon=4.0,
               predicted_Shannon=3.0,absolute_error=1.0,squared_error=1.0,last_prior_date=dates[1]),
          dict(biological_plot_id='P2',held_out_date='2025-08-17',observed_Shannon=4.0,
               predicted_Shannon=104.0,absolute_error=100.0,squared_error=10000.0,last_prior_date=dates[1])]
    right=[dict(biological_plot_id='P1',held_out_date='2025-08-17',observed_Shannon=4.0,
                predicted_Shannon=2.0,absolute_error=2.0,squared_error=4.0,last_prior_date=dates[0])]
    paired, summary = compare_fixed_errors(left,right)
    require(summary['n_common_plots']==1 and summary['MAE_A']==1.0 and summary['MAE_B']==2.0
            and summary['delta_RMSE']==1.0 and paired[0]['delta_abs_error']==1.0,
            'Paired metrics used noncommon plots or wrong sign.')
    checks.append(dict(check='paired_metrics_exact_common_plots',status='PASS',
        detail='Noncommon high-error plot excluded; B minus A error sign verified; original predictions unchanged.'))
    target = run/'audit/self_checks.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(dict(status='PASS',execution=execution,checks=checks,
        scientific_fixtures_executed_on_puma=True,fixture_data_are_synthetic=True,
        real_source_data_used=False),indent=2,sort_keys=True,allow_nan=False)+'\n')
    temporary.replace(target)


if __name__ == '__main__':
    main()
