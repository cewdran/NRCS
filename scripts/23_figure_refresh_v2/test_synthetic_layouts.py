"""Geometry checks only. Invented values; no NRCS scientific input is read."""
from pathlib import Path
import json,sys
from plot_layouts import community,associations,grenache_soil,whipstone_soil,inspect_layout,plt

OUT=Path(sys.argv[1]);OUT.mkdir(parents=True,exist_ok=True)

def verify(fig,name):
    result=inspect_layout(fig)
    (OUT/(name+'.json')).write_text(json.dumps(result,indent=2))
    fig.savefig(OUT/(name+'.png'),dpi=150)
    plt.close(fig)
    assert not result['text_outside_canvas'],(name,'outside',result['text_outside_canvas'])
    assert not result['protected_region_overlaps'],(name,'overlaps',result['protected_region_overlaps'])
    assert not result['clipped_marker_footprints'],(name,'clipped',result['clipped_marker_footprints'])
    print('PASS',name)

dates=['2023-10-20','2024-07-06','2024-08-14','2025-10-10']
spec={'dates':dates,'farm':'Synthetic farm','label':'Long cultivar layout stress test',
      'treatments':{str(i):c for i,c in enumerate(['T1','T2','T3','T4'])}}
rows=[];eigen={}
for di,dom in enumerate(['bacteria','archaea']):
    for i,d in enumerate(dates):
        eigen[(dom,d)]=[] if i==2 else ([1.] if i==1 else [.613,.187])
        for k,c in enumerate(['T1','T2','T3','T4'] if i!=2 else ['T1']):
            for rep in range(1,3 if i!=2 else 2):
                rows.append({'microbial_domain':dom,'collection_date':d,'figure_primary':True,
                    'display_code':c,'biological_replicate':str(rep),'analysis_sample':f'TOY_{di}_{i}_{k}_{rep}',
                    'Shannon':0 if (di,i,k,rep)==(1,3,0,1) else 6.01+rep*.01-k*.006,
                    'target_read_count':0 if (di,i,k,rep)==(1,3,0,1) else (3 if k==0 else 10000*(k+1)),
                    'saved_alpha_min100':k!=0,'PCoA1':0 if i==2 else -.4+k*.2,
                    'PCoA2':0 if i in (1,2) else .17*rep-.2})
for kind in ('Shannon','depth','PCoA'):verify(community(spec,kind,rows,eigen),'community_'+kind)
three=dict(spec,dates=dates[:3])
for kind in ('Shannon','depth','PCoA'):
    verify(community(three,kind,[r for r in rows if r['collection_date'] in three['dates']],eigen),'three_events_'+kind)

for n in (2,3,4):
    group=[];pairs=[]
    for i in range(n):
        group.append({'test_id':str(i),'analytic_system':'OFR_2023_2024_Field' if i==2 else 'Grape_PetiteSirah',
            'feature_label':'Candidatus Nitrosocosmicus','event':'2024-06-06','feature_class':'Genus',
            'variable':'soil_haney_H2O_Total_Organic_C' if i==2 else 'Phosphorus Olsen P, ppm P',
            'n':'8','rho':'0.999'})
        for k,c in enumerate(['Control','CC','CCS'] if i==2 else ['T1','T2','T3','T4']):
            pairs.append({'selected_test_id':str(i),'display_code':c,'value':str(12+k),'frozen_full_reference_CLR':str(2+k)})
    verify(associations('Synthetic',group,pairs),'associations_'+str(n))

soil=[];tests=[]
for endpoint in ['Phosphorus Olsen P, ppm P','Total N, ppm N']:
    for estimand in ['average_level','temporal_change']:tests.append({'endpoint':endpoint,'estimand':estimand,'holm_p_value':'.475'})
    for k,c in enumerate(['T1','T7','T8','T9']):
        for rep in range(2):
            for i,d in enumerate(['2023-10-20','2024-08-05']):soil.append({'endpoint':endpoint,'plot':c+str(rep),'event':d,'treatment':c,'value':str(15+k+rep+i)})
verify(grenache_soil(soil,tests),'grenache_soil')
wh=[]
for i,system in enumerate(['Annual crops','Chester','Natchez']):
    for predictor in ['1:1 Soil pH','Organic Carbon, % C']:
        for c in ['C','A','B','D']:
            for rep in range(3 if i==0 else 2):wh.append({'system':system,'predictor':predictor,'treatment':c,'value':str(8.1+.05*rep)})
verify(whipstone_soil(wh),'whipstone_soil')
# Regression gates must actually reject the defects identified in v2.
from plot_layouts import protect
bad,ax=plt.subplots()
protect(bad,bad.text(.2,.6,'Legend text'),'legend')
protect(bad,bad.text(.2,.6,'Statistics text'),'statistics')
ax.scatter([0,1],[0,1],s=25);ax.set_xlim(0,1);ax.set_ylim(0,1)
checks=inspect_layout(bad);plt.close(bad)
assert checks['protected_region_overlaps'] and checks['clipped_marker_footprints']
print('PASS overlap and clipped-marker rejection')
print('All synthetic geometry tests passed; no scientific dataset was loaded.')
