"""Verified, non-overwriting library publication and reversible test transactions."""
from __future__ import annotations
import contextlib, hashlib, io, json, ntpath, os, re, shutil, stat, time, uuid
from pathlib import Path
import windows_lock

ROOT = Path(__file__).resolve().parents[1]

def load(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def data(obj): return json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')
def require(ok, message):
    if not ok: raise ValueError(message)
def save(p, obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp');tmp.write_bytes(data(obj));os.replace(tmp,p)


def library_root(path):
    """Validate the user's spelling before resolving away links or junctions."""
    root=Path(path).absolute()
    require('..' not in root.parts,'Library root must not contain parent traversal')
    for parent in [*reversed(root.parents),root]:
        require(not parent.is_symlink() and not parent.is_junction(),'Library root/ancestor is a link or junction: '+str(parent))
    require(root.is_dir(),'Library root must already exist')
    return root

def checked(root, path):
    """Constrain every mutation; reject links/junctions, including parent directories."""
    root=library_root(root);path=Path(path).absolute()
    require(path!=root and path.is_relative_to(root),'Path escapes library root: '+str(path))
    require(path.resolve().is_relative_to(root.resolve()),'Resolved path escapes library root')
    require(not root.is_symlink() and not root.is_junction(),'Library root must not be a link')
    p=root
    for part in path.relative_to(root).parts:
        p=p/part
        require(not p.is_symlink() and not p.is_junction(),'Library link/junction refused: '+str(p))
    return path

@contextlib.contextmanager
def locked(root):
    lock=checked(root,root/'.lc2a.lock')
    owner=windows_lock.new_owner()
    token=data(owner)
    try:write_new(lock,token)
    except FileExistsError:raise ValueError('Library transaction already active; inspect .lc2a.lock before recovery')
    try:yield owner
    finally:
        if lock.exists() and lock.read_bytes()==token:lock.unlink()

def snapshot(root):
    root=library_root(root)
    files={}
    for p in root.rglob('*'):
        checked(root,p)
        if p.is_file() and p.name!='.lc2a.lock':files[p.relative_to(root).as_posix()]=digest(p)
    return files

def write_new(path, payload):
    """Exclusive creation: concurrent external writes are never overwritten."""
    path=Path(path);identity=None
    try:
        with path.open('xb') as f:
            info=os.fstat(f.fileno());identity=(info.st_dev,info.st_ino)
            f.write(payload);f.flush()
    except BaseException:
        # A buffered write can fail during close, outside the inner write call.
        # Remove only our own file, and preserve any replacement/external edit.
        if identity is not None:
            try:
                info=path.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==identity and payload.startswith(path.read_bytes()):path.unlink()
            except OSError:pass  # Keep the original exception and any ambiguous file.
        raise


def file_identity(path):
    info=Path(path).stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode),'Expected a regular file: '+str(path))
    return {'device':info.st_dev,'inode':info.st_ino}


def directory_identity(path):
    path=Path(path);info=path.stat(follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode) and not path.is_junction() and not path.is_symlink(),'Expected an ordinary directory: '+str(path))
    return {'device':info.st_dev,'inode':info.st_ino}


def directory_snapshot(root):
    root=library_root(root);result={}
    for path in root.rglob('*'):
        checked(root,path)
        if path.is_dir():result[path.relative_to(root).as_posix()]=directory_identity(path)
    return result


def payload_path(receipt,row):
    rel=row.get('stage_file','')
    require(bool(re.fullmatch(r'payload/[0-9]{6}\.bin',rel)),'Invalid transaction payload path')
    base=Path(receipt).parent.resolve();path=base/rel
    require(path.resolve().is_relative_to(base),'Payload escapes transaction directory')
    for part in [path.parent,path]:require(not part.is_symlink() and not part.is_junction(),'Transaction payload is a link')
    return path


