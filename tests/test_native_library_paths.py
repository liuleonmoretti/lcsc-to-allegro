"""Real same-name PSM shadowing must not replace the files under verification."""
import contextlib,io,shutil,sys,tempfile,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify
ROOT=verify.ROOT;FIX=ROOT/'tests/fixtures/seed_cache'

class LibraryPathEvidence(unittest.TestCase):
    def test_external_paths_or_missing_records_are_rejected(self):
        variants=['PSMPATH\t("shadow" ".")\nPADPATH\t"."\n',
                  'PSMPATH\t"."\nPADPATH\t("." "external")\n',
                  'PSMPATH\t"."\n','PSMPATH\t"."\nPADPATH\t"."\nPADPATH\t"."\n',
                  'PSMPATH\t"external"\nPADPATH\t"."\n']
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'paths.tsv'
            path.write_text('PSMPATH\t"."\nPADPATH\t"."\n',encoding='ascii')
            self.assertEqual(verify.check_library_paths(path),{'psmpath':['.'],'padpath':['.']})
            for value in variants:
                path.write_text(value,encoding='ascii')
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'verified working directory'):verify.check_library_paths(path)

class ActualLibraryShadow(unittest.TestCase):
    def test_good_and_bad_external_psm_cannot_override_opposite_local_file(self):
        original_run=verify.run
        for correct in (False,True):
            with self.subTest(correct_local=correct),tempfile.TemporaryDirectory() as temp:
                base=Path(temp);job=base/'job';shutil.copytree(FIX/'bad_job',job)
                try:
                    s=verify.load(job/'status.json');target=job/'native'/(s['name']+'.psm')
                    if correct:
                        shutil.copyfile(FIX/'correct.psm',target)
                        key=next(k for k in s['files'] if Path(k).as_posix().casefold()==target.relative_to(job).as_posix().casefold());s['files'][key]=verify.sha(target)
                    verify.save(job/'status.json',s);before=verify.sha(target)
                    def shadow(args,cwd,timeout=60):
                        if '-s' in args and args[args.index('-s')+1]=='placement.scr':
                            stage=Path(cwd);folder=stage/'shadow';folder.mkdir()
                            src=FIX/'bad_job/native/test_ref_prefix_1.psm' if correct else FIX/'correct.psm'
                            shutil.copyfile(src,folder/target.name)
                            script=stage/'placement.il';script.write_text('unless(axlSetVariable("psmpath" list("shadow" ".")) error("Private shadow setup failed"))\n'+script.read_text(encoding='ascii'),encoding='ascii')
                        return original_run(args,cwd,timeout)
                    with patch.object(verify,'run',side_effect=shadow),contextlib.redirect_stdout(io.StringIO()):code=verify.verify_job(job,export=False)
                    r=verify.load(job/'verification.json');self.assertEqual(code,0 if correct else 1,r.get('error'))
                    self.assertTrue(r['checks']['native_library_search_paths'])
                    self.assertEqual(verify.check_library_paths(job/'verification/native/library_paths.tsv'),{'psmpath':['.'],'padpath':['.']})
                    angle=next(t['angle'] for t in verify.read_native(job/'verification/native/placed0.tsv')['texts'] if t['layer']=='REF DES/ASSEMBLY_TOP')
                    self.assertEqual(angle,0.0 if correct else 180.0)
                    if correct:self.assertEqual(len(r['actual_board_layers']),16)
                    else:self.assertEqual(r['error'],'Compiled RefDes rotation differs on REF DES/ASSEMBLY_TOP')
                    self.assertEqual(before,verify.sha(target))
                    with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)
                except BaseException:
                    archive=ROOT/'outputs/测试失败证据'/('search_paths_'+uuid.uuid4().hex+'.zip');archive.parent.mkdir(parents=True,exist_ok=True)
                    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                        for p in base.rglob('*'):
                            if p.is_file():z.write(p,p.relative_to(base).as_posix())
                    print('Native search-path failure evidence: '+str(archive),file=sys.stderr)
                    raise

class LibraryPathRelease(unittest.TestCase):
    def setUp(self):self.source=Path(verify.load(ROOT/'work/f103_v2.json')['job'])
    def test_old_report_without_path_isolation_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            r['checks'].pop('native_library_search_paths',None);verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Native library search paths were not isolated'):verify.require_verified(job)
    def test_forged_flag_and_rehashed_foreign_path_are_reparsed(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';shutil.copytree(self.source,job);r=verify.load(job/'verification.json')
            path=job/'verification/native/library_paths.tsv';path.write_text('PSMPATH\t("foreign" ".")\nPADPATH\t"."\n',encoding='ascii')
            key=next(k for k in r['evidence_sha256'] if Path(k).as_posix()==path.relative_to(job).as_posix())
            r['evidence_sha256'][key]=verify.sha(path);r['checks']['native_library_search_paths']=True;verify.save(job/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'verified working directory'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
