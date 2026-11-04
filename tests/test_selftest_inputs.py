"""Prove the report gate detects input additions/removals, not only old hashes."""
import contextlib,io,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import selftest as runner

class RunnerInputs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_runner_');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'src').mkdir();(self.root/'tests/fixtures').mkdir(parents=True)
        for rel in ('src/original.py','tests/test_original.py','lc2a.ps1','test.ps1','config.json'):
            (self.root/rel).write_text('# fixture\n',encoding='utf-8')
    def run_fixture(self,action=lambda:None):
        class Action(unittest.TestCase):
            def runTest(self):action()
        with patch.object(runner,'ROOT',self.root),patch.object(sys,'argv',['selftest']),patch.object(unittest.defaultTestLoader,'discover',return_value=unittest.TestSuite([Action()])),contextlib.redirect_stdout(io.StringIO()):
            code=runner.main()
        summary=json.loads((self.root/'outputs/程序测试_latest.json').read_text(encoding='utf-8'))
        self.assertTrue((Path(summary['report_directory'])/'测试报告.md').is_file())
        return code,summary
    def add(self,rel):
        path=self.root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('new input\n',encoding='utf-8')
    def test_new_source_and_nested_fixture_invalidate_report(self):
        for rel in ('src/new_module.py','tests/fixtures/nested/reference.txt'):
            with self.subTest(rel=rel):
                code,summary=self.run_fixture(lambda:self.add(rel))
                self.assertEqual(code,1);self.assertEqual(summary['status'],'failed')
                self.assertIn(rel,summary['program_input_changes']['added'])
    def test_removed_source_invalidates_report(self):
        target=self.root/'src/original.py'
        self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        code,summary=self.run_fixture(target.unlink)
        self.assertEqual(code,1);self.assertIn('src/original.py',summary['program_input_changes']['removed'])
    def test_modified_source_invalidates_report(self):
        code,summary=self.run_fixture(lambda:(self.root/'src/original.py').write_text('# changed\n',encoding='utf-8'))
        self.assertEqual(code,1);self.assertIn('src/original.py',summary['program_input_changes']['modified'])
    def test_nested_initial_fixture_is_copied_to_evidence(self):
        self.add('tests/fixtures/nested/reference.txt');code,summary=self.run_fixture()
        self.assertEqual(code,0)
        copy=Path(summary['report_directory'])/'program/tests/fixtures/nested/reference.txt'
        self.assertEqual(copy.read_bytes(),(self.root/'tests/fixtures/nested/reference.txt').read_bytes())
    def test_generated_bytecode_does_not_invalidate_inputs(self):
        code,summary=self.run_fixture(lambda:self.add('src/__pycache__/original.cpython-314.pyc'))
        self.assertEqual(code,0);self.assertEqual(summary['program_input_changes'],{'added':[],'removed':[],'modified':[]})
    def test_datasheet_addition_invalidates_running_report(self):
        code,summary=self.run_fixture(lambda:self.add('docs/datasheets/new.pdf'))
        self.assertEqual(code,1);self.assertIn('docs/datasheets/new.pdf',summary['program_input_changes']['added'])
    def test_datasheet_change_invalidates_running_report(self):
        self.add('docs/datasheets/reviewed.pdf')
        code,summary=self.run_fixture(lambda:(self.root/'docs/datasheets/reviewed.pdf').write_bytes(b'changed document'))
        self.assertEqual(code,1);self.assertIn('docs/datasheets/reviewed.pdf',summary['program_input_changes']['modified'])
    def test_datasheet_removal_invalidates_running_report(self):
        self.add('docs/datasheets/reviewed.pdf');target=self.root/'docs/datasheets/reviewed.pdf';self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        code,summary=self.run_fixture(target.unlink)
        self.assertEqual(code,1);self.assertIn('docs/datasheets/reviewed.pdf',summary['program_input_changes']['removed'])
    def test_reviewed_documents_are_copied_into_input_snapshot(self):
        self.add('docs/datasheets/reviewed.pdf');code,summary=self.run_fixture();self.assertEqual(code,0)
        original=self.root/'docs/datasheets/reviewed.pdf';saved=Path(summary['report_directory'])/'program/docs/datasheets/reviewed.pdf'
        self.assertEqual(saved.read_bytes(),original.read_bytes())
    def test_unreadable_initial_input_has_failed_markdown_report(self):
        with patch.object(runner,'input_fingerprints',side_effect=OSError('Injected unreadable initial source')):
            code,summary=self.run_fixture()
        self.assertEqual(code,1);self.assertIn('unreadable initial',summary['error'])
    def test_unreadable_final_input_cannot_leave_passing_report(self):
        scan=runner.input_fingerprints;calls=[]
        def fail_second(root):
            calls.append(root)
            if len(calls)==2:raise OSError('Injected unreadable final source')
            return scan(root)
        with patch.object(runner,'input_fingerprints',side_effect=fail_second):code,summary=self.run_fixture()
        self.assertEqual(code,1);self.assertIn('Could not verify final',summary['error'])
    def test_corrupt_snapshot_copy_is_not_accepted_as_evidence(self):
        copier=shutil.copy2
        def corrupt(src,dst,*args,**kwargs):
            result=copier(src,dst,*args,**kwargs)
            if Path(src)==self.root/'src/original.py':Path(dst).write_bytes(b'Corrupt copied bytes')
            return result
        with patch.object(shutil,'copy2',side_effect=corrupt):code,summary=self.run_fixture()
        self.assertEqual(code,1);self.assertIn('copying snapshot',summary['error'])

if __name__=='__main__':unittest.main()
