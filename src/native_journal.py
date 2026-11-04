"""Capture each Allegro session before its rolling journal can be overwritten."""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path

def digest(data):return hashlib.sha256(data).hexdigest()
def decode(data):
    try:return data.decode('utf-8')
    except UnicodeDecodeError:return data.decode('gb18030',errors='replace')

def scan(data):
    text=decode(data);errors=[]
    for number,line in enumerate(text.splitlines(),1):
        stripped=line.lstrip()
        if stripped.startswith('\\i '):continue  # Journal command input, not output.
        if stripped.startswith('\\e '):errors.append(dict(line=number,text=line));continue
        message=re.sub(r'^(?:\\[a-z]\s+)?\(\d+:\d+:\d+\)\s*','',stripped)
        if re.search(r'(?i)^\s*(?:E-|ERROR\b|\*Error\*)|encountered a problem|fatal error',message):
            errors.append(dict(line=number,text=line))
    return dict(version_16_6=bool(re.search(r'(?im)^\\t .*allegro 16\.6(?:-|\b)',text)),
                started=bool(re.search(r'(?m)^\\t .*Journal start -',text)),
                ended=bool(re.search(r'(?m)^\\t .*Journal end -',text)),errors=errors)

def begin(command,folder):
    if Path(command[0]).name.lower()!='allegro.exe':return None
    folder=Path(folder);journal=folder/'allegro.jrl';prior=journal.read_bytes() if journal.is_file() else None
    records=list(folder.glob('allegro_session_*.json'))
    indices=[int(p.stem.rsplit('_',1)[1]) for p in records if re.fullmatch(r'allegro_session_\d{4}',p.stem)]
    prefix=folder/f'allegro_session_{max(indices,default=0)+1:04d}'
    return dict(prefix=prefix,journal=journal,prior_sha256=digest(prior) if prior is not None else None,command=list(command))

def finish(context,stdout,stderr,returncode,timed_out,error):
    if context is None:return None
    prefix=context['prefix'];journal=context['journal'];data=journal.read_bytes() if journal.is_file() else b''
    report=dict(schema='allegro_journal_v1',command=context['command'],returncode=returncode,timed_out=timed_out,
                process_error=error,prior_sha256=context['prior_sha256'],journal_sha256=digest(data),
                stdout_sha256=digest(stdout),stderr_sha256=digest(stderr),fresh=bool(data) and digest(data)!=context['prior_sha256'],**scan(data))
    prefix.with_suffix('.jrl').write_bytes(data)
    prefix.with_suffix('.stdout.bin').write_bytes(stdout);prefix.with_suffix('.stderr.bin').write_bytes(stderr)
    prefix.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def require_clean(report):
    if report is None:return
    if report.get('schema')!='allegro_journal_v1' or not report.get('fresh') or not report.get('version_16_6') or not report.get('started') or not report.get('ended'):
        raise ValueError('Native journal missing, stale, incomplete, or not Allegro 16.6')
    if report.get('returncode')!=0 or report.get('timed_out') or report.get('process_error'):
        raise ValueError('Native journal belongs to an unsuccessful process')
    if report.get('errors'):
        raise ValueError('Native journal reported an error: '+report['errors'][0]['text'][:1000])

def audit(folder,expected_scripts):
    """Reparse raw evidence; a hand-edited success flag cannot clear an error."""
    folder=Path(folder);records=sorted(folder.glob('allegro_session_*.json'))
    if len(records)!=len(expected_scripts):raise ValueError('Missing or extra native journal session records; rebuild/reverify')
    scripts=[];result=[]
    for index,path in enumerate(records,1):
        if path.name!=f'allegro_session_{index:04d}.json':raise ValueError('Non-contiguous native journal records')
        report=json.loads(path.read_text(encoding='utf-8'));raw=path.with_suffix('.jrl').read_bytes()
        for suffix,key in (('.jrl','journal_sha256'),('.stdout.bin','stdout_sha256'),('.stderr.bin','stderr_sha256')):
            data=path.with_suffix(suffix).read_bytes()
            if digest(data)!=report.get(key):raise ValueError('Native journal evidence digest differs')
            if suffix!='.jrl':
                errors=scan(data)['errors']
                if errors:raise ValueError('Native recorded '+suffix.split('.')[1]+' reported an error: '+errors[0]['text'][:1000])
        observed=scan(raw)
        if any(report.get(key)!=value for key,value in observed.items()):raise ValueError('Native journal summary differs from raw output')
        if report.get('fresh')!=(bool(raw) and digest(raw)!=report.get('prior_sha256')):raise ValueError('Native journal freshness evidence differs')
        command=report.get('command')
        if not isinstance(command,list) or not command or Path(command[0]).name.lower()!='allegro.exe' or command.count('-s')!=1:
            raise ValueError('Invalid native journal command')
        try:script=command[command.index('-s')+1]
        except IndexError:raise ValueError('Missing native journal script') from None
        scripts.append(script);require_clean(report)
        result.append(dict(record=path.name,script=script,journal_sha256=digest(raw),errors=0))
    if scripts!=list(expected_scripts):raise ValueError('Native journal session sequence differs')
    return result
