"""Puma-only layout refresh using immutable saved scores/models/coordinates."""

from pathlib import Path
import json,datetime,math
from hpc_guard import require_hpc

DOMAINS=('bacteria','archaea')

SHARED_DATES=('2023-11-20','2024-06-06','2024-12-06')

SYSTEM='OFR_2023_2024_Field'

SEPARATE='OFR_2025_Field'

COLORS={'Control':'#667785','CC':'#16877d','CCS':'#ad6822'}

def require(condition,message):
 if not condition:raise RuntimeError(message)

def truth(value):return str(value).strip().lower() in ('true','1','yes')

def save_json(path,data):path.write_text(json.dumps(data,indent=2)+'\n')

def undrawn_tick_labels(fig,renderer):
 # Pinned Matplotlib 3.10.8 Axis.draw uses exactly _update_ticks() to select
 # the ticks it draws. Cached Text visibility alone includes out-of-view ticks.
 # Classify only known tick-label objects; all other text stays fully audited.
 omitted={}
 for panel,ax in enumerate(fig.axes):
  for coordinate,axis in [('x',ax.xaxis),('y',ax.yaxis)]:
   update=getattr(axis,'_update_ticks',None)
   require(callable(update),'Pinned Matplotlib axis draw-list method unavailable')
   require(hasattr(axis,'majorTicks') and hasattr(axis,'minorTicks'),'Pinned Matplotlib tick caches unavailable')
   drawn=update() if axis.get_visible() else []
   require(isinstance(drawn,(list,tuple)),'Pinned Matplotlib axis draw-list result invalid')
   cached=list(axis.majorTicks)+list(axis.minorTicks)
   require(all(hasattr(t,'label1') and hasattr(t,'label2') for t in cached),'Pinned Matplotlib tick-label structure invalid')
   cached_ids={id(t) for t in cached};drawn_ids={id(t) for t in drawn}
   require(drawn_ids.issubset(cached_ids),'Pinned Matplotlib drawn tick absent from cache')
   limits=[float(v) for v in axis.get_view_interval()]
   for tick in cached:
    if id(tick) in drawn_ids:continue
    for side,label in [('label1',tick.label1),('label2',tick.label2)]:
     if not label.get_visible() or not label.get_text().strip():continue
     omitted[id(label)]={'text':label.get_text(),'bounds_pixels':list(label.get_window_extent(renderer).bounds),'axes':panel,'coordinate':coordinate,'side':side,'tick_location':float(tick.get_loc()),'axis_view_interval':limits,'reason':'Tick label absent from the pinned Axis.draw tick list'}
 return omitted

