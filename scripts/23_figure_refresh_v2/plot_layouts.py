"""Rendering functions. Production callers supply frozen tables on Puma.

No file loading, statistical fitting, CLR calculation or ordination lives here.
Synthetic layout tests may call these functions with invented values.
"""
import datetime
import textwrap
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

COLORS = {'T1':'#667785','T2':'#D48B31','T3':'#4D85A5','T4':'#8F68A8',
          'T7':'#D48B31','T8':'#16877D','T9':'#8F68A8','C':'#667785',
          'A':'#D48B31','B':'#16877D','D':'#8F68A8','Control':'#667785',
          'CC':'#16877D','CCS':'#AD6822'}

def truth(v):
    return str(v).lower() in ('true','1','1.0','yes')

def date(v):
    return datetime.date.fromisoformat(v).strftime('%d %b %Y')

def style():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,
        'axes.titlesize':8,'axes.labelsize':8,'xtick.labelsize':7,
        'ytick.labelsize':7,'legend.fontsize':7,'svg.fonttype':'none',
        'pdf.fonttype':42,'ps.fonttype':42})

def protect(fig, artist, label):
    if not hasattr(fig, '_nrcs_regions'):
        fig._nrcs_regions = []
    fig._nrcs_regions.append((label, artist))
    return artist

def text(fig, x, y, value, label, **kw):
    return protect(fig, fig.text(x,y,value,**kw),label)

