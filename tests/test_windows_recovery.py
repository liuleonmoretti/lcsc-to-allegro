import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import library,verify,windows_lock

class WindowsRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_recovery_');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'中文模拟库'
        (self.root/'footprint/MCU/Specific/docs').mkdir(parents=True)
        (self.root/'original.txt').write_bytes(b'original user data');self.before=library.snapshot(self.root)
        self.job=Path(verify.load(library.ROOT/'work/f103_v2.json')['job'])
        p=patch.object(library,'ROOT',self.base/'program');p.start();self.addCleanup(p.stop)
    def publish(self):return library.publish_job(self.job,self.root,'MCU',True,'测试收据与进程恢复；无生产发布',trial=True)
    def test_hard_exit_during_cleanup_recovers_and_is_idempotent(self):
        receipt=self.publish();record=library.load(receipt)
        victim=next(self.root/r['path'] for r in reversed(record['files']) if r['created'] and r['written'])
        config=self.base/'child.json';library.save(config,{'receipt':str(receipt),'victim':str(victim),'src':str(Path(library.__file__).parent)})
        script=self.base/'interrupt.py';script.write_text('''import json,os,sys
from pathlib import Path
c=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));sys.path.insert(0,c['src']);import library
unlink=Path.unlink
def interrupted(path,*args,**kwargs):
    result=unlink(path,*args,**kwargs)
    if path==Path(c['victim']):os._exit(73)
    return result
Path.unlink=interrupted
library.cleanup_test(c['receipt'])
''',encoding='ascii')
        si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
        child=subprocess.run([sys.executable,str(script),str(config)],capture_output=True,timeout=30,startupinfo=si)
        self.assertEqual(child.returncode,73,child.stderr)
        self.assertTrue((self.root/'.lc2a.lock').is_file())
        self.assertEqual(library.load(receipt)['state'],'cleanup_started')
        result=library.recover_test(receipt);self.assertEqual(result['state'],'cleaned')
        self.assertEqual(len(result['recovered_locks']),1);self.assertFalse((self.root/'.lc2a.lock').exists())
        library.recover_test(receipt);self.assertEqual(library.snapshot(self.root),self.before)
    def test_running_owner_cannot_be_recovered(self):
        receipt=self.publish();before=library.snapshot(self.root)
        with library.locked(self.root) as owner:
            record=library.load(receipt);record['lock_owner']=owner;library.save(receipt,record)
            lock=self.root/'.lc2a.lock';original=lock.read_bytes()
            with self.assertRaisesRegex(ValueError,'still running'):library.recover_test(receipt)
            self.assertEqual(lock.read_bytes(),original);self.assertEqual(library.snapshot(self.root),before)
        library.cleanup_test(receipt)
    def test_other_transaction_lock_cannot_be_removed(self):
        receipt=self.publish()
        with library.locked(self.root):
            original=(self.root/'.lc2a.lock').read_bytes()
            with self.assertRaisesRegex(ValueError,'different or unidentified'):library.recover_test(receipt)
            self.assertEqual((self.root/'.lc2a.lock').read_bytes(),original)
        library.cleanup_test(receipt)
    def test_unknown_legacy_owner_cannot_be_auto_removed(self):
        receipt=self.publish();record=library.load(receipt);owner={'pid':123,'token':'legacy'}
        record['lock_owner']=owner;library.save(receipt,record)
        lock=self.root/'.lc2a.lock';lock.write_bytes(library.data(owner));original=lock.read_bytes()
        with self.assertRaisesRegex(ValueError,'Legacy/unknown'):library.recover_test(receipt)
        self.assertEqual(lock.read_bytes(),original)
    def test_partial_publication_is_not_guessed_from_hash(self):
        receipt=self.publish();record=library.load(receipt);record['state']='prepared';library.save(receipt,record)
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'Partial publication'):library.recover_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_held_lock_cannot_be_replaced_while_checked(self):
        with library.locked(self.root):
            lock=self.root/'.lc2a.lock';original=lock.read_bytes()
            with windows_lock.exclusive_file(lock):
                with self.assertRaises(OSError):lock.write_bytes(b'replacement')
            self.assertEqual(lock.read_bytes(),original)
    def test_owner_identity_includes_process_creation_time(self):
        owner=windows_lock.new_owner();self.assertFalse(windows_lock.owner_has_exited(owner))
        # A changed creation identity represents PID reuse; no lock is removed here.
        owner['creation_filetime']-=1;self.assertTrue(windows_lock.owner_has_exited(owner))

if __name__=='__main__':unittest.main()