def directory_payload_path(receipt,row):
    rel=row.get('stage_dir','')
    require(bool(re.fullmatch(r'directories/[0-9]{6}',rel)),'Invalid staged directory path')
    base=Path(receipt).parent.resolve();path=base/rel
    require(path.resolve().is_relative_to(base),'Staged directory escapes transaction directory')
    for parent in [path.parent,path]:require(not parent.is_symlink() and not parent.is_junction(),'Staged directory is a link')
    return path


def stage_directories(receipt,plans,record,root):
    # Windows rename fails if the destination already exists. POSIX would allow
    # replacing an empty directory and is deliberately not used for this protocol.
    require(os.name=='nt','Recoverable directory promotion requires Windows rename semantics')
    missing=set()
    for target in plans:
        parent=target.parent
        while parent!=root and not parent.exists():
            checked(root,parent);missing.add(parent);parent=parent.parent
        require(parent.is_dir(),'Publication parent is not a directory: '+str(parent))
    record['directory_protocol']='identity_rename_v1'
    for index,dst in enumerate(sorted(missing,key=lambda p:(len(p.parts),str(p).casefold()))):
        row={'path':dst.relative_to(root).as_posix(),'stage_dir':f'directories/{index:06d}','promoted':False}
        record['directory_records'].append(row);save(receipt,record)
        source=directory_payload_path(receipt,row);source.parent.mkdir(exist_ok=True);source.mkdir()
        row['identity']=directory_identity(source);save(receipt,record)


def promote_directories(receipt,record,root):
    for row in record['directory_records']:
        source=directory_payload_path(receipt,row);dst=checked(root,root/row['path'])
        require(directory_identity(source)==row['identity'] and not any(source.iterdir()),'Staged directory changed before promotion')
        # Both absolute paths were confined above; only this empty private
        # directory is moved, with its persisted identity, without replacement.
        os.rename(source,dst)
        row['promoted']=True;record['created_directories'].append(row['path']);save(receipt,record)


def owns_directory(root,row):
    folder=checked(root,root/row['path'])
    if not folder.exists():return False
    require(row.get('identity') is not None and directory_identity(folder)==row['identity'],'Unowned or replaced publication directory: '+str(folder))
    return True


def stage_publication(receipt,plans,record,root):
    """Persist same-volume ownership witnesses before exposing any public file.

Cross-volume publication retains ordinary copy behavior; incomplete-copy recovery
still requires an audit rather than treating a matching filename/hash as ownership.
"""
    record['ownership_protocol']='hardlink_v1' if Path(receipt).parent.stat().st_dev==root.stat().st_dev else 'copy_v1'
    record['state']='staging';save(receipt,record)
    if record['ownership_protocol']=='hardlink_v1':
        for index,(row,payload) in enumerate(zip(record['files'],plans.values())):
            if not row['created']:continue
            row['stage_file']=f'payload/{index:06d}.bin';save(receipt,record)
            stage=payload_path(receipt,row);stage.parent.mkdir(exist_ok=True)
            write_new(stage,payload)
            require(digest(stage)==row['sha256'],'Staged payload hash mismatch')
            row['stage_identity']=file_identity(stage);save(receipt,record)
        first=next((r for r in record['files'] if r['created']),None)
        if first is not None:
            source=payload_path(receipt,first);probe=source.parent/'hardlink_probe'
            try:os.link(source,probe)
            except OSError as exc:
                record['ownership_protocol']='copy_v1';record['hardlink_unavailable']=str(exc)
            else:
                require(file_identity(probe)==file_identity(source),'Hardlink identity differs')
                probe.unlink()
        if record['ownership_protocol']=='hardlink_v1':stage_directories(receipt,plans,record,root)
    record['state']='ready';save(receipt,record)


