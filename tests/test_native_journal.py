"""Journal-only errors, stale logs, and independently reparsed release evidence."""
import json,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,native_journal as journal,native_runtime,verify
ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'tests/fixtures/native_journal'
COMMAND=['D:/Cadence/SPB_16.6/tools/pcb/bin/allegro.exe','-s','read.scr','seed.dra']

class JournalRecords(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def record(self,data,prior=None):
        path=self.root/'allegro.jrl'
        if prior is not None:path.write_bytes(prior)
        c=journal.begin(COMMAND,self.root)
        if data is not None:path.write_bytes(data)
        return journal.finish(c,b'',b'banner',0,False,None)
    def test_actual_clean_and_error_logs_with_zero_exit(self):
        self.assertEqual(journal.scan((FIX/'clean.jrl').read_bytes())['errors'],[])
        r=self.record((FIX/'error.jrl').read_bytes())
        with self.assertRaisesRegex(ValueError,'Native journal reported an error'):journal.require_clean(r)
    def test_old_bad_journal_and_rolled_backup_do_not_poison_new_clean_session(self):
        bad=(FIX/'error.jrl').read_bytes();(self.root/'allegro.jrl,1').write_bytes(bad)
        r=self.record((FIX/'clean.jrl').read_bytes(),bad);journal.require_clean(r)
        self.assertEqual(len(journal.audit(self.root,('read.scr',))),1)
    def test_unchanged_journal_is_not_evidence_of_a_new_session(self):
        data=(FIX/'clean.jrl').read_bytes();r=self.record(data,data)
        with self.assertRaisesRegex(ValueError,'stale'):journal.require_clean(r)
    def test_missing_journal_is_rejected_even_for_zero_exit(self):
        with self.assertRaisesRegex(ValueError,'missing'):journal.require_clean(self.record(None))
    def test_incomplete_journal_is_rejected(self):
        data=(FIX/'clean.jrl').read_bytes().split(b'Journal end -')[0]
        with self.assertRaisesRegex(ValueError,'incomplete'):journal.require_clean(self.record(data))
    def test_command_input_text_does_not_create_a_false_error(self):
        data=(FIX/'clean.jrl').read_bytes()+b'\\i (00:00:03) skill error("*Error* command literal")\n'
        self.assertFalse(journal.scan(data)['errors'])
    def test_timestamped_error_channel_and_plain_error_are_detected(self):
        base=(FIX/'clean.jrl').read_bytes()
        for line in (b'\\e (00:00:03) Window is disabled\n',b'   (00:00:03) E- native error\n',b'   (00:00:03) *Error* native error\n'):
            with self.subTest(line=line):self.assertTrue(journal.scan(base+line)['errors'])
    def test_audit_reparses_raw_error_despite_forged_empty_error_list(self):
        r=self.record((FIX/'error.jrl').read_bytes());r['errors']=[]
        verify.save(self.root/'allegro_session_0001.json',r)
        with self.assertRaisesRegex(ValueError,'summary differs'):journal.audit(self.root,('read.scr',))
    def test_audit_rejects_changed_raw_bytes_or_wrong_workflow(self):
        self.record((FIX/'clean.jrl').read_bytes())
        with self.assertRaisesRegex(ValueError,'sequence differs'):journal.audit(self.root,('view.scr',))
        with (self.root/'allegro_session_0001.jrl').open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(ValueError,'digest differs'):journal.audit(self.root,('read.scr',))
    def test_audit_requires_all_sessions_and_freshness_consistency(self):
        r=self.record((FIX/'clean.jrl').read_bytes())
        with self.assertRaisesRegex(ValueError,'Missing or extra'):journal.audit(self.root,('read.scr','saved.scr'))
        r['prior_sha256']=r['journal_sha256'];verify.save(self.root/'allegro_session_0001.json',r)
        with self.assertRaisesRegex(ValueError,'freshness'):journal.audit(self.root,('read.scr',))

class NativeJournalErrors(unittest.TestCase):
    def test_both_real_runners_reject_journal_only_error_after_output(self):
        cfg=verify.load(ROOT/'config.json');seed=Path(cfg['seed']);before=verify.sha(seed)
        for module in (lc2a,verify):
            with self.subTest(module=module.__name__),native_runtime.temporary_stage(prefix='lc2a_journal_test_') as temp:
                stage=Path(temp);shutil.copyfile(seed,stage/'seed.dra')
                (stage/'read.il').write_text('f=outfile("valid_marker.txt") fprintf(f "OK\\n") close(f)\nerror("LC2A_JOURNAL_REAL_REGRESSION")\n',encoding='ascii')
                (stage/'read.scr').write_text('skill errset(load("read.il") t)\nexit\n',encoding='ascii')
                with self.assertRaisesRegex(ValueError,'Native journal reported an error'):
                    module.run([Path(cfg['cadence_bin'])/'allegro.exe','-s','read.scr','seed.dra'],stage)
                self.assertEqual((stage/'valid_marker.txt').read_text(),'OK\n')
                r=verify.load(stage/'allegro_session_0001.json');self.assertEqual(r['returncode'],0)
                self.assertTrue(r['errors']);self.assertNotIn(b'LC2A_JOURNAL_REAL_REGRESSION',(stage/'allegro_session_0001.stderr.bin').read_bytes())
                self.assertEqual(before,verify.sha(seed))

class JournalRelease(unittest.TestCase):
    def setUp(self):self.source=Path(verify.load(ROOT/'work/f103_v2.json')['job'])
    def test_old_report_without_session_check_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            r['checks'].pop('native_journal_sessions',None);verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Native journal sessions were not checked'):verify.require_verified(job)
    def test_legacy_build_needs_reconversion_even_if_geometry_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(self.source,job);s=verify.load(job/'status.json');s.pop('native_journal_revision',None)
            verify.save(job/'status.json',s);r=verify.load(job/'verification.json');r['status_sha256']=verify.sha(job/'status.json');verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Native build journals are absent'):verify.require_verified(job)
    def test_forged_flag_and_updated_outer_hash_do_not_hide_journal_error(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            raw=job/'verification/native/allegro_session_0001.jrl';raw.write_bytes((FIX/'error.jrl').read_bytes())
            meta=raw.with_suffix('.json');m=verify.load(meta);m['journal_sha256']=verify.sha(raw);m['errors']=[];verify.save(meta,m)
            for path in (raw,meta):
                key=next(k for k in r['evidence_sha256'] if Path(k).as_posix()==path.relative_to(job).as_posix())
                r['evidence_sha256'][key]=verify.sha(path)
            r['checks']['native_journal_sessions']=True;verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'summary differs'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
