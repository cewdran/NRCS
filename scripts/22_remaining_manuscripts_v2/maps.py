"""Plan-based schematics: manuscript codes are authoritative; positions are context only."""
from common import *

def run(run,c):
 require_hpc()
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.patches import Rectangle
 style(plt);root=Path(run)/'results'/'maps';root.mkdir(parents=True)
 fig,axs=plt.subplots(1,2,figsize=(7.2,6.3));fig.subplots_adjust(left=.07,right=.97,top=.84,bottom=.37,wspace=.20)
 for ax,label,blocks,nslots in [(axs[0],'A  Grenache',[4,5,6],4),(axs[1],'B  Petite Sirah',[1,2,3],5)]:
  ax.set_xlim(-.5,3.5);ax.set_ylim(-.8,5.7);ax.axis('off');ax.set_title(label,loc='left',fontweight='bold',fontsize=10)
  for col,block in enumerate(blocks):
   ax.text(col+.4,5.1,'Block '+str(block),ha='center',fontsize=8)
   for slot in range(nslots):
    y=4.2-slot*.85
    ax.add_patch(Rectangle((col,y),.8,.65,facecolor='#E7ECEF',edgecolor='#667785',lw=.8))
    ax.text(col+.4,y+.325,'Plot slot',ha='center',va='center',fontsize=7)
  ax.text(1.4,-.65,'General plan arrangement',ha='center',fontsize=8)
 fig.suptitle('Page Springs | Field-plan context',x=.07,y=.97,ha='left',fontsize=11,fontweight='bold')
 fig.text(.07,.27,'Grenache analyzed codes\nT1  Fertigation control\nT7  Fresh grape pomace\nT8  Composted grape pomace + horse manure\nT9  Composted horse manure',va='top',fontsize=8)
 fig.text(.55,.27,'Petite Sirah analyzed codes\nT1  Fertigation control\nT2  Steer manure\nT3  Fish manure\nT4  Horse manure + pomace',va='top',fontsize=8)
 fig.text(.07,.075,'Petite Sirah plan: five slots per block; T5 is plan-only and is outside the analyzed four-arm cohort.\nCodes are not assigned to map positions. Not to scale; compass orientation is undocumented.',fontsize=7.5)
 finish(fig,root,'PageSprings_general_plot_layout','General layout extracted from the supplied Grenache and Petite Sirah field plans. Grenache contains three four-slot blocks labelled 4–6; Petite Sirah contains three five-slot planned blocks labelled 1–3. The manuscript codes and treatment definitions are authoritative, as confirmed by the user. Historical Grenache map/legend labels do not redefine the analyzed treatments. Petite Sirah T5 (horse manure/pomace with microbial inoculant) appears in the plan but is outside the current analyzed four-arm manuscript and is not added as a study arm. Positions are deliberately unassigned because the plans are being used for general layout, not to reconstruct sample identity. These context schematics are not georeferenced or to scale and do not document randomization, realized sampling membership or inferential blocking. The original plans and exact analytical sample keys accompany the package.',plt)
 fig,axs=plt.subplots(1,2,figsize=(7.2,7.0));fig.subplots_adjust(left=.07,right=.96,top=.84,bottom=.32,wspace=.25)
 ax=axs[0];ax.axis('off');ax.set_xlim(-.8,4.6);ax.set_ylim(-1.5,5.6);ax.set_title('A  Blackberry field',loc='left',fontweight='bold',fontsize=10)
 grid=[list('ABCD'),list('BCDA'),list('CDAB'),list('DABC')]
 for j,letter in enumerate(['Natchez','Natchez','Chester','Chester']):
  ax.text(j+.35,4.7,'Row '+str(j+1),ha='center',fontsize=7)
  ax.text(j+.35,4.35,letter,ha='center',fontsize=6.5,rotation=25)
 for i,row in enumerate(grid):
  for j,code in enumerate(row):
   ax.add_patch(Rectangle((j,3.2-i),.75,.8,facecolor=COLORS[code],edgecolor='white'))
   ax.text(j+.375,3.6-i,code,ha='center',va='center',color='white',fontweight='bold')
 ax.annotate('N',xy=(-.4,4.6),xytext=(-.4,3.7),ha='center',arrowprops={'arrowstyle':'->','color':'#333333'},fontsize=9)
 ax.text(1.85,-.6,'Rows west → east',ha='center',fontsize=8)
 ax.text(4.15,1.6,'Fence (east)',rotation=90,va='center',fontsize=7);ax.text(3.5,-.1,'Gate',fontsize=7)
 ax.text(1.8,-1.25,'Four 66.5 ft blocks along each 266 ft row',ha='center',fontsize=7.2)
 ax=axs[1];ax.axis('off');ax.set_xlim(-.6,3.4);ax.set_ylim(-1.5,5.6);ax.set_title('B  Annual field (2023 plan)',loc='left',fontweight='bold',fontsize=10)
 ax.add_patch(Rectangle((-.5,4.1),3.7,.35,facecolor='#CCD3D8'));ax.text(1.3,4.27,'Access road',ha='center',va='center',fontsize=7)
 ax.add_patch(Rectangle((-.5,-.05),.25,4.15,facecolor='#CCD3D8'))
 grid=[list('ABC'),list('BCD'),list('CDA'),list('DAB')]
 for j in range(3):ax.text(j+.35,4.7,'R'+str(j+1),ha='center',fontsize=8)
 for i,row in enumerate(grid):
  for j,code in enumerate(row):
   ax.add_patch(Rectangle((j,3.2-i),.75,.8 if i<3 else .64,facecolor=COLORS[code],edgecolor='white'))
   ax.text(j+.375,3.6-i if i<3 else 3.52-i,code,ha='center',va='center',color='white',fontweight='bold')
 ax.text(1.25,-.6,'Three replication strips; four plots per strip',ha='center',fontsize=7.2)
 ax.text(1.25,-1.25,'50 ft plots; terminal plots 40 ft',ha='center',fontsize=7.5)
 fig.suptitle('Whipstone | Field-plan context',x=.07,y=.97,ha='left',fontsize=11,fontweight='bold')
 fig.text(.07,.24,'A  Blood + feather meal\nB  Clover + buckwheat cover crop',fontsize=8,va='top')
 fig.text(.55,.24,'C  Control\nD  Meals + cover crop',fontsize=8,va='top')
 fig.text(.07,.135,'Blackberry: two plots per treatment within each cultivar; four rows are not four replicates per cultivar.\nAnnual: the 2023 lettuce plan describes arrangement; confirmed succession is lettuce, onion, marigolds.\nSchematics are not to scale. Annual-field compass orientation and randomization are undocumented.',fontsize=7.5,linespacing=1.5)
 finish(fig,root,'Whipstone_general_plot_layout','General arrangement transcribed from the supplied blackberry spreadsheet and annual-field Word plan, using the authoritative manuscript A–D treatment definitions. The blackberry compass supports north upward and rows west to east; rows 1–2 are Natchez and rows 3–4 Chester. Four 66.5-ft sections per row produce eight planned plots per cultivar, two per treatment; the four rows do not supply four independent replicates per cultivar. Within-row plant spacing is recorded as 3 ft. The annual plan has three replication strips and four treatment plots in each, with 50-ft plots and 40-ft terminal plots, 1-ft plant spacing and source-stated 150/120 plants per plot. Plant counts are not independent biological replicates. The annual plan records lettuce planted on 24 April 2023, lists Skyphos/Nevada/Magenta, and does not justify assigning cultivars to strips. Later crop planning notes do not supersede the confirmed annual succession of lettuce (2023), onion (2024), and marigolds (2025). Neither drawing establishes random assignment or an exact link from map position to analytical sample ID. Map codes do not alter source metadata or saved inferential models.',plt)
 js(root/'STATUS.json',{'state':'COMPLETE_REVIEW_PENDING','figures_expected':2,'manuscript_codes_authoritative':True,'georeferenced':False,'analytical_sample_positions_inferred':False,'manual_visual_review_complete':False})