def owns_target(receipt,record,row):
    """Reject any existing planned target whose ownership cannot be established."""
    root=library_root(record['library']);dst=checked(root,root/row['path'])
    if not row['created'] or not dst.exists():return False
    if row['written']:
        if row.get('identity') is not None:require(file_identity(dst)==row['identity'],'Replaced test file; ownership differs: '+str(dst))
    elif record.get('ownership_protocol')=='hardlink_v1' and row.get('stage_identity') is not None:
        stage=payload_path(receipt,row)
        require(stage.is_file() and file_identity(stage)==row['stage_identity'] and digest(stage)==row['sha256'],'Staging witness missing or changed: '+row['path'])
        require(file_identity(dst)==row['stage_identity'],'Unowned file at planned target: '+str(dst))
    else:raise ValueError('Unowned file at uncommitted target: '+str(dst))
    require(dst.is_file() and digest(dst)==row['sha256'],'Changed test file; cleanup refused: '+str(dst))
    return True

def plan_publication(job, root, category, specific, note, txid, trial):
    from verify import require_verified
    accepted=require_verified(job)
    require(note.strip(),'Record the actual review and remaining limitations')
    require(bool(re.fullmatch(r'[A-Za-z0-9_]+',category)),'Invalid library category')
    category_root=checked(root,root/'footprint'/category)
    require(category_root.is_dir(),'Unknown library category')
    cat=category_root/'Specific' if specific else category_root
    checked(root,cat)
    status_bytes=(job/'status.json').read_bytes()
    require(hashlib.sha256(status_bytes).hexdigest()==accepted['status_sha256'],'Source status changed while planning publication')
    status=json.loads(status_bytes)
    hashes={str(Path(k)).casefold():v for k,v in (status['files']|accepted['evidence_sha256']).items()}
    def source_bytes(rel):
        src=(job/rel).resolve();require(src.is_relative_to(job),'Source manifest escapes job')
        payload=src.read_bytes()
        if str(rel)=='status.json':require(payload==status_bytes,'Source status changed while planning publication')
        elif str(rel)=='verification.json':require(json.loads(payload)==accepted,'Verification report changed while planning publication')
        else:
            expected=hashes.get(str(Path(rel)).casefold())
            require(expected is not None and hashlib.sha256(payload).hexdigest()==expected,'Source file changed while planning publication: '+str(rel))
        return payload
    model=json.loads(source_bytes('model.json'));name=status['name']
    require(isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,30}',name)),'Invalid native name: default Allegro 16.6 flow requires 1-31 ASCII characters')
    require(not ntpath.isreserved(name),'Windows reserved device name cannot be published')
    doc=checked(root,cat/'docs'/(name+'_lc2a'))
    require(not doc.exists(),'Review documentation already exists; no duplicate publication')
    native=job/'native';core=[];plans={};reused=[]
    def add(dst,payload):
        dst=checked(root,dst);h=hashlib.sha256(payload).hexdigest()
        require(dst not in plans or plans[dst]==payload,'Duplicate destination with different contents')
        if dst.exists():require(dst.is_file() and digest(dst)==h,'Existing different definition: '+str(dst))
        plans[dst]=payload
    pads={p['padstack']:p for p in model['pads']}
    for ext in ('dra','psm'):
        src=native/(name+'.'+ext);dst=cat/src.name;payload=source_bytes(Path('native')/src.name)
        for existing in (root/'footprint').rglob('*'):
            if existing.name.casefold()!=src.name.casefold() or 'docs' in existing.relative_to(root).parts:continue
            checked(root,existing)
            require(digest(existing)==hashlib.sha256(payload).hexdigest(),'Existing different definition: '+str(existing))
        add(dst,payload);core.append(str(dst))
    for n,p in pads.items():
        require(bool(re.fullmatch(r'lc_[0-9a-f]{16}',n)),'Invalid generated padstack name')
        src=native/(n+'.pad');dst=root/'pad'/('SMD' if p['type']=='smd' else 'PTH')/src.name
        spec={k:p[k] for k in ('type','shape','size','layers','drill','mask')};payload=source_bytes(Path('native')/src.name)
        original_hash=hashlib.sha256(payload).hexdigest()
        candidates=[]
        for existing in (root/'pad').rglob('*'):
            if existing.name.casefold()!=src.name.casefold() or 'docs' in existing.relative_to(root).parts:continue
            checked(root,existing)
            if digest(existing)!=original_hash:
                meta=checked(root,existing.parent/'docs'/(n+'_lc2a.json'))
                require(meta.is_file(),'Existing pad lacks matching geometry metadata: '+str(existing))
                saved=load(meta)
                require(saved.get('spec')==spec and saved.get('sha256')==digest(existing),'Existing pad does not match verified metadata')
            candidates.append(existing)
        # Prefer the target definition when it exists. Native PAD files may vary in timestamps.
        if candidates:
            existing=next((x for x in candidates if x==dst),sorted(candidates)[0])
            payload=existing.read_bytes();reused.append(str(existing))
        add(dst,payload);core.append(str(dst))
        meta=dst.parent/'docs'/(n+'_lc2a.json')
        expected={'spec':spec,'sha256':hashlib.sha256(payload).hexdigest()}
        if meta.exists():
            checked(root,meta);require(load(meta)==expected,'Existing pad metadata differs: '+str(meta))
        else:add(meta,data(expected))
    # Auditable inputs, conversion manifest and passing native evidence; never failed-history folders.
    manifest=set(status['files'])|set(accepted['evidence_sha256'])
    manifest.update(['status.json','verification.json'])
    for rel in sorted(manifest):
        src=(job/rel).resolve()
        require(src.is_relative_to(job),'Source manifest escapes job')
        add(doc/Path(rel),source_bytes(rel))
    marker=dict(schema=1,transaction_id=txid,trial=trial,job=str(job),review_note=note,files=core,reused_pad_files=reused,time=time.time())
    add(doc/'publication.json',data(marker))
    return plans,core,doc