def skin(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',color='#E3E6E8',lw=.4)
    ax.set_axisbelow(True)

def codes_legend(fig, codes, xy, columns=4, size=7, label='legend'):
    leg=fig.legend(handles=[Line2D([],[],marker='o',ls='',color=COLORS[c],
        label=c,markersize=4) for c in codes],loc='lower left',
        bbox_to_anchor=xy,ncol=columns,frameon=False,fontsize=size,
        handletextpad=.4,columnspacing=.9,borderaxespad=0)
    return protect(fig,leg,label)

def community(spec, kind, rows, eigen):
    """Dates are rows; bacteria and archaea are columns. Coordinates are reused."""
    style()
    dates=spec['dates']; nr=len(dates); codes=list(spec['treatments'].values())
    fig,axs=plt.subplots(nr,2,figsize=(7.2,2.0*nr+1.15),squeeze=False)
    fig.subplots_adjust(left=.11,right=.97,top=.89,bottom=.20 if nr==3 else .15,wspace=.43,hspace=.9)
    title={'Shannon':'Observed diversity at each collection event',
           'depth':'Target-read depth and retention',
           'PCoA':'Descriptive community similarity within each event'}[kind]
    text(fig,.11,.98,spec['farm']+' | '+spec['label']+'\n'+title,'title',
         va='top',fontsize=10,fontweight='bold')
    for j,domain in enumerate(('bacteria','archaea')):
        domain_rows=[r for r in rows if r['microbial_domain']==domain]
        ymax=max([float(r['Shannon']) for r in domain_rows if truth(r['figure_primary'])] or [1])
        for i,d in enumerate(dates):
            ax=axs[i,j]; group=[r for r in domain_rows if r['collection_date']==d]
            primary=[r for r in group if truth(r['figure_primary'])]
            primary.sort(key=lambda r:(r['display_code'],float(r['biological_replicate']),r['analysis_sample']))
            protect(fig,ax.set_title(chr(65+2*i+j)+'  '+date(d)+' | '+domain+'; n = '+str(len(primary)),
                    loc='left',pad=8,fontsize=7.6),'panel title')
            if kind in ('Shannon','depth'):
                for k,code in enumerate(codes):
                    sub=[r for r in primary if r['display_code']==code]
                    xx=k+np.linspace(-.12,.12,len(sub)) if len(sub)>1 else [k]*len(sub)
                    yy=[float(r['Shannon' if kind=='Shannon' else 'target_read_count']) for r in sub]
                    for x,y,r in zip(xx,yy,sub):
                        ax.scatter(x,y,s=18,facecolor=COLORS[code] if truth(r['saved_alpha_min100']) else 'white',
                                   edgecolor=COLORS[code],lw=.8,zorder=3)
                    if kind=='Shannon' and yy:
                        ax.plot([k-.20,k+.20],[np.median(yy)]*2,color=COLORS[code],lw=1.4)
                ax.set_xticks(range(len(codes)),codes);ax.set_xlim(-.5,len(codes)-.5)
                if kind=='Shannon':
                    ax.set_ylim(-.06*ymax,1.14*ymax)
                    protect(fig,ax.set_ylabel('Observed ASV Shannon'),'axis label')
                else:
                    excluded=[r for r in group if not truth(r['figure_primary'])]
                    for r in excluded:
                        ax.scatter(codes.index(r['display_code']),float(r['target_read_count']),
                                   marker='x',color=COLORS[r['display_code']],s=24,zorder=3)
                    values=[float(r['target_read_count']) for r in group]
                    if min(values or [0])<=0:
                        ax.set_yscale('symlog',linthresh=1);ax.set_ylim(-.7,max(values+[100])*2)
                    else:
                        ax.set_yscale('log');ax.set_ylim(min(values+[100])*.45,max(values+[100])*2)
                    ax.axhline(100,color='#777777',ls=':',lw=.7)
                    protect(fig,ax.set_ylabel('Target reads'),'axis label')
                    protect(fig,ax.text(0,-.34,'Retained ≥100 reads: '+str(sum(truth(r['saved_alpha_min100']) for r in primary)),
                                       transform=ax.transAxes,fontsize=7,va='top'),'retention count')
            else:
                for r in primary:
                    ax.scatter(float(r['PCoA1']),float(r['PCoA2']),s=19,
                        facecolor=COLORS[r['display_code']] if truth(r['saved_alpha_min100']) else 'white',
                        edgecolor=COLORS[r['display_code']],lw=.8)
                # Unit aspect is preserved; no coordinate or eigenvalue is fitted here.
                ax.margins(.15);ax.set_aspect('equal',adjustable='box')
                fraction=eigen[(domain,d)]
                if len(fraction):
                    protect(fig,ax.set_xlabel('PCoA1 (%.1f%%)'%(100*fraction[0])),'axis label')
                else:
                    protect(fig,ax.set_xlabel('PCoA1 unavailable'),'axis label')
                protect(fig,ax.set_ylabel('PCoA2'+(' (%.1f%%)'%(100*fraction[1]) if len(fraction)>1 else ' unavailable')),'axis label')
                if len(fraction)<2:
                    ax.text(.02,.05,'One axis' if len(fraction)==1 else 'Single sample',
                            transform=ax.transAxes,fontsize=6.8)
            skin(ax)
    handles=[Line2D([],[],marker='o',color=COLORS[c],ls='',label=c,markersize=4) for c in codes]
    handles.append(Line2D([],[],marker='o',color='#555555',mfc='white',ls='',
                          label='Below saved 100-read threshold',markersize=4))
    if kind=='depth':handles.append(Line2D([],[],marker='x',color='#555555',ls='',label='Excluded primary record'))
    protect(fig,fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.10,.048),
        ncol=3,frameon=False,fontsize=7,columnspacing=1.3),'legend')
    note={'Shannon':'Saved year-cohort tests are reported separately; panels show observed values.',
          'depth':'Dotted line: 100 reads. Feature-depth eligibility is a separate saved flag.',
          'PCoA':'Saved coordinates; separate event/domain geometry. No treatment test is implied.'}[kind]
    text(fig,.11,.017,note,'footer',fontsize=7)
    return fig

