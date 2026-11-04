"""Chinese TEMP, child isolation and owned cleanup for real Windows directories."""
import json, os, stat, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import native_runtime, lc2a, verify, pad_summary

class NativeRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='runtime_test_');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.parent=self.root/'ascii';self.parent.mkdir()
        self.chinese=self.root/'中文临时目录';self.chinese.mkdir()

    def test_default_ascii_stage_is_unique_and_removed(self):
        with patch.object(native_runtime.tempfile,'gettempdir',return_value=str(self.parent)):
            with native_runtime.temporary_stage('lc2a_') as first:
                with native_runtime.temporary_stage('lc2a_') as second:
                    self.assertNotEqual(first,second);self.assertEqual(first.parent,self.parent);self.assertTrue(str(first).isascii())
                self.assertFalse(second.exists())
            self.assertFalse(first.exists())

    def test_chinese_default_falls_back_to_existing_windows_temp(self):
        system=self.root/'WindowsControl';(system/'Temp').mkdir(parents=True)
        with patch.object(native_runtime.tempfile,'gettempdir',return_value=str(self.chinese)),patch.dict(os.environ,{'SystemRoot':str(system),'LOCALAPPDATA':str(self.chinese)}):
            with native_runtime.temporary_stage('probe_') as stage:self.assertEqual(stage.parent,system/'Temp')
        self.assertEqual(list(self.chinese.iterdir()),[]);self.assertEqual(list((system/'Temp').iterdir()),[])

    def test_ascii_localappdata_precedes_system_directory(self):
        local=self.root/'LocalControl';(local/'Temp').mkdir(parents=True)
        system=self.root/'WindowsControl';(system/'Temp').mkdir(parents=True)
        with patch.object(native_runtime.tempfile,'gettempdir',return_value=str(self.chinese)),patch.dict(os.environ,{'SystemRoot':str(system),'LOCALAPPDATA':str(local)}):
            with native_runtime.temporary_stage('probe_') as stage:self.assertEqual(stage.parent,local/'Temp')

    def test_parent_list_denial_is_rejected_before_creating_child(self):
        with patch.object(native_runtime.os,'scandir',side_effect=PermissionError('injected LIST denial')),patch.object(native_runtime.tempfile,'mkdtemp') as create:
            with self.assertRaisesRegex(ValueError,'native_temp_root'):
                with native_runtime.temporary_stage('probe_',self.parent):self.fail('unreadable parent accepted')
            create.assert_not_called()

    def test_explicit_root_takes_precedence(self):
        with patch.object(native_runtime.tempfile,'gettempdir',side_effect=AssertionError('must not read default')):
            with native_runtime.temporary_stage('probe_',str(self.parent)) as stage:self.assertEqual(stage.parent,self.parent)

    def test_bad_explicit_root_does_not_silently_fall_back(self):
        for value in (str(self.chinese),str(self.root/'missing'),'relative',str(self.parent/'..'/'ascii'),str(self.root/'CON.txt')):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'native_temp_root'):
                with native_runtime.temporary_stage('probe_',value):self.fail('bad root accepted')
        self.assertEqual(list(self.parent.iterdir()),[])

    def test_unwritable_default_uses_next_candidate(self):
        denied=self.root/'denied';denied.mkdir();real_mkdtemp=tempfile.mkdtemp
        def create(*a,**kw):
            if Path(kw['dir'])==denied:raise PermissionError('injected ACL denial')
            return real_mkdtemp(*a,**kw)
        with patch.object(native_runtime,'candidate_directories',return_value=[str(denied),str(self.parent)]),patch.object(native_runtime.tempfile,'mkdtemp',side_effect=create):
            with native_runtime.temporary_stage('probe_') as stage:self.assertEqual(stage.parent,self.parent)

    def test_unwritable_all_candidates_explains_configuration(self):
        with patch.object(native_runtime.tempfile,'mkdtemp',side_effect=PermissionError('injected ACL denial')):
            with self.assertRaisesRegex(ValueError,'native_temp_root'):
                with native_runtime.temporary_stage('probe_',self.parent):self.fail('unwritable root accepted')

    def test_body_exception_cleans_owned_stage(self):
        with self.assertRaisesRegex(ValueError,'injected body error'):
            with native_runtime.temporary_stage('probe_',self.parent) as stage:
                (stage/'file.txt').write_text('owned');raise ValueError('injected body error')
        self.assertFalse(stage.exists())

    def test_readonly_native_file_can_be_cleaned(self):
        with native_runtime.temporary_stage('probe_',self.parent) as stage:
            f=stage/'readonly.pad';f.write_bytes(b'control');f.chmod(stat.S_IREAD)
        self.assertFalse(stage.exists())

    def test_replaced_directory_is_retained(self):
        with self.assertRaisesRegex(RuntimeError,'identity changed'):
            with native_runtime.temporary_stage('probe_',self.parent) as stage:
                moved=stage.with_name(stage.name+'_moved')
                self.assertEqual(stage.resolve().parent,self.parent.resolve());self.assertEqual(moved.absolute().parent,self.parent.resolve())
                stage.rename(moved);stage.mkdir();(stage/'external.txt').write_text('preserve')
        self.assertTrue(moved.is_dir());self.assertEqual((stage/'external.txt').read_text(),'preserve')

    def test_junction_root_is_rejected_without_touching_target(self):
        target=self.root/'target';target.mkdir();(target/'sentinel.txt').write_text('preserve')
        alias=self.root/'alias';p=subprocess.run(['cmd','/c','mklink','/J',str(alias),str(target)],capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        try:
            with self.assertRaisesRegex(ValueError,'symlink or junction'):native_runtime.checked_directory(alias)
        finally:alias.rmdir()
        self.assertEqual((target/'sentinel.txt').read_text(),'preserve')

    def test_unsafe_prefix_is_rejected(self):
        for prefix in ('../escape','bad/name','bad\\name','',None):
            with self.subTest(prefix=prefix),self.assertRaisesRegex(ValueError,'prefix'):
                with native_runtime.temporary_stage(prefix,self.parent):self.fail('unsafe prefix accepted')

    def test_child_junction_cleanup_does_not_traverse_external_target(self):
        external=self.root/'external';external.mkdir();(external/'sentinel.txt').write_text('preserve')
        with native_runtime.temporary_stage('probe_',self.parent) as stage:
            p=subprocess.run(['cmd','/c','mklink','/J',str(stage/'link'),str(external)],capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
        self.assertFalse(stage.exists());self.assertEqual((external/'sentinel.txt').read_text(),'preserve')

    def test_native_child_env_is_private_and_parent_unchanged(self):
        with patch.dict(os.environ,{'TEMP':str(self.chinese),'TMP':str(self.chinese),'TMPDIR':str(self.chinese),'LC2A_TEST_PRESERVE':'value'}):
            before=os.environ.copy()
            for name in ('allegro.exe','CREATE_SYM.EXE','extracta.exe','pad_designer.exe'):
                result=native_runtime.process_environment(name,self.parent)
                for k in ('TEMP','TMP','TMPDIR'):self.assertEqual(result[k],str(self.parent))
                self.assertEqual(result['LC2A_TEST_PRESERVE'],'value')
            self.assertEqual(dict(os.environ),dict(before))

    def test_downloader_python_can_keep_unicode_environment_and_cwd(self):
        self.assertIsNone(native_runtime.process_environment(sys.executable,self.chinese))

    def test_both_runners_pass_isolated_env_and_record_it(self):
        # This is an environment forwarding unit test, not a native CAD run.
        # Give the mocked process a complete journal just as a real run does.
        clean=(Path(__file__).parent/'fixtures/native_journal/clean.jrl').read_bytes()
        for module in (lc2a,verify):
            def fake_process(*args,**kwargs):
                (Path(kwargs['cwd'])/'allegro.jrl').write_bytes(clean+b'\n# mocked '+module.__name__.encode()+b'\n')
                return subprocess.CompletedProcess([],0,b'CONTROL OK\n',b'')
            with self.subTest(module=module.__name__),patch.object(module.subprocess,'run',side_effect=fake_process) as spawn:
                module.run(['allegro.exe'],self.parent)
                self.assertEqual(spawn.call_args.kwargs['env']['TEMP'],str(self.parent))
                record=json.loads((self.parent/'allegro_last_process.json').read_text(encoding='utf-8'))
                self.assertEqual(record['native_temp_directory'],str(self.parent))

    def test_native_unicode_working_dir_rejected_before_spawn(self):
        for module in (lc2a,verify):
            with self.subTest(module=module.__name__),patch.object(module.subprocess,'run') as spawn:
                with self.assertRaisesRegex(ValueError,'absolute ASCII'):module.run(['allegro.exe'],self.chinese)
                spawn.assert_not_called()
                record=json.loads((self.chinese/'allegro_last_process.json').read_text(encoding='utf-8'));self.assertTrue(record['error'])

    def test_pad_capture_forwards_configured_root(self):
        source=self.root/'source.pad';source.write_bytes(b'fixture')
        with patch.object(pad_summary.native_runtime,'temporary_stage',side_effect=ValueError('injected staging failure')) as stage:
            with self.assertRaisesRegex(ValueError,'injected staging'):pad_summary.capture(source,self.root/'capture','pad_designer.exe',native_temp_root=str(self.parent))
            self.assertEqual(stage.call_args.kwargs['configured'],str(self.parent))
        self.assertEqual(json.loads((self.root/'capture/capture.json').read_text(encoding='utf-8'))['status'],'failed')

if __name__=='__main__':unittest.main()