def publish_job(job,library,category,specific,note,trial=False):
    job=Path(job).resolve();root=library_root(library)
    checked(root,root/'footprint')
    txid=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10]
    receipt=ROOT/'outputs/入库记录'/txid/'receipt.json'
    with locked(root) as lock_owner:
        plans,core,doc=plan_publication(job,root,category,specific,note,txid,trial)
        record=dict(schema=1,transaction_id=txid,trial=trial,library=str(root.resolve()),job=str(job),
                    category=category,specific=specific,review_note=note,state='prepared',created_unix=time.time(),
                    files=[],created_directories=[],directory_records=[],core_files=core,doc=str(doc),lock_owner=lock_owner)
        for dst,payload in plans.items():
            record['files'].append({'path':dst.relative_to(root).as_posix(),'sha256':hashlib.sha256(payload).hexdigest(),'created':not dst.exists(),'written':False})
        save(receipt,record)
        try:
            # Metadata and hashes cannot prove that a reused native PAD has the
            # claimed geometry. Check byte-different candidates before any library write.
            record['reused_pad_preflight']=verify_reused_pad_plans(job,plans,core,receipt)
            save(receipt,record)
            stage_publication(receipt,plans,record,root)
            if record['ownership_protocol']=='hardlink_v1':promote_directories(receipt,record,root)
            for row,(dst,payload) in zip(record['files'],plans.items()):
                checked(root,dst)
                missing=[];parent=dst.parent
                while not parent.exists():missing.append(parent);parent=parent.parent
                if record['ownership_protocol']=='hardlink_v1':require(not missing,'Previously promoted directory disappeared')
                for folder in reversed(missing):
                    checked(root,folder);folder.mkdir()
                    record['directory_records'].append({'path':folder.relative_to(root).as_posix(),'identity':directory_identity(folder),'promoted':True})
                    record['created_directories'].append(folder.relative_to(root).as_posix());save(receipt,record)
                if row['created']:
                    if record['ownership_protocol']=='hardlink_v1':
                        stage=payload_path(receipt,row)
                        require(file_identity(stage)==row['stage_identity'] and digest(stage)==row['sha256'],'Staged file changed before promotion')
                        os.link(stage,dst)  # Atomic, exclusive creation; no overwrite.
                    else:write_new(dst,payload)
                    row['identity']=file_identity(dst);row['written']=True;save(receipt,record)
                require(dst.is_file() and digest(dst)==row['sha256'],'Published file hash mismatch: '+str(dst))
            record['state']='published';save(receipt,record)
            if not trial:
                # Production files must be independently editable. Test witnesses
                # remain in program outputs until/after the temporary cleanup.
                for row in record['files']:
                    if row.get('stage_file'):
                        stage=payload_path(receipt,row)
                        require(file_identity(stage)==row['stage_identity'] and digest(stage)==row['sha256'],'Staged file changed before release')
                        stage.unlink()
                record['witnesses_released']=True;save(receipt,record)
        except Exception as exc:
            record['error']=str(exc);record['state']='rolling_back';journal_errors=[]
            try:save(receipt,record)
            except Exception as error:journal_errors.append(str(error))
            rollback=[]
            for row in reversed(record['files']):
                if not row['created']:continue
                try:
                    dst=checked(root,root/row['path'])
                    if owns_target(receipt,record,row):dst.unlink()
                except Exception as error:rollback.append(str(error))
            try:record['retained_directories']=prune_dirs(root,record['created_directories'],record.get('directory_records'))
            except Exception as error:rollback.append(str(error))
            record['state']='rollback_incomplete' if rollback else 'rolled_back';record['rollback_errors']=rollback
            record['journal_errors']=journal_errors
            try:save(receipt,record)
            except Exception as error:journal_errors.append(str(error))
            if journal_errors:
                raise RuntimeError(f'Publication failed: {exc}; rollback={record["state"]}; receipt persistence also failed: '+ '; '.join(journal_errors)) from exc
            raise
    return receipt

