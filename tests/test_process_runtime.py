import ctypes,json,os,re,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class ProcessRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_process_');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
    def runners(self):
        for name,module in [('converter',lc2a),('verifier',verify)]:
            folder=self.base/name;folder.mkdir();yield module,folder
    def assert_dead(self,pid):
        if os.name!='nt':return
        api=ctypes.WinDLL('kernel32',use_last_error=True);api.OpenProcess.restype=ctypes.c_void_p
        handle=api.OpenProcess(0x100000,False,pid)
        if handle:
            api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
            api.CloseHandle.argtypes=[ctypes.c_void_p]
            try:self.assertEqual(api.WaitForSingleObject(handle,0),0)
            finally:api.CloseHandle(handle)
        else:self.assertEqual(ctypes.get_last_error(),87)
    def test_timeout_retains_output_and_ends_only_owned_child(self):
        si=None
        if os.name=='nt':
            si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
        sibling=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],startupinfo=si)
        try:
            for module,folder in self.runners():
                with self.subTest(module=module.__name__):
                    with self.assertRaises(subprocess.TimeoutExpired):
                        module.run([sys.executable,'-c','import os,time;print("TIMEOUT PID="+str(os.getpid()),flush=True);time.sleep(20)'],folder,timeout=.5)
                    text=(folder/'python_last_process.txt').read_text(encoding='utf-8')
                    match=re.search(r'TIMEOUT PID=(\d+)',text);self.assertIsNotNone(match)
                    self.assert_dead(int(match[1]));self.assertIsNone(sibling.poll())
                    info=json.loads((folder/'python_last_process.json').read_text(encoding='utf-8'))
                    self.assertTrue(info['timed_out']);self.assertEqual(info['timeout_seconds'],.5)
                    self.assertIn(b'TIMEOUT PID=',(folder/'python_last_process.stdout.bin').read_bytes())
        finally:sibling.terminate();sibling.wait(timeout=10)
    def test_gb18030_diagnostics_are_readable_and_original_bytes_retained(self):
        expected='中文故障记录\n';data=expected.encode('gb18030')
        for module,folder in self.runners():
            with self.subTest(module=module.__name__):
                output=module.run([sys.executable,'-c',f'import sys;sys.stderr.buffer.write({data!r})'],folder)
                self.assertEqual(output,expected)
                self.assertEqual((folder/'python_last_process.stderr.bin').read_bytes(),data)
    def test_zero_exit_native_error_still_fails_with_retained_log(self):
        for module,folder in self.runners():
            with self.subTest(module=module.__name__):
                with self.assertRaises(ValueError):module.run([sys.executable,'-c','print("E- injected runtime failure")'],folder)
                self.assertIn('injected runtime failure',(folder/'python_last_process.txt').read_text(encoding='utf-8'))
    def test_missing_executable_leaves_diagnostic(self):
        for module,folder in self.runners():
            with self.subTest(module=module.__name__):
                with self.assertRaises(OSError):module.run([folder/'absent_native.exe'],folder)
                info=json.loads((folder/'absent_native_last_process.json').read_text(encoding='utf-8'))
                self.assertTrue(info['error']);self.assertFalse(info['timed_out'])

if __name__=='__main__':unittest.main()
