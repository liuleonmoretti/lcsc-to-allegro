import argparse,copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify,reviewed_profiles

def real_job(key):
    p=lc2a.ROOT/'work'/key
    if not p.exists():raise unittest.SkipTest('Real integration sample has not been built')
    return Path(verify.load(p)['job'])

class Acceptance(unittest.TestCase):
    def setUp(self):
        self.job=real_job('f103_v2.json');self.s,self.m=verify.check_build(self.job)
        self.p,self.g=verify.read_expected(self.job,self.s)
    def test_windows_case_and_separator_manifest(self):
        self.assertEqual(self.s['status'],'built_and_geometry_checked')
        self.assertEqual(len(self.p),48)
    def test_chinese_label_source_and_output(self):
        self.assertIn('中文',self.s['label'])
        source=(self.job/self.s['source_file']).read_text(encoding='utf-8')
        self.assertIn('中文',source);self.assertNotIn('\ufffd',source)
        result=verify.require_verified(self.job)
        self.assertIn('中文',result['output_directory'])
        self.assertTrue((Path(result['output_directory'])/'docs/封装预览.svg').exists())
    def test_manufacturer_numbering_pitch_land_pattern(self):
        checks=verify.profile_checks(self.s,self.p,verify.design_rules(self.p,self.g),self.g)
        self.assertEqual(checks['manufacturer_pin_count'],48)
    def test_pin_swap_rejected_independently(self):
        actual=verify.read_native(self.job/'verification/native/drawing.tsv')['pins']
        bad=copy.deepcopy(actual);bad[0]['number'],bad[1]['number']=bad[1]['number'],bad[0]['number']
        with self.assertRaisesRegex(ValueError,'coordinate/rotation'):verify.compare_pins(self.p,bad,self.m)
    def test_y_mirror_rejected(self):
        actual=verify.read_native(self.job/'verification/native/drawing.tsv')['pins']
        for p in actual:p['at'][1]=-p['at'][1]
        with self.assertRaisesRegex(ValueError,'coordinate/rotation'):verify.compare_pins(self.p,actual,self.m)
    def test_wrong_pad_binding_rejected(self):
        actual=verify.read_native(self.job/'verification/native/drawing.tsv')['pins']
        actual[0]['padstack']='wrong_pad'
        with self.assertRaisesRegex(ValueError,'binding'):verify.compare_pins(self.p,actual,self.m)
    def test_missing_silkscreen_line_rejected(self):
        actual=verify.read_native(self.job/'verification/native/drawing.tsv')['graphics'];actual.pop()
        with self.assertRaisesRegex(ValueError,'Graphic count'):verify.compare_graphics(self.g,actual)
    def test_wrong_graphics_layer_rejected(self):
        actual=verify.read_native(self.job/'verification/native/drawing.tsv')['graphics'];actual[0]['layer']='ETCH/TOP'
        with self.assertRaisesRegex(ValueError,'Graphic layer'):verify.compare_graphics(self.g,actual)
    def test_silk_over_mask_rejected(self):
        g=copy.deepcopy(self.g)
        g.append(dict(kind='line',start=[-2.75,-4],end=[-2.75,-4.5],width=.15,layer=verify.LAYER['F.SilkS']))
        with self.assertRaisesRegex(ValueError,'Silkscreen-to-mask'):verify.design_rules(self.p,g)
    def test_courtyard_body_only_rejected(self):
        g=copy.deepcopy(self.g)
        for a in g:
            if a['layer']==verify.LAYER['F.CrtYd']:
                a['start']=[v*3.5/5.15 for v in a['start']];a['end']=[v*3.5/5.15 for v in a['end']]
        with self.assertRaisesRegex(ValueError,'excludes pads'):verify.design_rules(self.p,g)
    def test_body_center_offset_rejected(self):
        g=copy.deepcopy(self.g)
        for a in g:
            if a['layer']==verify.LAYER['F.Fab']:
                a['start'][0]+=.1;a['end'][0]+=.1
        with self.assertRaisesRegex(ValueError,'center is not origin'):verify.design_rules(self.p,g)
    def test_legacy_unverified_build_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);verify.save(p/'status.json',{'program_version':'0.1.0','status':'built_and_geometry_checked'})
            with self.assertRaisesRegex(ValueError,'Legacy'):verify.require_verified(p)
    def test_changed_source_native_or_evidence_blocks_release(self):
        for rel in [self.s['source_file'],'native/'+self.s['name']+'.psm','verification/native/disk_pads.txt']:
            with self.subTest(file=rel),tempfile.TemporaryDirectory() as tmp:
                job=Path(tmp)/'中文测试';shutil.copytree(self.job,job)
                with (job/rel).open('ab') as out:out.write(b'corruption')
                with self.assertRaisesRegex(ValueError,'changed|mismatch'):verify.require_verified(job)
    def test_missing_acceptance_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp)/'job';shutil.copytree(self.job,job);(job/'verification.json').unlink()
            with self.assertRaisesRegex(ValueError,'has not run'):verify.require_verified(job)
    def test_failed_acceptance_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp)/'job';shutil.copytree(self.job,job)
            verify.save(job/'verification.json',{'status':'failed'})
            with self.assertRaisesRegex(ValueError,'did not pass'):verify.require_verified(job)
    def test_success_flags_cannot_replace_missing_evidence(self):
        for case in ('empty_manifest','missing_raw_pad','wrong_count'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                job=Path(tmp)/'job';shutil.copytree(self.job,job);report=verify.load(job/'verification.json')
                if case=='empty_manifest':report['evidence_sha256']={}
                elif case=='wrong_count':report['pin_count']=47
                else:
                    for k in list(report['evidence_sha256']):
                        if 'raw_pads' in k:del report['evidence_sha256'][k]
                verify.save(job/'verification.json',report)
                with self.assertRaisesRegex(ValueError,'evidence|count differs'):verify.require_verified(job)
    def test_raw_pad_evidence_must_belong_to_the_verified_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp)/'job';shutil.copytree(self.job,job);report=verify.load(job/'verification.json')
            rel=next(k for k in report['evidence_sha256'] if k.endswith('capture.json'))
            path=verify.safe_rel(job,rel);capture=verify.load(path);capture['source_sha256']='0'*64
            verify.save(path,capture);report['evidence_sha256'][rel]=verify.sha(path)
            verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'different file'):verify.require_verified(job)
    def test_profile_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=self.job/self.s['review_profile']['original_source']
            with self.assertRaisesRegex(ValueError,'mismatch'):reviewed_profiles.prepare(src,'C53568',Path(tmp))

