"""Raw source errors must not disappear in an otherwise consistent KiCad/native chain."""
import copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import easyeda_raw,verify_upstream,lc2a,verify,reviewed_profiles

def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def child(n,k):return next(n for n in n if tag(n)==k)

class RawEasyEDA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='原始焊盘_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        self.raw=self.job/'source/.easyeda_cache/C123.json';self.raw.parent.mkdir(parents=True)
        self.original=self.job/'source/original.kicad_mod'
        self.data={'result':{'packageDetail':{'dataStr':{'head':{'x':100,'y':-200},'shape':[
            'PAD~ELLIPSE~90.123~-200~8~8~11~~1~2~~0~id1~0~~Y~0~0~0.2~90.123,-200',
            'PAD~ELLIPSE~110~-200~8~8~11~~2~2~~0~id2~0~~Y~0~0~0.2~110,-200']}}}}
        self.tree=sx.loads('(footprint "中文测试" (layer F.Cu) (property "LCSC Part" "C123") (pad 1 thru_hole circle (at -2.51 0 0) (size 2.032 2.032) (drill 1.016) (layers *.Cu *.Mask)) (pad 2 thru_hole circle (at 2.54 0 0) (size 2.032 2.032) (drill 1.016) (layers *.Cu *.Mask)))')
        self.evidence={'response_file':'source/.easyeda_cache/C123.json','footprint_file':'source/original.kicad_mod'}
    def raw_field(self,index,value):
        shapes=self.data['result']['packageDetail']['dataStr']['shape'];cells=shapes[0].split('~');cells[index]=str(value);shapes[0]='~'.join(cells)
    def write(self):
        self.raw.write_text(json.dumps(self.data),encoding='utf-8');self.original.write_text(sx.dumps(self.tree),encoding='utf-8')
    def prepare(self):
        self.write();out,audit=easyeda_raw.normalize(self.job,self.original,self.evidence)
        s=dict(lcsc='C123',fetch_evidence=self.evidence,raw_pad_audit=audit,source_file=out.name)
        self.rehash(s);return out,s
    def rehash(self,s):
        a=s['raw_pad_audit'];a['raw_response_sha256']=verify.sha(self.raw);a['original_export_sha256']=verify.sha(self.original)
        a['normalized_sha256']=verify.sha(self.job/a['normalized_file']);verify.save(self.job/'raw_pad_audit.json',a)
        s['files']={str(p.relative_to(self.job)):verify.sha(p) for p in self.job.rglob('*') if p.is_file()}
    def both_reject(self,index,value,message=None):
        _,s=self.prepare();self.raw_field(index,value);self.write();self.rehash(s)
        calls=[lambda:easyeda_raw.normalize(self.job,self.original,self.evidence),lambda:verify_upstream.check(self.job,s)]
        for i,call in enumerate(calls):
            with self.subTest(implementation=i),self.assertRaises(ValueError):call()
    def test_preserve_mask_and_restore_precision_with_chinese_path(self):
        out,s=self.prepare();r=verify_upstream.check(self.job,s)
        self.assertEqual(r['pad_count'],2);self.assertEqual(r['raw_mask_margins_mm'],[.0508])
        p=next(n for n in sx.loads(out.read_text(encoding='utf-8')) if tag(n)=='pad')
        self.assertAlmostEqual(child(p,'at')[1],-2.508758);self.assertAlmostEqual(child(p,'solder_mask_margin')[1],.0508)
        self.assertNotIn('solder_mask_margin',self.original.read_text(encoding='utf-8'))
    def test_nonplated_rejected_by_both(self):self.both_reject(15,'N')
    def test_unknown_plating_rejected_by_both(self):
        for flag in ('UNKNOWN','true','', '0'):
            with self.subTest(flag=flag):
                self.raw_field(15,'Y');self.both_reject(15,flag)
    def test_offset_hole_rejected_by_both(self):self.both_reject(19,'92.123,-200')
    def test_sub_native_tolerance_hole_center_rounding_is_recorded(self):
        self.raw_field(19,'90.1232,-200.0002');out,s=self.prepare();verify_upstream.check(self.job,s)
        self.assertAlmostEqual(easyeda_raw.raw_pads(self.data)['1']['hole_center_rounding_mm'][0],.0000508)
    def test_hole_center_disagreement_just_over_limit(self):self.both_reject(19,'90.124,-200')
    def test_missing_hole_center(self):self.both_reject(19,'')
    def test_slot_length_not_silently_circular(self):self.both_reject(13,'4')
    def test_slot_outline_not_silently_dropped(self):self.both_reject(14,'0,0,1,1')
    def test_nonzero_paste_expansion_rejected(self):self.both_reject(17,'-0.1')
    def test_mask_opening_must_be_positive(self):self.both_reject(18,'-5')
    def test_arbitrary_mask_preserved_without_profile(self):
        self.raw_field(18,'1.5');out,s=self.prepare();verify_upstream.check(self.job,s)
        p=next(n for n in sx.loads(out.read_text(encoding='utf-8')) if tag(n)=='pad');self.assertAlmostEqual(child(p,'solder_mask_margin')[1],.381)
    def test_nonfinite_dimensions_rejected(self):
        for index,value in ((2,'NaN'),(4,'Infinity'),(9,'-Infinity'),(18,'NaN')):
            with self.subTest(index=index):
                saved=copy.deepcopy(self.data);self.both_reject(index,value);self.data=saved
    def test_negative_hole_not_clamped_to_smd(self):self.both_reject(9,'-1')
    def test_zero_copper_not_clamped_positive(self):self.both_reject(4,'0')
    def test_unknown_and_bottom_layer_rejected(self):
        for layer in ('2','3','99'):
            with self.subTest(layer=layer):self.raw_field(6,'11');self.both_reject(6,layer)
    def test_nonorthogonal_angle_rejected(self):self.both_reject(11,'45')
    def test_ellipse_not_silently_circle(self):self.both_reject(5,'9')
    def test_duplicate_pin_rejected(self):self.both_reject(8,'2')
    def test_nonstandard_pin_normalization_rejected(self):self.both_reject(8,'1(FOO)')
    def test_schema_fields_cannot_disappear(self):
        _,s=self.prepare();shapes=self.data['result']['packageDetail']['dataStr']['shape'];shapes[0]='~'.join(shapes[0].split('~')[:-1])
        self.write();self.rehash(s)
        for call in (lambda:easyeda_raw.normalize(self.job,self.original,self.evidence),lambda:verify_upstream.check(self.job,s)):
            with self.assertRaises(ValueError):call()
    def test_extra_mechanical_hole_or_via_not_dropped(self):
        for command in ('HOLE','VIA'):
            with self.subTest(command=command):
                _,s=self.prepare();shapes=self.data['result']['packageDetail']['dataStr']['shape'];shapes.append(command+'~100~-200~1~id~0');self.write();self.rehash(s)
                for call in (lambda:easyeda_raw.normalize(self.job,self.original,self.evidence),lambda:verify_upstream.check(self.job,s)):
                    with self.assertRaises(ValueError):call()
                shapes.pop()
    def test_exported_fields_do_not_override_raw_geometry(self):
        for field_name,index,value in (('at',1,-2.0),('size',1,3),('drill',1,2)):
            with self.subTest(field=field_name):
                _,s=self.prepare();saved=copy.deepcopy(self.tree);p=next(n for n in self.tree if tag(n)=='pad');child(p,field_name)[index]=value;self.write();self.rehash(s)
                for call in (lambda:easyeda_raw.normalize(self.job,self.original,self.evidence),lambda:verify_upstream.check(self.job,s)):
                    with self.assertRaises(ValueError):call()
                self.tree=saved
    def test_normalized_mask_deletion_detected_after_coherent_rehash(self):
        out,s=self.prepare();tree=sx.loads(out.read_text(encoding='utf-8'));p=next(n for n in tree if tag(n)=='pad');p.remove(child(p,'solder_mask_margin'));out.write_text(sx.dumps(tree),encoding='utf-8');self.rehash(s)
        with self.assertRaisesRegex(ValueError,'solder_mask_margin'):verify_upstream.check(self.job,s)
    def test_normalized_position_cannot_use_export_rounding_tolerance(self):
        out,s=self.prepare();tree=sx.loads(out.read_text(encoding='utf-8'));p=next(n for n in tree if tag(n)=='pad');child(p,'at')[1]= -2.51;out.write_text(sx.dumps(tree),encoding='utf-8');self.rehash(s)
        with self.assertRaisesRegex(ValueError,'raw position'):verify_upstream.check(self.job,s)
    def test_nonpad_normalization_changes_rejected(self):
        out,s=self.prepare();tree=sx.loads(out.read_text(encoding='utf-8'));tree.append(sx.loads('(fp_text user "extra")'));out.write_text(sx.dumps(tree),encoding='utf-8');self.rehash(s)
        with self.assertRaisesRegex(ValueError,'non-pad'):verify_upstream.check(self.job,s)
    def test_missing_audit_blocks_online_release(self):
        _,s=self.prepare();s.pop('raw_pad_audit')
        with self.assertRaisesRegex(ValueError,'audit missing'):verify_upstream.check(self.job,s)
    def test_audit_hash_and_manifest_required(self):
        out,s=self.prepare();out.write_text(out.read_text(encoding='utf-8')+' ',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'unmanifested/changed'):verify_upstream.check(self.job,s)
    def test_source_cannot_bypass_audit(self):
        _,s=self.prepare();s['source_file']=self.evidence['footprint_file']
        with self.assertRaisesRegex(ValueError,'bypasses'):verify_upstream.check(self.job,s)
    def test_local_file_no_raw_claim(self):self.assertEqual(verify_upstream.check(self.job,{})['mode'],'local_file')
    def test_real_original_sources_and_reviewed_masks(self):
        for key in ('f103','pth'):
            with self.subTest(key=key):
                info=verify.load(lc2a.ROOT/'work'/(key+'_v2.json'));old=Path(info['job']);s=verify.load(old/'status.json')
                job=self.job/key;job.mkdir();ev=s['fetch_evidence']
                for rel in (ev['response_file'],ev['footprint_file']):
                    target=job/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/rel,target)
                audited,audit=easyeda_raw.normalize(job,job/ev['footprint_file'],ev,s['lcsc'])
                final,profile=reviewed_profiles.prepare(audited,s['lcsc'],job)
                state=dict(lcsc=s['lcsc'],fetch_evidence=ev,raw_pad_audit=audit,review_profile=profile,source_file=final.relative_to(job).as_posix(),files={str(p.relative_to(job)):verify.sha(p) for p in job.rglob('*') if p.is_file()})
                result=verify_upstream.check(job,state);self.assertEqual(result['pad_count'],48 if key=='f103' else 2)
                self.assertEqual(len(profile['mask_revisions']),result['pad_count'])

if __name__=='__main__':unittest.main()
