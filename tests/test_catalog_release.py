"""Release must recheck catalog assertions, not just a stored success flag."""
import shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class CatalogRelease(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='厂家目录发布_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)/'job'
        original=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job']);shutil.copytree(original,self.job)
        verify.require_verified(self.job)
    def test_legacy_report_missing_catalog_check_rejected(self):
        report=verify.load(self.job/'verification.json');report['checks'].pop('manufacturer_catalog_binding');verify.save(self.job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'Manufacturer catalog binding'):verify.require_verified(self.job)
    def test_coherently_rewritten_manufacturer_and_hashes_rejected_at_release(self):
        state=verify.load(self.job/'status.json');ev=state['fetch_evidence'];raw=self.job/ev['response_file'];data=verify.load(raw)
        data['result']['dataStr']['head']['c_para']['Manufacturer']='Unreviewed vendor';verify.save(raw,data)
        ev['response_sha256']=verify.sha(raw);ev['manufacturer_catalog']['manufacturer']='Unreviewed vendor'
        state['raw_pad_audit']['raw_response_sha256']=verify.sha(raw);verify.save(self.job/'raw_pad_audit.json',state['raw_pad_audit'])
        state['files']={rel:verify.sha(self.job/rel) for rel in state['files']};verify.save(self.job/'status.json',state)
        report=verify.load(self.job/'verification.json');report['status_sha256']=verify.sha(self.job/'status.json');verify.save(self.job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'Manufacturer catalog: reviewed manufacturer'):verify.require_verified(self.job)

if __name__=='__main__':unittest.main()
