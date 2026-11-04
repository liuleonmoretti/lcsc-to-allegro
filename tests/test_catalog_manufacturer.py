"""A correct C-number must not override contradictory manufacturer metadata."""
import argparse,contextlib,copy,io,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class ManufacturerCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='厂家目录中文_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def make(self,key='f103'):
        old=Path(verify.load(lc2a.ROOT/'work'/(key+'_v2.json'))['job']);s=verify.load(old/'status.json');job=self.root/key;job.mkdir()
        for rel in (s['fetch_evidence']['response_file'],s['fetch_evidence']['footprint_file']):
            p=job/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/rel,p)
        source=job/s['fetch_evidence']['footprint_file'];s['source_file']=source.relative_to(job).as_posix()
        s['fetch_evidence']=lc2a.downloaded_identity(job,source,s['lcsc'],s['review_profile']['profile'])
        self.refresh(job,s);return job,s
    def raw(self,job,s):return job/s['fetch_evidence']['response_file']
    def refresh(self,job,s):
        s['fetch_evidence']['response_sha256']=verify.sha(self.raw(job,s))
        s['files']={str(p.relative_to(job)):verify.sha(p) for p in job.rglob('*') if p.is_file()}
    def replace(self,job,s,keys,value,remove=False):
        data=verify.load(self.raw(job,s));cur=data
        for key in keys[:-1]:cur=cur[key]
        if remove:cur.pop(keys[-1],None)
        else:cur[keys[-1]]=value
        verify.save(self.raw(job,s),data);self.refresh(job,s)
    def parameters(self,*last):return ('result','dataStr','head','c_para',*last)
    def reject_both(self,job,s):
        for name,fn in [('producer',lambda:lc2a.downloaded_identity(job,job/s['source_file'],s['lcsc'],s['review_profile']['profile'])),
                        ('independent',lambda:verify.check_fetch_evidence(job,s))]:
            with self.subTest(implementation=name),self.assertRaisesRegex(ValueError,'Manufacturer catalog'):fn()
    def test_real_f103_metadata_matches_review(self):
        job,s=self.make();r=verify.check_fetch_evidence(job,s)
        self.assertEqual(r['manufacturer_catalog']['mpn'],'STM32F103C8T6');self.assertEqual(r['manufacturer_catalog']['manufacturer'],'ST(意法半导体)')
    def test_real_pth_metadata_matches_review(self):
        job,s=self.make('pth');r=verify.check_fetch_evidence(job,s)
        self.assertEqual(r['manufacturer_catalog']['mpn'],'1N4007RLG');self.assertEqual(r['manufacturer_catalog']['manufacturer'],'onsemi(安森美)')
    def test_same_package_different_mcu_suffix_is_rejected(self):
        job,s=self.make();self.replace(job,s,self.parameters('Manufacturer Part'),'STM32F103CBT6');self.reject_both(job,s)
    def test_other_manufacturer_is_not_inferred_as_st(self):
        job,s=self.make();self.replace(job,s,self.parameters('Manufacturer'),'GigaDevice(兆易创新)');self.reject_both(job,s)
    def test_component_title_must_agree_with_reviewed_mpn(self):
        job,s=self.make();self.replace(job,s,('result','title'),'STM32F103CBT6');self.reject_both(job,s)
    def test_symbol_name_must_agree_with_reviewed_mpn(self):
        job,s=self.make();self.replace(job,s,self.parameters('name'),'STM32F103CBT6');self.reject_both(job,s)
    def test_all_mpn_fields_can_agree_and_still_be_wrong(self):
        job,s=self.make()
        for path in [('result','title'),self.parameters('name'),self.parameters('Manufacturer Part')]:self.replace(job,s,path,'STM32F103CBT6')
        for key in ('title','name','mpn'):s['fetch_evidence']['manufacturer_catalog'][key]='STM32F103CBT6'
        self.reject_both(job,s)
    def test_package_labels_checked_at_all_three_locations(self):
        job,s=self.make();old=self.raw(job,s).read_bytes()
        for path in [self.parameters('package'),('result','packageDetail','title'),('result','packageDetail','dataStr','head','c_para','package')]:
            with self.subTest(path=path):
                self.raw(job,s).write_bytes(old);self.replace(job,s,path,'LQFP-64');self.reject_both(job,s)
    def test_coherent_wrong_package_labels_are_rejected(self):
        job,s=self.make()
        for path in [self.parameters('package'),('result','packageDetail','title'),('result','packageDetail','dataStr','head','c_para','package')]:self.replace(job,s,path,'LQFP-64')
        for key in ('package','package_title','package_parameter'):s['fetch_evidence']['manufacturer_catalog'][key]='LQFP-64'
        self.reject_both(job,s)
    def test_missing_identity_fields_are_not_assumed(self):
        job,s=self.make();old=self.raw(job,s).read_bytes()
        for path in [self.parameters('Manufacturer Part'),self.parameters('Manufacturer'),self.parameters('name'),self.parameters('Supplier'),self.parameters('Supplier Part'),('result','title')]:
            with self.subTest(path=path):
                self.raw(job,s).write_bytes(old);self.replace(job,s,path,None,True);self.reject_both(job,s)
    def test_nonstring_or_empty_labels_rejected(self):
        job,s=self.make()
        for value in (None,True,123,[],{},'', '   '):
            with self.subTest(value=value):self.replace(job,s,self.parameters('Manufacturer'),value);self.reject_both(job,s)
    def test_whitespace_trim_is_allowed_but_not_identity_guessing(self):
        job,s=self.make();self.replace(job,s,self.parameters('Manufacturer'),'  ST(意法半导体)  ')
        self.assertEqual(lc2a.downloaded_identity(job,job/s['source_file'],'C8734','C8734')['manufacturer_catalog'],s['fetch_evidence']['manufacturer_catalog'])
        verify.check_fetch_evidence(job,s)
    def test_fullwidth_mpn_does_not_match_ascii_part(self):
        job,s=self.make();self.replace(job,s,self.parameters('Manufacturer Part'),'ＳＴＭ３２Ｆ１０３Ｃ８Ｔ６');self.reject_both(job,s)
    def test_supplier_must_match_the_reviewed_catalog(self):
        job,s=self.make();self.replace(job,s,self.parameters('Supplier'),'Different supplier');self.reject_both(job,s)
    def test_bad_package_parameter_object_is_rejected(self):
        job,s=self.make();self.replace(job,s,('result','packageDetail','dataStr','head','c_para'),None);self.reject_both(job,s)
    def test_profile_and_request_cannot_differ(self):
        job,s=self.make();s['review_profile']['profile']='C53568';self.reject_both(job,s)
    def test_recorded_binding_missing_requires_regeneration(self):
        job,s=self.make();s['fetch_evidence'].pop('manufacturer_catalog')
        with self.assertRaisesRegex(ValueError,'stored binding'):verify.check_fetch_evidence(job,s)
    def test_wrong_record_cannot_override_correct_response(self):
        job,s=self.make();s['fetch_evidence']['manufacturer_catalog']['mpn']='STM32F103CBT6'
        with self.assertRaisesRegex(ValueError,'stored binding'):verify.check_fetch_evidence(job,s)
    def test_nonprofile_catalog_lookup_does_not_claim_manufacturer_fit(self):
        job,s=self.make();s.pop('review_profile');s['fetch_evidence']=lc2a.downloaded_identity(job,job/s['source_file'],'C8734')
        r=verify.check_fetch_evidence(job,s);self.assertFalse(r['manufacturer_catalog']['manufacturer_claim'])
    def test_wrong_metadata_stops_convert_before_profile_or_native_generation(self):
        job,s=self.make();raw=verify.load(self.raw(job,s));raw['result']['dataStr']['head']['c_para']['Manufacturer']='Wrong vendor'
        source=(job/s['source_file']).read_bytes()
        def fetch(args,cwd,timeout=120):
            out=Path(cwd);(out/'library.pretty').mkdir();(out/'library.pretty/part.kicad_mod').write_bytes(source)
            (out/'.easyeda_cache').mkdir();verify.save(out/'.easyeda_cache/C8734.json',raw);return 'Fault-injection response only; no network request.'
        args=argparse.Namespace(name='CATALOG_REJECT',lcsc='C8734',profile='C8734',input=None,refdes='U',label='目录故障',center=None,body_bounds=None,mask_margin=0,prepare_only=False)
        capture=io.StringIO()
        with patch.object(lc2a,'ROOT',self.root/'convert'),patch.object(lc2a,'run',side_effect=fetch),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(capture):code=lc2a.convert(args)
        native.assert_not_called();self.assertEqual(code,1);result=json.loads(capture.getvalue())
        self.assertIn('Manufacturer catalog',result['error']);self.assertFalse((Path(result['job'])/'reviewed').exists())

if __name__=='__main__':unittest.main()