def neighbor_axis_text_clearance(fig,renderer,minimum_points=2):
    """Audit drawn axis decoration text against OTHER visible plot rectangles.

    A hidden axes is not drawn. An axes with axison=False still contains data
    and titles, but its axis labels/ticks/offset text are not drawn. Completely
    off-canvas plot rectangles cannot visibly collide with a label; they remain
    possible owners of visible text. Arbitrary data annotations are excluded.
    """
    def demand(condition,message):
        if not condition:raise RuntimeError('Neighbor-axis text clearance: '+message)

    def finite(value,context):
        try:number=float(value)
        except (TypeError,ValueError,OverflowError) as exc:
            raise RuntimeError('Neighbor-axis text clearance: invalid '+context) from exc
        demand(math.isfinite(number),'nonfinite '+context)
        return number

    def rectangle(box,context,positive_area=False):
        coordinates=tuple(finite(getattr(box,name),context+'.'+name) for name in ('x0','y0','x1','y1'))
        x0,y0,x1,y1=coordinates
        demand(x1>=x0 and y1>=y0,'reversed '+context)
        if positive_area:demand(x1>x0 and y1>y0,'zero-area '+context)
        return coordinates

    def bounds(rect):
        x0,y0,x1,y1=rect
        return [x0,y0,x1-x0,y1-y0]

    minimum=finite(minimum_points,'minimum_points')
    demand(minimum>0,'minimum_points must be positive')
    converter=getattr(renderer,'points_to_pixels',None)
    demand(callable(converter),'renderer points_to_pixels unavailable')
    required_pixels=finite(converter(minimum),'minimum_pixels')
    demand(required_pixels>0,'minimum_pixels must be positive')
    width,height=(finite(v,'canvas_extent') for v in fig.canvas.get_width_height())
    demand(width>0 and height>0,'canvas extent must be positive')
    plot_rectangles=[];skipped_axes=[];visible_owners=[]
    for index,ax in enumerate(fig.axes):
        if not ax.get_visible():
            skipped_axes.append({'axes':index,'reason':'Axes artist is hidden'})
            continue
        rect=rectangle(ax.get_window_extent(renderer),'axes '+str(index)+' plot rectangle',True)
        visible_owners.append((index,ax))
        x0,y0,x1,y1=rect
        clipped=(max(0.0,x0),max(0.0,y0),min(width,x1),min(height,y1))
        if clipped[2]<=clipped[0] or clipped[3]<=clipped[1]:
            skipped_axes.append({'axes':index,'reason':'Plot rectangle is completely outside the canvas; target only is omitted','bounds_pixels':bounds(rect)})
            continue
        plot_rectangles.append((index,clipped))
    selected=[]
    for index,ax in visible_owners:
        candidates=[]
        for name in ('title','_left_title','_right_title'):
            demand(hasattr(ax,name),'pinned axes title artist unavailable: '+name)
            candidates.append(('title.'+name,getattr(ax,name)))
        if ax.axison:
            for coordinate,axis in (('x',ax.xaxis),('y',ax.yaxis)):
                if not axis.get_visible():continue
                candidates.append((coordinate+'.label',axis.label))
                offset=getattr(axis,'get_offset_text',None)
                demand(callable(offset),'pinned axis offset-text API unavailable')
                candidates.append((coordinate+'.offset_text',offset()))
                update=getattr(axis,'_update_ticks',None)
                demand(callable(update),'pinned axis draw-list API unavailable')
                drawn=update()
                demand(isinstance(drawn,(list,tuple)),'pinned axis draw-list result invalid')
                demand(hasattr(axis,'majorTicks') and hasattr(axis,'minorTicks'),'pinned tick caches unavailable')
                cached=list(axis.majorTicks)+list(axis.minorTicks)
                demand({id(t) for t in drawn}.issubset({id(t) for t in cached}),'drawn tick absent from pinned cache')
                for tick in drawn:
                    demand(hasattr(tick,'label1') and hasattr(tick,'label2'),'pinned drawn tick-label structure invalid')
                    candidates.extend(((coordinate+'.tick.label1',tick.label1),(coordinate+'.tick.label2',tick.label2)))
        seen=set()
        for kind,artist in candidates:
            if id(artist) in seen:continue
            seen.add(id(artist))
            if not artist.get_visible() or not artist.get_text().strip():continue
            rect=rectangle(artist.get_window_extent(renderer),'axes '+str(index)+' '+kind)
            selected.append({'axes':index,'kind':kind,'text':artist.get_text(),'rect':rect})
    violations=[];checked_pairs=0;minimum_observed=None
    for item in selected:
        tx0,ty0,tx1,ty1=item['rect']
        for target,rect in plot_rectangles:
            if target==item['axes']:continue
            px0,py0,px1,py1=rect
            dx=max(px0-tx1,tx0-px1,0.0)
            dy=max(py0-ty1,ty0-py1,0.0)
            distance=finite(math.hypot(dx,dy),'bbox distance')
            clearance_points=finite(distance/required_pixels*minimum,'clearance_points')
            checked_pairs+=1
            minimum_observed=clearance_points if minimum_observed is None else min(minimum_observed,clearance_points)
            if distance+1e-8<required_pixels:
                violations.append({'owning_axes':item['axes'],'target_axes':target,'kind':item['kind'],'text':item['text'],'text_bounds_pixels':bounds(item['rect']),'target_plot_bounds_pixels':bounds(rect),'clearance_pixels':distance,'clearance_points':clearance_points,'minimum_points':minimum})
    return {'status':'PASS' if not violations else 'FAILED','minimum_clearance_points':minimum,'minimum_clearance_pixels':required_pixels,'minimum_observed_clearance_points':minimum_observed,'comparison_tolerance_pixels':1e-8,'selection':'Visible axis labels, offset text, titles and exact pinned Axis.draw tick labels against OTHER axes visible plot rectangles; arbitrary data annotations excluded','checked_pairs':checked_pairs,'axes_plot_rectangles':[{'axes':index,'visible_bounds_pixels':bounds(rect)} for index,rect in plot_rectangles],'selected_text':[{'axes':v['axes'],'kind':v['kind'],'text':v['text'],'bounds_pixels':bounds(v['rect'])} for v in selected],'skipped_axes':skipped_axes,'violations':violations,'scientific_values_changed':False}

def circle_scatter_artist_records(ax,renderer):
    """Read existing circle-scatter geometry without changing any artist data."""
    def demand(condition,message):
        if not condition:raise RuntimeError('Circle-scatter geometry: '+message)
    def finite(value,context):
        try:number=float(value)
        except (TypeError,ValueError,OverflowError) as exc:
            raise RuntimeError('Circle-scatter geometry: invalid '+context) from exc
        demand(math.isfinite(number),'nonfinite '+context)
        return number
    convert=getattr(renderer,'points_to_pixels',None)
    demand(callable(convert),'renderer points_to_pixels unavailable')
    demand(finite(convert(1.0),'pixels per point')>0,'pixels per point must be positive')
    demand(hasattr(ax,'transData') and hasattr(ax,'collections'),'required scatter axes APIs unavailable')
    records=[]
    for collection_index,collection in enumerate(ax.collections):
        if not collection.get_visible():continue
        for name in ('get_offsets','get_paths','get_sizes','get_linewidths','get_offset_transform'):
            demand(callable(getattr(collection,name,None)),'required scatter collection API unavailable: '+name)
        offsets=list(collection.get_offsets())
        demand(bool(offsets),'visible scatter collection has no offsets')
        paths=list(collection.get_paths())
        demand(len(paths)==1,'expected one normalized circle path')
        box=paths[0].get_extents()
        marker_bounds=[finite(getattr(box,name),'normalized marker '+name) for name in ('x0','y0','x1','y1')]
        demand(all(abs(got-want)<=1e-8 for got,want in zip(marker_bounds,(-.5,-.5,.5,.5))),'unsupported normalized marker bounds; explicit circle marker required')
        sizes=[finite(v,'marker size') for v in collection.get_sizes()]
        widths=[finite(v,'marker linewidth') for v in collection.get_linewidths()]
        demand(len(sizes) in (1,len(offsets)) and all(v>0 for v in sizes),'marker size array must be positive and broadcastable')
        demand(len(widths) in (1,len(offsets)) and all(v>=0 for v in widths),'marker linewidth array must be nonnegative and broadcastable')
        transform=collection.get_offset_transform()
        demand(transform is ax.transData,'scatter offsets must use this axes data transform')
        demand(callable(getattr(transform,'transform',None)),'scatter offset transform unavailable')
        for point_index,offset in enumerate(offsets):
            demand(len(offset)==2,'scatter offset must have two coordinates')
            data=[finite(v,'scatter data coordinate') for v in offset]
            display=transform.transform(tuple(data))
            demand(len(display)==2,'transformed scatter center must have two coordinates')
            center=[finite(v,'display center') for v in display]
            size=sizes[0 if len(sizes)==1 else point_index]
            linewidth=widths[0 if len(widths)==1 else point_index]
            radius_points=finite(math.sqrt(size)/2+linewidth/2,'marker and stroke radius')
            radius_pixels=finite(convert(radius_points),'marker radius pixels')
            demand(radius_pixels>0,'marker radius pixels must be positive')
            records.append({'collection':collection_index,'point':point_index,'data_coordinates':data,'center_pixels':center,'size_points_squared':size,'linewidth_points':linewidth,'radius_including_stroke_points':radius_points,'radius_including_stroke_pixels':radius_pixels,'normalized_marker_extents':marker_bounds})
    demand(bool(records),'visible axes has no supported scatter points')
    return records

