"""Real FIXED cache false-accept regression and fresh-board evidence gates."""
import contextlib,io,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify

FIX=Path(__file__).parent/'fixtures/seed_cache'

def current():return Path(verify.load(verify.ROOT/'work/f103_v2.json')['job'])

def rehash_evidence(job,path):
    report=verify.load(job/'verification.json');relative=path.relative_to(job).as_posix()
    key=next(k for k in report['evidence_sha256'] if k.replace('\\','/')==relative)
    report['evidence_sha256'][key]=verify.sha(path);verify.save(job/'verification.json',report)

class SeedCache(unittest.TestCase):
    def test_real_fixed_seed_does_not_hide_wrong_disk_psm(self):
        cfg=verify.load(verify.ROOT/'config.json');seed=FIX/'fixed_seed.brd';before=verify.sha(seed)
        for correct in (False,True):
            with self.subTest(correct_disk=correct),tempfile.TemporaryDirectory() as temp:
                job=Path(temp)/'job';shutil.copytree(FIX/'bad_job',job);state=verify.load(job/'status.json')
                if correct:
                    target=job/'native'/(state['name']+'.psm');shutil.copy2(FIX/'correct.psm',target)
                    key=next(k for k in state['files'] if k.replace('\\','/').casefold()==target.relative_to(job).as_posix().casefold())
                    state['files'][key]=verify.sha(target);verify.save(job/'status.json',state)
                load=verify.load
                def injected(path):return dict(cfg,board_seed=str(seed)) if Path(path).resolve()==(verify.ROOT/'config.json').resolve() else load(path)
                with patch.object(verify,'load',side_effect=injected),contextlib.redirect_stdout(io.StringIO()):code=verify.verify_job(job,export=False)
                report=verify.load(job/'verification.json')
                self.assertEqual(code,0 if correct else 1,report.get('error'))
                if not correct:self.assertEqual(report['error'],'Compiled RefDes rotation differs on REF DES/ASSEMBLY_TOP')
                self.assertTrue(report['checks']['fresh_test_board_without_cached_symbols'])
                self.assertEqual((job/'verification/native/seed_empty.tsv').read_text(encoding='ascii'),'SEED\t0\t0\tnil\n')
                self.assertEqual(before,verify.sha(seed))
                with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)

    def test_old_report_without_cache_clearance_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);r=verify.load(job/'verification.json')
            r['checks'].pop('fresh_test_board_without_cached_symbols',None);verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Test board cache was not independently cleared'):verify.require_verified(job)

    def test_forged_empty_seed_flag_cannot_hide_remaining_objects_or_ignore_fixed(self):
        for line in ('SEED\t1\t1\tnil\n','SEED\t0\t1\tnil\n','SEED\t0\t0\tt\n'):
            with self.subTest(line=line),tempfile.TemporaryDirectory() as temp:
                job=Path(temp)/'job';shutil.copytree(current(),job);target=job/'verification/native/seed_empty.tsv'
                target.write_text(line,encoding='ascii');rehash_evidence(job,target)
                with self.assertRaisesRegex(ValueError,'Test board still contains symbols/definitions or ignores FIXED'):verify.require_verified(job)

    def test_missing_cleaned_seed_evidence_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);(job/'verification/native/seed_cleaned.brd').unlink()
            with self.assertRaisesRegex(ValueError,'Test evidence changed/missing: .*seed_cleaned.brd'):verify.require_verified(job)

    def test_changed_seed_digest_is_rejected_even_with_updated_report_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);target=job/'verification/native/seed_cleanup.json'
            data=verify.load(target);data['source_sha256']='0'*64;verify.save(target,data);rehash_evidence(job,target)
            with self.assertRaisesRegex(ValueError,'Input test seed digest differs'):verify.require_verified(job)

    def test_incomplete_cleanup_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);target=job/'verification/native/seed_cleanup.json'
            data=verify.load(target);data['cleanup_completed']=False;verify.save(target,data);rehash_evidence(job,target)
            with self.assertRaisesRegex(ValueError,'Test seed cleanup did not complete'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
