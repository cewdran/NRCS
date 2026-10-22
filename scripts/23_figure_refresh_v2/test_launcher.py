"""Launcher-only tests with dummy executables; no Slurm or science is run."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parent


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nrcs_launcher_test_')
        self.root = Path(self.tmp.name)
        self.package = self.root/'dummy package'
        self.package.mkdir()
        (self.package/'worker.py').write_text('DO NOT EXECUTE: test placeholder\n')
        (self.package/'CONFIG.json').write_text('{}\n')
        self.run = self.root/'dummy run'
        self.run.mkdir()
        self.runtime = self.root/'dummy runtime'
        self.report = self.root/'report.json'
        self.runtime.write_text(
            '#!'+sys.executable+'\n'
            'import json,os,sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["TEST_REPORT"]).write_text(json.dumps({\n'
            '"args":sys.argv[1:],"module":os.environ.get("TEST_MODULE"),\n'
            '"pythonpath":os.environ.get("PYTHONPATH"),\n'
            '"no_user_site":os.environ.get("PYTHONNOUSERSITE"),\n'
            '"cwd":str(Path.cwd())}))\n')
        self.runtime.chmod(0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def launch(self, module_body, runtime=None):
        # This dummy executable never reads or executes the dummy worker.
        # The production HPC guard is neither called nor changed by these tests.
        env = os.environ.copy()
        for key in ('BASH_ENV','ENV','PYTHONPATH','PYTHONHOME'):
            env.pop(key, None)
        env.update(TEST_REPORT=str(self.report),
                   SLURM_JOB_ID='synthetic-launcher-test',
                   PYTHONPATH='/synthetic/unwanted/path')
        script = 'module() { '+module_body+'; }; export -f module; exec /bin/bash "$@"'
        return subprocess.run(['/bin/bash','-c',script,'test',str(BASE/'RUN.sh'),
                               str(self.package),str(self.run),str(runtime or self.runtime)],
                              env=env,text=True,capture_output=True)

    def test_loads_exact_module_before_runtime_and_clears_pythonpath(self):
        p = self.launch('export TEST_MODULE="$*"')
        self.assertEqual(p.returncode,0,p.stderr)
        got = json.loads(self.report.read_text())
        self.assertEqual(got['module'],'load python/3.11/3.11.4')
        self.assertEqual(got['args'],[str(self.package/'worker.py'),str(self.run)])
        self.assertEqual(got['cwd'],str(self.run))
        self.assertIsNone(got['pythonpath'])
        self.assertEqual(got['no_user_site'],'1')

    def test_module_failure_stops_before_runtime(self):
        p = self.launch('return 23')
        self.assertNotEqual(p.returncode,0)
        self.assertFalse(self.report.exists())

    def test_missing_runtime_is_reported_after_module_load(self):
        p = self.launch('export TEST_MODULE="$*"',self.root/'absent runtime')
        self.assertEqual(p.returncode,2)
        self.assertIn('pinned runtime unavailable after module load',p.stderr)
        self.assertFalse(self.report.exists())

    def test_bad_arguments_stop(self):
        p = subprocess.run(['/bin/bash',str(BASE/'RUN.sh')],text=True,capture_output=True)
        self.assertEqual(p.returncode,2)
        self.assertIn('arguments required',p.stderr)

    def test_submission_filters_inherited_batch_options(self):
        spec = importlib.util.spec_from_file_location('refresh_submit',BASE/'submit.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from unittest.mock import patch
        with patch.dict(os.environ,{'SBATCH_MEM':'999G','BASH_ENV':'unwanted',
                                   'ENV':'unwanted','NRCS_TEST_KEEP':'yes'}):
            env = mod.submission_environment()
        self.assertNotIn('SBATCH_MEM',env)
        self.assertNotIn('BASH_ENV',env)
        self.assertNotIn('ENV',env)
        self.assertEqual(env['NRCS_TEST_KEEP'],'yes')

    def test_login_wrappers_use_standard_python(self):
        for filename, driver in (('START_HERE.sh','submit.py'),('CHECK.sh','check.py')):
            text = (BASE/filename).read_text()
            self.assertIn('exec python3 "$TASK_DIR/'+driver+'"',text)
            self.assertNotIn('runtime_env/bin/python',text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