def verify_reused_pad_plans(source,plans,core,receipt):
    from verify import verify_job
    overrides={}
    for path in core:
        dst=Path(path)
        if dst.suffix.lower()!='.pad':continue
        payload=plans[dst]
        if payload!=(source/'native'/dst.name).read_bytes():overrides[dst.name]=payload
    if not overrides:return None
    job=Path(receipt).parent/'复用焊盘预检';job.mkdir(parents=True)
    status=load(source/'status.json')
    for rel in status['files']:
        src=(source/rel).resolve();require(src.is_relative_to(source.resolve()),'Source manifest escapes job')
        # This private clone is about to receive candidate PAD bytes. Preserve
        # protection on the original while creating writable working files.
        dst=job/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    provenance=[]
    for name,payload in overrides.items():
        dst=job/'native'/name;dst.write_bytes(payload)
        rel=next(k for k in status['files'] if str(Path(k)).casefold()==str(Path('native')/name).casefold())
        provenance.append({'file':name,'original_sha256':status['files'][rel],'candidate_sha256':digest(dst)})
        status['files'][rel]=digest(dst)
    status['reused_pad_preflight']={'source_status_sha256':digest(source/'status.json'),'overrides':provenance,'before_library_write':True}
    save(job/'status.json',status)
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):code=verify_job(job,export=False)
    (Path(receipt).parent/'reuse_native_verify.txt').write_text(stream.getvalue(),encoding='utf-8')
    result=load(job/'verification.json')
    require(code==0,'Reused PAD native preflight failed before library writes: '+result.get('error','inspect receipt evidence'))
    return str(job/'verification.json')

