#!/usr/bin/env python3
from hpc_guard import require_hpc
execution = require_hpc()
from pathlib import Path
import sys, tarfile, shutil, subprocess, importlib.metadata, json
import picrust2, picrust2.default as defaults
from common import require, sha, save, table

package, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
require(importlib.metadata.version('PICRUSt2') == '2.6.3', 'Unexpected PICRUSt2 version')
root = Path(picrust2.__file__).resolve().parent
tar_sha = 'c06afafb0d37b1451a1e112a18887c733ad3dc9c6d171a44a364ef22a21df03b'

paths = []
for name in ['default_fasta_bac','default_tree_bac','default_hmm_bac','default_model_bac',
             'default_fasta_arc','default_tree_arc','default_hmm_arc','default_model_arc',
             'default_regroup_map','default_pathway_map']:
    paths.append(Path(getattr(defaults, name)))
for tables in [defaults.default_tables_bac, defaults.default_tables_arc]:
    paths += [Path(tables[k]) for k in ['16S','EC','KO']]
paths += [Path(defaults.default_map[k]) for k in ['EC','KO','METACYC']]
references = []
for path in sorted(set(paths)):
    require(path.is_file() and path.stat().st_size > 0, 'Missing PICRUSt2 reference: '+str(path))
    references.append({'path':str(path), 'relative_to_picrust2':str(path.relative_to(root)),
                       'bytes':path.stat().st_size, 'sha256':sha(path)})
table(out/'reference_files.csv', references)
if True:
    expected = json.loads((package/'runtime_env/READY.json').read_text())
    require(references == expected['references'], 'Reference files changed since setup')
versions = {'PICRUSt2':importlib.metadata.version('PICRUSt2')}
for lib in ['numpy','pandas','scipy','biom-format','dendropy']:
    versions[lib] = importlib.metadata.version(lib)
for args in [['picrust2_pipeline.py','--version'], ['epa-ng','--version'], ['gappa','--help'],
             ['Rscript','-e','cat(as.character(packageVersion("castor")), "\\n"); sessionInfo()']]:
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    require(proc.returncode == 0, 'Environment check failed: '+repr(args)+'\n'+proc.stdout)
    (out/(args[0]+'_version.txt')).write_text(proc.stdout)
for command in ['hmmalign','glpsol','metagenome_pipeline.py','pathway_pipeline.py','add_descriptions.py']:
    require(shutil.which(command) is not None, 'Missing executable '+command)
help_text = subprocess.check_output(['picrust2_pipeline.py','--help'], text=True)
(out/'picrust2_help.txt').write_text(help_text)
for flag in ['--ref_dir1','--ref_dir2','--min_align','--max_nsti','--in_traits']:
    require(flag in help_text, 'Unexpected PICRUSt2 interface '+flag)
script = Path(shutil.which('picrust2_pipeline.py'))
if True:
    require(versions == expected['versions'], 'Software versions changed since setup')
    require(sha(script) == expected['pipeline_script_sha256'], 'Pipeline executable changed since setup')
save(out/'environment.json', {'execution':execution, 'python':sys.version, 'versions':versions,
    'source_reference_tar_sha256':tar_sha, 'references':references, 'pipeline_script_sha256':sha(script),
    'setup_checks':'Imports, interfaces, references and executable checks; full dataset pipeline runs in analysis job'})
print(json.dumps({'environment':'READY', 'versions':versions, 'references_verified':len(references)}, indent=2))
