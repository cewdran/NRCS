"""Puma-only synthetic DataFrame index-label regression; no biological inputs."""
from pathlib import Path
import ast
import json
from hpc_guard import require_hpc


def require(value,message):
 if not value:raise RuntimeError(message)


def indexing_expression_and_keys(path,function_name,target):
 tree=ast.parse(path.read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function_name]
 require(len(functions)==1,'Structural check: production function missing')
 expressions=[];sortkeys=[]
 for node in ast.walk(functions[0]):
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==target for t in node.targets):
   calls=[n for n in ast.walk(node.value) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
   if any(n.func.attr=='set_index' for n in calls):expressions.append(node.value)
  if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='sort_values':
   keys=ast.literal_eval(node.args[0])
   if 'analysis_sample' in keys:sortkeys.append(keys)
 require(len(expressions)==1 and len(sortkeys)==1,'Structural check: ambiguous production indexing/sort selection')
 calls=[n for n in ast.walk(expressions[0]) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
 require(any(n.func.attr=='rename_axis' and len(n.args)==1 and isinstance(n.args[0],ast.Constant) and n.args[0].value is None for n in calls),'Structural check: production index name was not cleared')
 return ast.Expression(body=expressions[0]),sortkeys[0]


def run_check(run):
 execution=require_hpc()
 # Import occurs only on a verified allocated Puma node.
 import pandas as pd
 base=Path(__file__).resolve().parent
 rows=[{'analysis_sample':'SYNTH_B','analytic_system':'SYNTHETIC_FIELD','collection_date':'SYNTHETIC_DATE','sort_treatment':0,'sort_replicate':1,'biological_replicate':1,'treatment_label':'SYNTHETIC_GROUP','sentinel':'keep_B'},
       {'analysis_sample':'SYNTH_A','analytic_system':'SYNTHETIC_FIELD','collection_date':'SYNTHETIC_DATE','sort_treatment':0,'sort_replicate':1,'biological_replicate':1,'treatment_label':'SYNTHETIC_GROUP','sentinel':'keep_A'},
       {'analysis_sample':'SYNTH_C','analytic_system':'SYNTHETIC_FIELD','collection_date':'SYNTHETIC_DATE','sort_treatment':0,'sort_replicate':2,'biological_replicate':2,'treatment_label':'SYNTHETIC_GROUP','sentinel':'keep_C'}]
 expected=[rows[1],rows[0],rows[2]];cases=[]
 for filename,function,target in [('family_figures.py','worker','metadata'),('diversity_panels.py','draw_shannon','data')]:
  frame=pd.DataFrame(rows);expression,keys=indexing_expression_and_keys(base/filename,function,target)
  old_error=None
  try:frame.set_index('analysis_sample',drop=False).sort_values(keys)
  except ValueError as exc:old_error=str(exc)
  require(old_error is not None and 'analysis_sample' in old_error and 'ambiguous' in old_error,'Structural check: original ambiguity not reproduced')
  indexed=eval(compile(ast.fix_missing_locations(expression),filename,'eval'),{'__builtins__':{}},{'metadata':frame,'members':[r['analysis_sample'] for r in rows]})
  require(indexed.index.name is None and list(indexed.index)==[r['analysis_sample'] for r in rows],'Structural check: sample index keys changed')
  require(list(indexed.columns)==list(frame.columns),'Structural check: retained columns changed')
  ordered=indexed.sort_values(keys)
  require(ordered.to_dict(orient='records')==expected,'Structural check: production sort or values changed')
  require(list(ordered.index)==[r['analysis_sample'] for r in expected],'Structural check: sorted index identity changed')
  cases.append({'file':filename,'function':function,'status':'PASS','old_ambiguity_reproduced':True,'sort_keys':keys,'expected_ID_order':[r['analysis_sample'] for r in expected],'sample_keys_columns_values_preserved':True})
 receipt={'status':'PASS_PUMA_SYNTHETIC_INDEX_LABEL_REGRESSION','execution':execution,'cases':cases,'biological_inputs_read':False,'inferential_tests_added':False,'alpha_or_FDR_recomputed':False}
 out=Path(run)/'audit'/'STRUCTURAL_INDEX_SELF_CHECK.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(receipt,indent=2)+'\n')
 return receipt
