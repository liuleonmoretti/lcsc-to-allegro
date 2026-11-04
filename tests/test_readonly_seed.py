"""Exercise both real readonly templates through the isolated CLI pipeline."""
import hashlib,json,os,shutil,stat,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify

ROOT=Path(__file__).resolve().parents[1]

def identity(path):
    path=Path(path);s=path.stat()
    return (hashlib.sha256(path.read_bytes()).hexdigest(),s.st_dev,s.st_ino,s.st_mtime_ns,s.st_file_attributes)

class ReadonlySeed(unittest.TestCase):
    def test_both_readonly_source_templates_pass_real_cli_and_remain_protected(self):
        self.assertEqual(os.name,'nt','This regression exercises the Windows 16.6 backend')
        cfg=verify.load(ROOT/'config.json');original={key:identity(cfg[key]) for key in ('seed','board_seed')}
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp)/'只读模板 程序';app.mkdir()
            shutil.copytree(ROOT/'src',app/'src',ignore=shutil.ignore_patterns('__pycache__'))
            source=app/'测试输入.kicad_mod';shutil.copyfile(ROOT/'tests/fixtures/seed_cache/bad_job/source.kicad_mod',source)
            local=dict(cfg)
            for key in ('seed','board_seed'):
                target=app/(key+Path(cfg[key]).suffix);shutil.copyfile(cfg[key],target)
                target.chmod(stat.S_IREAD);local[key]=str(target)
                self.assertTrue(target.stat().st_file_attributes&stat.FILE_ATTRIBUTE_READONLY)
            protected={key:identity(local[key]) for key in ('seed','board_seed')}
            verify.save(app/'config.json',local)
            def cli(script,*args):
                p=subprocess.run([sys.executable,'-X','utf8',str(app/'src'/script),*map(str,args)],cwd=app,capture_output=True,encoding='utf-8',timeout=180)
                self.assertEqual(p.returncode,0,(p.stdout+p.stderr)[-5000:]);return json.loads(p.stdout)
            result=cli('lc2a.py','convert','--input',source,'--name','TEST_READONLY_COMBINED','--refdes','J','--label','只读模板 回归')
            job=Path(result['job']);state=verify.load(job/'status.json')
            state['fault_injection']={'test_only':True,'case':'both_readonly_templates'};verify.save(job/'status.json',state)
            report=cli('verify.py','--job',job)
            self.assertEqual(report['status'],'passed');self.assertEqual(len(report['actual_board_layers']),16)
            self.assertTrue(report['presentation']['layers']['REF DES/SILKSCREEN_TOP']['visible'])
            self.assertEqual(protected,{key:identity(local[key]) for key in ('seed','board_seed')})
            self.assertEqual(original,{key:identity(cfg[key]) for key in ('seed','board_seed')})
            for path in (job/'native/TEST_READONLY_COMBINED.dra',job/'verification/native/placement.brd'):
                self.assertFalse(path.stat().st_file_attributes&stat.FILE_ATTRIBUTE_READONLY)
            with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
