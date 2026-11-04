"""A cached pad must neither replace disk dimensions nor invalidate a good part."""
import contextlib,io,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify
from test_seed_cache import FIX,current,rehash_evidence

PAD_FIX=Path(__file__).parent/'fixtures/pad_cache'

class PadCache(unittest.TestCase):
    def test_real_correct_disk_is_independent_of_seed_pad_geometry(self):
        cfg=verify.load(verify.ROOT/'config.json')
        for name in ('good_seed_pad','wrong_seed_pad'):
            seed=PAD_FIX/(name+'.brd');before=verify.sha(seed)
            with self.subTest(seed=name),tempfile.TemporaryDirectory() as temp:
                job=Path(temp)/'job';shutil.copytree(FIX/'bad_job',job);state=verify.load(job/'status.json')
                target=job/'native'/(state['name']+'.psm');shutil.copy2(FIX/'correct.psm',target)
                key=next(k for k in state['files'] if k.replace('\\','/').casefold()==target.relative_to(job).as_posix().casefold())
                state['files'][key]=verify.sha(target);verify.save(job/'status.json',state)
                load=verify.load
                def injected(path):return dict(cfg,board_seed=str(seed)) if Path(path).resolve()==(verify.ROOT/'config.json').resolve() else load(path)
                with patch.object(verify,'load',side_effect=injected),contextlib.redirect_stdout(io.StringIO()):code=verify.verify_job(job,export=False)
                report=verify.load(job/'verification.json');native=job/'verification/native'
                self.assertEqual(code,0,report.get('error'))
                self.assertTrue(report['checks']['fresh_test_board_without_cached_padstacks'])
                self.assertEqual((native/'seed_padstacks.tsv').read_text(encoding='ascii'),'PADSTACKS\t0\n')
                self.assertEqual(report['test_board_cleanup']['fresh_session_padstack_count'],0)
                model=verify.load(job/'model.json');actual=verify.pad_definitions(native/'disk_pads.txt',model)[model['pads'][0]['padstack']]
                self.assertEqual(actual['geometry']['TOP']['size'],[1.8,1.8]);self.assertEqual(actual['drill']['diameter'],1.)
                self.assertEqual(before,verify.sha(seed))
                with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)

    def test_old_report_without_pad_cache_check_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);r=verify.load(job/'verification.json')
            r['checks'].pop('fresh_test_board_without_cached_padstacks',None);verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Test board padstack cache was not independently cleared'):verify.require_verified(job)

    def test_remaining_padstack_and_contradictory_count_are_rejected(self):
        for data in ('PADSTACKS\t1\nPADSTACK\tLC_WRONG\n','PADSTACKS\t0\nPADSTACK\tLC_WRONG\n','PADSTACKS\t-1\n'):
            with self.subTest(data=data),tempfile.TemporaryDirectory() as temp:
                job=Path(temp)/'job';shutil.copytree(current(),job);target=job/'verification/native/seed_padstacks.tsv'
                target.write_text(data,encoding='ascii');rehash_evidence(job,target)
                with self.assertRaisesRegex(ValueError,'Test board still contains cached padstack definitions'):verify.require_verified(job)

    def test_missing_pad_cache_readback_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(current(),job);(job/'verification/native/seed_padstacks.tsv').unlink()
            with self.assertRaisesRegex(ValueError,'Test evidence changed/missing: .*seed_padstacks.tsv'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
