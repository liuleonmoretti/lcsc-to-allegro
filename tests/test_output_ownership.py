"""Output ownership is separate from geometry validity and directory location."""
import _winapi,contextlib,io,ntpath,os,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify


class OutputOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='输出归属 [测试]_');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.a=self.base/'first/task_abcdef';self.b=self.base/'copied/task_abcdef'
        self.a.mkdir(parents=True);self.b.mkdir(parents=True);self.out=self.base/'outputs/中文 输出 [成品]_abcdef'
        (self.out/'docs').mkdir(parents=True)
        self.old=dict(status='passed',job=str(self.a),output_directory=str(self.out),fixture=True)
        verify.save(self.out/'docs/verification.json',self.old)
        (self.out/'任务路径.txt').write_text(str(self.a)+'\n',encoding='utf-8')
        (self.out/'user_keep.txt').write_bytes(b'keep unchanged')
        for job in (self.a,self.b):verify.save(job/'status.json',dict(status='failed'));verify.save(job/'verification.json',self.old)
        self.root_patch=patch.object(verify,'ROOT',self.base);self.root_patch.start();self.addCleanup(self.root_patch.stop)

    def snapshot(self):return {p.relative_to(self.out).as_posix():verify.sha(p) for p in self.out.rglob('*') if p.is_file()}

    def fail(self,job):
        stream=io.StringIO()
        with patch.object(verify,'native_tests',side_effect=AssertionError('Expected preflight failure')),contextlib.redirect_stdout(stream):
            self.assertEqual(verify.verify_job(job,export=False),1)
        return verify.load(job/'verification.json')

    def fixture_files(self,job):
        (job/'native').mkdir();(job/'native/TEST.dra').write_bytes(b'fixture, not native data')
        (job/'verification').mkdir();(job/'verification/preview.svg').write_text('<svg/>',encoding='utf-8')
        verify.save(job/'model.json',dict(fixture=True));(job/'source.kicad_mod').write_text('(module FIXTURE)',encoding='utf-8')
        return dict(label='中文 输出 [成品]',name='TEST',source_file='source.kicad_mod')

    def junction(self,target,alias):
        self.assertTrue(target.resolve().is_relative_to(self.base.resolve()));self.assertTrue(alias.absolute().is_relative_to(self.base.absolute()))
        _winapi.CreateJunction(str(target),str(alias))
        def cleanup():
            self.assertTrue(alias.is_junction());self.assertTrue(alias.absolute().is_relative_to(self.base.absolute()));alias.rmdir()
        self.addCleanup(cleanup)

    def test_failed_copy_does_not_invalidate_original_export(self):
        before=self.snapshot();r=self.fail(self.b)
        self.assertEqual(self.snapshot(),before);self.assertEqual(r['output_invalidation']['status'],'not_modified')
        self.assertIn('another job',r['output_invalidation']['reason'])

    def test_forged_previous_job_cannot_override_output_owner(self):
        verify.save(self.b/'verification.json',dict(self.old,job=str(self.b)));before=self.snapshot()
        self.assertEqual(self.fail(self.b)['output_invalidation']['status'],'not_modified');self.assertEqual(self.snapshot(),before)

    def test_owned_legacy_export_is_marked_failed(self):
        r=self.fail(self.a)
        self.assertEqual(r['output_invalidation']['status'],'marked_failed');self.assertTrue((self.out/'FAILED_请勿使用.txt').is_file())
        self.assertEqual(verify.load(self.out/'docs/verification.json')['status'],'failed')
        self.assertEqual((self.out/'user_keep.txt').read_bytes(),b'keep unchanged')

    def test_missing_owner_preserves_existing_output(self):
        (self.out/'任务路径.txt').unlink();before=self.snapshot()
        self.assertEqual(self.fail(self.a)['output_invalidation']['status'],'not_modified');self.assertEqual(self.snapshot(),before)

    def test_foreign_document_owner_is_not_overridden(self):
        verify.save(self.out/'docs/verification.json',dict(self.old,job=str(self.b)));before=self.snapshot()
        self.assertEqual(self.fail(self.a)['output_invalidation']['status'],'not_modified');self.assertEqual(self.snapshot(),before)

    def test_corrupt_previous_report_still_records_current_failure(self):
        (self.a/'verification.json').write_text('{broken',encoding='utf-8');before=self.snapshot();r=self.fail(self.a)
        self.assertEqual(r['status'],'failed');self.assertIn('previous_report_error',r);self.assertEqual(self.snapshot(),before)

    def test_nonobject_previous_report_still_records_current_failure(self):
        verify.save(self.a/'verification.json',['invalid']);r=self.fail(self.a)
        self.assertIn('not an object',r['previous_report_error'])

    def test_failure_report_write_error_is_recorded_without_claiming_no_changes(self):
        original=verify.output_write;before=(self.out/'docs/verification.json').read_bytes()
        def inject(path,payload):
            if Path(path)==self.out/'docs/verification.json':raise OSError('injected report write failure')
            return original(path,payload)
        with patch.object(verify,'output_write',side_effect=inject):r=self.fail(self.a)
        self.assertEqual(r['output_invalidation']['status'],'failed_to_mark')
        self.assertEqual(r['output_invalidation']['modified_files'],[str(self.out/'FAILED_请勿使用.txt')])
        self.assertEqual((self.out/'docs/verification.json').read_bytes(),before);self.assertTrue((self.out/'FAILED_请勿使用.txt').is_file())

    def test_hardlinked_report_does_not_modify_external_file(self):
        external=self.base/'external.json';external.write_bytes((self.out/'docs/verification.json').read_bytes())
        (self.out/'docs/verification.json').unlink();os.link(external,self.out/'docs/verification.json');before=self.snapshot();digest=verify.sha(external)
        r=self.fail(self.a);self.assertEqual(verify.sha(external),digest);self.assertEqual(self.snapshot(),before)
        self.assertIn('hardlink',r['output_invalidation']['reason'])

    def test_docs_junction_is_rejected_before_writing_other_directory(self):
        old=self.out/'docs';external=self.base/'other_docs';self.assertTrue(old.resolve().is_relative_to(self.base.resolve()));old.rename(external)
        self.junction(external,old);before=(external/'verification.json').read_bytes();r=self.fail(self.a)
        self.assertEqual((external/'verification.json').read_bytes(),before);self.assertIn('junction',r['output_invalidation']['reason'])

    def test_output_directory_junction_is_rejected(self):
        alias=self.base/'outputs/alias';self.junction(self.out,alias)
        verify.save(self.a/'verification.json',dict(self.old,output_directory=str(alias)));before=self.snapshot()
        self.assertIn('junction',self.fail(self.a)['output_invalidation']['reason']);self.assertEqual(self.snapshot(),before)

    def test_output_root_itself_is_not_a_valid_target(self):
        verify.save(self.a/'verification.json',dict(self.old,output_directory=str(self.base/'outputs')));before=self.snapshot()
        self.assertIn('escapes',self.fail(self.a)['output_invalidation']['reason']);self.assertEqual(self.snapshot(),before)

    def test_output_path_parent_traversal_is_rejected(self):
        spelling=self.base/'outputs/child/../中文 输出 [成品]_abcdef'
        with self.assertRaisesRegex(ValueError,'escapes'):verify.checked_output(spelling)

    def test_valid_clone_cannot_overwrite_existing_export(self):
        state=self.fixture_files(self.b);before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'another job'):verify.export_passed(self.b,state,dict(self.old,job=str(self.b)))
        self.assertEqual(self.snapshot(),before)

    def test_same_job_can_reexport_legacy_output_without_deleting_user_files(self):
        state=self.fixture_files(self.a);self.assertEqual(verify.export_passed(self.a,state,dict(self.old)),self.out)
        self.assertEqual((self.out/'TEST.dra').read_bytes(),b'fixture, not native data');self.assertEqual((self.out/'user_keep.txt').read_bytes(),b'keep unchanged')

    def test_fresh_output_has_directory_identity_and_task_binding(self):
        out=self.base/'outputs/新 成品';verify.claim_output(self.a,out);owner=verify.load(out/'.lc2a-output.json')
        self.assertEqual(owner['job'],str(self.a));self.assertEqual(owner['directory_identity'],verify.output_directory_identity(out))
        self.assertEqual(verify.owned_output(self.a,out),out)

    def test_replaced_modern_directory_is_not_reclaimed(self):
        out=self.base/'outputs/new';verify.claim_output(self.a,out);backup=self.base/'owned_backup'
        self.assertTrue(out.resolve().is_relative_to(self.base.resolve()));self.assertTrue(backup.absolute().is_relative_to(self.base.absolute()))
        out.rename(backup);shutil.copytree(backup,out)
        with self.assertRaisesRegex(ValueError,'identity differs'):verify.owned_output(self.a,out)

    def test_existing_empty_directory_is_not_claimed(self):
        target=self.base/'outputs/unowned';target.mkdir()
        with self.assertRaisesRegex(ValueError,'no owner'):verify.claim_output(self.a,target)
        self.assertEqual(list(target.iterdir()),[])

    def test_atomic_write_failure_preserves_target_and_removes_owned_temp(self):
        target=self.out/'user_keep.txt';before=target.read_bytes()
        with patch.object(verify.os,'replace',side_effect=OSError('injected replace failure')):
            with self.assertRaisesRegex(OSError,'injected'):verify.output_write(target,b'new bytes')
        self.assertEqual(target.read_bytes(),before);self.assertFalse(list(self.out.glob('.lc2a-write-*')))

    def test_device_stems_in_display_labels_get_safe_output_prefix(self):
        state=self.fixture_files(self.a)
        for label in ('NUL.txt','CON.中文说明','COM1.模块','NUL .txt'):
            with self.subTest(label=label):
                state['label']=label;verify.save(self.a/'status.json',state)
                out=verify.export_passed(self.a,state,dict(job=str(self.a),status='passed'))
                self.assertEqual(out.name,'FP_'+label+'_abcdef');self.assertFalse(ntpath.isreserved(out.name))
                self.assertEqual(verify.load(out/'docs/status.json')['label'],label)

    def test_ordinary_and_chinese_labels_keep_their_directory_spelling(self):
        state=self.fixture_files(self.a)
        for label in ('CON','电源 模块.v1'):
            with self.subTest(label=label):
                state['label']=label;verify.save(self.a/'status.json',state)
                out=verify.export_passed(self.a,state,dict(job=str(self.a),status='passed'))
                self.assertEqual(out.name,label+'_abcdef');self.assertEqual(verify.load(out/'docs/status.json')['label'],label)

    def test_reserved_output_component_rejected_before_filesystem_checks(self):
        for spelling in ('NUL.txt','CON.中文/child.json'):
            with self.subTest(spelling=spelling),patch.object(Path,'stat') as stat:
                with self.assertRaisesRegex(ValueError,'Windows reserved component'):verify.checked_output(self.base/'outputs'/spelling)
                stat.assert_not_called()


if __name__=='__main__':unittest.main()
