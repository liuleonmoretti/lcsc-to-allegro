"""Reference text case and compiled orientation must match the declared contract."""
import argparse,copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify


class Prefix(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='位号_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_lowercase_stops_before_job_download_or_native_and_explains_case_change(self):
        for prefix in ('u','AbCd'):
            options=argparse.Namespace(name='TEST_REFERENCE',refdes=prefix)
            with patch.object(lc2a,'ROOT',self.root),patch.object(lc2a,'native_build') as native:
                with self.assertRaisesRegex(ValueError,'16.6 uppercases RefDes text'):lc2a.convert(options)
                native.assert_not_called()
            self.assertEqual(list(self.root.iterdir()),[])
    def test_invalid_prefix_values_stop_before_io(self):
        for prefix in ('','ABCDE','U1','U*','位号',None,1):
            with patch.object(lc2a,'ROOT',self.root):
                with self.assertRaisesRegex(ValueError,'Invalid reference prefix'):
                    lc2a.convert(argparse.Namespace(name='TEST_REFERENCE',refdes=prefix))
            self.assertEqual(list(self.root.iterdir()),[])
    def test_direct_native_entry_does_not_silently_uppercase(self):
        with patch.object(lc2a,'ROOT',self.root),patch.object(lc2a,'run') as native:
            with self.assertRaisesRegex(ValueError,'16.6 uppercases RefDes text'):
                lc2a.native_build(self.root,{'pads':[]},'TEST_REFERENCE','u')
            native.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])
    def test_independent_state_requires_an_exact_uppercase_prefix(self):
        for prefix in ('u','AbCd','ABCDE',None):
            verify.save(self.root/'status.json',dict(status='built_and_geometry_checked',program_version='0.2.0',name='TEST_REFERENCE',refdes=prefix))
            with self.assertRaisesRegex(ValueError,'Reference prefix must be'):verify.check_build(self.root)


class Rotation(unittest.TestCase):
    def setUp(self):
        self.job=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job'])
        self.dra=verify.read_native(self.job/'verification/native/drawing.tsv')['texts']
    def rows(self,name):return verify.read_native(self.job/'verification/native'/name)['texts']
    def test_real_saved_placed_rotated_and_moved_angles(self):
        for filename,rotation in [('placed0.tsv',0),('placed90.tsv',90),('moved.tsv',0)]:
            proof=verify.check_refdes_rotation(self.dra,self.rows(filename),rotation)
            self.assertTrue(all(p['expected_deg']==p['actual_deg']==rotation for p in proof.values()))
    def test_assembly_text_rotated_180_is_rejected_with_other_fields_unchanged(self):
        rows=self.rows('placed0.tsv');next(t for t in rows if t['layer']=='REF DES/ASSEMBLY_TOP')['angle']+=180
        with self.assertRaisesRegex(ValueError,'rotation differs.*ASSEMBLY_TOP'):verify.check_refdes_rotation(self.dra,rows,0)
    def test_silkscreen_text_rotated_180_is_rejected_after_symbol_rotation(self):
        rows=self.rows('placed90.tsv');next(t for t in rows if t['layer']=='REF DES/SILKSCREEN_TOP')['angle']+=180
        with self.assertRaisesRegex(ValueError,'rotation differs.*SILKSCREEN_TOP'):verify.check_refdes_rotation(self.dra,rows,90)
    def test_missing_nonfinite_or_duplicate_angle_evidence_rejected(self):
        for value in (None,float('nan'),float('inf')):
            rows=self.rows('placed0.tsv')
            if value is None:rows[0].pop('angle')
            else:rows[0]['angle']=value
            with self.assertRaisesRegex(ValueError,'[Rr]otation'):verify.check_refdes_rotation(self.dra,rows,0)
        rows=self.rows('placed0.tsv');rows.append(copy.deepcopy(rows[0]))
        with self.assertRaisesRegex(ValueError,'duplicated'):verify.check_refdes_rotation(self.dra,rows,0)
    def test_full_turn_angle_is_equivalent(self):
        rows=self.rows('placed90.tsv')
        for row in rows:row['angle']+=360
        proof=verify.check_refdes_rotation(self.dra,rows,90)
        self.assertTrue(all(p['actual_deg']==90 for p in proof.values()))
    def test_old_report_without_orientation_proof_cannot_publish(self):
        with tempfile.TemporaryDirectory() as d:
            job=Path(d)/'job';shutil.copytree(self.job,job);report=verify.load(job/'verification.json')
            report['checks'].pop('compiled_refdes_orientation',None);verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'orientation has not'):verify.require_verified(job)
    def test_a_forged_success_flag_cannot_bypass_saved_rotation_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            job=Path(d)/'job';shutil.copytree(self.job,job);report=verify.load(job/'verification.json')
            rel='verification/native/placed0.tsv';path=job/rel;rows=path.read_text(encoding='ascii').splitlines();changed=0
            for i,line in enumerate(rows):
                v=line.split('\t')
                if v[0]=='T' and v[1]=='REF DES/ASSEMBLY_TOP':v[12]='180.000000';rows[i]='\t'.join(v);changed+=1
            self.assertEqual(changed,1);path.write_text('\n'.join(rows)+'\n',encoding='ascii')
            key=next(k for k in report['evidence_sha256'] if Path(k).as_posix()==rel)
            report['checks']['compiled_refdes_orientation']=True;report['evidence_sha256'][key]=verify.sha(path)
            verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'rotation differs.*ASSEMBLY_TOP'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