class ThroughHole(unittest.TestCase):
    def setUp(self):
        self.job=real_job('pth_v2.json');self.s,self.m=verify.check_build(self.job)
        self.p,self.g=verify.read_expected(self.job,self.s)
        self.report=self.job/'verification/native/disk_pads.txt'
    def test_real_through_hole_all_layers_and_drill(self):
        verify.require_verified(self.job);verify.check_pads(self.p,self.m,self.report)
        self.assertAlmostEqual(verify.design_rules(self.p,self.g)['minimum_annular_ring_mm'],.4,places=4)
    def tamper(self,field,value):
        rows=self.report.read_text(encoding='ascii').splitlines()
        for i,row in enumerate(rows):
            v=row.split('!')
            if row.startswith('S!LC_') and v[3]=='~DRILL':v[field]=value;rows[i]='!'.join(v)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.txt';p.write_text('\n'.join(rows),encoding='ascii')
            with self.assertRaises(ValueError):verify.check_pads(self.p,self.m,p)
    def test_unplated_hole_rejected(self):self.tamper(12,'N')
    def test_wrong_drill_rejected(self):self.tamper(7,'0.8000')
    def test_offset_drill_rejected(self):self.tamper(9,'0.1000')
    def test_missing_internal_copper_rejected(self):
        text='\n'.join(r for r in self.report.read_text(encoding='ascii').splitlines() if '!internal_pad_def!' not in r)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.txt';p.write_text(text,encoding='ascii')
            with self.assertRaisesRegex(ValueError,'internal_pad_def'):verify.check_pads(self.p,self.m,p)

class ProcessFailure(unittest.TestCase):
    def test_error_text_with_zero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'reported an error'):
                verify.assert_native_clean('E- Program has encountered a problem and must exit.',Path(d))
    def test_minidump_is_failure_even_with_clean_output(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'AllegroMiniDump.dmp').write_bytes(b'crash marker')
            with self.assertRaisesRegex(ValueError,'crash/recovery'):verify.assert_native_clean('OK',Path(d))

if __name__=='__main__':unittest.main()