def associations(farm, group, pairs):
    style(); nr=1 if len(group)<=2 else 2
    fig=plt.figure(figsize=(7.2,4.7 if nr==1 else 8.3))
    text(fig,.11,.975,farm+' | Descriptive soil associations','title',va='top',fontsize=10,fontweight='bold')
    for i,saved in enumerate(group):
        left=.11+(i%2)*.46
        bottom=.38 if nr==1 else (.62 if i<2 else .18)
        height=.38 if nr==1 else .23
        ax=fig.add_axes([left,bottom,.39,height]); ss=[r for r in pairs if r['selected_test_id']==saved['test_id']]
        codes=list(dict.fromkeys(r['display_code'] for r in ss))
        for c in codes:
            sub=[r for r in ss if r['display_code']==c]
            ax.scatter([float(r['value']) for r in sub],[float(r['frozen_full_reference_CLR']) for r in sub],
                       s=22,color=COLORS[c],edgecolor='white',lw=.4)
        system={'Grape_PetiteSirah':'Petite Sirah','Grape_Grenache':'Grenache',
                'Blackberry_Natchez':'Natchez','Blackberry_Chester':'Chester',
                'OFR_2023_2024_Field':'Oatman 2023–2024 field'}[saved['analytic_system']]
        label=saved['feature_label'].replace('Candidatus ','Ca. ')
        protect(fig,ax.set_title(chr(65+i)+'  '+label+'\n'+system+' | '+date(saved['event']),
                loc='left',fontsize=8,pad=8),'panel title')
        variable=saved['variable']
        if variable=='soil_haney_H2O_Total_Organic_C':variable='Haney H2O total organic C (ppm C)'
        protect(fig,ax.set_xlabel('\n'.join(textwrap.wrap(variable,34,break_long_words=False)),fontsize=7.4),'axis label')
        protect(fig,ax.set_ylabel('Full-reference '+saved['feature_class']+' CLR',fontsize=7.6),'axis label')
        ax.margins(.09);skin(ax)
        stats_y=.205 if nr==1 else bottom-.09
        text(fig,left,stats_y,'n = '+saved['n']+'; rho = '+format(float(saved['rho']),'.3f')+'\nGlobal BY q = 1.0',
             'statistics',fontsize=7.2,va='top')
        codes_legend(fig,codes,(left,.105 if nr==1 else bottom-.145),size=6.8,label='panel legend')
    text(fig,.11,.025 if nr==1 else .017,
         'Across-treatment examples; none survives the saved global BY correction.',
         'footer',fontsize=7.2)
    return fig

def grenache_soil(rows, tests):
    style();fig,axs=plt.subplots(1,2,figsize=(7.2,4.8))
    fig.subplots_adjust(left=.11,right=.97,top=.78,bottom=.37,wspace=.43)
    text(fig,.11,.975,'Page Springs | Grenache measured soil trajectories', 'title',va='top',fontsize=10,fontweight='bold')
    endpoints=['Phosphorus Olsen P, ppm P','Total N, ppm N'];codes=['T1','T7','T8','T9']
    dates=['2023-10-20','2024-08-05']
    for i,(endpoint,ax) in enumerate(zip(endpoints,axs)):
        rr=[r for r in rows if r['endpoint']==endpoint]
        for plot in sorted(set(r['plot'] for r in rr)):
            pp=sorted([r for r in rr if r['plot']==plot],key=lambda r:r['event'])
            assert [r['event'] for r in pp]==dates
            assert len(set(r['treatment'] for r in pp))==1
            ax.plot([0,1],[float(r['value']) for r in pp],color=COLORS[pp[0]['treatment']],marker='o',ms=3,lw=.8)
        protect(fig,ax.set_title(chr(65+i)+'  '+('Olsen P' if i==0 else 'Reported total N')+'; 8 paired plots',loc='left',pad=8),'panel title')
        protect(fig,ax.set_ylabel('Olsen P (ppm P)' if i==0 else 'Reported total N (ppm N)'),'axis label')
        ax.set_xticks([0,1],[date(d) for d in dates],fontsize=7);ax.set_xlim(-.12,1.12);ax.margins(y=.15);skin(ax)
        tt={r['estimand']:r for r in tests if r['endpoint']==endpoint}
        text(fig,.11+i*.505,.235,'Between-treatment comparisons\nAverage level: Holm p = '+format(float(tt['average_level']['holm_p_value']),'.3g')+
             '\nTemporal change: Holm p = '+format(float(tt['temporal_change']['holm_p_value']),'.3g'),
             'statistics',fontsize=7,va='top')
    codes_legend(fig,codes,(.30,.075))
    text(fig,.11,.025,'Two plots per treatment. Total-N method was not specified in the reports.', 'footer',fontsize=7)
    return fig