def pad_scatter_axes_in_points(fig,renderer,center_padding_points=5):
    """Set equal-unit explicit limits with fixed physical center padding."""
    def demand(condition,message):
        if not condition:raise RuntimeError('Physical scatter padding: '+message)
    def finite(value,context):
        try:number=float(value)
        except (TypeError,ValueError,OverflowError) as exc:
            raise RuntimeError('Physical scatter padding: invalid '+context) from exc
        demand(math.isfinite(number),'nonfinite '+context)
        return number
    padding=finite(center_padding_points,'center_padding_points')
    demand(padding>0,'center_padding_points must be positive')
    convert=getattr(renderer,'points_to_pixels',None)
    demand(callable(convert),'renderer points_to_pixels unavailable')
    pixels_per_point=finite(convert(1.0),'pixels per point')
    demand(pixels_per_point>0,'pixels per point must be positive')
    panels=[];skipped=[]
    for index,ax in enumerate(fig.axes):
        if not ax.get_visible():
            skipped.append({'axes':index,'reason':'Axes artist is hidden'});continue
        for name in ('get_window_extent','get_xlim','get_ylim','set_xlim','set_ylim','set_aspect'):
            demand(callable(getattr(ax,name,None)),'required layout API unavailable: '+name)
        box=ax.get_window_extent(renderer)
        x0,y0,x1,y1=[finite(getattr(box,name),'plot '+name) for name in ('x0','y0','x1','y1')]
        width=finite((x1-x0)/pixels_per_point,'plot width points')
        height=finite((y1-y0)/pixels_per_point,'plot height points')
        demand(width>2*padding and height>2*padding,'plot box too small for requested physical padding')
        records=circle_scatter_artist_records(ax,renderer)
        xs=[r['data_coordinates'][0] for r in records];ys=[r['data_coordinates'][1] for r in records]
        xmin,xmax=min(xs),max(xs);ymin,ymax=min(ys),max(ys)
        xspan=finite(xmax-xmin,'data x span');yspan=finite(ymax-ymin,'data y span')
        units=finite(max(xspan/(width-2*padding),yspan/(height-2*padding)),'units per physical point')
        fallback=None
        old_xlim=[finite(v,'existing x limit') for v in ax.get_xlim()]
        old_ylim=[finite(v,'existing y limit') for v in ax.get_ylim()]
        demand(len(old_xlim)==2 and len(old_ylim)==2,'existing limits must be pairs')
        if units==0:
            old_xspan=finite(abs(old_xlim[1]-old_xlim[0]),'existing x span')
            old_yspan=finite(abs(old_ylim[1]-old_ylim[0]),'existing y span')
            units=finite(max(old_xspan/width,old_yspan/height),'zero-span fallback scale')
            demand(units>0,'zero-span fallback requires a finite positive existing view scale')
            fallback='Both coordinate spans are zero; existing finite view scale retained per physical point'
        demand(units>0,'units per point must be positive')
        center_x=finite(xmin+xspan/2,'data center x');center_y=finite(ymin+yspan/2,'data center y')
        half_x=finite(units*width/2,'x half-range');half_y=finite(units*height/2,'y half-range')
        xlimits=[finite(center_x-half_x,'new x lower'),finite(center_x+half_x,'new x upper')]
        ylimits=[finite(center_y-half_y,'new y lower'),finite(center_y+half_y,'new y upper')]
        demand(xlimits[1]>xlimits[0] and ylimits[1]>ylimits[0],'explicit padded limits must have positive spans')
        ax.set_aspect('equal',adjustable='box')
        ax.set_xlim(*xlimits);ax.set_ylim(*ylimits)
        panels.append({'axes':index,'point_count':len(records),'plot_box_points':[width,height],'center_padding_points':padding,'data_extrema':[xmin,xmax,ymin,ymax],'data_center':[center_x,center_y],'units_per_point':units,'prior_xlim':old_xlim,'prior_ylim':old_ylim,'padded_xlim':xlimits,'padded_ylim':ylimits,'aspect':'equal','adjustable':'box','zero_span_fallback':fallback})
    demand(bool(panels),'no visible scatter panels were padded')
    return {'status':'EXPLICIT_EQUAL_UNIT_PHYSICAL_PADDING_APPLIED','center_padding_points':padding,'panels':panels,'skipped_axes':skipped,'scientific_values_changed':False,'method':'units_per_point=max(xspan/(width_points-2*padding),yspan/(height_points-2*padding)); symmetric explicit limits match physical plot-box aspect'}

