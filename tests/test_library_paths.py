"""Check actual Windows directory junctions at the root and its ancestors."""
import _winapi,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import library,verify

class LibraryPaths(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_paths_');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.actual=self.base/'实际 目录 [原库]'
        (self.actual/'nested/footprint/MCU/docs').mkdir(parents=True)
        (self.actual/'original.txt').write_bytes(b'Preserve original')
        self.job=Path(verify.load(library.ROOT/'work/f103_v2.json')['job'])
    def junction(self):
        alias=self.base/'中文入口 [别名]'
        # PowerShell 5 New-Item resolves bracketed junction targets as patterns.
        # The fixture needs literal Unicode paths, just like the tested program.
        _winapi.CreateJunction(str(self.actual),str(alias))
        def remove_alias():
            self.assertTrue(alias.is_junction());self.assertEqual(alias.resolve(),self.actual.resolve())
            self.assertTrue(self.actual.resolve().is_relative_to(self.base.resolve()));alias.rmdir()
        self.addCleanup(remove_alias)
        self.assertTrue(alias.is_junction())
        return alias
    def test_actual_chinese_path_with_brackets_is_accepted(self):
        self.assertEqual(library.snapshot(self.actual),{'original.txt':library.digest(self.actual/'original.txt')})
    def test_root_junction_is_rejected_before_snapshot_or_trial(self):
        alias=self.junction();before=library.snapshot(self.actual)
        with self.assertRaisesRegex(ValueError,'root/ancestor'):library.snapshot(alias)
        with self.assertRaisesRegex(ValueError,'root/ancestor'):library.trial_job(self.job,alias,'MCU',False,'Fixture only',native=False)
        self.assertEqual(library.snapshot(self.actual),before)
    def test_ancestor_junction_is_rejected_even_when_final_directory_is_ordinary(self):
        alias=self.junction();nested=alias/'nested';self.assertFalse(nested.is_junction())
        with self.assertRaisesRegex(ValueError,'root/ancestor'):library.snapshot(nested)
    def test_parent_traversal_is_rejected_before_normalization(self):
        with self.assertRaisesRegex(ValueError,'parent traversal'):library.snapshot(self.actual/'nested/..')

if __name__=='__main__':unittest.main()
