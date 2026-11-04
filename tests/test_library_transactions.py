import contextlib,copy,json,os,shutil,sys,tempfile,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import library,lc2a,verify

class LibraryTransactions(unittest.TestCase):
    def setUp(self):
        info=lc2a.ROOT/'work/f103_v2.json'
        if not info.exists():self.skipTest('Run test.ps1 -Integration first')
        self.job=Path(json.loads(info.read_text(encoding='utf-8-sig'))['job'])
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'中文 公共库';self.root.mkdir()
        for category in ('MCU','Common'):(self.root/'footprint'/category/'Specific/docs').mkdir(parents=True)
        (self.root/'已有说明.md').write_text('用户原有的文件不得改变',encoding='utf-8')
        self.original=library.snapshot(self.root)
        p=patch.object(library,'ROOT',self.base/'程序 中文');p.start();self.addCleanup(p.stop)
    def publish(self,category='MCU',trial=True):
        return library.publish_job(self.job,self.root,category,True,'实验：实际原生加载与几何检查通过；无制造认证',trial)
    def test_publish_classifies_real_smd_and_cleanup_restores_library(self):
        result=library.trial_job(self.job,self.root,'MCU',True,'实际几何通过，文件操作测试后清理',native=False)
        self.assertTrue(result['library_restored']);self.assertEqual(library.snapshot(self.root),self.original)
        self.assertTrue(result['library_directories_restored']);self.assertEqual(result['before_directories'],result['after_directories'])
        record=library.load(result['receipt'])
        self.assertTrue(any(x['path'].startswith('pad/SMD/') for x in record['files']))
        self.assertTrue(any(x['path'].startswith('footprint/MCU/Specific/') for x in record['files']))
        self.assertEqual(record['state'],'cleaned')
        self.assertTrue((Path(result['receipt']).parent/'入库测试.md').exists())
    def test_preexisting_identical_file_survives_cleanup(self):
        status=library.load(self.job/'status.json');name=status['name']
        src=self.job/'native'/(name+'.psm');dst=self.root/'footprint/MCU/Specific'/src.name
        dst.write_bytes(src.read_bytes());before=library.snapshot(self.root)
        receipt=self.publish();library.cleanup_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_partial_write_failure_rolls_back(self):
        original_write=library.write_new;calls=[]
        def failing(path,payload):
            calls.append(path)
            if len(calls)==4:raise OSError('Injected disk write failure')
            return original_write(path,payload)
        with patch.object(library,'write_new',side_effect=failing):
            with self.assertRaisesRegex(OSError,'Injected'):self.publish()
        self.assertEqual(library.snapshot(self.root),self.original)
        receipts=list((library.ROOT/'outputs/入库记录').rglob('receipt.json'))
        self.assertEqual(library.load(receipts[-1])['state'],'rolled_back')
    def test_persistent_receipt_failure_does_not_prevent_rollback(self):
        original_save=library.save;failures=[]
        def failing(path,record):
            if any(r.get('written') for r in record.get('files',[])):
                failures.append(path);raise OSError('Injected persistent receipt failure')
            return original_save(path,record)
        with patch.object(library,'save',side_effect=failing):
            with self.assertRaisesRegex(RuntimeError,'rollback=rolled_back'):self.publish()
        self.assertGreaterEqual(len(failures),2)
        self.assertEqual(library.snapshot(self.root),self.original)
        self.assertFalse((self.root/'.lc2a.lock').exists())
    def test_close_failure_removes_only_owned_payload(self):
        target=self.base/'created.bin';original_open=Path.open
        @contextlib.contextmanager
        def closing_failure(path,*args,**kwargs):
            with original_open(path,*args,**kwargs) as stream:yield stream
            if path==target and args==('xb',):raise OSError('Injected close failure')
        with patch.object(Path,'open',closing_failure):
            with self.assertRaisesRegex(OSError,'close failure'):library.write_new(target,b'payload')
        self.assertFalse(target.exists())
    def test_close_failure_preserves_external_edit(self):
        target=self.base/'changed.bin';original_open=Path.open
        @contextlib.contextmanager
        def closing_failure(path,*args,**kwargs):
            with original_open(path,*args,**kwargs) as stream:yield stream
            if path==target and args==('xb',):
                with original_open(path,'wb') as stream:stream.write(b'external content')
                raise OSError('Injected close failure after external edit')
        with patch.object(Path,'open',closing_failure):
            with self.assertRaisesRegex(OSError,'external edit'):library.write_new(target,b'payload')
        self.assertEqual(target.read_bytes(),b'external content')
    def test_close_failure_preserves_replacement_with_same_bytes(self):
        target=self.base/'replaced.bin';replacement=self.base/'replacement.bin';original_open=Path.open
        @contextlib.contextmanager
        def closing_failure(path,*args,**kwargs):
            with original_open(path,*args,**kwargs) as stream:yield stream
            if path==target and args==('xb',):
                with original_open(replacement,'xb') as stream:stream.write(b'payload')
                os.replace(replacement,target)
                raise OSError('Injected close failure after replacement')
        with patch.object(Path,'open',closing_failure):
            with self.assertRaisesRegex(OSError,'replacement'):library.write_new(target,b'payload')
        self.assertEqual(target.read_bytes(),b'payload')
    def test_external_change_blocks_all_cleanup(self):
        receipt=self.publish();record=library.load(receipt)
        dst=self.root/record['files'][0]['path'];dst.write_bytes(b'external edit; preserve')
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'Changed test file'):library.cleanup_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_regular_publication_cannot_be_automatically_removed(self):
        receipt=self.publish(trial=False);before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'explicit test'):library.cleanup_test(receipt)
        self.assertEqual(library.snapshot(self.root),before)
    def test_duplicate_publication_does_not_modify_files(self):
        receipt=self.publish();before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'documentation already exists'):self.publish()
        self.assertEqual(library.snapshot(self.root),before);library.cleanup_test(receipt)
    def test_shared_pad_preserved_until_dependent_test_removed(self):
        first=self.publish();second=self.publish('Common')
        before=library.snapshot(self.root)
        with self.assertRaisesRegex(ValueError,'Shared pad'):library.cleanup_test(first)
        self.assertEqual(library.snapshot(self.root),before)
        library.cleanup_test(second)
        for p in library.load(first)['core_files']:
            if p.endswith('.pad'):self.assertTrue(Path(p).exists())
        library.cleanup_test(first);self.assertEqual(library.snapshot(self.root),self.original)
    def test_preexisting_pad_with_matching_spec_is_reused(self):
        receipt=self.publish();record=library.load(receipt)
        pad=next(self.root/r['path'] for r in record['files'] if r['path'].endswith('.pad'))
        meta=pad.parent/'docs'/(pad.stem+'_lc2a.json')
        # Generate a second real native definition with the same geometry, rather
        # than appending bytes and assuming a corrupt binary is still valid.
        info=library.load(self.job/'status.json');model=library.load(self.job/'model.json')
        rebuilt=self.base/'重新生成';rebuilt.mkdir()
        lc2a.native_build(rebuilt,model,info['name'],info['refdes'])
        candidate=(rebuilt/'native'/pad.name).read_bytes()
        self.assertNotEqual(candidate,pad.read_bytes())
        pad.write_bytes(candidate)
        saved=library.load(meta);saved['sha256']=library.digest(pad);library.save(meta,saved)
        try:
            second=self.publish('Common')
        except Exception:
            # A native timeout is a failed test, never a reason to retry or
            # increase its limit. Keep its raw scratch evidence before cleanup.
            dest=lc2a.ROOT/'outputs/测试失败证据'/('pad_reuse_'+time.strftime('%Y%m%d_%H%M%S')+'_'+os.urandom(3).hex())
            try:
                dest.mkdir(parents=True,exist_ok=False)
                archive=dest/'scratch.zip'
                entries={}
                with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                    for f in sorted(self.base.rglob('*')):
                        if f.is_file():
                            rel=f.relative_to(self.base).as_posix();entries[rel]=verify.sha(f);z.write(f,rel)
                with zipfile.ZipFile(archive) as z:
                    if z.testzip() is not None:raise RuntimeError('Failure evidence ZIP CRC check failed')
                verify.save(dest/'manifest.json',dict(test=self.id(),source_job=str(self.job),scratch=str(self.base),archive_sha256=verify.sha(archive),files=entries))
                print('Native test failure evidence: '+str(dest),file=sys.stderr)
            except Exception as archive_error:
                print('Failure evidence archive also failed: '+str(archive_error),file=sys.stderr)
            raise
        r=library.load(second)
        padrow=next(x for x in r['files'] if x['path'].endswith('.pad'))
        self.assertFalse(padrow['created']);self.assertEqual(padrow['sha256'],library.digest(pad))
        self.assertEqual(library.load(r['reused_pad_preflight'])['status'],'passed')
        library.cleanup_test(second);self.assertTrue(pad.exists())
    def test_reuse_preflight_failure_happens_before_any_library_write(self):
        before=library.snapshot(self.root)
        with patch.object(library,'verify_reused_pad_plans',side_effect=ValueError('Injected native pad mismatch')):
            with self.assertRaisesRegex(ValueError,'native pad mismatch'):self.publish()
        self.assertEqual(library.snapshot(self.root),before)
        receipt=next((library.ROOT/'outputs/入库记录').rglob('receipt.json'))
        self.assertEqual(library.load(receipt)['state'],'rolled_back')
    def test_source_edit_after_validation_rejected_before_publication(self):
        original=self.job;self.job=self.base/'任务副本';shutil.copytree(original,self.job)
        check=verify.require_verified
        def changing(job):
            result=check(job);s=library.load(job/'status.json');p=job/'native'/(s['name']+'.dra')
            p.write_bytes(p.read_bytes()+b' concurrent edit')
            return result
        with patch.object(verify,'require_verified',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'Source file changed while planning'):self.publish()
        self.assertEqual(library.snapshot(self.root),self.original)
    def test_report_replaced_after_validation_rejected(self):
        original=self.job;self.job=self.base/'报告副本';shutil.copytree(original,self.job)
        check=verify.require_verified
        def changing(job):
            result=check(job);changed=copy.deepcopy(result);changed['status']='failed';library.save(job/'verification.json',changed)
            return result
        with patch.object(verify,'require_verified',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'Verification report changed while planning'):self.publish()
        self.assertEqual(library.snapshot(self.root),self.original)
    def test_repeated_round_trip_and_repeated_cleanup(self):
        for _ in range(2):
            receipt=self.publish();library.cleanup_test(receipt);library.cleanup_test(receipt)
            self.assertEqual(library.snapshot(self.root),self.original)
    def test_path_escape_rejected_before_delete(self):
        receipt=self.publish();record=library.load(receipt)
        record['files'][0]['path']='../outside.txt';library.save(receipt,record)
        with self.assertRaisesRegex(ValueError,'escapes'):library.cleanup_test(receipt)
    def test_lock_prevents_overlapping_transaction(self):
        with library.locked(self.root):
            with self.assertRaisesRegex(ValueError,'already active'):self.publish()
        self.assertEqual(library.snapshot(self.root),self.original)
    def test_failed_library_native_check_still_cleans_owned_files(self):
        with patch.object(library,'verify_library_files',side_effect=ValueError('Injected native mismatch')):
            with self.assertRaisesRegex(ValueError,'Library trial failed'):
                library.trial_job(self.job,self.root,'MCU',True,'故障注入')
        self.assertEqual(library.snapshot(self.root),self.original)
        result=library.load(next((library.ROOT/'outputs/入库记录').rglob('trial.json')))
        self.assertEqual(result['status'],'failed');self.assertIn('Injected',result['error'])
    def test_unexpected_empty_directory_prevents_false_restored_report(self):
        cleanup=library.cleanup_test
        def external_directory(receipt):
            result=cleanup(receipt);(self.root/'external_empty_directory').mkdir();return result
        with patch.object(library,'cleanup_test',side_effect=external_directory):
            with self.assertRaisesRegex(ValueError,'Library trial failed'):
                library.trial_job(self.job,self.root,'MCU',True,'Directory snapshot fault',native=False)
        result=library.load(next((library.ROOT/'outputs/入库记录').rglob('trial.json')))
        self.assertTrue(result['library_files_restored']);self.assertFalse(result['library_directories_restored'])
        self.assertEqual(result['added_directories'],['external_empty_directory'])
        self.assertTrue((self.root/'external_empty_directory').is_dir())

if __name__=='__main__':unittest.main()
