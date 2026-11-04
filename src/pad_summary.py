"""Capture 16.6 Pad Designer's report without importing a PAD into a board.

Only process orchestration lives here; the verifier interprets the report against
its separately parsed source. A private TEMP prevents reading another session.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, tempfile, time
from pathlib import Path
import native_runtime


def capture(source, destination, executable, timeout=30,*,native_temp_root=None):
    source=Path(source);destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    metadata={'source':str(source),'source_sha256':before,'status':'failed'}
    try:
        with native_runtime.temporary_stage(prefix='lc2a_padsummary_',configured=native_temp_root) as temp:
            stage=Path(temp)
            if not str(stage).isascii():raise ValueError('Pad Designer staging path must be ASCII')
            shutil.copy2(source,stage/'probe.pad')
            (stage/'report.scr').write_text('pse_summary\npause 3\nsetwindow text\nclose\nsetwindow form.padedit\npse_exit\n',encoding='ascii')
            own_temp=stage/'tmp';own_temp.mkdir()
            metadata['staging_directory']=str(stage);metadata['native_temp_directory']=str(own_temp)
            captured=destination/'captured';captured.mkdir()
            env=os.environ.copy()
            for key in ('TEMP','TMP','TMPDIR'):env[key]=str(own_temp)
            si=None
            if os.name=='nt':
                si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
            def collect():
                for file in own_temp.rglob('*'):
                    if file.is_file():
                        try:
                            data=file.read_bytes()
                            target=captured/file.relative_to(own_temp)
                            target.parent.mkdir(parents=True,exist_ok=True)
                            # Preserve the fullest snapshot of a transient report.
                            if not target.exists() or len(data)>=target.stat().st_size:target.write_bytes(data)
                        except OSError:pass  # A writer may briefly hold the file open.
            try:
                with (stage/'process_stdout.txt').open('wb') as out:
                    process=subprocess.Popen([str(executable),'-nograph','-s','report.scr','probe.pad'],cwd=stage,env=env,stdout=out,stderr=subprocess.STDOUT,startupinfo=si)
                    metadata['pid']=process.pid;deadline=time.monotonic()+timeout
                    try:
                        while process.poll() is None and time.monotonic()<deadline:
                            collect();time.sleep(.03)
                        collect()
                        if process.poll() is None:
                            metadata['timed_out']=True
                            raise TimeoutError('Owned Pad Designer report process timed out')
                        metadata['returncode']=process.returncode
                    finally:
                        # Popen owns this process handle; never close a user's session.
                        if process.poll() is None:process.kill();process.wait(timeout=10)
                output=(stage/'process_stdout.txt').read_text(encoding='utf-8',errors='replace')
                if process.returncode:raise RuntimeError('Pad Designer failed: '+output[-2000:])
                if 'pad_designer 16.6' not in output:raise ValueError('Pad report did not run in 16.6')
                logs=output+'\n'+(stage/'pad_designer.jrl').read_text(encoding='ascii',errors='replace')
                if re.search(r'(?im)^\s*E-|\*Error\*|encountered a problem|fatal error',logs):
                    raise ValueError('Pad Designer reported a native error')
                if any(p.suffix.lower() in ('.dmp','.sav') for p in stage.rglob('*')):
                    raise ValueError('Pad Designer crash/recovery artifact detected')
                if hashlib.sha256((stage/'probe.pad').read_bytes()).hexdigest()!=before or hashlib.sha256(source.read_bytes()).hexdigest()!=before:
                    raise ValueError('PAD changed while producing its summary')
                reports={}
                for p in captured.rglob('*'):
                    if p.is_file():
                        data=p.read_bytes()
                        if b'Padstack Name:' in data and b'Layer Name' in data and b'NCDRILL' in data and b'DRILL SYMBOL' in data:
                            reports[hashlib.sha256(data).hexdigest()]=data
                if len(reports)!=1:raise ValueError('Missing or ambiguous raw PAD summary; no verification result')
                (destination/'summary.txt').write_bytes(next(iter(reports.values())))
                metadata['report_sha256']=next(iter(reports));metadata['status']='captured'
            finally:
                for p in stage.iterdir():
                    if p.is_file():shutil.copy2(p,destination/p.name)
    except Exception as exc:
        metadata['error']=str(exc);raise
    finally:
        (destination/'capture.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return destination/'summary.txt'
