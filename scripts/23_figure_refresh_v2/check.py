from pathlib import Path
import hashlib,json,subprocess
BASE=Path(__file__).resolve().parent

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    latest=BASE/'LATEST_RUN.txt'
    if not latest.is_file():raise SystemExit('No refresh submission receipt yet.')
    run=Path(latest.read_text().strip());s=json.loads((run/'submission.json').read_text());job=s['job_id']
    print('RUN_DIRECTORY='+str(run))
    raw=subprocess.check_output(['sacct','-j',job,'--noheader','--parsable2','--format=JobIDRaw,State,ExitCode'],text=True)
    matches=[x.split('|') for x in raw.splitlines() if x.split('|')[0]==job]
    if matches:_,state,exitcode,*_=matches[0]
    else:
        state=subprocess.check_output(['squeue','-h','-j',job,'-o','%T'],text=True).strip() or 'UNKNOWN';exitcode='UNKNOWN'
    print('refresh job='+job+' state='+state+' exit='+exitcode)
    complete=run/'completion.json'
    if not complete.is_file():
        print('RESULTS_READY=false; refresh completion record not yet available')
        if state not in ('PENDING','RUNNING','CONFIGURING','COMPLETING','UNKNOWN'):
            print('Please return submission.json, stdout.log and stderr.log from '+str(run))
        return
    c=json.loads(complete.read_text());valid=Path(c['archive']).is_file() and sha(c['archive'])==c['sha256']
    ready=(state=='COMPLETED' and exitcode=='0:0' and c['state']=='HPC_FIGURES_REFRESHED_REVIEW_PENDING' and valid)
    print('RESULTS_READY='+str(ready).lower());print('SCIENTIFIC_AND_VISUAL_REVIEW_PENDING=true')
    if ready:
        print('RETURN_ZIP='+c['archive']);print('RETURN_CHECKSUM='+c['checksum'])
    elif valid and c['state']=='INCOMPLETE_OR_FAILED':
        print('RETURN_FOR_DIAGNOSIS='+c['archive']);print('RETURN_CHECKSUM='+c['checksum'])
    elif not valid:print('ARCHIVE_VERIFICATION_FAILED=true')
    else:print('Final scheduler completion is not yet confirmed; check again.')

if __name__=='__main__':main()
