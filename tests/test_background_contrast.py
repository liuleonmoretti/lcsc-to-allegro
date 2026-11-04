"""Saved visibility must include readable foreground/background colors."""
import json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify

ROOT=Path(__file__).resolve().parents[1]
FIX=Path(__file__).parent/'fixtures/background_contrast'

def view(background=(0,0,0),foreground=(245,245,245)):
    rows=['BG\t'+'\t'.join(map(str,background))]
    for cls in ('REF DES','PACKAGE GEOMETRY'):
        for sub,visible in [('SILKSCREEN_TOP','t'),('ASSEMBLY_TOP','nil')]:
            rows.append('\t'.join(['V',cls+'/'+sub,visible,*map(str,foreground)]))
    return '\n'.join(rows)+'\n'

class Contrast(unittest.TestCase):
    def check(self,text):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'view.tsv';path.write_text(text,encoding='ascii')
            return verify.check_default_view(path)
    def test_black_white_and_identical_colors_have_known_ratios(self):
        self.assertEqual(verify.color_contrast([0,0,0],[255,255,255]),21)
        self.assertEqual(verify.color_contrast([245,245,245],[245,245,245]),1)
    def test_bright_identical_foreground_and_background_rejected(self):
        with self.assertRaisesRegex(ValueError,'contrast below'):self.check(view((245,245,245)))
    def test_near_identical_bright_colors_are_also_rejected(self):
        with self.assertRaisesRegex(ValueError,'contrast below'):self.check(view((250,250,250)))
    def test_dark_text_on_white_is_readable_and_not_rejected_by_brightness(self):
        r=self.check(view((255,255,255),(0,0,0)))
        self.assertEqual(r['background_rgb'],[255,255,255]);self.assertTrue(all(x==21 for x in r['layer_background_contrast'].values()))
    def test_adjacent_gray_levels_straddle_policy_boundary(self):
        with self.assertRaisesRegex(ValueError,'contrast below'):self.check(view(foreground=(116,116,116)))
        self.assertTrue(all(x>=4.5 for x in self.check(view(foreground=(117,117,117)))['layer_background_contrast'].values()))
    def test_missing_duplicate_or_invalid_background_cannot_pass(self):
        for text in (view().split('\n',1)[1],view()+'BG\t0\t0\t0\n',
                     view((-1,0,0)),view((256,0,0)),view().replace('BG\t0\t0\t0','BG\tx\t0\t0')):
            with self.subTest(text=text),self.assertRaises(ValueError):self.check(text)
    def test_bad_layer_rgb_and_visibility_are_rejected(self):
        for text in (view(foreground=(256,200,200)),view(foreground=(-1,200,200)),view().replace('SILKSCREEN_TOP\tt','SILKSCREEN_TOP\tyes')):
            with self.subTest(text=text),self.assertRaises(ValueError):self.check(text)

class Release(unittest.TestCase):
    def setUp(self):self.source=Path(verify.load(ROOT/'work/f103_v2.json')['job'])
    def test_old_report_without_contrast_check_cannot_publish(self):
        with tempfile.TemporaryDirectory() as d:
            job=Path(d)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            r['checks'].pop('default_view_background_contrast',None);verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'background contrast has not'):verify.require_verified(job)
    def test_success_flag_and_updated_hash_do_not_bypass_low_contrast(self):
        with tempfile.TemporaryDirectory() as d:
            job=Path(d)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            path=job/'verification/native/default_view.tsv';path.write_text(view((245,245,245)),encoding='ascii')
            rel=next(k for k in r['evidence_sha256'] if Path(k).as_posix()=='verification/native/default_view.tsv')
            r['evidence_sha256'][rel]=verify.sha(path);r['checks']['default_view_background_contrast']=True
            verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'contrast below'):verify.require_verified(job)

class NativeLightSeed(unittest.TestCase):
    def test_actual_light_seed_generates_readable_black_background(self):
        seed=FIX/'light_seed.dra';before=verify.sha(seed);cfg=verify.load(ROOT/'config.json')
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp)/'背景中文测试';app.mkdir()
            shutil.copytree(ROOT/'src',app/'src',ignore=shutil.ignore_patterns('__pycache__'))
            source=app/'输入.kicad_mod';shutil.copyfile(ROOT/'tests/fixtures/seed_cache/bad_job/source.kicad_mod',source)
            cfg['seed']=str(seed);verify.save(app/'config.json',cfg)
            def cli(script,*args):
                p=subprocess.run([sys.executable,'-X','utf8',str(app/'src'/script),*map(str,args)],cwd=app,capture_output=True,encoding='utf-8',timeout=180)
                self.assertEqual(p.returncode,0,(p.stdout+p.stderr)[-5000:]);return json.loads(p.stdout)
            converted=cli('lc2a.py','convert','--input',source,'--name','TEST_LIGHT_SEED','--refdes','J','--label','浅色模板 回归')
            job=Path(converted['job']);s=verify.load(job/'status.json')
            self.assertEqual(s['presentation_revision'],2)
            s['fault_injection']={'test_only':True,'case':'light_seed_regression'};verify.save(job/'status.json',s)
            r=cli('verify.py','--job',job)
            self.assertEqual(r['status'],'passed');self.assertEqual(len(r['actual_board_layers']),16)
            self.assertEqual(r['presentation']['background_rgb'],[0,0,0])
            self.assertTrue(r['checks']['default_view_background_contrast'])
            self.assertTrue(all(x>=4.5 for x in r['presentation']['layer_background_contrast'].values()))
            self.assertEqual(verify.sha(seed),before)
            with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