def whipstone_soil(rows):
    style();fig,axs=plt.subplots(3,2,figsize=(7.2,8.2))
    fig.subplots_adjust(left=.11,right=.97,top=.88,bottom=.14,wspace=.45,hspace=.65)
    text(fig,.11,.975,'Whipstone | Measured soil context in November 2023','title',va='top',fontsize=10,fontweight='bold')
    systems=['Annual crops','Chester','Natchez'];codes=['C','A','B','D']
    for i,system in enumerate(systems):
        for j,predictor in enumerate(['1:1 Soil pH','Organic Carbon, % C']):
            ax=axs[i,j];rr=[r for r in rows if r['system']==system and r['predictor']==predictor]
            labels=[]
            for k,code in enumerate(codes):
                ss=[r for r in rr if r['treatment']==code]; yy=[float(r['value']) for r in ss]
                assert len(yy)==(3 if i==0 else 2)
                ax.scatter(k+np.linspace(-.10,.10,len(yy)),yy,s=19,color=COLORS[code],zorder=3)
                ax.plot([k-.22,k+.22],[np.median(yy)]*2,color=COLORS[code],lw=1.4)
                labels.append(code+'\nn = '+str(len(yy)))
            protect(fig,ax.set_title(chr(65+2*i+j)+'  '+system+' | 16 Nov 2023',loc='left',pad=8),'panel title')
            protect(fig,ax.set_ylabel('1:1 soil pH' if j==0 else 'Organic carbon (% C)'),'axis label')
            ax.set_xticks(range(4),labels);ax.set_xlim(-.5,3.5);ax.margins(y=.18);skin(ax)
    text(fig,.11,.060,'C: control   A: blood + feather meal   B: cover crop   D: meals + cover crop', 'treatments',fontsize=7)
    text(fig,.11,.025,'Points are physical plots; horizontal lines show observed medians.', 'footer',fontsize=7)
    return fig

def inspect_layout(fig):
    from layout_audit import audit_rendered_text
    result=audit_rendered_text(fig)
    renderer=fig.canvas.get_renderer(); regions=[]; overlaps=[]
    for label,artist in getattr(fig,'_nrcs_regions',[]):
        if artist.get_visible():regions.append((label,artist.get_window_extent(renderer)))
    for i,(a,bb) in enumerate(regions):
        for b,cc in regions[i+1:]:
            width=min(bb.x1,cc.x1)-max(bb.x0,cc.x0)
            height=min(bb.y1,cc.y1)-max(bb.y0,cc.y0)
            if width>1 and height>1:overlaps.append({'first':a,'second':b,'width_px':width,'height_px':height})
    result['protected_region_overlaps']=overlaps
    # Test full marker footprints, rather than only their centers, against axes.
    clipped=[]
    for ai,ax in enumerate(fig.axes):
        bbox=ax.get_window_extent(renderer)
        for collection in ax.collections:
            offsets=collection.get_offsets()
            if len(offsets)==0:continue
            xy=collection.get_offset_transform().transform(offsets)
            sizes=collection.get_sizes(); radius=(float(max(sizes))**.5/2+1)*fig.dpi/72 if len(sizes) else 0
            for x,y in xy:
                if x-radius<bbox.x0-.5 or x+radius>bbox.x1+.5 or y-radius<bbox.y0-.5 or y+radius>bbox.y1+.5:
                    clipped.append({'axes':ai,'x_px':float(x),'y_px':float(y),'radius_px':radius})
    result['clipped_marker_footprints']=clipped
    return result
