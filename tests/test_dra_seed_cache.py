"""Real CLI rebuilds cannot inherit a DRA template's same-name pad definition."""
import json,os,shutil,stat,subprocess,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify
from test_readonly_seed import identity

ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'tests/fixtures/dra_cache'

class DraSeedCache(unittest.TestCase):
    def exercise(self,label):
        seed=FIX/(label+'.dra');before=identity(seed);cfg=verify.load(ROOT/'config.json')
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp)/'模板同名焊盘 中文程序';app.mkdir()
            try:
                shutil.copytree(ROOT/'src',app/'src',ignore=shutil.ignore_patterns('__pycache__'))
                source=app/'输入.kicad_mod';shutil.copyfile(FIX/'source.kicad_mod',source)
                template=app/'只读模板.dra';shutil.copyfile(seed,template);template.chmod(stat.S_IREAD)
                protected=identity(template);cfg['seed']=str(template);verify.save(app/'config.json',cfg)
                def cli(script,*args):
                    result=subprocess.run([sys.executable,'-X','utf8',str(app/'src'/script),*map(str,args)],
                        cwd=app,capture_output=True,encoding='utf-8',timeout=180)
                    (app/(script+'_console.txt')).write_text(result.stdout+result.stderr,encoding='utf-8')
                    self.assertEqual(result.returncode,0,(result.stdout+result.stderr)[-5000:]);return json.loads(result.stdout)
                converted=cli('lc2a.py','convert','--input',source,'--name','TEST_DRA_CACHE','--refdes','D',
                              '--mask-margin','0.05','--label','同名缓存 '+label)
                job=Path(converted['job']);state=verify.load(job/'status.json')
                state['fault_injection']={'test_only':True,'case':'dra_cache_'+label};verify.save(job/'status.json',state)
                report=cli('verify.py','--job',job)
                self.assertEqual(report['status'],'passed');self.assertEqual(len(report['actual_board_layers']),16)
                model=verify.load(job/'model.json');expected='lc_d625e5d0125554c7'
                self.assertEqual({p['padstack'] for p in model['pads']},{expected})
                for path in (job/'native/pads_report.txt',job/'verification/native/drawing_pads.txt',job/'verification/native/disk_pads.txt'):
                    pads=verify.pad_definitions(path,model)
                    self.assertEqual(set(pads),{expected},str(path))
                    for layer in ('TOP','BOTTOM','internal_pad_def'):
                        self.assertEqual(pads[expected]['geometry'][layer]['size'],[1.8,1.8])
                    self.assertEqual(pads[expected]['drill']['diameter'],1.)
                self.assertEqual(protected,identity(template));self.assertEqual(before,identity(seed))
                self.assertTrue(template.stat().st_file_attributes&stat.FILE_ATTRIBUTE_READONLY)
                self.assertTrue(report['presentation']['layers']['REF DES/SILKSCREEN_TOP']['visible'])
                with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)
            except BaseException:
                # Keep raw native failure evidence before TemporaryDirectory cleanup.
                archive=ROOT/'work/test_failure_archives'/('dra_cache_'+label+'_'+uuid.uuid4().hex+'.zip')
                archive.parent.mkdir(parents=True,exist_ok=True)
                with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                    for p in app.rglob('*'):
                        if p.is_file() and '__pycache__' not in p.parts:z.write(p,p.relative_to(app).as_posix())
                print('DRA cache failure evidence: '+str(archive),file=sys.stderr)
                raise

    def test_correct_same_name_template_rebuilds(self):self.exercise('good')
    def test_wrong_same_name_template_is_replaced_by_source_dimensions(self):self.exercise('wrong')

if __name__=='__main__':unittest.main()
