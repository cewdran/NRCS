#!/usr/bin/env python3
"""Analytical fixtures to validate custom routines on the allocated Puma node."""
from hpc_guard import require_hpc
execution=require_hpc()
from pathlib import Path
import sys
import numpy as np
from common import require,save
from statistics_core import (hellinger_frame,treatment_test,paired_change_test,
    feature_treatment_contrast,paired_feature_contrast,holm_adjust,hellinger_pca)
checks=[]
def check(label,condition):
    require(bool(condition),'HPC validation failed: '+label)
    checks.append({'check':label,'passed':True})
check('Known Hellinger coordinates',np.allclose(hellinger_frame([[9,16]]),[[0.6,0.8]]))
try:hellinger_frame([[0,0]])
except ValueError:check('Reject all-zero profile',True)
else:check('Reject all-zero profile',False)
Y=np.array([[1.,0.]]*3+[[0.,1.]]*3)
meta=[{'biological_plot_id':'P'+str(i),'collection_date':'2020-01-01','treatment':'A' if i<3 else 'B'} for i in range(6)]
t=treatment_test(Y,meta,seed=1)
check('Exact 3+3 allocation space',t['status']=='TESTED' and t['assignment_space']==20 and t['permutations']==20)
check('Two symmetric perfect-separation allocations',np.isclose(t['p_value'],0.1) and np.isclose(t['partial_r2'],1))
repeated=[];values=[]
for row,y in zip(meta,Y):
    for date in ['2020-01-01','2021-01-01']:
        repeated.append(dict(row,collection_date=date));values.append(y)
u=treatment_test(values,repeated,seed=1)
check('Repeated visits do not multiply independent plots',u['n_independent_plots']==6 and u['assignment_space']==20 and np.isclose(u['p_value'],t['p_value']))
fixed=[];fixedvalues=[]
for row,y in zip(meta,Y):
    for date in (['2020-01-01','2021-01-01'] if row['treatment']=='A' else ['2020-01-01']):
        fixed.append(dict(row,collection_date=date));fixedvalues.append(y)
f=treatment_test(fixedvalues,fixed,seed=1)
check('Labels do not cross unequal visit patterns',f['status']=='SKIPPED' and f['assignment_space']==1 and f['movable_label_plot_count']==0)
x=paired_change_test([[0.125,0.0]]*6,seed=1)
check('Exact six-pair sign symmetry',x['status']=='TESTED' and x['assignment_space']==64 and np.isclose(x['p_value'],2/64))
z=paired_change_test([[0.0,0.0]]*6,seed=1)
check('Zero-change test is explicit',z['status']=='SKIPPED' and z['all_deltas_zero'])
a=feature_treatment_contrast([0.]*3+[0.125]*3,['A']*3+['B']*3,'A','B',seed=1)
check('Scalar exact treatment sign and p',np.isclose(a['p_value'],0.1) and np.isclose(a['mean_difference'],0.125))
check('Degenerate Welch interval exposed',a['ci_low'] is None and a['ci_high'] is None)
b=paired_feature_contrast([0.125]*6,seed=1)
check('Scalar sign-flip test agrees',np.isclose(b['p_value'],2/64) and np.isclose(b['mean_change'],0.125))
check('Known Holm family adjustment',np.allclose(holm_adjust([0.01,0.04,0.03,1.0]),[0.04,0.09,0.09,1.0]))
check('Holm preserves missing value',holm_adjust([None])[0] is None)
Q=np.array([[1.,0.],[0.,1.],[np.sqrt(0.5),np.sqrt(0.5)]])
p=hellinger_pca(Q);coords=np.asarray(p['coordinates'])
check('Two-axis ordination preserves three-point distances',all(np.isclose(np.linalg.norm(Q[i]-Q[j]),np.linalg.norm(coords[i]-coords[j])) for i in range(3) for j in range(3)))
save(Path(sys.argv[1])/'audit/STATISTICAL_SELF_CHECKS.json',{'execution':execution,'type':'Analytical fixture validation on Puma, not NRCS study results','checks':checks,'passed':True})
print('HPC statistical self-checks passed:',len(checks))
