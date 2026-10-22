"""Shared file checks and plot exports. Scientific imports are delayed until Puma."""
from pathlib import Path
import hashlib,json,datetime,os,sys,importlib.metadata,shutil,csv,textwrap
from hpc_guard import require_hpc
BASE=Path(__file__).resolve().parent
COLORS={'T1':'#667785','T2':'#D48B31','T3':'#4D85A5','T4':'#8F68A8','T7':'#D48B31','T8':'#16877D','T9':'#8F68A8','C':'#667785','A':'#D48B31','B':'#16877D','D':'#8F68A8','Control':'#667785','CC':'#16877D','CCS':'#AD6822'}
def need(x,msg):
 if not x:raise RuntimeError(msg)
def truth(x):return str(x).strip().lower() in ('true','1','1.0','yes')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def js(p,x):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def cfg():return json.loads((BASE/'CONFIG.json').read_text())
def verify_package():
 for line in (BASE/'SHA256SUMS.txt').read_text().splitlines():
  h,r=line.split('  ',1);need(sha(BASE/r)==h,'Package changed: '+r)
def environment(run):
 execution=require_hpc();verify_package();c=cfg();need(sys.version_info[:3]==(3,11,4),'Pinned Python version differs')
 need(Path(sys.executable).absolute()==Path(c['runtime_python']).absolute(),'Wrong pinned Python interpreter')
 need(sha(c['runtime_ready'])==c['runtime_ready_sha256'],'Runtime READY changed')
 actual={x:importlib.metadata.version(x) for x in c['versions']};need(actual==c['versions'],'Runtime package versions differ')
 js(Path(run)/'audit'/('environment_'+os.environ['SLURM_JOB_ID']+'.json'),{'execution':execution,'versions':actual,'python':sys.version,'executable':sys.executable,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 return c
def source(role,run,c):
 if role in c['local_sources']:
  spec=c['local_sources'][role];p=BASE/spec['file'];need(sha(p)==spec['sha256'],'Bundled source changed '+role);return p
 records=json.loads((Path(run)/'STAGE_STATUS.json').read_text())['sources'];rec=next(x for x in records if x['role']==role)
 need(sha(rec['snapshot'])==rec['sha256'],'Staged source changed '+role);return Path(rec['snapshot'])
def frame(role,run,c,pd):return pd.read_csv(source(role,run,c),dtype=str,keep_default_na=False)
def workbook(run,c,pd,name):
 w=json.loads(source('corrected_workbook',run,c).read_text())['tables'][name]
 return pd.DataFrame(w['rows'],columns=w['columns']).fillna('')
def style(plt):plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.titlesize':8.5,'axes.labelsize':8,'xtick.labelsize':7.5,'ytick.labelsize':7.5,'legend.fontsize':8,'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42})
def date(x):return datetime.date.fromisoformat(x).strftime('%d %b %Y')
def wrap(x,n=45):return '\n'.join(textwrap.wrap(str(x),n,break_long_words=False))
def code_for(row,c):
 if str(row['farm'])=='Oatman Flats':return {'Control':'Control','Cover_crop_only':'CC','Cover_crop_and_sheep':'CCS'}[str(row['treatment'])]
 specs=[s for s in c['systems'] if s['farm']==row['farm'] and s['system']==row.get('analytic_system',row.get('system'))]
 need(len(specs)==1,'Unknown farm/system treatment context')
 label=str(row.get('treatment_label',''))
 aliases={'Control_fertigation_only':'ControlFertigationOnly','Steer_manure':'SteerManure','Fish_manure':'FishManure','Horse_manure_pomace':'HorseManurePomace','Fresh_grape_pomace':'FreshGrapePomace','Composted_grape_pomace_and_horse_manure':'CompostedGrapePomaceHorseManure','Composted_horse_manure':'CompostedHorseManure','Blood_meal_and_Feather_meal':'BloodMealFeatherMeal','Blood_meal_and_Feather_meal_and_cover_crop':'BloodMealFeatherMealCC','Control':'Control','Cover_crop_only':'CC'}
 if label not in specs[0]['treatments']:label=aliases[str(row['treatment'])]
 return specs[0]['treatments'][label]
def finish(fig,out,stem,caption,plt):
 from layout_audit import audit_rendered_text
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 layout=audit_rendered_text(fig);bad=layout['text_outside_canvas']
 js(out/(stem+'_LAYOUT_QC.json'),layout)
 # Real overflow still stops publication export; retain a diagnostic preview.
 if bad:fig.savefig(out/(stem+'_DIAGNOSTIC.png'),dpi=150,facecolor='white')
 need(not bad,'Text outside figure canvas in '+stem+': '+repr(bad))
 for ext in ('pdf','svg','eps','png','tif'):
  opts={'dpi':600 if ext=='tif' else 300,'facecolor':'white'}
  if ext=='tif':opts['pil_kwargs']={'compression':'tiff_lzw'}
  fig.savefig(out/(stem+'.'+ext),**opts)
 # Force RGB TIFF without altering geometry/resolution.
 from PIL import Image
 p=out/(stem+'.tif')
 with Image.open(p) as im:rgb=im.convert('RGB');rgb.save(p,compression='tiff_lzw',dpi=(600,600))
 (out/(stem+'_caption.txt')).write_text(caption+'\n');plt.close(fig)
def export_table(df,out,name):
 p=Path(out)/name;p.parent.mkdir(parents=True,exist_ok=True);df.to_csv(p,index=False)
def one(df,mask,label):
 sub=df.loc[mask];need(len(sub)==1,'Expected one '+label+'; got '+str(len(sub)));return sub.iloc[0]
