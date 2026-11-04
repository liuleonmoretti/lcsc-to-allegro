"""ASCII-only, privately owned working directories for legacy Cadence processes.

Geometry code is intentionally absent. User inputs/results keep Unicode paths;
only temporary native work and the native child's environment use ASCII.
"""
from __future__ import annotations
import ntpath, os, re, shutil, stat, tempfile
from contextlib import contextmanager
from pathlib import Path

NATIVE_EXECUTABLES={'allegro.exe','create_sym.exe','extracta.exe','pad_designer.exe'}

def checked_directory(value):
    path=Path(value)
    if not path.is_absolute() or not str(path).isascii() or '..' in path.parts or ntpath.isreserved(str(path)):
        raise ValueError('Native temporary directory must be an absolute ASCII directory without reserved components')
    for item in (path,*path.parents):
        if item.is_symlink() or item.is_junction():raise ValueError('Native temporary directory cannot contain a symlink or junction')
    resolved=path.resolve(strict=True)
    if not resolved.is_dir() or not str(resolved).isascii():raise ValueError('Native temporary directory is unavailable or resolves to a non-ASCII path')
    # Windows Temp may allow CREATE without LIST. That was sufficient for Python
    # mkdtemp but failed real Cadence startup/save on this machine.
    with os.scandir(resolved):pass
    return resolved

def candidate_directories(configured=None):
    if configured is not None:return [configured]
    choices=[]
    try:choices.append(tempfile.gettempdir())
    except OSError:pass
    if os.name=='nt':
        if os.environ.get('LOCALAPPDATA'):choices.append(str(Path(os.environ['LOCALAPPDATA'])/'Temp'))
        if os.environ.get('SystemRoot'):choices.append(str(Path(os.environ['SystemRoot'])/'Temp'))
    return list(dict.fromkeys(choices))

@contextmanager
def temporary_stage(prefix,configured=None):
    if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',prefix):raise ValueError('Invalid native temporary prefix')
    attempts=[];stage=None
    for candidate in candidate_directories(configured):
        try:
            base=checked_directory(candidate)
            stage=Path(tempfile.mkdtemp(prefix=prefix,dir=base))
            resolved=stage.resolve(strict=True)
            if resolved.parent!=base or stage.is_symlink() or stage.is_junction():raise ValueError('Native temporary path does not belong to its selected parent')
            identity=stage.stat(follow_symlinks=False);identity=(identity.st_dev,identity.st_ino)
            break
        except (OSError,ValueError) as exc:
            attempts.append(str(candidate)+': '+str(exc))
            if stage is not None:raise
    if stage is None:
        raise ValueError('No writable ASCII native temporary directory. Set native_temp_root in config.json to an existing writable absolute ASCII directory. '+ '; '.join(attempts))
    try:yield stage
    finally:
        # Validate the exact owned target immediately before recursive cleanup.
        # A moved/replaced directory is retained instead of deleting unknown data.
        if stage.exists() or stage.is_symlink() or stage.is_junction():
            def check_owner():
                current=stage.stat(follow_symlinks=False)
                if stage.is_symlink() or stage.is_junction() or stage.resolve(strict=True).parent!=base or (current.st_dev,current.st_ino)!=identity:
                    raise RuntimeError('Native temporary directory identity changed; retained without cleanup: '+str(stage))
            def clear_readonly(function,path,error):
                if not isinstance(error,PermissionError):raise error
                check_owner();target=Path(path)
                if target.is_symlink() or target.is_junction() or not target.resolve(strict=True).is_relative_to(stage.resolve(strict=True)):raise error
                info=target.stat(follow_symlinks=False)
                if not (getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_READONLY):raise error
                target.chmod(info.st_mode|stat.S_IWRITE)
                function(path)
            check_owner()
            shutil.rmtree(stage,onexc=clear_readonly)

def process_environment(executable,cwd):
    if Path(executable).name.lower() not in NATIVE_EXECUTABLES:return None
    stage=checked_directory(cwd)
    env=os.environ.copy()
    # The caller owns this unique stage and runs its native commands sequentially.
    # Do not change the parent process, registry or a user's running CAD session.
    for key in ('TEMP','TMP','TMPDIR'):env[key]=str(stage)
    return env