def own_axes_circle_marker_clearance(fig,renderer,minimum_gap_points=2,minimum_center_padding_points=5):
    """Verify final transformed centers and complete circle/stroke footprints."""
    def demand(condition,message):
        if not condition:raise RuntimeError('Own-axes circle clearance: '+message)
    def finite(value,context):
        try:number=float(value)
        except (TypeError,ValueError,OverflowError) as exc:
            raise RuntimeError('Own-axes circle clearance: invalid '+context) from exc
        demand(math.isfinite(number),'nonfinite '+context)
        return number
    minimum_gap=finite(minimum_gap_points,'minimum_gap_points')
    minimum_center=finite(minimum_center_padding_points,'minimum_center_padding_points')
    demand(minimum_gap>0 and minimum_center>0,'physical clearance requirements must be positive')
    convert=getattr(renderer,'points_to_pixels',None)
    demand(callable(convert),'renderer points_to_pixels unavailable')
    pixels_per_point=finite(convert(1.0),'pixels per point')
    demand(pixels_per_point>0,'pixels per point must be positive')
    required_gap=finite(convert(minimum_gap),'required gap pixels')
    required_center=finite(convert(minimum_center),'required center pixels')
    demand(required_gap>0 and required_center>0,'physical clearance pixels must be positive')
    violations=[];panels=[];skipped=[];point_count=0
    for index,ax in enumerate(fig.axes):
        if not ax.get_visible():
            skipped.append({'axes':index,'reason':'Axes artist is hidden'});continue
        box=ax.get_window_extent(renderer)
        x0,y0,x1,y1=[finite(getattr(box,name),'plot '+name) for name in ('x0','y0','x1','y1')]
        demand(x1>x0 and y1>y0,'plot rectangle must have positive finite dimensions')
        records=circle_scatter_artist_records(ax,renderer)
        for record in records:
            cx,cy=record['center_pixels'];radius=record['radius_including_stroke_pixels']
            center_edges={'left':finite(cx-x0,'left center margin'),'right':finite(x1-cx,'right center margin'),'bottom':finite(cy-y0,'bottom center margin'),'top':finite(y1-cy,'top center margin')}
            ink_edges={edge:finite(value-radius,edge+' ink clearance') for edge,value in center_edges.items()}
            record.update(axes=index,center_edge_margins_points={k:v/pixels_per_point for k,v in center_edges.items()},ink_edge_clearances_points={k:v/pixels_per_point for k,v in ink_edges.items()})
            bad_center=[edge for edge,value in center_edges.items() if value+1e-8<required_center]
            bad_ink=[edge for edge,value in ink_edges.items() if value+1e-8<required_gap]
            if bad_center or bad_ink:
                violations.append(dict(record,center_padding_failed_edges=bad_center,marker_ink_failed_edges=bad_ink))
        point_count+=len(records)
        panels.append({'axes':index,'plot_bounds_pixels':[x0,y0,x1-x0,y1-y0],'points':records})
    demand(bool(panels),'no visible scatter panels were checked')
    return {'status':'PASS' if not violations else 'FAILED','minimum_marker_ink_gap_points':minimum_gap,'minimum_center_padding_points':minimum_center,'comparison_tolerance_pixels':1e-8,'point_count':point_count,'panels':panels,'skipped_axes':skipped,'violations':violations,'marker_geometry':'Explicit circle marker; actual normalized path extents [-0.5,-0.5,0.5,0.5] verified; radius=sqrt(size_points_squared)/2+linewidth_points/2','scientific_values_changed':False}

def export(fig,path):
 require_hpc()
 from matplotlib.text import Text
 fig.canvas.draw();renderer=fig.canvas.get_renderer();width,height=fig.canvas.get_width_height();bad=[];texts=[]
 omitted=undrawn_tick_labels(fig,renderer)
 neighbor=neighbor_axis_text_clearance(fig,renderer,minimum_points=2)
 marker=own_axes_circle_marker_clearance(fig,renderer,minimum_gap_points=2,minimum_center_padding_points=5)
 for artist in fig.findobj(match=Text):
  if not artist.get_visible() or not artist.get_text().strip():continue
  if id(artist) in omitted:continue
  box=artist.get_window_extent(renderer);record={'text':artist.get_text(),'bounds_pixels':list(box.bounds)};texts.append(record)
  if box.x0 < -2 or box.y0 < -2 or box.x1 > width+2 or box.y1 > height+2:bad.append(record)
 overlaps=[]
 for li,legend in enumerate(fig.legends):
  box=legend.get_window_extent(renderer)
  for other in fig.legends[li+1:]:
   if box.overlaps(other.get_window_extent(renderer)):overlaps.append({'legend_pair_overlap':True})
  for text in fig.texts:
   if text.get_visible() and text.get_text().strip() and box.overlaps(text.get_window_extent(renderer)):overlaps.append({'legend_figure_text_overlap':text.get_text()})
  if box.x0 < -2 or box.y0 < -2 or box.x1 > width+2 or box.y1 > height+2:bad.append({'legend_bounds_pixels':list(box.bounds)})
  for i,ax in enumerate(fig.axes):
   axisbox=ax.get_tightbbox(renderer)
   if axisbox is not None and box.overlaps(axisbox):overlaps.append({'legend_bounds_pixels':list(box.bounds),'axes':i,'axes_tight_bounds_pixels':list(axisbox.bounds)})
 audit={'status':'PASS' if not bad and not overlaps and neighbor['status']=='PASS' and marker['status']=='PASS' else 'FAILED','own_axes_circle_marker_clearance':marker,'physical_scatter_padding':getattr(fig,'_physical_scatter_padding_audit',None),'neighbor_axis_text_clearance':neighbor,'canvas_pixels':[width,height],'all_text':texts,'not_drawn_tick_labels':list(omitted.values()),'tick_label_selection':'Exact pinned Matplotlib 3.10.8 Axis.draw _update_ticks list','out_of_canvas':bad,'legend_axes_overlap':overlaps,'scientific_values_changed':False}
 path.with_name(path.name+'_LAYOUT_QC.json').write_text(json.dumps(audit,indent=2)+'\n')
 require(not bad and not overlaps and neighbor['status']=='PASS' and marker['status']=='PASS','Layout containment/legend-gutter/neighbor-clearance/marker-ink check failed: '+path.name)
 for suffix in ['pdf','svg','png','tif']:
  kwargs={'dpi':600 if suffix=='tif' else 300}
  if suffix=='tif':kwargs['pil_kwargs']={'compression':'tiff_lzw'}
  fig.savefig(path.with_suffix('.'+suffix),**kwargs)


