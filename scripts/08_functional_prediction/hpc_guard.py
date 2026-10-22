"""Require a real running Slurm allocation on a Puma compute node."""
import os,re,socket,subprocess,sys

def command(args):
 return subprocess.check_output(args,universal_newlines=True,stderr=subprocess.STDOUT,timeout=30).strip()

def puma_cluster():
 cluster=os.environ.get('SLURM_CLUSTER_NAME','').lower()
 if not cluster:
  text=command(['scontrol','show','config'])
  match=re.search(r'^\s*ClusterName\s*=\s*(\S+)',text,re.M)
  cluster=match.group(1).lower()if match else ''
 if cluster!='puma':raise RuntimeError('This package requires the Puma Slurm cluster; observed '+repr(cluster))
 return cluster

def require_hpc():
 try:
  job=os.environ.get('SLURM_JOB_ID','')
  if not job.isdigit():raise RuntimeError('No Slurm job allocation. Scientific execution is HPC-only.')
  cluster=puma_cluster()
  record=command(['scontrol','show','job','-o',job])
  if not re.search(r'\bJobState=RUNNING\b',record):raise RuntimeError('The Slurm job is not recorded as RUNNING.')
  match=re.search(r'\bNodeList=(\S+)',record)
  if not match:raise RuntimeError('No allocated node list in Slurm record.')
  nodes=set(command(['scontrol','show','hostnames',match.group(1)]).splitlines())
  node=socket.gethostname().split('.')[0]
  if node not in nodes:raise RuntimeError('Current host is not an allocated compute node.')
  return {'job_id':job,'cluster':cluster,'node':node,'job_record':record}
 except Exception as exc:
  raise SystemExit('HPC_ONLY: '+str(exc))