def prune_dirs(root,dirs,records=None):
    retained=[]
    if records is not None:
        for row in sorted(records,key=lambda r:len(Path(r['path']).parts),reverse=True):
            folder=checked(root,root/row['path'])
            if owns_directory(root,row):
                if any(folder.iterdir()):retained.append(row['path'])
                else:folder.rmdir()
        return retained
    # Legacy receipts predate directory identities; only recorded empty folders.
    for rel in reversed(dirs):
        folder=checked(root,root/rel)
        if folder.is_dir():
            if any(folder.iterdir()):retained.append(rel)
            else:folder.rmdir()
    return retained

def cleanup_test(receipt,allow_partial=False):
    receipt=Path(receipt).resolve();record=load(receipt)
    require(record.get('schema')==1 and record.get('trial') is True,'Only an explicit test transaction can be cleaned automatically')
    eligible=record['state'] in ('published','cleanup_started','cleaned')
    require(eligible or (allow_partial and record.get('ownership_protocol')=='hardlink_v1'),'Transaction is not eligible for cleanup')
    root=library_root(record['library'])
    with locked(root) as lock_owner:
        # Validate ALL paths and hashes before removing anything.
        owned=[]
        for row in record['files']:
            checked(root,root/row['path'])
            if owns_target(receipt,record,row):owned.append(row)
        for row in record.get('directory_records',[]):owns_directory(root,row)
        if record['state']=='cleaned':return record
        ownpads={str(checked(root,root/r['path'])).casefold() for r in owned if r['path'].lower().endswith('.pad')}
        for marker in (root/'footprint').rglob('publication.json'):
            checked(root,marker);other=load(marker)
            if other.get('transaction_id')==record['transaction_id']:continue
            used={str(Path(p).absolute()).casefold() for p in other.get('files',[])}
            require(not ownpads.intersection(used),'Shared pad is referenced by another publication; clean the dependent test first')
        record['state']='cleanup_started';record['lock_owner']=lock_owner;save(receipt,record)
        for row in reversed(owned):
            dst=checked(root,root/row['path'])
            if dst.exists():
                require(owns_target(receipt,record,row),'File changed during cleanup: '+str(dst));dst.unlink()
        record['retained_directories']=prune_dirs(root,record['created_directories'],record.get('directory_records'))
        record['state']='cleaned';record['cleaned_unix']=time.time();save(receipt,record)
    return record


def recover_test(receipt):
    """Recover a test using a matching dead owner and persisted file identities."""
    receipt=Path(receipt).resolve();record=load(receipt)
    require(record.get('schema')==1 and record.get('trial') is True,'Only an explicit test transaction can be recovered')
    partial=record.get('state') not in ('published','cleanup_started','cleaned')
    if partial:
        require(record.get('state') in ('staging','ready','rolling_back','rollback_incomplete','rolled_back') and record.get('ownership_protocol')=='hardlink_v1','Partial publication lacks ownership witnesses; audit required')
        require(windows_lock.owner_has_exited(record.get('lock_owner',{})),'Partial publication owner is still running; no recovery performed')
    root=Path(record['library']).absolute();lock=checked(root,root/'.lc2a.lock')
    if lock.exists():
        with windows_lock.exclusive_file(lock) as (owner,discard):
            require(owner==record.get('lock_owner'),'Lock belongs to a different or unidentified transaction')
            require(windows_lock.owner_has_exited(owner),'Library lock owner is still running; no recovery performed')
            record.setdefault('recovered_locks',[]).append({'owner':owner,'time':time.time(),'reason':'Owner process exited or PID belongs to a different creation time'})
            save(receipt,record)  # Record the identity before removing this exact file handle.
            discard()
    return cleanup_test(receipt,allow_partial=partial)

