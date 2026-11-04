"""Release must require the new independent evidence gate, not legacy success."""
import shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class ManufacturerRelease(unittest.TestCase):
    def copy_job(self):
        tmp=tempfile.TemporaryDirectory(prefix='厂家资料发布_');self.addCleanup(tmp.cleanup);job=Path(tmp.name)/'job'
        shutil.copytree(Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job']),job);return job
    def test_legacy_success_without_document_binding_cannot_publish(self):
        job=self.copy_job();r=verify.load(job/'verification.json');r['checks'].pop('manufacturer_document_binding',None);verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'Manufacturer document binding'):verify.require_verified(job)
    def test_actual_recipe_evidence_is_rechecked_at_release(self):
        job=self.copy_job();s=verify.load(job/'status.json');s['review_profile']['datasheet_sha256']='0'*64
        verify.save(job/'reviewed/review.json',s['review_profile'])
        s['files']={p:verify.sha(job/p) for p in s['files']};verify.save(job/'status.json',s)
        r=verify.load(job/'verification.json');r['status_sha256']=verify.sha(job/'status.json');verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'datasheet digest record differs'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
