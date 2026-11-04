"""Raw stdout/stderr must be semantically checked again before release."""
import shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import native_journal as journal,verify
ROOT=verify.ROOT;FIX=ROOT/'tests/fixtures/native_journal'

class StreamRecords(unittest.TestCase):
    def record(self,root,stdout=b'',stderr=b''):
        context=journal.begin(['allegro.exe','-s','read.scr','seed.dra'],root)
        (root/'allegro.jrl').write_bytes((FIX/'clean.jrl').read_bytes())
        return journal.finish(context,stdout,stderr,0,False,None)
    def test_clean_stream_banners_and_warnings_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.record(root,b'Loading design\n',b'WARNING: diagnostic warning\nAllegro 16.6\n')
            self.assertEqual(len(journal.audit(root,('read.scr',))),1)
    def test_both_streams_reject_errors_despite_clean_journal(self):
        for stream in ('stdout','stderr'):
            for line in (b'E- failed\n',b'*Error* failed\n',b'ERROR(SPMHNI-1): failed\n',b'Fatal error\n',b'Program has encountered a problem\n'):
                with self.subTest(stream=stream,line=line),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);self.record(root,**{stream:line})
                    with self.assertRaisesRegex(ValueError,'Native recorded '+stream+' reported an error'):journal.audit(root,('read.scr',))
    def test_stream_hash_is_still_checked_before_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.record(root);(root/'allegro_session_0001.stderr.bin').write_bytes(b'changed\n')
            with self.assertRaisesRegex(ValueError,'digest differs'):journal.audit(root,('read.scr',))
    def test_gb18030_stream_error_is_not_lost(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.record(root,stderr='版本信息\nE- 原生错误\n'.encode('gb18030'))
            with self.assertRaisesRegex(ValueError,'原生错误'):journal.audit(root,('read.scr',))

class StreamRelease(unittest.TestCase):
    def check_release(self,folder):
        source=Path(verify.load(ROOT/'work/f103_v2.json')['job'])
        for stream in ('stdout','stderr'):
            with self.subTest(stream=stream),tempfile.TemporaryDirectory() as temp:
                job=Path(temp)/'job';shutil.copytree(source,job)
                r=verify.load(job/'verification.json');s=verify.load(job/'status.json')
                raw=job/folder/f'allegro_session_0001.{stream}.bin';raw.write_bytes(raw.read_bytes()+b'\nE- INTENTIONAL_EVIDENCE_ERROR\n')
                meta=job/folder/'allegro_session_0001.json';m=verify.load(meta);m[stream+'_sha256']=verify.sha(raw);verify.save(meta,m)
                for p in (raw,meta):
                    rel=p.relative_to(job).as_posix()
                    if folder=='native':
                        key=next(k for k in s['files'] if Path(k).as_posix()==rel);s['files'][key]=verify.sha(p)
                    for k in [k for k in r['evidence_sha256'] if Path(k).as_posix()==rel]:r['evidence_sha256'][k]=verify.sha(p)
                if folder=='native':
                    verify.save(job/'status.json',s);r['status_sha256']=verify.sha(job/'status.json')
                verify.save(job/'verification.json',r)
                with self.assertRaisesRegex(ValueError,'Native recorded '+stream+' reported an error'):verify.require_verified(job)
    def test_rehashed_verification_streams_cannot_publish(self):self.check_release('verification/native')
    def test_rehashed_build_streams_cannot_publish(self):self.check_release('native')