def model_for(tests,domain,system,date,threshold):
 c=system+'__'+date
 rows=tests.loc[tests.domain.eq(domain)&tests.cohort.eq(c)&tests.minimum_reads.eq(threshold)&tests.outcome.eq('Shannon')]
 require(len(rows)==1,'Missing/ambiguous saved Shannon model: '+str((domain,c,threshold)))
 row=rows.iloc[0];members=[x for x in str(row.sample_set).split(';') if x]
 require(len(members)==len(set(members))==int(row.n),'Saved model membership/count mismatch')
 return row,members

def style(plt):
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,'axes.titlesize':8.2,'xtick.labelsize':8,'ytick.labelsize':8,'legend.fontsize':8,'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42})

def pretty_q(value):
 try:
  v=float(value)
  return format(v,'.3g') if math.isfinite(v) else 'NA'
 except (TypeError,ValueError):return 'NA'

def title_date(date):return datetime.date.fromisoformat(date).strftime('%d %b %Y')

def draw_shannon(ax,metadata,tests,descs,domain,system,date,np,y_limits=None):
 model,members=model_for(tests,domain,system,date,0);sensitivity,sens_members=model_for(tests,domain,system,date,100)
 data=metadata.set_index('analysis_sample',drop=False).rename_axis(None).loc[members].copy();groups=[x for x in ['Control','CC','CCS'] if x in set(data.treatment_label)]
 nlabels=[]
 for x,treatment in enumerate(groups):
  sub=data.loc[data.treatment_label.eq(treatment)].sort_values(['biological_replicate','analysis_sample']);n=len(sub);jitter=np.linspace(-.11,.11,n) if n>1 else np.array([0.])
  for offset,(_,row) in zip(jitter,sub.iterrows()):
   retained=str(row.analysis_sample) in sens_members
   ax.scatter(x+offset,row.Shannon,s=20,c=COLORS[treatment] if retained else 'white',edgecolors=COLORS[treatment],linewidths=.9,zorder=3,marker='o')
   if not retained:ax.annotate(str(row.analysis_sample)+'*',(x+offset,row.Shannon),xytext=(-6,7),textcoords='offset points',ha='right',va='bottom',fontsize=8)
  d=descs.loc[descs.domain.eq(domain)&descs.cohort.eq(system+'__'+date)&descs.minimum_reads.eq(0)&descs.treatment.eq(treatment)]
  require(len(d)==1,'Saved median disappeared during plotting');med=float(d.iloc[0]['median']);ax.hlines(med,x-.20,x+.20,color=COLORS[treatment],linewidth=1.4,zorder=2)
  label=treatment
  if system==SEPARATE:label='Control/EAST' if treatment=='Control' else 'CC/WEST'
  nlabels.append(label+'\n(n='+str(n)+')')
 ax.set_xticks(range(len(groups)),nlabels);ax.set_xlim(-.45,len(groups)-.55)
 if y_limits:ax.set_ylim(*y_limits)
 ax.grid(axis='y',color='#e0e4e8',linewidth=.5);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
 ax.set_title(title_date(date)+'\nBH q='+pretty_q(model.q_BH)+'; BY q='+pretty_q(model.q_BY),fontsize=8.1,pad=7)
 return {'domain':domain,'cohort':system+'__'+date,'minimum_reads':0,'sample_set':model.sample_set,'saved_p':model.p,'saved_q_BH':model.q_BH,'saved_q_BY':model.q_BY,'BH_family_n':int(model.BH_family_n)}