def verify_library_files(receipt):
    """Reread the files actually in the library, including reused PAD definitions."""
    from verify import verify_job
    receipt=Path(receipt);record=load(receipt);source=Path(record['job'])
    job=receipt.parent/'实际入库文件验收';job.mkdir()
    status=load(source/'status.json')
    for rel in status['files']:
        src=(source/rel).resolve();require(src.is_relative_to(source.resolve()),'Source manifest escapes job')
        # Library files replace this disposable source clone before readback.
        # Do not carry the original file's READONLY attribute into that target.
        dst=job/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    root=Path(record['library'])
    for path in record['core_files']:
        src=checked(root,Path(path));dst=job/'native'/src.name
        row=next(r for r in record['files'] if (root/r['path'])==src)
        require(digest(src)==row['sha256'],'Library file changed before native readback')
        shutil.copy2(src,dst)
        # New derivative test manifest explicitly records library provenance.
        rel=next(k for k in status['files'] if str(Path(k)).casefold()==str(Path('native')/src.name).casefold())
        status['files'][rel]=digest(dst)
    status['library_revalidation']={'transaction_id':record['transaction_id'],'source_status_sha256':digest(source/'status.json'),
                                    'library_files':record['core_files']}
    save(job/'status.json',status)
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):code=verify_job(job,export=False)
    (receipt.parent/'library_native_verify.txt').write_text(stream.getvalue(),encoding='utf-8')
    require(code==0,'Actual library native verification failed; inspect receipt evidence')
    return str(job/'verification.json')

def trial_job(job,library,category,specific,note,native=True):
    root=library_root(library);before=snapshot(root);before_dirs=directory_snapshot(root)
    receipt=publish_job(job,root,category,specific,note,trial=True)
    error=None;native_report=None;cleanup_error=None
    try:
        record=load(receipt)
        require(all((root/r['path']).is_file() and digest(root/r['path'])==r['sha256'] for r in record['files']),'Library readback differs')
        if native:native_report=verify_library_files(receipt)
    except Exception as exc:error=str(exc)
    finally:
        try:cleanup_test(receipt)
        except Exception as exc:cleanup_error=str(exc)
    after=snapshot(root);after_dirs=directory_snapshot(root);same=before==after and before_dirs==after_dirs
    result={'status':'passed' if same and not error and not cleanup_error else 'failed','receipt':str(receipt),'category':category,'specific':specific,
            'library':str(root),'before_files':len(before),'after_files':len(after),'library_restored':same,
            'before_directories':len(before_dirs),'after_directories':len(after_dirs),'library_files_restored':before==after,'library_directories_restored':before_dirs==after_dirs,
            'native_readback_requested':native,'native_report':native_report,'error':error,'cleanup_error':cleanup_error,
            'added':sorted(set(after)-set(before)),'removed':sorted(set(before)-set(after)),
            'changed':[p for p in before.keys()&after.keys() if before[p]!=after[p]],
            'added_directories':sorted(after_dirs.keys()-before_dirs.keys()),'removed_directories':sorted(before_dirs.keys()-after_dirs.keys()),
            'replaced_directories':sorted(p for p in before_dirs.keys()&after_dirs.keys() if before_dirs[p]!=after_dirs[p])}
    save(receipt.parent/'trial.json',result)
    (receipt.parent/'入库测试.md').write_text('# 分类入库与清理测试\n\n'+
        f'- 结果：**{result["status"]}**\n- 类别：`{category}`；Specific：`{specific}`\n'+
        f'- 库路径：`{root}`\n- 入库前/清理后文件数：{len(before)} / {len(after)}\n'+
        f'- 入库前/清理后目录数：{len(before_dirs)} / {len(after_dirs)}；目录列表及身份恢复：{before_dirs==after_dirs}\n'+
        f'- 全库原有文件哈希及文件列表恢复：{same}\n- 逐文件所有权、哈希与清理记录：`receipt.json`\n'+
        f'- 实际入库文件原生加载复验：{native}；报告：`{native_report}`\n- 错误：{error}；清理错误：{cleanup_error}\n'+
        '- 原始转换/验证样本继续保留在程序 outputs 中。\n',encoding='utf-8')
    require(result['status']=='passed','Library trial failed; inspect '+str(receipt.parent/'trial.json'))
    return result
