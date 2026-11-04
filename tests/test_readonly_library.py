"""Use real PAD/PSM files to validate mutable clones of protected inputs."""
import hashlib,os,shutil,stat,sys,tempfile,time,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import library,verify

FIX=Path(__file__).parent/'fixtures/readonly_library'

def signature(root):
    result={}
    for p in root.rglob('*'):
        if not p.is_file():continue
        s=p.stat();result[p.relative_to(root).as_posix()]=(verify.sha(p),s.st_dev,s.st_ino,s.st_mtime_ns,s.st_file_attributes)
    return result

class ReadonlyLibrary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        original=Path(verify.load(verify.ROOT/'work/pth_v2.json')['job']);self.source=self.root/'source';self.source.mkdir()
        state=verify.load(original/'status.json')
        for rel in state['files']:
            target=self.source/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original/rel,target)
            if target.suffix.lower() in ('.dra','.psm','.pad'):target.chmod(stat.S_IREAD)
        state['fault_injection']={'test_only':True,'case':'readonly_library_regression'};verify.save(self.source/'status.json',state)

    def native_check(self,function,*args):
        try:return function(*args)
        except Exception:
            # Preserve the failed attempt before unittest cleans the owned copy.
            # Propagate the original failure; no retry or relaxed native checks.
            dest=verify.ROOT/'outputs/测试失败证据'/('readonly_library_'+time.strftime('%Y%m%d_%H%M%S')+'_'+os.urandom(3).hex())
            try:
                dest.mkdir(parents=True,exist_ok=False);archive=dest/'scratch.zip';entries={}
                with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                    for path in sorted(self.root.rglob('*')):
                        if not path.is_file():continue
                        rel=path.relative_to(self.root).as_posix();entries[rel]=verify.sha(path);z.write(path,rel)
                with zipfile.ZipFile(archive) as z:
                    assert z.testzip() is None
                    assert all(hashlib.sha256(z.read(k)).hexdigest()==v for k,v in entries.items())
                verify.save(dest/'manifest.json',dict(test=self.id(),scratch=str(self.root),archive_sha256=verify.sha(archive),files=entries))
                print('Readonly native failure evidence: '+str(dest),file=sys.stderr)
            except Exception as archive_error:
                print('Failure evidence archive also failed: '+str(archive_error),file=sys.stderr)
            raise

    def test_library_readback_overlays_readonly_source_and_library_files(self):
        root=self.root/'library';root.mkdir();rows=[];core=[]
        for p in (self.source/'native').iterdir():
            if p.suffix.lower() not in ('.dra','.psm','.pad'):continue
            target=root/p.name;shutil.copyfile(p,target);target.chmod(stat.S_IREAD)
            core.append(str(target));rows.append(dict(path=target.name,sha256=verify.sha(target)))
        before_source=signature(self.source);before_library=signature(root)
        receipt=self.root/'report/receipt.json';receipt.parent.mkdir()
        verify.save(receipt,dict(job=str(self.source),library=str(root),transaction_id='test-only-readonly',core_files=core,files=rows,test_fixture=True))
        report=Path(self.native_check(library.verify_library_files,receipt));r=verify.load(report)
        self.assertEqual(r['status'],'passed');self.assertEqual(len(r['actual_board_layers']),16)
        self.assertEqual(before_source,signature(self.source));self.assertEqual(before_library,signature(root))
        with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(report.parent)

    def test_reused_pad_preflight_overlays_readonly_source_pad(self):
        pad=next((self.source/'native').glob('*.pad'));candidate=FIX/pad.name;payload=candidate.read_bytes()
        self.assertNotEqual(payload,pad.read_bytes(),'A genuinely different validated PAD is needed to exercise override')
        root=self.root/'library';root.mkdir();target=root/pad.name;target.write_bytes(payload);target.chmod(stat.S_IREAD)
        before_source=signature(self.source);before_library=signature(root)
        receipt=self.root/'report/receipt.json';receipt.parent.mkdir()
        result=Path(self.native_check(library.verify_reused_pad_plans,self.source,{target:payload},[str(target)],receipt));r=verify.load(result)
        self.assertEqual(r['status'],'passed');self.assertEqual(len(r['actual_board_layers']),16)
        state=verify.load(result.parent/'status.json');self.assertTrue(state['reused_pad_preflight']['before_library_write'])
        self.assertEqual(verify.sha(result.parent/'native'/pad.name),verify.sha(candidate))
        self.assertEqual(before_source,signature(self.source));self.assertEqual(before_library,signature(root))
        with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(result.parent)


if __name__=='__main__':unittest.main()
