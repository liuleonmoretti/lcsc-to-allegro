"""Exercise real crash gaps and distinguish ownership from equal file contents."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import library,verify

CHILD='''import json,os,sys
from pathlib import Path
c=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));sys.path.insert(0,c['src']);import library
library.ROOT=Path(c['program']);root=Path(c['root'])
link=os.link;write=library.write_new;rename=os.rename;mkdir=Path.mkdir
def linked(src,dst,*args,**kwargs):
    result=link(src,dst,*args,**kwargs);dst=Path(dst)
    if dst.is_relative_to(root):
        if c['point']=='first' and dst.suffix=='.dra':os._exit(74)
        if c['point']=='last' and dst.name=='publication.json':os._exit(74)
    return result
def written(path,payload):
    result=write(path,payload);path=Path(path)
    if c['point']=='stage' and path.suffix=='.bin' and path.parent.name=='payload':os._exit(74)
    return result
def moved(src,dst,*args,**kwargs):
    result=rename(src,dst,*args,**kwargs)
    if c['point']=='directory' and Path(dst)==root/'pad':os._exit(74)
    return result
def made(path,*args,**kwargs):
    result=mkdir(path,*args,**kwargs)
    if c['point']=='directory_stage' and Path(path).parent.name=='directories':os._exit(74)
    return result
os.link=linked;library.write_new=written;os.rename=moved;Path.mkdir=made
library.publish_job(Path(c['job']),root,'MCU',True,'Fault test only',trial=True)
'''

class PartialPublication(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_partial_');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'中文 模拟库'
        (self.root/'footprint/MCU/Specific/docs').mkdir(parents=True)
        (self.root/'original.txt').write_bytes(b'original user data')
        self.before=library.snapshot(self.root)
        self.job=Path(verify.load(library.ROOT/'work/f103_v2.json')['job'])
        p=patch.object(library,'ROOT',self.base/'program');p.start();self.addCleanup(p.stop)
    def publish(self,trial=True):
        return library.publish_job(self.job,self.root,'MCU',True,'Fault test; no manufacturing claim',trial=trial)
    def interrupt(self,point):
        config=self.base/'child.json'
        library.save(config,{'src':str(Path(library.__file__).parent),'program':str(library.ROOT),'root':str(self.root),'job':str(self.job),'point':point})
        script=self.base/'child.py';script.write_text(CHILD,encoding='ascii')
        si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
        child=subprocess.run([sys.executable,str(script),str(config)],capture_output=True,timeout=45,startupinfo=si)
        self.assertEqual(child.returncode,74,child.stderr)
        receipts=list((library.ROOT/'outputs/入库记录').glob('*/receipt.json'));self.assertEqual(len(receipts),1)
        self.assertTrue((self.root/'.lc2a.lock').is_file())
        return receipts[0]
    def test_hard_exit_after_first_promotion_before_journal(self):
        receipt=self.interrupt('first');record=library.load(receipt)
        pending=[r for r in record['files'] if r['created'] and (self.root/r['path']).exists() and not r['written']]
        self.assertEqual(len(pending),1);self.assertEqual(record['state'],'ready')
        self.assertEqual(library.file_identity(self.root/pending[0]['path']),pending[0]['stage_identity'])
        library.recover_test(receipt);library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),self.before);self.assertFalse((self.root/'.lc2a.lock').exists())
    def test_hard_exit_during_private_staging_does_not_create_library_files(self):
        receipt=self.interrupt('stage');record=library.load(receipt)
        self.assertEqual(record['state'],'staging');self.assertEqual(library.snapshot(self.root),self.before)
        self.assertTrue(any((receipt.parent/r.get('stage_file','absent')).is_file() for r in record['files']))
        library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),self.before);self.assertFalse((self.root/'.lc2a.lock').exists())
    def test_hard_exit_after_last_public_file_cleans_all_recorded_directories(self):
        before_dirs={str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_dir()}
        receipt=self.interrupt('last');record=library.load(receipt)
        self.assertGreater(sum(r['written'] for r in record['files']),5)
        self.assertFalse(record['files'][-1]['written']);self.assertTrue((self.root/record['files'][-1]['path']).exists())
        library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),self.before)
        self.assertEqual({str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_dir()},before_dirs)
    def test_hard_exit_after_directory_promotion_before_journal(self):
        before_dirs=library.directory_snapshot(self.root)
        receipt=self.interrupt('directory');record=library.load(receipt)
        row=next(r for r in record['directory_records'] if r['path']=='pad')
        self.assertFalse(row['promoted']);self.assertNotIn('pad',record['created_directories'])
        self.assertEqual(library.directory_identity(self.root/'pad'),row['identity'])
        library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),self.before);self.assertEqual(library.directory_snapshot(self.root),before_dirs)
    def test_hard_exit_while_staging_directory_has_no_public_side_effect(self):
        before_dirs=library.directory_snapshot(self.root)
        receipt=self.interrupt('directory_stage');record=library.load(receipt)
        self.assertEqual(record['state'],'staging');self.assertTrue(record['directory_records'])
        self.assertNotIn('identity',record['directory_records'][0])
        library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),self.before);self.assertEqual(library.directory_snapshot(self.root),before_dirs)
    def test_replaced_empty_directory_is_not_deleted_during_recovery(self):
        receipt=self.interrupt('directory');target=self.root/'pad';owned=self.base/'saved_owned';rival=self.base/'rival'
        rival.mkdir();rival_id=library.directory_identity(rival)
        for path in (target,owned,rival):self.assertTrue(path.resolve().is_relative_to(self.base.resolve()))
        self.assertFalse(any(target.iterdir()));target.rename(owned);rival.rename(target)
        before_dirs=library.directory_snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'Unowned or replaced publication directory'):library.recover_test(receipt)
        self.assertEqual(library.directory_identity(target),rival_id);self.assertEqual(library.directory_snapshot(self.root),before_dirs)
        self.assertEqual(library.snapshot(self.root),self.before)
    def test_foreign_file_in_owned_directory_is_preserved_and_reported(self):
        receipt=self.interrupt('directory');foreign=self.root/'pad/external.txt';foreign.write_bytes(b'External file')
        result=library.recover_test(receipt)
        self.assertEqual(foreign.read_bytes(),b'External file');self.assertIn('pad',result['retained_directories'])
    def test_exception_after_directory_promotion_rolls_back_owned_directory(self):
        before_dirs=library.directory_snapshot(self.root);rename=os.rename;hit=[]
        def interrupted(src,dst,*args,**kwargs):
            result=rename(src,dst,*args,**kwargs)
            if Path(dst)==self.root/'pad':hit.append(dst);raise OSError('Injected directory promotion failure')
            return result
        with patch.object(os,'rename',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'directory promotion failure'):self.publish()
        self.assertEqual(len(hit),1);self.assertEqual(library.directory_snapshot(self.root),before_dirs)
        self.assertEqual(library.snapshot(self.root),self.before)
    def test_competing_empty_directory_is_never_replaced(self):
        rename=os.rename;foreign=[]
        def competing(src,dst,*args,**kwargs):
            if Path(dst)==self.root/'pad':
                Path(dst).mkdir();foreign.append(library.directory_identity(dst))
            return rename(src,dst,*args,**kwargs)
        with patch.object(os,'rename',side_effect=competing):
            with self.assertRaises(FileExistsError):self.publish()
        self.assertEqual(len(foreign),1);self.assertEqual(library.directory_identity(self.root/'pad'),foreign[0])
        self.assertEqual(library.snapshot(self.root),self.before)
    def test_identical_replacement_at_unjournaled_target_is_preserved(self):
        receipt=self.interrupt('first');record=library.load(receipt)
        row=next(r for r in record['files'] if r['path'].endswith('.dra'));target=self.root/row['path']
        other=self.base/'replacement.bin';other.write_bytes(target.read_bytes());os.replace(other,target)
        self.assertNotEqual(library.file_identity(target),row['stage_identity'])
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'Unowned file'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before);self.assertTrue(target.is_file())
    def test_external_edit_to_unjournaled_file_is_preserved(self):
        receipt=self.interrupt('first');record=library.load(receipt)
        target=self.root/next(r['path'] for r in record['files'] if r['path'].endswith('.dra'))
        target.write_bytes(b'External edit; do not delete')
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'witness missing or changed'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_missing_witness_refuses_partial_recovery(self):
        receipt=self.interrupt('first');record=library.load(receipt)
        row=next(r for r in record['files'] if r['path'].endswith('.dra'))
        witness=library.payload_path(receipt,row)
        self.assertTrue(witness.resolve().is_relative_to(receipt.parent.resolve()));witness.unlink()
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'witness missing or changed'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_exception_after_promotion_rolls_back_unjournaled_owned_link(self):
        link=os.link;hit=[]
        def interrupted(src,dst,*args,**kwargs):
            result=link(src,dst,*args,**kwargs)
            if Path(dst).is_relative_to(self.root) and Path(dst).suffix=='.dra':
                hit.append(dst);raise OSError('Injected failure after promotion')
            return result
        with patch.object(os,'link',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'after promotion'):self.publish()
        self.assertEqual(len(hit),1);self.assertEqual(library.snapshot(self.root),self.before)
        receipt=next((library.ROOT/'outputs/入库记录').glob('*/receipt.json'))
        self.assertEqual(library.load(receipt)['state'],'rolled_back')
    def test_same_bytes_created_by_another_writer_are_not_overwritten_or_rolled_back(self):
        link=os.link;hit=[]
        def rival(src,dst,*args,**kwargs):
            if Path(dst).is_relative_to(self.root) and Path(dst).suffix=='.dra':
                Path(dst).write_bytes(Path(src).read_bytes());hit.append(Path(dst))
            return link(src,dst,*args,**kwargs)
        with patch.object(os,'link',side_effect=rival):
            with self.assertRaises(FileExistsError):self.publish()
        self.assertEqual(len(hit),1);self.assertTrue(hit[0].is_file())
        receipt=next((library.ROOT/'outputs/入库记录').glob('*/receipt.json'))
        record=library.load(receipt);self.assertEqual(record['state'],'rollback_incomplete')
        self.assertTrue(any('Unowned file' in error for error in record['rollback_errors']))
    def test_copy_fallback_still_publishes_and_cleans_when_hardlinks_unavailable(self):
        with patch.object(os,'link',side_effect=OSError('Fixture filesystem has no hardlinks')):receipt=self.publish()
        self.assertEqual(library.load(receipt)['ownership_protocol'],'copy_v1')
        library.cleanup_test(receipt);self.assertEqual(library.snapshot(self.root),self.before)
    def test_copy_protocol_partial_state_is_not_recovered_from_matching_hashes(self):
        with patch.object(os,'link',side_effect=OSError('Fixture filesystem has no hardlinks')):receipt=self.publish()
        record=library.load(receipt);record['state']='ready';library.save(receipt,record)
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'lacks ownership witnesses'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
        record['state']='published';library.save(receipt,record);library.cleanup_test(receipt)
    def test_partial_state_with_running_owner_is_not_recovered(self):
        receipt=self.publish();record=library.load(receipt);record['state']='ready';library.save(receipt,record)
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'owner is still running'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
        record['state']='published';library.save(receipt,record);library.cleanup_test(receipt)
    def test_formal_publication_releases_links_and_preserves_source(self):
        before=library.digest(self.job/'native'/ (verify.load(self.job/'status.json')['name']+'.dra'))
        receipt=self.publish(trial=False);record=library.load(receipt)
        self.assertEqual(record['ownership_protocol'],'hardlink_v1');self.assertTrue(record['witnesses_released'])
        for row in record['files']:
            if row.get('stage_file'):self.assertFalse(library.payload_path(receipt,row).exists())
        row=next(r for r in record['files'] if r['path'].endswith('.dra'));target=self.root/row['path']
        self.assertEqual(target.stat().st_nlink,1);target.write_bytes(b'User edit after formal publication')
        self.assertEqual(library.digest(self.job/'native'/target.name),before)

if __name__=='__main__':unittest.main()
