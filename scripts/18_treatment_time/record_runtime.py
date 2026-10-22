"""Record actual runtime allocation using only the Python standard library."""
from hpc_guard import require_hpc
execution=require_hpc()
import argparse
import platform
import socket
import sys
from pathlib import Path
from common import save,utc

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('event',choices=['start','end'])
    parser.add_argument('--audit-dir',required=True)
    parser.add_argument('--exit-code',type=int)
    args=parser.parse_args()
    record={'event':args.event,'utc':utc(),'execution':execution,
        'hostname':socket.gethostname(),'interpreter':sys.executable,'python':sys.version,
        'platform':platform.platform(),'pipeline_exit_code':args.exit_code if args.event=='end' else None,
        'scientific_review_pending':True,'visual_review_pending':True,
        'ten_year_forecast_generated':False,'predictive_validation_established':False}
    save(Path(args.audit_dir)/('EXECUTION_'+args.event.upper()+'.json'),record)

if __name__=='__main__': main()
