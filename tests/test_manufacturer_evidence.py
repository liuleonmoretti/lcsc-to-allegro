"""Manufacturer review provenance must survive coherent record/hash changes."""
import copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import reviewed_profiles,verify,lc2a

class ManufacturerEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='厂家依据_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def make(self,key='f103'):
        old=Path(verify.load(lc2a.ROOT/'work'/(key+'_v2.json'))['job']);state=verify.load(old/'status.json');profile=state['review_profile']['profile']
        job=self.root/key;job.mkdir();src=job/'source.kicad_mod';shutil.copy2(old/state['review_profile']['original_source'],src)
        out,record=reviewed_profiles.prepare(src,profile,job)
        s={'review_profile':record,'source_file':out.relative_to(job).as_posix(),'source_sha256':verify.sha(out)}
        self.refresh(job,s);return job,s
    def refresh(self,job,state,save_review=True):
        if save_review:verify.save(job/'reviewed/review.json',state['review_profile'])
        state['files']={str(p.relative_to(job)):verify.sha(p) for p in job.rglob('*') if p.is_file()}
    def reject(self,job,s,pattern):
        with self.assertRaisesRegex(ValueError,pattern):verify.check_manufacturer_evidence(job,s)
    def test_both_real_documents_are_bound_to_reviewed_versions(self):
        for key,rev,pages in (('f103','20',[98,99,100]),('pth','13',[1,7])):
            with self.subTest(key=key):
                job,s=self.make(key);result=verify.check_manufacturer_evidence(job,s)
                self.assertEqual(result['review_basis']['document_revision'],rev)
                self.assertEqual(result['review_basis']['pdf_pages_1_based'],pages)
    def test_missing_pdf_is_rejected_even_when_removed_from_manifest(self):
        job,s=self.make();pdf=job/s['review_profile']['datasheet'];self.assertTrue(pdf.resolve().is_relative_to(job.resolve()));pdf.unlink();self.refresh(job,s)
        self.reject(job,s,'missing reviewed datasheet')
    def test_swapped_pdf_rejected_after_coherent_hash_update(self):
        job,s=self.make();pdf=job/s['review_profile']['datasheet'];shutil.copy2(lc2a.ROOT/'docs/datasheets/1N4007.pdf',pdf)
        s['review_profile']['datasheet_sha256']=verify.sha(pdf);self.refresh(job,s)
        self.reject(job,s,'independently reviewed document/version')
    def test_appended_bytes_not_accepted_as_same_document(self):
        job,s=self.make();pdf=job/s['review_profile']['datasheet'];pdf.write_bytes(pdf.read_bytes()+b'\nchanged')
        s['review_profile']['datasheet_sha256']=verify.sha(pdf);self.refresh(job,s);self.reject(job,s,'independently reviewed')
    def test_self_reported_pdf_hash_is_not_authority(self):
        job,s=self.make();s['review_profile']['datasheet_sha256']='0'*64;self.refresh(job,s);self.reject(job,s,'digest record differs')
    def test_pdf_must_be_in_conversion_manifest(self):
        job,s=self.make();key=next(k for k in s['files'] if k.lower().endswith('.pdf'));s['files'].pop(key)
        self.reject(job,s,'missing from conversion manifest')
    def test_pdf_path_cannot_point_at_an_unrelated_file(self):
        job,s=self.make();s['review_profile']['datasheet']='source.kicad_mod';self.refresh(job,s);self.reject(job,s,'wrong datasheet path')
    def test_old_unknown_or_noninteger_recipe_revision_rejected(self):
        job,s=self.make()
        for version in (3,999,4.0,True):
            with self.subTest(version=version):
                s['review_profile']['revision']=version;self.refresh(job,s);self.reject(job,s,'unreviewed recipe revision')
    def test_review_basis_is_required(self):
        job,s=self.make();s['review_profile'].pop('review_basis');self.refresh(job,s);self.reject(job,s,'review basis differs')
    def test_wrong_document_revision_date_or_page_rejected(self):
        job,s=self.make();basis=copy.deepcopy(s['review_profile']['review_basis'])
        for field,value in (('document_revision','19'),('document_date','2024-01'),('pdf_pages_1_based',[1]),('references',['Figure 1'])):
            with self.subTest(field=field):
                s['review_profile']['review_basis']=dict(basis,**{field:value});self.refresh(job,s);self.reject(job,s,'review basis differs')
    def test_wrong_package_mpn_or_publisher_rejected(self):
        job,s=self.make();basis=copy.deepcopy(s['review_profile']['review_basis'])
        for field,value in (('mpn','STM32F103CBT6'),('package','LQFP64'),('publisher','Other'),('policy_id','unreviewed')):
            with self.subTest(field=field):
                s['review_profile']['review_basis']=dict(basis,**{field:value});self.refresh(job,s);self.reject(job,s,'review basis differs')
    def test_old_user_pdf_cannot_claim_current_url_bytes(self):
        job,s=self.make('pth');s['review_profile']['review_basis']['url_role']='exact_download_origin';self.refresh(job,s)
        self.reject(job,s,'review basis differs')
    def test_wrong_url_or_manufacturer_label_rejected(self):
        job,s=self.make();original=copy.deepcopy(s['review_profile'])
        for field,value in (('url','https://example.com/fake.pdf'),('manufacturer','Different manufacturer')):
            with self.subTest(field=field):
                s['review_profile']=dict(original,**{field:value});self.refresh(job,s);self.reject(job,s,'manufacturer or reference URL differs')
    def test_recorded_dimension_policy_cannot_disagree_with_recipe(self):
        job,s=self.make();original=copy.deepcopy(s['review_profile'])
        for field,value in (('body_bounds_mm',[-4,-4,4,4]),('courtyard_bounds_mm',[-9,-9,9,9]),('mask_margin_mm',.08)):
            with self.subTest(field=field):
                s['review_profile']=dict(original,**{field:value});self.refresh(job,s);self.reject(job,s,'dimensional policy differs')
    def test_reviewed_source_identity_and_digest_are_bound(self):
        job,s=self.make();original=copy.deepcopy(s['review_profile'])
        for field,value in (('reviewed_source','source.kicad_mod'),('reviewed_sha256','0'*64)):
            with self.subTest(field=field):
                s['review_profile']=dict(original,**{field:value});self.refresh(job,s);self.reject(job,s,'reviewed source')
    def test_missing_saved_review_rejected(self):
        job,s=self.make();p=job/'reviewed/review.json';self.assertTrue(p.resolve().is_relative_to(job.resolve()));p.unlink();self.refresh(job,s,False)
        self.reject(job,s,'saved review missing')
    def test_unmanifested_saved_review_rejected(self):
        job,s=self.make();s['files'].pop(str(Path('reviewed/review.json')));self.reject(job,s,'saved review missing')
    def test_saved_review_must_equal_status(self):
        job,s=self.make();other=copy.deepcopy(s['review_profile']);other['manufacturer']='different';verify.save(job/'reviewed/review.json',other);self.refresh(job,s,False)
        self.reject(job,s,'saved review differs')
    def test_duplicate_review_json_keys_rejected(self):
        job,s=self.make();p=job/'reviewed/review.json';t=p.read_text(encoding='utf-8');p.write_text('{"revision":4,'+t[1:],encoding='utf-8');self.refresh(job,s,False)
        self.reject(job,s,'duplicate saved review key')
    def test_nonfinite_saved_review_json_rejected(self):
        job,s=self.make();p=job/'reviewed/review.json';t=p.read_text(encoding='utf-8');p.write_text('{"invalid":NaN,'+t[1:],encoding='utf-8');self.refresh(job,s,False)
        self.reject(job,s,'non-finite review JSON')
    def test_no_profile_makes_no_manufacturer_claim(self):
        result=verify.check_manufacturer_evidence(self.root,{})
        self.assertEqual(result['mode'],'no_profile');self.assertFalse(result['manufacturer_claim'])
    def test_unknown_profile_is_not_a_known_manufacturer_review(self):
        job,s=self.make();s['review_profile']['profile']='UNKNOWN';self.refresh(job,s);self.reject(job,s,'unknown profile')
    def test_producer_rejects_wrong_local_pdf_before_writing_review(self):
        job,s=self.make();sandbox=self.root/'wrong_input';(sandbox/'src').mkdir(parents=True);(sandbox/'docs/datasheets').mkdir(parents=True)
        shutil.copy2(lc2a.ROOT/'docs/datasheets/1N4007.pdf',sandbox/'docs/datasheets/STM32F103C8.pdf')
        new=self.root/'newjob';new.mkdir();src=new/'input.kicad_mod';shutil.copy2(job/'source.kicad_mod',src)
        with patch.object(reviewed_profiles,'__file__',str(sandbox/'src/reviewed_profiles.py')):
            with self.assertRaisesRegex(ValueError,'datasheet bytes/version changed'):reviewed_profiles.prepare(src,'C8734',new)
        self.assertFalse((new/'reviewed').exists())
    def test_producer_rejects_missing_local_pdf_before_writing_review(self):
        job,s=self.make();sandbox=self.root/'missing_input';(sandbox/'src').mkdir(parents=True)
        new=self.root/'newjob';new.mkdir();src=new/'input.kicad_mod';shutil.copy2(job/'source.kicad_mod',src)
        with patch.object(reviewed_profiles,'__file__',str(sandbox/'src/reviewed_profiles.py')):
            with self.assertRaisesRegex(ValueError,'datasheet is missing'):reviewed_profiles.prepare(src,'C8734',new)
        self.assertFalse((new/'reviewed').exists())

if __name__=='__main__':unittest.main()