def combined_outputs(run,cfg):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.lines import Line2D
 style(plt);out=run/'results'/'combined';out.mkdir(parents=True,exist_ok=False)
 metadata={d:pd.read_csv(run/'results'/d/'observed_Shannon_values_and_exact_eligibility.csv',keep_default_na=False) for d in DOMAINS}
 tests=pd.concat([pd.read_csv(run/'results'/d/'saved_Shannon_model_rows.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True);descs=pd.concat([pd.read_csv(run/'results'/d/'saved_Shannon_descriptive_rows.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True)
 tests.minimum_reads=pd.to_numeric(tests.minimum_reads).astype(int);descs.minimum_reads=pd.to_numeric(descs.minimum_reads).astype(int)
 annotations=[]
 fig,axes=plt.subplots(2,3,figsize=(6.5,4.7),sharey='row');fig.subplots_adjust(left=.09,right=.99,top=.85,bottom=.13,wspace=.24,hspace=.76)
 for row,domain in enumerate(DOMAINS):
  full=metadata[domain].loc[metadata[domain].analytic_system.eq(SYSTEM)];values=pd.to_numeric(full.Shannon).to_numpy(float);span=max(float(values.max()-values.min()),.25);limits=(max(0,float(values.min())-span*.10),float(values.max())+span*.16)
  for col,date in enumerate(SHARED_DATES):
   ax=axes[row,col];a=draw_shannon(ax,metadata[domain],tests,descs,domain,SYSTEM,date,np,limits);a['panel']=chr(65+row*3+col);annotations.append(a);ax.text(-.14,1.30,a['panel'],transform=ax.transAxes,fontweight='bold',fontsize=8.5)
   if col==0:ax.set_ylabel(domain.capitalize()+'\nObserved Shannon diversity')
 fig.suptitle('Oatman Flats Ranch: observed Shannon diversity',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold')
 fig.text(.09,.02,'Saved observed values and treatment medians; * outside the saved 100-read Shannon sensitivity.',fontsize=8)
 stem='Fig2_Oatman_Flats_Ranch_Shannon_all_shared_field_events';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Observed bacterial and archaeal Shannon diversity across all three sampling events in the shared 2023-2024 Oatman Flats Ranch field. Panels A-C show bacteria and D-F archaea. Each point is a saved observed target-domain 16S rDNA Shannon value (natural logarithms) for one physical plot at that sampling event; lines are saved treatment medians. Treatment-group counts are printed under each panel. Vertical scales are shared across the three dates within each domain, with domain scales interpreted separately. The primary positive-target sample sets and their exact saved read-depth-adjusted treatment models are reused; plotted scores are not covariate-adjusted values. Printed BH and BY q values retain their original complete four-event correction families, including the separate 2025 event, although only the three shared-field events are displayed here. No alpha value, model or adjusted probability was recalculated. The open, labelled D094 point is retained in the primary population and absent from the saved minimum-100-read Shannon sensitivity. CC, cover crop; CCS, cover crop plus sheep.\n')
 # Separate field: never join this event to original-field trajectories.
 fig,axes=plt.subplots(1,2,figsize=(6.5,3.2));fig.subplots_adjust(left=.09,right=.985,top=.75,bottom=.22,wspace=.23)
 for i,domain in enumerate(DOMAINS):
  a=draw_shannon(axes[i],metadata[domain],tests,descs,domain,SEPARATE,'2025-05-21',np);a['panel']='2025_'+domain;annotations.append(a);axes[i].set_ylabel('Observed Shannon diversity');axes[i].text(.02,1.32,domain.capitalize(),transform=axes[i].transAxes,fontweight='bold',fontsize=8.5)
 fig.suptitle('Separate 2025 field: treatment is confounded with EAST/WEST field side',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.09,.035,'21 May 2025; exact saved six-sample models. This field is independent of the 2023-2024 field.',fontsize=8)
 stem='FigS3_Oatman_Flats_Ranch_Shannon_separate_2025_field';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Observed bacterial and archaeal Shannon diversity in the separate Oatman Flats Ranch field sampled on 21 May 2025. Each point is one saved observed 16S rDNA target-domain Shannon value and each horizontal line is the saved treatment median; the two domains retain their own vertical scales. Control samples occupied EAST and cover-crop samples WEST, so treatment is inseparable from field side. Exact saved primary six-sample treatment-model annotations are displayed with their original BH/BY correction families. These are within-field associations, not estimates of a causal management effect or continuations of the 2023-2024 field. The complete minimum-100-read model rows remain in Supplemental Table S3, with their own adjusted probabilities even where sample membership is unchanged. No alpha scores, statistical tests or adjusted values were recalculated.\n')
 # Diagnostics reuse source read counts and complete saved threshold-specific q-values.
 fig,axes=plt.subplots(2,2,figsize=(6.5,5.8));fig.subplots_adjust(left=.13,right=.985,top=.89,bottom=.18,wspace=.30,hspace=.83)
 all_events=[(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')];short_dates=['20 Nov 2023','06 Jun 2024','06 Dec 2024','21 May 2025*']
 for col,domain in enumerate(DOMAINS):
  ax=axes[0,col];md=metadata[domain].copy()
  for e,(system,date) in enumerate(all_events):
   part=md.loc[md.analytic_system.eq(system)&md.collection_date.eq(date)].sort_values(['treatment_label','biological_replicate','analysis_sample']);offsets=np.linspace(-.20,.20,len(part))
   for xx,(_,record) in zip(offsets,part.iterrows()):
    _,s100=model_for(tests,domain,system,date,100);member=str(record.analysis_sample) in s100;ax.scatter(e+xx,float(record.target_read_count),s=20,c=COLORS[record.treatment_label] if member else 'white',edgecolors=COLORS[record.treatment_label],linewidths=.8,zorder=3)
    if not member:ax.annotate(str(record.analysis_sample),(e+xx,float(record.target_read_count)),xytext=(-4,6),textcoords='offset points',ha='right',fontsize=8)
  inventory=pd.read_csv(run/'results'/domain/'sample_retention_by_event.csv');zero=int(pd.to_numeric(inventory.zero_target_records).sum());ax.set_yscale('log');ax.axhline(100,color='#a4aab0',linewidth=.8,linestyle='--');ax.set_xticks(range(4),short_dates,rotation=35,ha='right');ax.set_ylabel('Target-domain 16S rDNA reads');ax.set_title(chr(65+col)+'  '+domain.capitalize()+': accepted library depths\nExcluded zero-target records: '+str(zero),loc='left',fontsize=8,pad=8);ax.grid(axis='y',linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
  ax=axes[1,col]
  for threshold,dy,marker in [(0,-.17,'o'),(100,.17,'s')]:
   xx=[];yy=[]
   for e,(system,date) in enumerate(all_events):
    m,_=model_for(tests,domain,system,date,threshold);q=float(m.q_BY);xx.append(q);yy.append(e+dy);ax.annotate('n='+str(int(m.n)),(q,e+dy),xytext=(-3 if q>.8 else 12 if q<.15 else 3,-2),textcoords='offset points',ha='right' if q>.8 else 'left',fontsize=8)
   ax.scatter(xx,yy,s=24,marker=marker,facecolors='#315b76' if threshold==0 else 'white',edgecolors='#315b76',linewidths=.8,label='Positive target' if threshold==0 else 'Minimum 100 reads',zorder=3)
  ax.axvline(.05,color='#aa6633',linestyle='--',linewidth=.8);ax.set_xlim(-.04,1.08);ax.set_yticks(range(4),['Nov 2023','Jun 2024','Dec 2024','May 2025*']);ax.invert_yaxis();ax.set_xlabel('Saved BY-adjusted q');ax.set_title(chr(67+col)+'  '+domain.capitalize()+': saved Shannon sensitivity',loc='left');ax.grid(axis='x',linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
 treatment_handles=[Line2D([],[],color=COLORS[t],marker='o',linestyle='',label=t) for t in ['Control','CC','CCS']];fig.legend(handles=treatment_handles,loc='lower left',bbox_to_anchor=(.09,.075),ncol=3,frameon=False)
 handles=[Line2D([],[],color='#315b76',marker='o',linestyle='',label='Positive target'),Line2D([],[],color='#315b76',marker='s',markerfacecolor='white',linestyle='',label='Minimum 100 reads')];fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.09,.04),ncol=2,frameon=False)
 fig.suptitle('Oatman Flats Ranch: read-depth, retention and saved-model diagnostics',x=.095,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.095,.017,'* Separate 2025 field. Generic feature-depth flags are distinct from the 100-read Shannon criterion.',fontsize=8)
 stem='FigS4_Oatman_Flats_Ranch_target_read_depth_and_retention';export(fig,out/stem);plt.close(fig)
 (out/(stem+'_legend.txt')).write_text('Target-read depth, sample retention and saved Shannon sensitivity diagnostics at Oatman Flats Ranch. Panels A-B display the saved target-domain read counts for every accepted positive-target Shannon sample, using logarithmic vertical axes; the dashed horizontal line marks 100 reads. Open points identify samples omitted from the exact saved minimum-100-read Shannon population. Zero-target excluded records are reported rather than placed at an artificial positive read count. Panels C-D compare saved BY-adjusted values and cohort sample sizes for the positive-target and minimum-100-read Shannon analyses at each of the four field-specific events. The dashed vertical line is q=0.05. Values retain the original four-event correction families separately within domain and threshold; they were not recalculated for the displayed subset. The 2025 field remains independent and treatment is confounded with EAST/WEST side there. The source feature-depth-sensitivity flag is exported separately in the sample inventory and is not relabelled as the 100-read Shannon criterion. D094 is retained in the primary bacterial population; excluded archaeal C094 is not reinstated.\n')
 pd.DataFrame(annotations).to_csv(out/'PANEL_SAVED_MODEL_ANNOTATIONS.csv',index=False)
 inventory=pd.concat([pd.read_csv(run/'results'/d/'complete_corrected_sample_retention_inventory.csv',keep_default_na=False) for d in DOMAINS],ignore_index=True);inventory.to_csv(out/'Table_S2_complete_sample_retention_and_source_eligibility.csv',index=False)
 tests.sort_values(['domain','cohort','minimum_reads']).to_csv(out/'Table_S3_saved_Shannon_models_all_four_events.csv',index=False)
 descs.sort_values(['domain','cohort','minimum_reads','treatment']).to_csv(out/'saved_Shannon_descriptives_all_four_events.csv',index=False)
 save_json(out/'COMBINED_STATUS.json',{'status':'OBSERVED_DIVERSITY_AND_DIAGNOSTIC_FIGURES_CREATED_REVIEW_PENDING','observed_Shannon_recalculated':False,'statistical_models_recalculated':False,'FDR_recalculated':False,'main_figure':'Fig2_Oatman_Flats_Ranch_Shannon_all_shared_field_events','supplementary_figures':['FigS3_Oatman_Flats_Ranch_Shannon_separate_2025_field','FigS4_Oatman_Flats_Ranch_target_read_depth_and_retention'],'full_original_four_event_families_preserved':True,'visual_review_complete':False})

def combined_pcoa_figure(run,cfg):
 require_hpc()
 import numpy as np
 import pandas as pd
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.lines import Line2D
 style(plt);out=run/'results'/'combined';fig,axes=plt.subplots(2,4,figsize=(6.5,5.8));fig.subplots_adjust(left=.10,right=.99,top=.83,bottom=.31,wspace=.78,hspace=1.10);audit=[]
 for row,domain in enumerate(DOMAINS):
  coords=pd.read_csv(run/'results'/domain/'PCoA_all_positive_coordinates_and_sample_metadata.csv',keep_default_na=False)
  for col,(system,date) in enumerate([(SYSTEM,d) for d in SHARED_DATES]+[(SEPARATE,'2025-05-21')]):
   sub=coords.loc[coords.analytic_system.eq(system)&coords.collection_date.eq(date)];require(len(sub)>0,'PCoA rendering cohort absent');ax=axes[row,col]
   for _,r in sub.iterrows():
    included=truth(r.saved_Shannon_min100_included);ax.scatter(float(r.PCoA1),float(r.PCoA2),s=21,c=COLORS[r.treatment_label] if included else 'white',edgecolors=COLORS[r.treatment_label],linewidths=.8,zorder=3,marker='o')
    if not included:ax.annotate(str(r.analysis_sample)+'*',(float(r.PCoA1),float(r.PCoA2)),xytext=(-2,7),textcoords='offset points',ha='right',fontsize=8)
   rank=int(sub.iloc[0].positive_axis_count);require((pd.to_numeric(sub.positive_axis_count)==rank).all(),'Mixed positive-rank metadata');ax.set_aspect('equal',adjustable='datalim');ax.set_xlabel(('PCoA1 ('+format(float(sub.iloc[0].PCoA1_fraction)*100,'.1f')+'%)') if rank>0 else 'No positive coordinate axis');ax.set_ylabel(('PCoA2 ('+format(float(sub.iloc[0].PCoA2_fraction)*100,'.1f')+'%)') if rank>1 else 'No second positive axis');
   if rank<2:ax.set_yticks([0]);ax.text(.02,.02,'One-dimensional geometry' if rank==1 else 'Zero positive inertia',transform=ax.transAxes,fontsize=8)
   ax.set_title(title_date(date)+('\nseparate field' if system==SEPARATE else ''),fontsize=8.2,pad=8);ax.text(-.33,1.30,chr(65+row*4+col),transform=ax.transAxes,fontweight='bold',fontsize=8.5);ax.grid(linewidth=.4,color='#e4e7e9');ax.spines[['top','right']].set_visible(False)
   if system==SEPARATE:ax.set_facecolor('#f1f3f5')
   audit.append({'domain':domain,'cohort':system+'__'+date,'sample_set':';'.join(sub.analysis_sample),'lingoes_constant':float(sub.iloc[0].lingoes_constant),'PCoA1_fraction':float(sub.iloc[0].PCoA1_fraction),'PCoA2_fraction':float(sub.iloc[0].PCoA2_fraction)})
 handles=[Line2D([],[],color=COLORS[t],marker='o',linestyle='',label=t) for t in ['Control','CC','CCS']]+[Line2D([],[],color='#444444',marker='o',markerfacecolor='white',linestyle='',label='Outside saved minimum-100 Shannon population')]
 fig.text(.10,.93,'Bacteria',fontweight='bold',fontsize=8.5);fig.text(.10,.56,'Archaea',fontweight='bold',fontsize=8.5)
 fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.10,.105),ncol=2,frameon=False,fontsize=8);fig.suptitle('Oatman Flats Ranch: descriptive ASV community similarity',x=.09,y=.985,ha='left',fontsize=9,fontweight='bold');fig.text(.10,.025,'Each domain/event has an independent ordination.\nShaded panels: separate 2025 field (Control/EAST; CC/WEST).',fontsize=8,va='bottom',linespacing=1.2)
 # Measure full allocated boxes after the existing initial equal/datalim draw;
 # then make explicit equal-unit padded limits and preserve those boxes with box aspect.
 fig.canvas.draw()
 fig._physical_scatter_padding_audit=pad_scatter_axes_in_points(fig,fig.canvas.get_renderer(),center_padding_points=5)
 stem='FigS7_Oatman_Flats_Ranch_descriptive_Bray_Curtis_PCoA';export(fig,out/stem);plt.close(fig);pd.DataFrame(audit).to_csv(out/'PCoA_PANEL_AND_CORRECTION_AUDIT.csv',index=False)
 (out/(stem+'_legend.txt')).write_text('Descriptive Bray-Curtis principal-coordinate analyses (PCoA) of accepted unrarefied target-domain ASV profiles at Oatman Flats Ranch. Each panel is calculated independently for one domain and exact field-specific sampling event, using unit-sum relative ASV profiles. Squared Bray-Curtis dissimilarities are double-centered; the Lingoes adjustment adds twice the magnitude of the most negative eigenvalue to off-diagonal squared distances only when a negative eigenvalue exceeds the frozen numerical tolerance. Final positive axes define coordinates, and percentages use the sum of positive eigenvalues for that panel. The complete distances, before/after eigenvalues, correction constants and reconstruction checks accompany the figure. Points are accepted samples with treatment colors; open labelled points are outside the saved minimum-100-read Shannon population, which is a display flag and does not change the plotted primary PCoA cohort. Generic feature-depth flags remain separately recorded. Archaeal C094 is not reinstated; bacterial D094 remains in its accepted cohort. Shaded 2025 panels represent the separate field with Control/EAST and CC/WEST confounding. Axes have equal coordinate-unit aspect within panels and are not linked across dates or domains. Any rank-deficient geometry is explicitly labelled without assigning a second positive axis. Observed community similarity can be influenced by read depth and composition; visible clustering is not a treatment test. No PERMANOVA, dispersion test, new treatment p-value or q-value was calculated.\n')
 save_json(out/'PCoA_COMBINED_STATUS.json',{'status':'DESCRIPTIVE_ORDINATION_FIGURE_CREATED_REVIEW_PENDING','hypothesis_tests_added':False,'p_values_added':False,'q_values_added':False,'visual_review_complete':False})
