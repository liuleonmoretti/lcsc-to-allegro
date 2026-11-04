"""Independent acceptance tests for lc2a outputs (no call to the converter parser).

Reopen .dra, load .psm + disk .pad files into an unrelated board, place/rotate/
move, and compare with a separately parsed source. Test failure blocks release.
"""
from __future__ import annotations
import argparse, hashlib, html, json, math, ntpath, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import sexpdata
import pad_summary
import native_runtime
import native_journal
import verify_pin_ids
import board_sides
from verify_graphics_source import check_text_layer
from shapely.geometry import Point, LineString, box
from shapely.affinity import rotate, translate
from shapely.ops import polygonize, polygonize_full, unary_union

ROOT=Path(__file__).resolve().parents[1]
TOL=.00021  # 16.6 database is in mm with 4 decimal places.
LAYER={'F.SilkS':'PACKAGE GEOMETRY/SILKSCREEN_TOP','F.Fab':'PACKAGE GEOMETRY/ASSEMBLY_TOP','F.CrtYd':'PACKAGE GEOMETRY/PLACE_BOUND_TOP'}
PLACEMENTS=((0,(20,20)),(90,(50,20)),(180,(80,20)),(270,(110,20)))
PLACEMENT_MOVE=(10,15)
NATIVE_STATES=tuple((f'placed{angle}.tsv',anchor,angle) for angle,anchor in PLACEMENTS)+tuple(
    ('moved.tsv' if angle==0 else f'saved{angle}.tsv',
     (anchor[0]+PLACEMENT_MOVE[0],anchor[1]+PLACEMENT_MOVE[1]),angle) for angle,anchor in PLACEMENTS)
BOTTOM_STATES=tuple(('bottom_'+name,(anchor[0],anchor[1]+40),angle) for name,anchor,angle in NATIVE_STATES)
BOARD_STATES=tuple((*state,False) for state in NATIVE_STATES)+tuple((*state,True) for state in BOTTOM_STATES)

def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(ok,msg):
    if not ok:raise ValueError(msg)
def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def get(n,k,default=None):return next((v for v in n if tag(v)==k),default)
def vec(n):return list(map(float,n[1:3]))
def close(a,b):return abs(float(a)-float(b))<=TOL
def angleclose(a,b):return abs((float(a)-float(b)+180)%360-180)<.01

def run(args,cwd,timeout=60):
    si=None
    if os.name=='nt':
        si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
    command=[str(x) for x in args];base=Path(cwd)/(Path(args[0]).stem+'_last_process');child_env=None
    journal_context=native_journal.begin(command,cwd);journal_report=None
    def record(stdout=b'',stderr=b'',code=None,timed_out=False,error=None):
        nonlocal journal_report
        base.with_suffix('.stdout.bin').write_bytes(stdout);base.with_suffix('.stderr.bin').write_bytes(stderr)
        def decode(data):
            try:return data.decode('utf-8')
            except UnicodeDecodeError:return data.decode('gb18030',errors='replace')
        output=decode(stdout)+decode(stderr)
        if error:output+='\nPROCESS ERROR: '+error+'\n'
        base.with_suffix('.txt').write_text(output,encoding='utf-8')
        save(base.with_suffix('.json'),dict(command=command,timeout_seconds=timeout,timed_out=timed_out,returncode=code,error=error,
             working_directory=str(Path(cwd).resolve()),native_temp_directory=child_env.get('TEMP') if child_env is not None else None))
        journal_report=native_journal.finish(journal_context,stdout,stderr,code,timed_out,error)
        return output
    try:
        child_env=native_runtime.process_environment(command[0],cwd)
        p=subprocess.run(command,cwd=cwd,capture_output=True,timeout=timeout,startupinfo=si,env=child_env)
    except subprocess.TimeoutExpired as exc:record(exc.stdout or b'',exc.stderr or b'',timed_out=True,error=str(exc));raise
    except (OSError,ValueError) as exc:record(error=str(exc));raise
    output=record(p.stdout,p.stderr,p.returncode)
    require(p.returncode==0,f'Native test process failed ({p.returncode}): '+output[-3000:])
    assert_native_clean(output,Path(cwd))
    native_journal.require_clean(journal_report)
    return output

def assert_native_clean(output,folder):
    require(not re.search(r'(?im)^\s*E-|\*Error\*|encountered a problem|fatal error',output),'Native session reported an error even if exit code was zero')
    require(not list(folder.glob('*.dmp')) and not list(folder.glob('*.SAV')),'Native crash/recovery artifact detected')

def safe_rel(job,rel):
    p=(job/rel).resolve();require(p.is_relative_to(job.resolve()),'Manifest path escapes job');return p

def check_build(job):
    s=load(job/'status.json')
    require(s['status']=='built_and_geometry_checked','Conversion did not pass; no acceptance test or release allowed')
    require(s.get('program_version')=='0.2.0','Legacy build must be converted again with v0.2 before release')
    name=s.get('name')
    require(isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,30}',name)),'Invalid native footprint name in conversion status')
    require(not ntpath.isreserved(name),'Windows reserved device name in conversion status')
    require(isinstance(s.get('refdes'),str) and bool(re.fullmatch(r'[A-Z]{1,4}',s['refdes'])),
            'Reference prefix must be 1-4 uppercase ASCII letters for exact 16.6 preservation')
    required={'model.json','build.il',s['source_file'].replace('\\','/'),f'native/{s["name"]}.dra',f'native/{s["name"]}.psm'}
    paths={str(Path(k)).replace('\\','/').casefold():v for k,v in s['files'].items()}
    require({x.casefold() for x in required}<=paths.keys(),'Incomplete conversion manifest')
    for rel,h in s['files'].items():
        p=safe_rel(job,rel);require(p.is_file() and sha(p)==h,'Build file changed/missing: '+rel)
    require(sha(safe_rel(job,s['source_file']))==s['source_sha256'],'Source digest mismatch')
    m=load(job/'model.json')
    for p in m['pads']:
        require(f'native/{p["padstack"]}.pad'.casefold() in paths,'Pad file missing from build manifest')
    revision=s.get('native_journal_revision',0)
    require(revision in (0,1),'Unknown native journal revision')
    if revision==1:
        journal_files={f'native/allegro_session_{i:04d}{suffix}' for i in range(1,4) for suffix in ('.json','.jrl','.stdout.bin','.stderr.bin')}
        require(journal_files<=paths.keys(),'Missing native build journal manifest entries')
        native_journal.audit(job/'native',('run.scr','view.scr','inspect.scr'))
    return s,m

def require_verified(job):
    s,model=check_build(job)
    require(not s.get('fault_injection',{}).get('test_only'),'Intentional fault experiment cannot be published')
    require((job/'verification.json').is_file(),'Independent verify has not run')
    r=load(job/'verification.json')
    require(r.get('status')=='passed','Independent verify did not pass')
    required_checks=('immutable_conversion_manifest','source_geometry_rules','dra_all_pins_graphics_layers_refdes','psm_loaded_placed_rotated90_moved','disk_pad_copper_internal_masks_paste_drill_plating_offsets','raw_pad_all_layers_thermal_antipad','body_origin_courtyard_silk_clearance','native_sessions_without_errors_or_crash_files')
    require(all(r.get('checks',{}).get(k) is True for k in required_checks),'Incomplete independent test checks')
    require(r.get('checks',{}).get('analytic_curved_clearance') is True,'Analytic curved clearance has not been independently checked; verify again')
    require(r.get('checks',{}).get('native_actual_clearance') is True,'Actual native pad/hole/mask clearance has not been independently checked; verify again')
    require(r.get('checks',{}).get('native_placement_enclosure') is True,'Actual native placement enclosure has not been independently checked; verify again')
    require(r.get('checks',{}).get('assembly_all_geometry_enclosure') is True,'All assembly geometry has not been independently enclosed and centered; verify again')
    require(r.get('checks',{}).get('native_pad_quantization') is True,'Native pad dimension quantization has not been independently checked; verify again')
    expected=read_expected(job,s)[0]
    check_pin_identity(expected,model)
    check_pad_quantization(job,s,expected)
    require(r.get('checks',{}).get('compiled_placement_boundary_geometry') is True,'Compiled placement boundary geometry has not been independently checked; regenerate and verify')
    require(r.get('checks',{}).get('compiled_refdes_orientation') is True,'Compiled RefDes orientation has not been independently checked; verify again')
    require(r.get('checks',{}).get('psm_all_quarter_turns_saved') is True,'All four PSM orientations and saved moves have not been independently checked; verify again')
    require(r.get('checks',{}).get('psm_top_bottom_actual_layers') is True,'Top/bottom PSM placement and actual board layers have not been independently checked; verify again')
    require(r.get('checks',{}).get('fresh_test_board_without_cached_symbols') is True,'Test board cache was not independently cleared and checked; verify again')
    require(r.get('checks',{}).get('fresh_test_board_without_cached_padstacks') is True,'Test board padstack cache was not independently cleared and checked; verify again')
    require(r.get('checks',{}).get('default_view_background_contrast') is True,'Saved background contrast has not been independently checked; verify again')
    require(r.get('checks',{}).get('native_journal_sessions') is True,'Native journal sessions were not checked; verify again')
    require(r.get('checks',{}).get('native_build_journals') is True and s.get('native_journal_revision')==1,'Native build journals are absent; reconvert before publishing')
    require(r.get('checks',{}).get('native_library_search_paths') is True,'Native library search paths were not isolated and checked; verify again')
    if s.get('presentation_revision',0)>=1:
        require(r.get('checks',{}).get('default_view_refdes_font_clearance') is True,'Default view / RefDes has not been independently checked')
    if s.get('pin_identity_revision',0)>=1:
        require(r.get('checks',{}).get('source_pin_lexical_identity') is True,'Lexical source pin identity has not been independently checked')
    if s.get('review_profile'):
        require(r.get('checks',{}).get('manufacturer_profile_body_polarity') is True,'Manufacturer body/polarity policy has not been independently checked; verify again')
        require(r.get('checks',{}).get('manufacturer_profile_body_geometry') is True,'Reviewed assembly body geometry has not been independently checked; verify again')
        require(r.get('checks',{}).get('profile_source_graphics_safe') is True,'Original profile graphics have not been independently checked; regenerate')
        require(r.get('checks',{}).get('manufacturer_document_binding') is True,'Manufacturer document binding has not been independently checked; regenerate')
        check_manufacturer_evidence(job,s)
        from verify_graphics_source import profile_source
        profile_source(job,s)
    if 'lcsc' in s:
        require(r.get('checks',{}).get('download_request_source_identity') is True,'Download request/source identity has not been independently checked; regenerate and verify')
        if s.get('review_profile'):require(r.get('checks',{}).get('manufacturer_catalog_binding') is True,'Manufacturer catalog binding has not been independently checked; regenerate')
        check_fetch_evidence(job,s)
        require(r.get('checks',{}).get('raw_easyeda_pad_geometry') is True,'Raw EasyEDA PAD geometry has not been independently checked; regenerate and verify')
        require(r.get('checks',{}).get('raw_easyeda_graphic_inventory') is True,'Raw EasyEDA graphic inventory has not been independently checked; regenerate')
        if not s.get('review_profile'):require(r.get('checks',{}).get('raw_easyeda_graphic_geometry') is True,'Raw EasyEDA graphic geometry has not been independently checked; regenerate')
        from verify_upstream import check as check_raw_source
        check_raw_source(job,s)
    require(r.get('status_sha256')==sha(job/'status.json'),'Conversion status changed since test')
    pads={p['padstack'] for p in model['pads']}
    require(r.get('pin_count')==len(model['pads']) and r.get('padstack_count')==len(pads),'Acceptance pin/padstack count differs')
    proof=r.get('evidence_sha256');require(isinstance(proof,dict) and proof,'Missing acceptance evidence manifest')
    canonical={str(Path(k)).replace('\\','/').casefold():v for k,v in proof.items()}
    require(len(canonical)==len(proof),'Duplicate acceptance evidence path')
    mandatory={f'verification/native/{f}' for f in ('drawing.tsv','drawing_pads.txt','disk_pads.txt','placed0.tsv','placed90.tsv','moved.tsv','placement.brd','default_view.tsv','test_layout.json')}
    mandatory|={f'verification/native/{filename}' for filename,_,_ in NATIVE_STATES}
    mandatory|={f'verification/native/{filename}' for filename,_,_ in BOTTOM_STATES}
    mandatory|={f'verification/native/{filename}.layers' for filename,_,_,_ in BOARD_STATES}
    mandatory.add('verification/program/board_sides.py')
    mandatory.add('verification/program/native_journal.py')
    mandatory.add('verification/native/library_paths.tsv')
    mandatory|={f'verification/native/allegro_session_{i:04d}{suffix}' for i in range(1,5) for suffix in ('.json','.jrl','.stdout.bin','.stderr.bin')}
    mandatory|={f'verification/native/{name}' for name in ('seed_input.brd','seed_cleaned.brd','seed_cleanup.json','seed_empty.tsv','seed_padstacks.tsv','seed_cleanup_stdout.txt')}
    mandatory|={f'verification/program/{f}' for f in ('lc2a.py','verify.py','pad_summary.py','reviewed_profiles.py')}
    if r.get('native_runtime_revision',0)>=1:mandatory.add('verification/program/native_runtime.py')
    if r.get('source_pin_identity_revision',0)>=1:
        mandatory|={'verification/program/kicad_source.py','verification/program/verify_pin_ids.py'}
    if 'lcsc' in s:mandatory|={f'verification/program/{f}' for f in ('easyeda_raw.py','verify_upstream.py')}
    if 'lcsc' in s or s.get('review_profile'):mandatory|={f'verification/program/{f}' for f in ('source_graphics.py','verify_graphics_source.py')}
    for name in pads:mandatory|={f'verification/native/raw_pads/{name}/{f}' for f in ('summary.txt','capture.json','probe.pad')}
    require({k.casefold() for k in mandatory}<=canonical.keys(),'Missing mandatory acceptance evidence')
    for rel,h in proof.items():
        require(isinstance(h,str) and bool(re.fullmatch(r'[0-9a-f]{64}',h)),'Invalid acceptance evidence digest')
        p=safe_rel(job,rel);require(p.is_file() and sha(p)==h,'Test evidence changed/missing: '+rel)
    native_journal.audit(job/'verification/native',('read.scr','seed_cleanup.scr','placement.scr','saved.scr'))
    build_records={f'native/allegro_session_{i:04d}{suffix}' for i in range(1,4) for suffix in ('.json','.jrl','.stdout.bin','.stderr.bin')}
    require(build_records<={str(Path(k)).replace('\\','/') for k in s['files']},'Missing native build journal manifest entries')
    native_journal.audit(job/'native',('run.scr','view.scr','inspect.scr'))
    for name in pads:
        folder=job/'verification/native/raw_pads'/name;capture=load(folder/'capture.json')
        require(capture.get('status')=='captured' and capture.get('returncode')==0 and not capture.get('timed_out'),'Raw PAD capture did not complete')
        require(capture.get('source_sha256')==sha(job/'native'/(name+'.pad'))==sha(folder/'probe.pad'),'Raw PAD evidence belongs to a different file')
        require(capture.get('report_sha256')==sha(folder/'summary.txt'),'Raw PAD report digest differs')
    check_clean_seed(job/'verification/native')
    check_library_paths(job/'verification/native/library_paths.tsv')
    # A success flag cannot substitute for the freshly observed saved palette.
    check_default_view(job/'verification/native/default_view.tsv')
    # Re-read the hashed evidence, so an added success flag cannot authorize old wrong rotations.
    drawing=read_native(job/'verification/native/drawing.tsv')
    for filename,_,rotation in NATIVE_STATES:
        check_refdes_rotation(drawing['texts'],read_native(job/'verification/native'/filename)['texts'],rotation)
    for filename,anchor,rotation,mirrored in BOARD_STATES:
        placed=read_native(job/'verification/native'/filename)
        board_sides.check_layers(expected,placed['pins'],job/'verification/native'/(filename+'.layers'),anchor,rotation,mirrored)
        if mirrored:
            normalized=board_sides.bottom_to_local(placed,anchor,rotation)
            compare_pins(expected,normalized['pins'],model)
            check_refdes_rotation(drawing['texts'],normalized['texts'])
    return r

def check_manufacturer_evidence(job,s):
    """Independently pinned evidence for the explicit reviewed recipes.

    Does not import the producer's document map, or trust a report's self-hash.
    This establishes review provenance; geometry still requires profile_checks.
    """
    pr=s.get('review_profile')
    if not pr:return {'mode':'no_profile','manufacturer_claim':False}
    def need(ok,msg):require(ok,'Manufacturer evidence: '+msg)
    identity=pr.get('profile');need(identity in ('C8734','C53568','C62715'),'unknown profile')
    if identity=='C8734':
        expected=dict(schema='bound_manufacturer_v1',mpn='STM32F103C8T6',package='LQFP48 (5B)',publisher='STMicroelectronics',
            document_id='DS5319',document_revision='20',document_date='2025-07',filename='STM32F103C8.pdf',
            sha256='1e54ce18e6c34c5b4a78986f26e245e143748c721009fc9af5953795c30686bc',source_kind='manufacturer_pdf',
            pdf_pages_1_based=[98,99,100],references=['Figure 55','Table 62','Figure 56'],url_role='manufacturer_reference',policy_id='C8734_LQFP48_REVIEW_1')
        title='ST STM32F103C8T6, DS5319 Rev 20, Figure 55/56';url='https://www.st.com/resource/en/datasheet/stm32f103c8.pdf'
        body=[-3.5,-3.5,3.5,3.5];boundary=[-5.15,-5.15,5.15,5.15];mask=.03
    elif identity=='C53568':
        expected=dict(schema='bound_manufacturer_v1',mpn='1N4007RLG',package='DO41 / CASE 59-10 ISSUE U',publisher='onsemi',
            document_id='1N4001/D',document_revision='13',document_date='2012-10',filename='1N4007.pdf',
            sha256='3967a81f94842515b1ff4258fc779a87ba9c7a11f464cd974dd0eba1941c84b7',source_kind='user_supplied_manufacturer_pdf',
            pdf_pages_1_based=[1,7],references=['Polarity band','CASE 59-10 ISSUE U'],url_role='current_manufacturer_reference_not_pinned_file_origin',policy_id='C53568_DO41_REVIEW_2')
        title='onsemi 1N4007RLG, CASE 59-10 ISSUE U; user supplied datasheet page 7';url='https://www.onsemi.com/pdf/datasheet/1n4001-d.pdf'
        body=[-2.605,-1.35,2.605,1.35];boundary=[-5.60,-1.75,5.60,1.75];mask=.05
    else:
        expected=dict(schema='bound_manufacturer_v1',mpn='LTV-817-A',package='LTV-817 standard DIP4',publisher='LITE-ON',
            document_id='LTV-817 / 827 / 847 series',document_revision=None,document_date='2010-01',filename='C62715_LTV817A.pdf',
            sha256='616ee1f8582185fc8562fb98e868051147a00d58ac8c76baf75251fdbbb689ce',source_kind='manufacturer_pdf_via_lcsc',
            pdf_pages_1_based=[2,11],references=['LTV-817 OUTLINE DIMENSIONS','RANK TABLE A'],url_role='pinned_file_origin',policy_id='C62715_DIP4_REVIEW_1')
        title='LITE-ON LTV-817-A, January 2010, LTV-817 outline page 2 and rank A page 11'
        url='https://datasheet.lcsc.com/datasheet/pdf/02b3eef263084ccda653b96b967d41b5.pdf?productCode=C62715'
        body=[-2.55,-3.5,2.55,3.5];boundary=[-3.05,-5.15,3.05,5.15];mask=.05
    rel=pr.get('datasheet');need(isinstance(rel,str) and rel.replace('\\','/').casefold()==('reviewed/'+expected['filename']).casefold(),'wrong datasheet path')
    pdf=safe_rel(job,rel);need(pdf.is_file(),'missing reviewed datasheet')
    actual=sha(pdf);need(actual==expected['sha256'],'datasheet differs from independently reviewed document/version')
    need(pr.get('datasheet_sha256')==actual,'datasheet digest record differs')
    manifest={str(Path(k)).replace('\\','/').casefold():v for k,v in s['files'].items()}
    need(manifest.get(rel.replace('\\','/').casefold())==actual,'datasheet missing from conversion manifest')
    need(type(pr.get('revision')) is int and pr['revision']=={'C8734':4,'C53568':5,'C62715':1}[identity],'unreviewed recipe revision; regenerate')
    need(pr.get('review_basis')==expected,'document/package/review basis differs')
    need(pr.get('manufacturer')==title and pr.get('url')==url,'manufacturer or reference URL differs')
    need(pr.get('body_bounds_mm')==body and pr.get('courtyard_bounds_mm')==boundary and pr.get('mask_margin_mm')==mask,'recorded dimensional policy differs')
    if identity=='C53568':
        # Independently transcribed from the controlling inch table, not the
        # producer's rounding function or the secondary metric column.
        dimension_basis=dict(controlling_unit='inch',mm_per_inch=25.4,
            maximum_dimensions_inch={'A':.205,'B':.106,'D':.034,'F':.050},
            maximum_dimensions_mm={'A':5.207,'B':2.6924,'D':.8636,'F':1.27},
            assembly_envelope_mm=[5.21,2.70],envelope_policy='round each maximum body dimension outward to 0.01 mm',
            nominal_hole_mm=1.0,nominal_diametral_allowance_mm=.1364,nominal_allowance_minimum_policy_mm=.10,
            lead_diameter_uncontrolled_within_F=True,manufactured_fit_verified=False)
        need(pr.get('dimensional_basis')==dimension_basis,'controlling-unit dimensional basis differs')
    if identity=='C62715':
        dimension_basis=dict(drawing_dimension_unit='mm',body_nominal_mm=[4.6,6.5],body_tolerance_mm=[.5,.5],
            assembly_envelope_mm=[5.1,7.0],pitch_nominal_mm=2.54,pitch_tolerance_mm=.25,row_nominal_mm=7.62,
            row_at_body_tolerance_mm=.30,free_lead_span_mm=[7.62,9.98],lead_width_nominal_mm=.5,
            lead_width_tolerance_mm=.1,lead_width_max_mm=.6,lead_thickness_nominal_mm=.26,lead_thickness_tolerance_mm=None,
            width_max_thickness_nominal_diagonal_mm=.65391131,fully_toleranced_lead_diagonal_mm=None,
            nominal_hole_mm=1.0,manufacturer_recommended_pcb_hole_mm=None,formed_lead_fit_verified=False,manufactured_fit_verified=False)
        need(pr.get('dimensional_basis')==dimension_basis,'DIP4 dimensional basis or unverified fit limits differ')
    source=safe_rel(job,s['source_file']);source_name=pr.get('reviewed_source')
    need(isinstance(source_name,str) and safe_rel(job,source_name)==source,'reviewed source identity differs')
    need(pr.get('reviewed_sha256')==sha(source)==s.get('source_sha256'),'reviewed source digest differs')
    review=job/'reviewed/review.json'
    need(review.is_file() and manifest.get('reviewed/review.json')==sha(review),'saved review missing from manifest')
    def pairs(items):
        result={}
        for key,val in items:need(key not in result,'duplicate saved review key');result[key]=val
        return result
    def invalid(value):raise ValueError('Manufacturer evidence: non-finite review JSON '+value)
    need(json.loads(review.read_text(encoding='utf-8-sig'),object_pairs_hook=pairs,parse_constant=invalid)==pr,'saved review differs from conversion status')
    return dict(mode='bound_manufacturer_v1',profile=identity,review_basis=expected,datasheet_sha256=actual,
        manufacturer_reference=url,limitation='Pinned reviewed document and policy; not automatic review of other revisions, manufacturing approval or proof that a URL still serves these bytes.')

def check_fetch_evidence(job,s):
    """Independent request -> API component/package -> original KiCad identity audit."""
    if 'lcsc' not in s:return {'mode':'local_file','note':'No online request identity asserted.'}
    requested=s['lcsc'];require(isinstance(requested,str) and bool(re.fullmatch(r'C[0-9]+',requested)),'Reference download has invalid requested C-number')
    evidence=s.get('fetch_evidence')
    require(isinstance(evidence,dict) and evidence.get('schema')=='easyeda_legacy_v1','Reference download evidence missing; regenerate')
    require(evidence.get('requested_lcsc')==requested,'Reference download recorded request differs')
    expected_raw='source/.easyeda_cache/'+requested+'.json'
    require(evidence.get('response_file')==expected_raw,'Reference download response path differs from request')
    raw=safe_rel(job,expected_raw);source_name=evidence.get('footprint_file')
    require(isinstance(source_name,str) and source_name.startswith('source/') and source_name.endswith('.kicad_mod'),'Reference download original footprint path is invalid')
    original=safe_rel(job,source_name)
    paths={str(Path(k)).replace('\\','/').casefold():v for k,v in s['files'].items()}
    for path,name,digest_key in ((raw,expected_raw,'response_sha256'),(original,source_name,'footprint_sha256')):
        require(path.is_file() and evidence.get(digest_key)==sha(path)==paths.get(name.casefold()),'Reference download file missing/changed/unmanifested: '+name)
    def pairs(items):
        require(len({k for k,_ in items})==len(items),'Reference download JSON contains duplicate keys')
        return dict(items)
    def invalid(value):raise ValueError('Reference download non-finite JSON value '+value)
    data=json.loads(raw.read_text(encoding='utf-8-sig'),object_pairs_hook=pairs,parse_constant=invalid)
    require(isinstance(data,dict) and data.get('success') is True and type(data.get('code')) is int and data['code']==0,'Reference download API success is invalid')
    def at(*keys):
        value=data
        for k in keys:
            require(isinstance(value,dict) and k in value,'Reference download missing '+'/'.join(keys));value=value[k]
        return value
    require(at('result','lcsc','number')==requested,'Reference download API C-number differs')
    result=at('result');head=at('result','dataStr','head');package=at('result','packageDetail');phead=at('result','packageDetail','dataStr','head')
    require(all(isinstance(v,dict) for v in (result,head,package,phead)),'Reference download malformed component/package')
    if result.get('szlcsc') is not None:require(at('result','szlcsc','number')==requested,'Reference download szlcsc C-number differs')
    params=at('result','dataStr','head','c_para');require(isinstance(params,dict),'Reference download malformed parameters')
    if params.get('Supplier Part') not in (None,''):require(params['Supplier Part']==requested,'Reference download symbol C-number differs')
    component=at('result','uuid');pkg=at('result','packageDetail','uuid')
    require(all(isinstance(v,str) and v for v in (component,pkg)),'Reference download component/package IDs missing')
    require(head.get('puuid')==pkg,'Reference download package link differs')
    require(head.get('uuid') in (None,'',component) and phead.get('uuid') in (None,'',pkg),'Reference download embedded UUID differs')
    require(str(head.get('docType'))=='2' and str(phead.get('docType'))=='4','Reference download document types differ')
    require(evidence.get('component_uuid')==component and evidence.get('package_uuid')==pkg,'Reference download recorded IDs differ')
    catalog_binding={'mode':'no_profile','manufacturer_claim':False}
    if s.get('review_profile'):
        identity=s['review_profile'].get('profile')
        require(identity==requested and identity in ('C8734','C53568','C62715'),'Manufacturer catalog: profile/request differs')
        if identity=='C8734':
            part='STM32F103C8T6';maker='ST(意法半导体)';package_name='LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL'
        elif identity=='C53568':
            part='1N4007RLG';maker='onsemi(安森美)';package_name='DO-41_BD2.4-L4.7-P8.70-D0.9-RD'
        else:
            part='LTV-817-A';maker='LITEON(光宝)';package_name='DIP-4_L4.6-W6.5-P2.54-LS7.6-BL'
        # Re-read the actual API response instead of trusting the converter's
        # record or inferring a manufacturer from the requested catalog number.
        def label(value,wanted,field):
            require(isinstance(value,str) and value.strip()==wanted,'Manufacturer catalog: reviewed '+field+' differs or is missing')
            return value.strip()
        pp=phead.get('c_para',{})
        require(isinstance(pp,dict),'Manufacturer catalog: missing package parameters')
        catalog_binding=dict(schema='reviewed_catalog_v1',profile=identity,
            title=label(result.get('title'),part,'title'),name=label(params.get('name'),part,'symbol name'),
            mpn=label(params.get('Manufacturer Part'),part,'MPN'),manufacturer=label(params.get('Manufacturer'),maker,'manufacturer'),
            supplier=label(params.get('Supplier'),'LCSC','supplier'),supplier_part=label(params.get('Supplier Part'),identity,'supplier part'),
            package=label(params.get('package'),package_name,'symbol package'),package_title=label(package.get('title'),package_name,'package title'),
            package_parameter=label(pp.get('package'),package_name,'package parameter'))
        require(evidence.get('manufacturer_catalog')==catalog_binding,'Manufacturer catalog: stored binding missing or differs; regenerate')
    for src in {original,safe_rel(job,s['source_file'])}:
        tree=sexpdata.loads(src.read_text(encoding='utf-8-sig'))
        require(tag(tree) in ('footprint','module'),'Reference download is not a KiCad footprint')
        values=[str(n[2]) for n in tree if tag(n)=='property' and len(n)>=3 and str(n[1])=='LCSC Part']
        require(values==[requested],'Reference download KiCad C-number is missing, ambiguous or different')
    return dict(requested_lcsc=requested,component_uuid=component,package_uuid=pkg,response_sha256=sha(raw),
                footprint_sha256=sha(original),mode='easyeda_legacy_v1',manufacturer_catalog=catalog_binding,
                limitation='Catalog assertions consistently match the reviewed device; not proof of physical authenticity or manufacturer geometry validation.')

def check_pin_identity(expected,model):
    original=[p['number'] for p in expected];produced=[p['number'] for p in model['pads']]
    require(len(produced)==len(original) and set(produced)==set(original),
            'Model pin identifiers differ from lexical source; numeric/case coercion is forbidden')
    return dict(source_numbers=original,model_numbers=produced,comparison='exact source text')

def read_expected(job,s):
    """Separate source-to-reference implementation, rather than model.json as oracle."""
    tree=verify_pin_ids.load(safe_rel(job,s['source_file']).read_text(encoding='utf-8-sig'))
    profile=s.get('review_profile')
    if profile:
        identity=profile.get('profile')
        require(identity in ('C8734','C53568','C62715'),'Unknown manufacturer profile')
        identities=[str(n[2]) for n in tree if tag(n)=='property' and len(n)>=3 and str(n[1])=='LCSC Part']
        require(identities==[identity],'Manufacturer profile/source C-number mismatch or ambiguity')
        require(not s.get('lcsc') or s['lcsc']==identity,'Manufacturer profile/requested C-number mismatch')
    c=s['source_center_mm'];pads=[];graphics=[]
    def one(n,k,required=True,arity=None):
        values=[a for a in n if tag(a)==k]
        require(len(values)<=1,'Reference source contains duplicate '+k)
        require(not required or values,'Reference source missing '+k)
        value=values[0] if values else None
        if value is not None and arity is not None:require(len(value) in arity,'Reference source malformed '+k)
        return value
    def audit(n,known,start):
        names=[]
        for value in n[start:]:
            if isinstance(value,list):
                k=tag(value);require(k in known,'Reference source unsupported field '+k);names.append(k)
            else:require(str(value)=='locked','Reference source unsupported scalar '+str(value))
        require(len(names)==len(set(names)),'Reference source duplicate fields')
    def finite(v):
        value=float(v);require(math.isfinite(value),'Reference non-finite dimension');return value
    def transform(pt):return [finite(pt[0])-finite(c[0]),finite(c[1])-finite(pt[1])]
    for n in tree[2:]:
        kind=tag(n)
        if kind=='pad':
            require(isinstance(n[1],str) and bool(re.fullmatch(r'[A-Z0-9_+.-]+',n[1])),
                    'Reference pin identifier cannot be preserved by 16.6: '+str(n[1]))
            require(len(n[1])<=31,'Reference pin number exceeds the 16.6 limit of 31 ASCII characters')
            audit(n,{'at','size','drill','layers','solder_mask_margin','net','pinfunction','pintype','locked','uuid','tstamp'},4)
            require(str(n[2]) in ('smd','thru_hole') and str(n[3]) in ('rect','circle','oval'),'Reference unsupported pad type/shape')
            at=one(n,'at',arity=(3,4));size=one(n,'size',arity=(3,));layers=[str(v) for v in one(n,'layers')[1:]]
            angle=finite(at[3]) if len(at)==4 else 0
            require(abs(angle/90-round(angle/90))<1e-8,'Reference unsupported pad rotation')
            require(len(layers)==len(set(layers)),'Reference duplicate layers')
            expected_layers={'F.Cu','F.Paste','F.Mask'} if str(n[2])=='smd' else {'*.Cu','*.Mask','F.Mask','B.Mask'}
            require(set(layers)<=expected_layers and ('F.Cu' if str(n[2])=='smd' else '*.Cu') in layers,'Reference unsupported/missing copper layers')
            drill=one(n,'drill',required=str(n[2])=='thru_hole',arity=(2,))
            require(str(n[2])!='smd' or drill is None,'Reference SMD with drill')
            masknode=one(n,'solder_mask_margin',required=False,arity=(2,))
            p=dict(number=str(n[1]),type=str(n[2]),shape=str(n[3]),at=transform(vec(at)),
                   angle=(-angle)%360,size=[finite(x) for x in size[1:]],layers=layers,
                   drill=finite(drill[1]) if drill else 0,mask=finite(masknode[1] if masknode else s['conversion_options']['mask_margin']))
            require(min(p['size'])>0 and min(p['size'])+2*p['mask']>0,'Reference invalid pad/mask size')
            require(p['shape']!='circle' or close(*p['size']),'Reference unequal circle dimensions')
            require(p['type']!='thru_hole' or 0<p['drill']<min(p['size']),'Reference invalid drill/ring')
            pads.append(p)
        elif kind in ('fp_line','fp_rect','fp_circle'):
            audit(n,{'end','layer','width','stroke','fill','locked','uuid','tstamp'}|({'center'} if kind=='fp_circle' else {'start'}),1)
            layer=LAYER[str(one(n,'layer',arity=(2,))[1])]
            stroke=one(n,'stroke',required=False);legacy=one(n,'width',required=False,arity=(2,))
            require(not (stroke and legacy),'Reference ambiguous stroke/width')
            if stroke:
                audit(stroke,{'type','width'},1)
                require(str(one(stroke,'type',arity=(2,))[1]) in ('solid','default'),'Reference unsupported stroke style')
                w=one(stroke,'width',arity=(2,))
            else:w=legacy
            require(w is not None,'Reference missing graphic width');width=finite(w[1]);require(width>0,'Reference invalid graphic width')
            fill=one(n,'fill',required=False,arity=(2,));require(fill is None or str(fill[1]) in ('none','no'),'Reference unsupported filled graphic')
            if kind=='fp_circle':
                center=vec(one(n,'center',arity=(3,)));radius=math.dist(center,vec(one(n,'end',arity=(3,))))
                require(math.isfinite(radius) and radius>0,'Reference invalid circle')
                graphics.append(dict(kind='circle',center=transform(center),radius=radius,width=width,layer=layer))
            else:
                a=vec(one(n,'start',arity=(3,)));b=vec(one(n,'end',arity=(3,)))
                require(all(math.isfinite(v) for v in a+b) and math.dist(a,b)>0,'Reference invalid line')
                if kind=='fp_rect':require(a[0]!=b[0] and a[1]!=b[1],'Reference zero-area rectangle')
                pts=[a,b] if kind=='fp_line' else [a,[b[0],a[1]],b,[a[0],b[1]],a]
                for u,v in zip(pts,pts[1:]):graphics.append(dict(kind='line',start=transform(u),end=transform(v),width=width,layer=layer))
        elif kind.startswith('fp_') and kind!='fp_text':raise ValueError('Unverified source graphic '+kind)
        elif isinstance(n,list):
            require(kind in {'layer','tedit','tstamp','version','generator','generator_version','descr','tags','attr','property','fp_text','model','uuid','path'},'Reference unsupported footprint field '+kind)
            if kind in ('fp_text','property'):check_text_layer(n)
            if kind=='layer':require(len(n)==2 and str(n[1])=='F.Cu','Reference bottom-side footprint unsupported')
        else:require(str(n) in ('locked','placed'),'Reference unsupported footprint scalar')
    require(pads and len(pads)==len({p['number'] for p in pads}),'Missing or duplicate source pins')
    return pads,graphics

READER=r'''
procedure(lcReadBoundary(o f)
 let((aa bb cc)
  aa=car(o->bBox) bb=cadr(o->bBox)
  fprintf(f "B\t%s\t%.6f\t%.6f\t%.6f\t%.6f\t%d\t%d\n" o->layer car(aa) cadr(aa) car(bb) cadr(bb) length(o->voids) length(o->segments))
  foreach(seg o->segments
   aa=car(seg->startEnd) bb=cadr(seg->startEnd)
   cond(
    (seg->objType == "line" fprintf(f "BL\t%.6f\t%.6f\t%.6f\t%.6f\n" car(aa) cadr(aa) car(bb) cadr(bb)))
    (seg->objType == "arc"
     cc=seg->xy
     fprintf(f "BA\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%L\t%L\n" car(aa) cadr(aa) car(bb) cadr(bb) car(cc) cadr(cc) seg->radius seg->isClockwise seg->isCircle))
    (t error("Unsupported placement boundary segment"))
   )
  )
  fprintf(f "BE\n")
 )
)
procedure(lcReadView(filename)
 f=outfile(filename)
 rgb=axlColorGet('background)
 fprintf(f "BG\t%d\t%d\t%d\n" car(rgb) cadr(rgb) caddr(rgb))
 foreach(name list("REF DES/SILKSCREEN_TOP" "REF DES/ASSEMBLY_TOP" "PACKAGE GEOMETRY/SILKSCREEN_TOP" "PACKAGE GEOMETRY/ASSEMBLY_TOP")
  layer=axlLayerGet(name) rgb=axlColorGet(layer->color)
  fprintf(f "V\t%s\t%L\t%d\t%d\t%d\n" name axlIsVisibleLayer(name) car(rgb) cadr(rgb) caddr(rgb))
 )
 close(f)
)
procedure(lcAudit(o f)
 cond(
  (o->objType == "pin"
   fprintf(f "P\t%s\t%.6f\t%.6f\t%.6f\t%s\t%L\n" o->number car(o->xy) cadr(o->xy) o->rotation o->name o->isThrough))
  (o->objType == "path" foreach(seg o->segments lcAudit(seg f)))
  (o->objType == "line"
   aa=car(o->startEnd) bb=cadr(o->startEnd)
   fprintf(f "L\t%s\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\n" o->layer o->width car(aa) cadr(aa) car(bb) cadr(bb)))
  (o->objType == "arc"
   unless(o->isCircle error("Unsupported native arc in readback"))
   aa=car(o->startEnd) cc=o->xy
   rr=sqrt((car(aa)-car(cc))*(car(aa)-car(cc))+(cadr(aa)-cadr(cc))*(cadr(aa)-cadr(cc)))
   fprintf(f "C\t%s\t%.6f\t%.6f\t%.6f\t%.6f\n" o->layer o->width car(cc) cadr(cc) rr))
  (o->objType == "text"
   aa=car(o->bBox) bb=cadr(o->bBox)
   tb=axlGetParam(strcat("paramTextBlock:" o->textBlock))
   fprintf(f "T\t%s\t%s\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%L\n" o->layer o->text car(o->xy) cadr(o->xy) car(aa) cadr(aa) car(bb) cadr(bb) tb->width tb->height tb->photoWidth o->rotation o->isMirrored))
  (o->objType == "shape" lcReadBoundary(o f))
  (t fprintf(f "UNEXPECTED\t%s\n" o->objType))
 )
)
procedure(lcReadAll(filename)
 axlClearSelSet()
 axlVisibleDesign(t)
 axlSetFindFilter(?enabled list("alltypes") ?onButtons list("alltypes"))
 axlAddSelectAll()
 f=outfile(filename)
 fprintf(f "UNITS\t%L\n" axlDBGetDesignUnits())
 foreach(o axlGetSelSet() lcAudit(o f))
 close(f)
)
procedure(lcReadSymbol(s filename)
 f=outfile(filename)
 fprintf(f "S\t%.6f\t%.6f\t%.6f\n" car(s->xy) cadr(s->xy) s->rotation)
 foreach(o s->children lcAudit(o f))
 foreach(o s->pins lcAudit(o f))
 close(f)
)
'''

def test_drawing_limit(drawing,model):
    """Leave room for every initial/saved quarter-turn state and rotated labels."""
    coordinates=[]
    for g in drawing['graphics']:
        b=geometry_path(g).bounds;w=g['width']/2
        coordinates.extend((b[0]-w,b[1]-w,b[2]+w,b[3]+w))
    for item in drawing['texts']+drawing['bounds']:
        if 'bounds' in item:coordinates.extend(item['bounds'])
    mapping={p['number']:p for p in model['pads']}
    for pin in drawing['pins']:
        require(pin['number'] in mapping,'Unknown native pin while sizing test drawing')
        source=mapping[pin['number']];radius=math.hypot(*source['size'])/2+max(0,source['mask'])+1
        coordinates.extend(v+sign*radius for v in pin['at'] for sign in (-1,1))
    require(coordinates and all(math.isfinite(x) for x in coordinates),'Invalid test drawing dimensions')
    farthest_anchor=max(abs(value) for _,anchor,_,_ in BOARD_STATES for value in anchor)
    return math.ceil(max(100,max(abs(x) for x in coordinates)+farthest_anchor+10))


def check_clean_seed(native):
    """Check cleanup of a private board copy, observed again in the placement session."""
    record=load(native/'seed_cleanup.json')
    require(record.get('schema')=='private_seed_cleanup_v1' and record.get('private_copy_only') is True,'Missing private seed cleanup provenance')
    require(record.get('source_sha256')==sha(native/'seed_input.brd'),'Input test seed digest differs')
    require(record.get('cleaned_sha256')==sha(native/'seed_cleaned.brd'),'Cleaned test seed digest differs')
    require(record.get('cleanup_completed') is True,'Test seed cleanup did not complete')
    rows=(native/'seed_empty.tsv').read_text(encoding='ascii').splitlines()
    require(rows==['SEED\t0\t0\tnil'],'Test board still contains symbols/definitions or ignores FIXED during placement')
    padrows=(native/'seed_padstacks.tsv').read_text(encoding='ascii').splitlines()
    require(padrows==['PADSTACKS\t0'],'Test board still contains cached padstack definitions')
    return dict(source_sha256=record['source_sha256'],cleaned_sha256=record['cleaned_sha256'],
                fresh_session_symbol_count=0,fresh_session_definition_count=0,fresh_session_padstack_count=0,ignore_fixed_during_placement=False,
                scope='Only a private copied test board is cleared; original seed and user designs are not changed.')


def check_library_paths(path):
    # This exact list is intentional: no fallback to a user's same-name library.
    text=Path(path).read_text(encoding='ascii')
    # 16.6 returns a string, rather than a list, for a single path item.
    require(text=='PSMPATH\t"."\nPADPATH\t"."\n',
            'Native library search paths are not restricted to the verified working directory')
    return dict(psmpath=['.'],padpath=['.'])


def native_tests(job,s,validate_dra=None,validate_pads=None):
    cfg=load(ROOT/'config.json');cad=Path(cfg['cadence_bin']);name=s['name']
    old=job/'verification/native'
    if old.exists():
        history=job/'verification_history'/(time.strftime('%Y%m%d_%H%M%S')+'_'+os.urandom(3).hex())
        require(old.resolve().is_relative_to(job) and history.resolve().is_relative_to(job),'Invalid evidence archive path')
        history.parent.mkdir(exist_ok=True);old.rename(history)
    with native_runtime.temporary_stage(prefix='lc2a_verify_',configured=cfg.get('native_temp_root')) as d:
        stage=Path(d);require(str(stage).isascii(),'Native temporary path must be ASCII')
        try:
            for f in (job/'native').iterdir():
                if f.suffix in ('.dra','.psm','.pad'):shutil.copy2(f,stage/f.name)
            # Inspect saved visibility before lcReadAll temporarily enables every layer.
            (stage/'read.il').write_text(READER+'\nlcReadView("default_view.tsv")\nlcReadAll("drawing.tsv")\n',encoding='ascii')
            (stage/'read.scr').write_text('skill errset(load("read.il") t)\nexit\n',encoding='ascii')
            # A hidden graphics session exercises the same saved layer state as opening DRA.
            output=run([cad/'allegro.exe','-s','read.scr',name+'.dra'],stage)
            (stage/'drawing_stdout.txt').write_text(output,encoding='utf-8')
            require((stage/'drawing.tsv').is_file(),'DRA readback failed')
            require('16.6' in output,'Test session was not Allegro 16.6')
            # Reject malformed DRA before feeding it into 16.6's placement path.
            # A missing-pin mutant exposed a native crash there, even with DRC disabled.
            if validate_dra is not None:validate_dra(stage/'drawing.tsv')
            # Inspect both embedded DRA definitions and original disk PADs before
            # a target board can discard layers absent from its own stack-up.
            run([cad/'extracta.exe','-q',name+'.dra',cfg['pad_report_view'],'drawing_pads.txt'],stage)
            for pad in sorted(stage.glob('*.pad')):
                pad_summary.capture(pad,stage/'raw_pads'/pad.stem,cad/'pad_designer.exe',native_temp_root=cfg.get('native_temp_root'))
            if validate_pads is not None:validate_pads(stage)
            limit=test_drawing_limit(read_native(stage/'drawing.tsv'),load(job/'model.json'))
            save(stage/'test_layout.json',dict(requested_extents_mm=[-limit,-limit,limit,limit],
                placement_anchors_mm=[[*anchor,angle] for _,anchor,angle,_ in BOARD_STATES],margin_mm=10,
                states=[dict(file=filename,anchor_mm=anchor,rotation_deg=angle,mirrored=mirror) for filename,anchor,angle,mirror in BOARD_STATES]))
            # A user-selected seed can contain FIXED symbols and cached PSMs.
            # Clear only this private copy, save, then inspect in a fresh session.
            # A read-only source template is valid. The disposable working copy
            # must be writable without changing the source file's protection.
            shutil.copyfile(cfg['board_seed'],stage/'placement.brd')
            shutil.copy2(stage/'placement.brd',stage/'seed_input.brd')
            cleanup='''axlVisibleDesign(t)
axlDBControl('drcEnable nil)
axlDBIgnoreFixed(t)
axlSetFindFilter(?enabled list("alltypes") ?onButtons list("alltypes"))
axlAddSelectAll()
axlDeleteObject(axlGetSelSet())
axlClearSelSet()
axlDBIgnoreFixed(nil)
axlPurgePadstacks('padstacks nil)
axlSaveDesign(?noMru t ?noConfirm t)
f=outfile("seed_cleanup_done.txt") fprintf(f "OK\\n") close(f)
'''
            (stage/'seed_cleanup.il').write_text(cleanup,encoding='ascii')
            (stage/'seed_cleanup.scr').write_text('skill errset(load("seed_cleanup.il") t)\nexit\n',encoding='ascii')
            output=run([cad/'allegro.exe','-s','seed_cleanup.scr','placement.brd'],stage)
            (stage/'seed_cleanup_stdout.txt').write_text(output,encoding='utf-8')
            require((stage/'seed_cleanup_done.txt').is_file(),'Private test seed cleanup did not complete')
            shutil.copy2(stage/'placement.brd',stage/'seed_cleaned.brd')
            save(stage/'seed_cleanup.json',dict(schema='private_seed_cleanup_v1',private_copy_only=True,
                source_path=str(cfg['board_seed']),source_sha256=sha(stage/'seed_input.brd'),
                cleaned_sha256=sha(stage/'seed_cleaned.brd'),cleanup_completed=True))
            code=READER+board_sides.PAD_LAYER_READER+f'''
axlVisibleDesign(t)
axlDBControl('drcEnable nil)
f=outfile("seed_empty.tsv")
fprintf(f "SEED\\t%d\\t%d\\t%L\\n" length(axlDBGetDesign()->symbols) length(axlDBGetDesign()->symdefs) axlDBIgnoreFixed())
close(f)
f=outfile("seed_padstacks.tsv")
fprintf(f "PADSTACKS\\t%d\\n" length(axlDBGetDesign()->padstacks))
foreach(pad axlDBGetDesign()->padstacks fprintf(f "PADSTACK\\t%s\\n" pad->name))
close(f)
unless(null(axlDBGetDesign()->symbols) && null(axlDBGetDesign()->symdefs) && null(axlDBIgnoreFixed()) error("Test seed retains symbols/definitions or ignores FIXED"))
unless(null(axlDBGetDesign()->padstacks) error("Test seed retains cached padstack definitions"))
axlDBChangeDesignUnits("millimeters" 4)
axlDBChangeDesignExtents(list(-{limit}:-{limit} {limit}:{limit}))
unless(axlSetVariable("psmpath" list(".")) error("Could not isolate PSM search path"))
unless(axlSetVariable("padpath" list(".")) error("Could not isolate PAD search path"))
unless(equal(axlGetVariableList("psmpath") ".") && equal(axlGetVariableList("padpath") ".") error("Native library search paths were not isolated"))
f=outfile("library_paths.tsv")
fprintf(f "PSMPATH\\t%L\\n" axlGetVariableList("psmpath"))
fprintf(f "PADPATH\\t%L\\n" axlGetVariableList("padpath"))
close(f)
'''
            for angle,anchor in PLACEMENTS:
                code+=f'''r=axlDBCreateSymbol(list("{name}" "PACKAGE") {anchor[0]}:{anchor[1]} nil {angle})
unless(r error("PSM placement failed"))
s=car(r)
lcReadSymbol(s "placed{angle}.tsv")
lcReadPadLayers(s "placed{angle}.tsv.layers")
unless(axlTransformObject(s ?move {PLACEMENT_MOVE[0]}:{PLACEMENT_MOVE[1]}) error("PSM move failed"))
'''
            for angle,anchor in PLACEMENTS:
                code+=f'''r=axlDBCreateSymbol(list("{name}" "PACKAGE") {anchor[0]}:{anchor[1]+40} t {angle})
unless(r error("Bottom PSM placement failed"))
s=car(r)
lcReadSymbol(s "bottom_placed{angle}.tsv")
lcReadPadLayers(s "bottom_placed{angle}.tsv.layers")
unless(axlTransformObject(s ?move {PLACEMENT_MOVE[0]}:{PLACEMENT_MOVE[1]}) error("Bottom PSM move failed"))
'''
            code+='''axlSaveDesign(?noMru t ?noConfirm t)
f=outfile("test_done.txt") fprintf(f "OK\\n") close(f)
'''
            (stage/'placement.il').write_text(code,encoding='ascii')
            (stage/'placement.scr').write_text('skill errset(load("placement.il") t)\nskill axlSaveDesign(?noMru t ?noConfirm t)\nexit\n',encoding='ascii')
            output=run([cad/'allegro.exe','-s','placement.scr','placement.brd'],stage)
            (stage/'placement_stdout.txt').write_text(output,encoding='utf-8')
            require((stage/'test_done.txt').is_file(),'PSM placement/move failed; read verification/native/placement_stdout.txt')
            check_clean_seed(stage)
            # A fresh session is essential: 16.6 caches child DBIDs after transforms.
            # Do not force-refresh stale child IDs; inspect the persisted board instead.
            code=READER+board_sides.PAD_LAYER_READER+'\nforeach(sym axlDBGetDesign()->symbols\n'
            for angle,_ in PLACEMENTS:
                filename='moved.tsv' if angle==0 else f'saved{angle}.tsv'
                code+=f''' when(sym->rotation == {angle}.0
  if(sym->isMirrored then
   lcReadSymbol(sym "bottom_{filename}") lcReadPadLayers(sym "bottom_{filename}.layers")
  else
   lcReadSymbol(sym "{filename}") lcReadPadLayers(sym "{filename}.layers")
  )
 )
'''
            code+=')\n'
            (stage/'saved.il').write_text(code,encoding='ascii')
            (stage/'saved.scr').write_text('skill errset(load("saved.il") t)\nexit\n',encoding='ascii')
            output=run([cad/'allegro.exe','-s','saved.scr','placement.brd'],stage)
            (stage/'saved_stdout.txt').write_text(output,encoding='utf-8')
            for filename,_,_,_ in BOARD_STATES:
                require((stage/filename).is_file(),'PSM state readback missing: '+filename)
                require((stage/(filename+'.layers')).is_file(),'Actual board-layer readback missing: '+filename)
            run([cad/'extracta.exe','-q','placement.brd',cfg['pad_report_view'],'disk_pads.txt'],stage)
            require((stage/'disk_pads.txt').is_file(),'Disk pad geometry extraction failed')
        finally:
            dest=job/'verification/native';dest.mkdir(parents=True,exist_ok=True)
            for f in stage.iterdir():
                if f.is_file():shutil.copy2(f,dest/f.name)
                elif f.name=='raw_pads':shutil.copytree(f,dest/f.name)

def read_native(path):
    pins=[];graphics=[];texts=[];bounds=[];anchor=None;units='';pending=None
    def finite(values):
        result=list(map(float,values));require(all(math.isfinite(n) for n in result),'Non-finite native boundary value');return result
    for row in path.read_text(encoding='ascii').splitlines():
        v=row.split('\t')
        require(pending is None or v[0] in ('BL','BA','BE'),'Truncated native placement boundary')
        if v[0]=='P':pins.append(dict(number=v[1],at=[float(v[2]),float(v[3])],angle=float(v[4]),padstack=v[5].lower(),through=v[6]=='t'))
        elif v[0]=='L':graphics.append(dict(kind='line',layer=v[1],width=float(v[2]),start=[float(v[3]),float(v[4])],end=[float(v[5]),float(v[6])]))
        elif v[0]=='C':graphics.append(dict(kind='circle',layer=v[1],width=float(v[2]),center=[float(v[3]),float(v[4])],radius=float(v[5])))
        elif v[0]=='T':
            text=dict(layer=v[1],text=v[2],at=[float(v[3]),float(v[4])])
            if len(v)==14:
                text.update(bounds=list(map(float,v[5:9])),font_width=float(v[9]),font_height=float(v[10]),stroke=float(v[11]),angle=float(v[12]),mirrored=v[13]=='t')
            else:require(len(v)==5,'Malformed native text record')
            texts.append(text)
        elif v[0]=='B':
            require(len(v) in (6,8),'Malformed native placement boundary')
            item=dict(layer=v[1],bounds=finite(v[2:6]));bounds.append(item)
            if len(v)==8:
                item.update(voids=int(v[6]),segment_count=int(v[7]),segments=[]);pending=item
                require(item['voids']>=0 and item['segment_count']>0,'Invalid native placement boundary counts')
        elif v[0]=='BL':
            require(pending is not None and len(v)==5,'Malformed native boundary line');values=finite(v[1:])
            pending['segments'].append(dict(kind='line',start=values[:2],end=values[2:]))
        elif v[0]=='BA':
            require(pending is not None and len(v)==10 and v[8] in ('t','nil') and v[9] in ('t','nil'),'Malformed native boundary arc')
            values=finite(v[1:8]);pending['segments'].append(dict(kind='arc',start=values[:2],end=values[2:4],center=values[4:6],radius=values[6],clockwise=v[8]=='t',circle=v[9]=='t'))
        elif v[0]=='BE':
            require(pending is not None and len(v)==1 and len(pending['segments'])==pending['segment_count'],'Incomplete native placement boundary segments');pending=None
        elif v[0]=='S':anchor=list(map(float,v[1:4]))
        elif v[0]=='UNITS':units=v[1]
        else:raise ValueError('Unknown native readback record: '+row)
    require(pending is None,'Truncated native placement boundary')
    return dict(pins=pins,graphics=graphics,texts=texts,bounds=bounds,anchor=anchor,units=units)

def color_contrast(foreground,background):
    """sRGB relative luminance contrast; a display policy, not certification.

    Formula: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
    """
    def luminance(rgb):
        require(len(rgb)==3 and all(type(c) is int and 0<=c<=255 for c in rgb),'Invalid default view RGB')
        c=[v/255 for v in rgb]
        c=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in c]
        return sum(a*b for a,b in zip(c,(.2126,.7152,.0722)))
    a,b=sorted((luminance(foreground),luminance(background)))
    return (b+.05)/(a+.05)


def check_default_view(path):
    layers={};background=None
    for row in path.read_text(encoding='ascii').splitlines():
        v=row.split('\t')
        if v[0]=='BG':
            require(len(v)==4 and background is None,'Missing/duplicate or malformed default background record')
            background=list(map(int,v[1:]));color_contrast(background,background);continue
        require(len(v)==6 and v[0]=='V' and v[2] in ('t','nil'),'Malformed default visibility record')
        require(v[1] not in layers,'Duplicate default visibility record')
        layers[v[1]]=dict(visible=v[2]=='t',rgb=list(map(int,v[3:])))
    require(background is not None,'Missing saved default background evidence; verify again')
    ratios={}
    for cls in ('REF DES','PACKAGE GEOMETRY'):
        for subclass,visible in [('SILKSCREEN_TOP',True),('ASSEMBLY_TOP',False)]:
            key=cls+'/'+subclass
            require(key in layers and layers[key]['visible']==visible,'Default layer visibility differs: '+key)
            ratios[key]=color_contrast(layers[key]['rgb'],background)
            require(ratios[key]>=4.5,'Default layer/background contrast below 4.5:1: '+key)
    return dict(layers=layers,background_rgb=background,layer_background_contrast=ratios,minimum_background_contrast=4.5)


def check_presentation(path,texts,pads,graphics,refdes):
    view=check_default_view(path)
    labels={}
    for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
        matches=[t for t in texts if t['layer']==layer]
        require(len(matches)==1 and matches[0]['text']==refdes+'*','Missing/misplaced true RefDes label on '+layer)
        t=matches[0];labels[layer]=t
        require('bounds' in t,'Missing native RefDes font/bounds evidence')
        require(t['font_height']>=.6 and t['font_width']>=.3 and t['stroke']>=.1,'RefDes font too small or zero-width stroke')
        require(not t['mirrored'] and angleclose(t['angle'],0),'RefDes mirrored or rotated in source DRA')
    assembly=labels['REF DES/ASSEMBLY_TOP']
    require(math.dist(assembly['at'],[0,0])<TOL,'Assembly RefDes is not centered at origin')
    silk=labels['REF DES/SILKSCREEN_TOP']
    require(close(silk['at'][0],0),'Silkscreen RefDes is not horizontally centered')
    outline=unary_union([geometry_path(g).buffer(g['width']/2) for g in graphics])
    require(silk['bounds'][1]>outline.bounds[3]+.15-TOL,'Silkscreen RefDes overlaps body or outline')
    label_box=box(*silk['bounds'])
    masks=[mask_pad(p) for p in pads if has_top_mask(p)]
    clearance=min((solid_to_pad_gap(label_box,p) for p in masks),default=999)
    require(clearance>=.15-TOL,'RefDes-to-mask clearance below 0.15mm')
    return dict(**view,silkscreen_refdes=silk,assembly_refdes=assembly,refdes_to_mask_mm=round(clearance,6))

def compare_pins(expected,actual,model,anchor=(0,0),rotation=0):
    require(len(expected)==len(actual),'Pin count changed')
    ap={p['number']:p for p in actual};mp={p['number']:p for p in model['pads']}
    require(set(ap)=={p['number'] for p in expected},'Pin numbering changed/duplicated')
    for p in expected:
        x,y=p['at'];rad=math.radians(rotation)
        xy=[anchor[0]+x*math.cos(rad)-y*math.sin(rad),anchor[1]+x*math.sin(rad)+y*math.cos(rad)]
        a=ap[p['number']]
        require(math.dist(a['at'],xy)<TOL and angleclose(a['angle'],p['angle']+rotation),'Pin coordinate/rotation differs: '+p['number'])
        require(a['padstack']==mp[p['number']]['padstack'],'Pin-to-padstack binding differs: '+p['number'])
        require(a['through']==(p['type']=='thru_hole'),'Through/SMD pin type differs')

def check_refdes_rotation(expected,actual,rotation=0):
    checked={}
    for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
        source=[t for t in expected if t['layer']==layer];placed=[t for t in actual if t['layer']==layer]
        require(len(source)==len(placed)==1,'Compiled RefDes missing or duplicated on '+layer)
        source,placed=source[0],placed[0]
        require('angle' in source and 'angle' in placed,'Missing compiled RefDes rotation evidence on '+layer)
        want=source['angle']+rotation;got=placed['angle']
        require(math.isfinite(want) and math.isfinite(got) and angleclose(got,want),'Compiled RefDes rotation differs on '+layer)
        checked[layer]={'expected_deg':want%360,'actual_deg':got%360}
    return checked

def compare_refdes(expected,actual,pads,graphics,anchor=(0,0),rotation=0,actual_pads=None):
    rotations=check_refdes_rotation(expected,actual,rotation)
    mapped=[];clearances=[]
    for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
        e=[t for t in expected if t['layer']==layer];a=[t for t in actual if t['layer']==layer]
        require(len(e)==1 and len(a)==1 and e[0]['text']==a[0]['text'],'Compiled RefDes missing or changed on '+layer)
        e,a=e[0],a[0];x,y=e['at'];rad=math.radians(rotation)
        xy=[anchor[0]+x*math.cos(rad)-y*math.sin(rad),anchor[1]+x*math.sin(rad)+y*math.cos(rad)]
        require(math.dist(xy,a['at'])<TOL,'Compiled RefDes position differs on '+layer)
        for k in ('font_width','font_height','stroke'):
            require(k in e and k in a,'Missing compiled RefDes font evidence')
        require(a['font_height']>=.6 and a['font_width']>=.3 and a['stroke']>=.1,'Compiled RefDes font too small or zero-width stroke')
        if any(not close(e[k],a[k]) for k in ('font_width','font_height','stroke')):
            mapped.append({'layer':layer,'source':{k:e[k] for k in ('font_width','font_height','stroke')},'board':{k:a[k] for k in ('font_width','font_height','stroke')}})
        require(not a['mirrored'],'Compiled RefDes unexpectedly mirrored')
        if layer.endswith('/SILKSCREEN_TOP'):
            local_box=rotate(translate(box(*a['bounds']),-anchor[0],-anchor[1]),-rotation,origin=(0,0))
            mask_source=pads if actual_pads is None else actual_pads
            mask_box=local_box if actual_pads is None else box(*a['bounds'])
            masks=[mask_pad(p) for p in mask_source if has_top_mask(p)]
            clearance=min((solid_to_pad_gap(mask_box,p) for p in masks),default=999);clearances.append(clearance)
            require(clearance>=.15-TOL,'Compiled RefDes-to-mask clearance below 0.15mm')
            top=max(geometry_path(g).buffer(g['width']/2).bounds[3] for g in graphics)
            require(local_box.bounds[1]>=top+.15-TOL,'Compiled RefDes overlaps outline')
    return dict(font_mapping=mapped,refdes_to_mask_mm=round(min(clearances),6),rotation=rotations)

def geometry_path(g):
    return LineString([g['start'],g['end']]) if g['kind']=='line' else Point(g['center']).buffer(g['radius'],quad_segs=128).boundary

def compare_graphics(expected,actual,anchor=(0,0),rotation=0):
    require(len(expected)==len(actual),'Graphic count differs (lost or extra line/circle)')
    remaining=actual.copy()
    for g in expected:
        a=translate(rotate(geometry_path(g),rotation,origin=(0,0)),*anchor)
        found=None
        for i,h in enumerate(remaining):
            if g['kind']==h['kind'] and g['layer']==h['layer'] and close(g['width'],h['width']) and a.hausdorff_distance(geometry_path(h))<TOL:
                found=i;break
        require(found is not None,'Graphic layer/shape/width/position differs: '+str(g))
        remaining.pop(found)

def closed_region(paths,description):
    require(paths,description+' has no boundary edges')
    union=unary_union(paths)
    require(abs(sum(p.length for p in paths)-union.length)<TOL,description+' contains duplicated/overlapping edges')
    regions,cuts,dangles,invalid=polygonize_full(union)
    require(len(regions.geoms)==1 and cuts.is_empty and dangles.is_empty and invalid.is_empty,description+' must be one closed non-branching contour')
    region=regions.geoms[0]
    require(region.is_valid and region.area>0 and not region.interiors,description+' is invalid or contains holes')
    return region

def source_placement_region(graphics):
    # Independent polygonization; the converter assembles an endpoint graph.
    paths=[geometry_path(g) for g in graphics if g['layer']==LAYER['F.CrtYd']]
    return closed_region(paths,'Source placement boundary')

def native_boundary_region(boundary):
    require('segments' in boundary and boundary.get('segment_count')==len(boundary['segments']),'Native placement boundary lacks complete edge evidence')
    require(boundary.get('voids')==0,'Native placement boundary has unexpected voids')
    paths=[]
    for seg in boundary['segments']:
        a,b=seg['start'],seg['end']
        if seg['kind']=='line':
            require(math.dist(a,b)>0,'Native placement boundary has a zero-length edge');paths.append(LineString([a,b]));continue
        require(seg['kind']=='arc','Unsupported native placement boundary segment')
        c=seg['center'];r=seg['radius']
        require(math.isfinite(r) and r>0 and close(math.dist(a,c),r) and close(math.dist(b,c),r),'Native placement boundary arc radius/endpoints differ')
        if seg['circle']:
            require(math.dist(a,b)<TOL,'Native placement boundary circle is not closed')
            paths.append(Point(c).buffer(r,quad_segs=128).boundary);continue
        start=math.atan2(a[1]-c[1],a[0]-c[0]);end=math.atan2(b[1]-c[1],b[0]-c[0])
        sweep=-((start-end)%(2*math.pi)) if seg['clockwise'] else (end-start)%(2*math.pi)
        require(abs(sweep)>1e-10,'Native placement boundary arc has zero sweep')
        count=max(1,math.ceil(abs(sweep)/(math.pi/256)))
        points=[a]+[[c[0]+r*math.cos(start+sweep*i/count),c[1]+r*math.sin(start+sweep*i/count)] for i in range(1,count)]+[b]
        paths.append(LineString(points))
    region=closed_region(paths,'Native placement boundary')
    require(all(close(a,b) for a,b in zip(region.bounds,boundary['bounds'])),'Native placement boundary bounds disagree with its edges')
    return region

def compare_placement_boundary(graphics,bounds,anchor=(0,0),rotation=0):
    require(len(bounds)==1 and bounds[0]['layer']==LAYER['F.CrtYd'],'Missing or extra explicit placement boundary; regenerate legacy automatic envelopes')
    expected=translate(rotate(source_placement_region(graphics),rotation,origin=(0,0)),*anchor)
    actual=native_boundary_region(bounds[0])
    error=expected.boundary.hausdorff_distance(actual.boundary)
    require(error<TOL and expected.buffer(TOL).covers(actual) and actual.buffer(TOL).covers(expected),'Compiled placement boundary geometry differs from source courtyard')
    return dict(area_mm2=actual.area,source_area_mm2=expected.area,maximum_boundary_error_mm=error,voids=bounds[0]['voids'],segment_count=bounds[0]['segment_count'])

def read_pad_rows(report):
    rows={}
    for row in report.read_text(encoding='ascii').splitlines():
        if row.startswith('S!'):
            v=row.split('!');require(len(v)>=27,'Malformed PAD_DEF row')
            ident=(v[1].lower(),v[3]);require(ident not in rows,'Duplicate PAD_DEF row')
            rows[ident]=v
    require(rows,'Missing PAD_DEF rows')
    return rows


def reference_native_dimensions(p,margin=0):
    dimensions=[]
    for value in p['size']:
        value=(Decimal(str(value))+Decimal(str(margin))*2).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP)
        require(value.is_finite() and value>0,'Reference dimension is below the positive database grid')
        dimensions.append(float(value))
    a,b=dimensions
    if p['shape']=='rect':name='SQUARE' if a==b else 'RECTANGLE'
    elif p['shape']=='oval':name='CIRCLE' if a==b else ('OBLONG_X' if a>b else 'OBLONG_Y')
    else:
        require(p['shape']=='circle' and a==b,'Reference circle has unequal database dimensions');name='CIRCLE'
    return dict(shape=name,size=dimensions)


def check_pad_quantization(job,state,pads):
    rows=[]
    for p in pads:
        drill=Decimal(str(p['drill'] or 0)).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
        require(not p['drill'] or drill>0,'Reference drill is below the positive database grid')
        rows.append(dict(number=p['number'],source_shape=p['shape'],source_size_mm=p['size'],source_mask_mm=p['mask'],source_drill_mm=p['drill'] or 0,
                         copper=reference_native_dimensions(p),mask=reference_native_dimensions(p,p['mask']) if has_top_mask(p) or 'B.Mask' in p['layers'] else None,
                         paste=reference_native_dimensions(p) if 'F.Paste' in p['layers'] else None,drill_mm=float(drill)))
    expected=dict(schema='pad_dimensions_4dp_half_up_v1',units='mm',step_mm=.0001,pads=rows)
    revision=state.get('pad_quantization_revision',0)
    require(revision in (0,1),'Unknown pad quantization revision')
    if revision==1:
        require('pad_quantization.json' in state['files'],'Missing pad quantization manifest entry')
        require(load(job/'pad_quantization.json')==expected,'Pad quantization record differs from independently calculated source dimensions')
    return dict(expected,record_origin='build_manifest' if revision else 'derived_from_legacy_source')


def check_pads(expected,model,report):
    rows=read_pad_rows(report)
    mp={p['number']:p for p in model['pads']}
    for p in expected:
        n=mp[p['number']]['padstack'];w,h=p['size']
        def check(layer,needed,margin=0):
            r=rows.get((n,layer))
            if needed:
                require(r is not None and r[6]==reference_native_dimensions(p,margin)['shape'] and close(r[7],w+2*margin) and close(r[8],h+2*margin),'Disk pad dimensions/shape differ: '+n+' '+layer)
                require(close(r[9],0) and close(r[10],0),'Nonzero pad offset '+n)
            else:require(r is None or (close(r[7],0) and close(r[8],0)),'Unexpected pad on '+layer)
        th=p['type']=='thru_hole'
        check('TOP',True);check('BOTTOM',th);check('internal_pad_def',th)
        for (padname,layer),row in rows.items():
            if padname!=n or layer.startswith('~'):continue
            check(layer,layer=='TOP' or th)
            require(not row[11] and not row[12],'Unexpected copper flash/shape reference '+layer)
            require(all(not row[i] for i in (13,18,19,20,25,26)) and all(close(row[i] or 0,0) for i in (14,15,16,17,21,22,23,24)),'Unexpected anti/thermal definition on '+layer)
        check('~TSM','F.Mask' in p['layers'] or '*.Mask' in p['layers'],p['mask'])
        check('~BSM','B.Mask' in p['layers'] or '*.Mask' in p['layers'],p['mask'])
        check('~TPM','F.Paste' in p['layers']);check('~BPM',False)
        check('~TFM',False);check('~BFM',False)
        r=rows.get((n,'~DRILL'))
        if th:
            require(r is not None and close(r[6],p['drill']) and close(r[7],p['drill']) and close(r[8],p['drill']),'Hole diameter differs')
            require(r[12]=='P' and r[21]=='CIRCLE_DRILL','Hole is not circular/plated')
            require(close(r[9],0) and close(r[10],0),'Hole offset differs')
        else:require(r is None or (close(r[7],0) and close(r[8],0)),'SMD unexpectedly contains a drill')


def check_raw_pad(p,text):
    """Interpret Pad Designer's complete file report; no board layer mapping."""
    require(re.search(r'Software Version\s*:\s*16\.6-',text),'Raw PAD report is not from 16.6')
    def field(name):
        matches=re.findall(r'^'+re.escape(name)+r':\s*([^\r\n]+)',text,re.M)
        require(len(matches)==1,'Missing/duplicate raw PAD field '+name);return matches[0].strip()
    require(field('Units')=='MILLIMETERS' and field('Decimal places')=='4','Unsupported raw PAD report units/precision')
    th=p['type']=='thru_hole'
    require(field('Type')==('Through' if th else 'Single (Surface)'),'Raw PAD type differs')
    require(field('Single Mode')==('Off' if th else 'On') and field('Enable ARK')=='Off','Unsupported raw PAD mode')
    require(field('Suppress pads')=='Allowed','Unexpected raw PAD suppression mode')
    sections=re.split(r'^NCDRILL\s*$',text,flags=re.M)
    require(len(sections)==2,'Incomplete/duplicate raw PAD drill section')
    header=re.split(r'^Layer Name[^\n]*\n-+\s*\n',sections[0],flags=re.M)
    require(len(header)==2,'Missing raw PAD layer table')
    layers={};current=None
    number=r'[+-]?\d+(?:\.\d+)?'
    for line in header[1].splitlines():
        if not line.strip():continue
        if not line[0].isspace():
            current=line.strip();require(current not in layers,'Duplicate raw PAD layer '+current);layers[current]={};continue
        require(current is not None,'Raw PAD geometry lacks a layer')
        row=re.fullmatch(r'\s*(REGULAR-PAD|THERMAL-PAD|ANTI-PAD)\s+(.+?)\s*',line)
        require(row is not None,'Malformed raw PAD geometry row')
        kind,geometry=row.groups();require(kind not in layers[current],'Duplicate raw PAD geometry '+current)
        if geometry=='Not Defined':value=None
        else:
            match=re.fullmatch(r'(Circle|Square|Rectangle|Oblong_X|Oblong_Y)\s+('+number+r')\s+('+number+r')\s+('+number+r')/('+number+r')',geometry)
            require(match is not None,'Unknown raw PAD shape/flash/geometry on '+current)
            value={'shape':match[1],'size':list(map(float,match.group(2,3))),'offset':list(map(float,match.group(4,5)))}
            require(all(math.isfinite(v) for v in value['size']+value['offset']),'Nonfinite raw PAD geometry')
        layers[current][kind]=value
    technical={'TOP SOLDERMASK','BOTTOM SOLDERMASK','TOP PASTEMASK','BOTTOM PASTEMASK','TOP FILMMASK','BOTTOM FILMMASK'}
    required={'BEGIN LAYER','DEFAULT INTERNAL'}|technical|({'END LAYER'} if th else set())
    require(required<=layers.keys(),'Missing raw PAD layer sections')
    w,h=p['size']
    for layer,rows in layers.items():
        wanted={'REGULAR-PAD'} if layer in technical else {'REGULAR-PAD','THERMAL-PAD','ANTI-PAD'}
        require(set(rows)==wanted,'Incomplete raw PAD geometry on '+layer)
        if layer not in technical:
            require(rows['THERMAL-PAD'] is None and rows['ANTI-PAD'] is None,'Unexpected raw anti/thermal definition on '+layer)
            needed=layer=='BEGIN LAYER' or th;margin=0
        else:
            needed={'TOP SOLDERMASK':'F.Mask' in p['layers'] or '*.Mask' in p['layers'],
                'BOTTOM SOLDERMASK':'B.Mask' in p['layers'] or '*.Mask' in p['layers'],
                'TOP PASTEMASK':'F.Paste' in p['layers']}.get(layer,False)
            margin=p['mask'] if layer.endswith('SOLDERMASK') else 0
        value=rows['REGULAR-PAD']
        if needed:
            require(value is not None and value['shape'].upper()==reference_native_dimensions(p,margin)['shape'] and all(close(a,b) for a,b in zip(value['size'],[w+2*margin,h+2*margin])),'Raw PAD dimensions/shape differ on '+layer)
            require(all(close(a,0) for a in value['offset']),'Raw PAD offset differs on '+layer)
        else:require(value is None,'Unexpected raw PAD geometry on '+layer)
    drill_parts=re.split(r'^DRILL SYMBOL\s*$',sections[1],flags=re.M)
    require(len(drill_parts)==2 and drill_parts[1].strip(),'Incomplete/duplicate raw PAD drill symbol')
    drill=drill_parts[0].strip();symbol=' '.join(drill_parts[1].split())
    hole=None
    if th:
        match=re.fullmatch('('+number+r')\s+Circle-Drill\s+Plated\s+Tolerance:\s+('+number+')/('+number+r')\s+Offset:\s+('+number+')/('+number+')',drill)
        require(match is not None,'Raw hole is not circular/plated or report is malformed')
        require(close(match[1],p['drill']),'Raw hole diameter differs')
        require(all(close(float(v),0) for v in match.group(2,3,4,5)),'Raw hole offset/tolerance differs')
        values=symbol.split();require(len(values)==3 and values[0]=='Circle' and close(values[1],p['drill']) and close(values[2],p['drill']),'Raw drill symbol differs')
        hole={'diameter':float(match[1]),'offset':list(map(float,match.group(4,5)))}
    else:require(drill=='Not Drilled' and symbol=='Not Defined 0.0000 0.0000','Raw SMD unexpectedly contains a drill')
    names={'BEGIN LAYER':'TOP','END LAYER':'BOTTOM','DEFAULT INTERNAL':'internal_pad_def',
           'TOP SOLDERMASK':'~TSM','BOTTOM SOLDERMASK':'~BSM','TOP PASTEMASK':'~TPM',
           'BOTTOM PASTEMASK':'~BPM','TOP FILMMASK':'~TFM','BOTTOM FILMMASK':'~BFM'}
    return {'layers':sorted(layers),'through_hole':th,'geometry':{names.get(k,k):v['REGULAR-PAD'] for k,v in layers.items()},'drill':hole}


def check_raw_pads(expected,model,directory):
    mapping={p['number']:p['padstack'] for p in model['pads']};result={}
    for p in expected:
        name=mapping[p['number']]
        # Every source pin is compared, even if several share a native padstack.
        result[name]=check_raw_pad(p,(directory/name/'summary.txt').read_text(encoding='ascii'))
    return result


def pad_definitions(report,model):
    rows=read_pad_rows(report);definitions={}
    for name in {p['padstack'] for p in model['pads']}:
        geometry={};hole=None
        for (padname,layer),r in rows.items():
            if padname!=name:continue
            if layer=='~DRILL':
                if float(r[7])>0:hole={'diameter':float(r[6]),'offset':list(map(float,r[9:11]))}
            else:
                geometry[layer]=None if not r[6] else dict(shape=r[6],size=list(map(float,r[7:9])),offset=list(map(float,r[9:11])))
        require(geometry,'Missing actual pad definition: '+name)
        definitions[name]={'geometry':geometry,'drill':hole}
    return definitions


def native_pad_layers(expected,pins,model,definitions):
    """Use actual dimensions, hole/mask offsets and actual instance transforms.

    The existing source-to-native tolerance checks remain mandatory. They do
    not prove that the resulting copper/ring/mask clearances meet the rule.
    """
    mapping={p['number']:p['padstack'] for p in model['pads']};by_pin={p['number']:p for p in pins}
    require(len(by_pin)==len(pins) and set(by_pin)=={p['number'] for p in expected},'Actual pad geometry has missing/duplicate pins')
    used={mapping[p['number']] for p in expected}
    require(used<=definitions.keys(),'Actual pad definitions incomplete')
    layers=sorted({layer for name in used for layer in definitions[name]['geometry'] if not layer.startswith('~')})
    result={layer:[] for layer in layers}
    for original in expected:
        pin=by_pin[original['number']];definition=definitions[mapping[original['number']]];geometry=definition['geometry'];hole=definition['drill']
        def point(offset):
            rad=math.radians(pin['angle']);x,y=offset
            return [pin['at'][0]+x*math.cos(rad)-y*math.sin(rad),pin['at'][1]+x*math.sin(rad)+y*math.cos(rad)]
        def actual(value):
            shapes={'CIRCLE':'circle','SQUARE':'rect','RECTANGLE':'rect','OBLONG_X':'oval','OBLONG_Y':'oval'}
            require(value['shape'].upper() in shapes,'Unsupported actual pad shape')
            require(all(math.isfinite(v) for v in value['size']+value['offset']) and min(value['size'])>0,'Invalid actual pad dimensions')
            if value['shape'].upper() in ('CIRCLE','SQUARE'):require(abs(value['size'][0]-value['size'][1])<1e-8,'Actual round/square pad is asymmetric')
            return dict(shape=shapes[value['shape'].upper()],size=list(value['size']),at=point(value['offset']),angle=pin['angle'])
        mask=actual(geometry['~TSM']) if geometry.get('~TSM') else None
        if hole:require(math.isfinite(hole['diameter']) and hole['diameter']>0 and all(math.isfinite(v) for v in hole['offset']),'Invalid actual hole geometry')
        for layer in layers:
            value=geometry.get(layer,geometry.get('internal_pad_def')) if original['type']=='thru_hole' else geometry.get(layer)
            if value is None:continue
            result[layer].append(dict(original,**actual(value),top_mask=mask,drill=hole['diameter'] if hole else 0,
                                      hole_at=point(hole['offset']) if hole else None))
    require('TOP' in result and len(result['TOP'])==len(expected),'Actual top copper is incomplete')
    return result

def pad_polygon(p,margin=0):
    w,h=[v+2*margin for v in p['size']]
    if p['shape']=='rect':g=box(-w/2,-h/2,w/2,h/2)
    elif p['shape']=='circle':g=Point(0,0).buffer(w/2,quad_segs=128)
    else:
        if w>=h:g=LineString([(-(w-h)/2,0),((w-h)/2,0)]).buffer(h/2,quad_segs=128)
        else:g=LineString([(0,-(h-w)/2),(0,(h-w)/2)]).buffer(w/2,quad_segs=128)
    return translate(rotate(g,p['angle'],origin=(0,0)),*p['at'])

def pad_core(p,margin=0):
    """Exact rectangle, or point/segment plus a circular radius (capsule).

    Polygonized circles remain useful for visualization/region comparisons,
    but an inscribed polygon overestimates the copper or mask clearance.
    """
    w,h=[v+2*margin for v in p['size']]
    require(w>0 and h>0,'Invalid exact pad dimensions')
    if p['shape']=='rect':core=box(-w/2,-h/2,w/2,h/2);radius=0.
    elif p['shape']=='circle':core=Point(0,0);radius=w/2
    elif p['shape']=='oval':
        radius=min(w,h)/2
        if w==h:core=Point(0,0)
        elif w>h:core=LineString([(-(w-h)/2,0),((w-h)/2,0)])
        else:core=LineString([(0,-(h-w)/2),(0,(h-w)/2)])
    else:raise ValueError('Unsupported exact pad shape')
    return translate(rotate(core,p['angle'],origin=(0,0)),*p['at']),radius


def copper_gap(a,b):
    ac,ar=pad_core(a);bc,br=pad_core(b)
    return max(0.,ac.distance(bc)-ar-br)


def solid_to_pad_gap(solid,p,margin=0):
    core,radius=pad_core(p,margin)
    return max(0.,solid.distance(core)-radius)


def has_top_mask(p):
    return p['top_mask'] is not None if 'top_mask' in p else 'F.Mask' in p['layers'] or '*.Mask' in p['layers']


def mask_pad(p):
    if 'top_mask' in p:
        require(p['top_mask'] is not None,'Missing actual top mask geometry')
        return p['top_mask']
    return dict(p,size=[v+2*p['mask'] for v in p['size']])


def silk_to_pad_gap(g,p):
    core,radius=pad_core(mask_pad(p))
    if g['kind']=='line':distance=LineString([g['start'],g['end']]).distance(core)-radius
    elif g['kind']=='circle':
        # Radial distances across a connected convex pad form one interval.
        # Handle outside, inside, intersecting and surrounding pads. A silk
        # circle is a stroked curve, not a filled disk.
        center=Point(g['center']);minimum=max(0.,center.distance(core)-radius)
        coordinates=core.exterior.coords if core.geom_type=='Polygon' else core.coords
        maximum=max(math.dist(g['center'],v) for v in coordinates)+radius
        distance=max(minimum-g['radius'],g['radius']-maximum,0.)
    else:raise ValueError('Unsupported exact silk shape')
    return max(0.,distance-g['width']/2)


def annular_ring(p):
    core,radius=pad_core(p);hole=Point(p.get('hole_at') or p['at'])
    if core.geom_type=='Polygon':
        distance=hole.distance(core.boundary) if core.covers(hole) else -hole.distance(core)
    else:distance=radius-hole.distance(core)
    return distance-p['drill']/2


def copper_metrics(pads):
    clearance=min((copper_gap(a,b) for i,a in enumerate(pads) for b in pads[i+1:]),default=999)
    require(clearance>=.15-TOL,'Copper spacing below local 0.15mm acceptance rule')
    rings=[annular_ring(p) for p in pads if p['drill']]
    require(not rings or min(rings)>=.15-TOL,'Annular ring below 0.15mm')
    return dict(clearance_method='analytic_capsule_v1',copper_clearance_mm=round(clearance,6),minimum_annular_ring_mm=round(min(rings),6) if rings else None)


def clearance_metrics(pads,graphics):
    metrics=copper_metrics(pads)
    masks=[p for p in pads if has_top_mask(p)]
    silk=[g for g in graphics if g['layer'].endswith('/SILKSCREEN_TOP')]
    require(silk,'No silkscreen geometry')
    silkclear=min((silk_to_pad_gap(a,b) for a in silk for b in masks),default=999)
    require(silkclear>=.15-TOL,'Silkscreen-to-mask clearance below 0.15mm (stroke width included)')
    return dict(metrics,silk_to_mask_mm=round(silkclear,6))


def native_layer_rules(layers,graphics):
    metrics={}
    for layer,pads in layers.items():
        if not pads:continue
        try:metrics[layer]=clearance_metrics(pads,graphics) if layer=='TOP' else copper_metrics(pads)
        except ValueError as exc:raise ValueError('Native '+layer+': '+str(exc)) from exc
    return metrics


def body_region(graphics):
    paths=[geometry_path(g) for g in graphics if g['layer']==LAYER['F.Fab']]
    polys=list(polygonize(unary_union(paths))) if paths else []
    require(polys,'Missing or open contour: '+LAYER['F.Fab'])
    # Every closed face is part of the assembly envelope. Retain open details
    # too: selecting only the largest face silently drops protruding islands
    # and lines and disagrees with the converter's all-geometry bbox center.
    return unary_union([*polys,*paths])


def outer_coordinates(geometry):
    if geometry.is_empty:return
    if geometry.geom_type=='Polygon':yield from geometry.exterior.coords
    elif hasattr(geometry,'geoms'):
        for part in geometry.geoms:yield from outer_coordinates(part)
    else:yield from geometry.coords


def enclosure_domain(graphics,bounds=None):
    if bounds is None:
        region=source_placement_region(graphics)
        source=[g for g in graphics if g['layer']==LAYER['F.CrtYd']]
        circle=source[0] if len(source)==1 and source[0]['kind']=='circle' else None
    else:
        require(len(bounds)==1 and bounds[0]['layer']==LAYER['F.CrtYd'],'Missing/extra actual placement boundary')
        region=native_boundary_region(bounds[0]);segments=bounds[0]['segments'];arcs=[s for s in segments if s['kind']=='arc']
        circle=None
        if arcs:
            require(len(arcs)==len(segments) and all(s['center']==arcs[0]['center'] and s['radius']==arcs[0]['radius'] for s in arcs),
                    'Actual placement enclosure supports a circle or linear contour, not mixed arcs')
            circle=arcs[0]
    return dict(region=region,circle=circle)


def core_inside_domain(domain,core,radius=0):
    circle=domain['circle']
    if circle:
        farthest=max((math.dist(circle['center'],v) for v in outer_coordinates(core)),default=math.inf)+radius
        return farthest<=circle['radius']+TOL
    region=domain['region']
    if radius>0 and region.covers(core):return core.distance(region.boundary)+TOL>=radius
    if radius<=TOL:return region.buffer(TOL-radius,quad_segs=256).covers(core)
    return False


def check_placement_enclosure(pads,graphics,bounds=None):
    domain=enclosure_domain(graphics,bounds);body=body_region(graphics)
    assembly=[g for g in graphics if g['layer']==LAYER['F.Fab']]
    require(core_inside_domain(domain,body),'Place bound excludes body')
    circles=[g for g in assembly if g['kind']=='circle']
    for g in circles:
        # Polygonization is useful for filled faces, but an inscribed polygon
        # must not replace any circle's true radius, even with other details.
        require(core_inside_domain(domain,Point(g['center']),g['radius']),'Place bound excludes body circle')
    for p in pads:
        core,radius=pad_core(p)
        require(core_inside_domain(domain,core,radius),'Place bound excludes pads')
    return dict(method='circle_capsule_enclosure_v1',boundary_kind='circle' if domain['circle'] else 'linear_contour',
                assembly_outline_policy='all_closed_faces_plus_open_lines_and_exact_circles_v1',
                assembly_object_count=len(assembly),assembly_circle_count=len(circles),
                pad_layer_shapes_checked=len(pads),body_bounds_mm=list(body.bounds),place_bound_mm=list(domain['region'].bounds))


def native_enclosure(layers,drawing):
    return check_placement_enclosure([p for pads in layers.values() for p in pads],drawing['graphics'],drawing['bounds'])


def design_rules(pads,graphics):
    metrics=clearance_metrics(pads,graphics)
    enclosure=check_placement_enclosure(pads,graphics)
    body=body_region(graphics)
    x1,y1,x2,y2=body.bounds
    require(abs((x1+x2)/2)<TOL and abs((y1+y2)/2)<TOL,'Assembly geometry bounding-box center is not origin (0,0)')
    return dict(metrics,body_bounds_mm=list(body.bounds),place_bound_mm=enclosure['place_bound_mm'],placement_enclosure=enclosure)

def reviewed_body_rectangle(graphics,bounds):
    """The reviewed profiles choose an unbroken rectangular body envelope.

    Check the full region, not just its bounds. This is an explicit assembly
    drawing policy for these profiles, not a ban on other manufacturers' shapes
    or a claim that a physical IC has no chamfers.
    """
    body=closed_region([geometry_path(g) for g in graphics if g['layer']==LAYER['F.Fab']],
                       'Reviewed assembly body')
    expected=box(*bounds);error=body.boundary.hausdorff_distance(expected.boundary)
    require(error<TOL and body.buffer(TOL).covers(expected) and expected.buffer(TOL).covers(body),
            'Reviewed assembly body geometry differs from rectangular envelope policy')
    return dict(outline_policy='single rectangular assembly envelope',area_mm2=body.area,
                expected_area_mm2=expected.area,maximum_boundary_error_mm=error)

def profile_checks(s,pads,metrics,graphics,geometry_origin='source'):
    require(geometry_origin in ('source','native'),'Unknown profile geometry origin')
    pr=s.get('review_profile')
    if not pr:return {'profile':None,'limitation':'No automatic manufacturer profile: generic geometry checks only; reviewer must establish device fit.'}
    require(pr.get('profile') in ('C8734','C53568','C62715'),'Unknown manufacturer profile')
    pd={p['number']:p for p in pads}
    if pr['profile']=='C8734':
        require(len(pads)==48,'Manufacturer requires 48 leads')
        require(all(p['type']=='smd' and p['shape']=='rect' for p in pads),'ST land pattern must be rectangular SMD')
        for i in range(1,49):
            group=(i-1)//12;k=(i-1)%12
            xy=[(-2.75+.5*k,-4.25),(4.25,-2.75+.5*k),(2.75-.5*k,4.25),(-4.25,2.75-.5*k)][group]
            size=(.3,1.2) if group in (0,2) else (1.2,.3)
            require(math.dist(pd[str(i)]['at'],xy)<TOL and math.dist(pd[str(i)]['size'],size)<TOL,'ST footprint example mismatch, pin '+str(i))
        require(all(close(a,b) for a,b in zip(metrics['body_bounds_mm'],[-3.5,-3.5,3.5,3.5])),'ST body size mismatch')
        body_geometry=reviewed_body_rectangle(graphics,[-3.5,-3.5,3.5,3.5])
        require(all(set(p['layers'])=={'F.Cu','F.Paste','F.Mask'} for p in pads),'ST reviewed pad layers differ (copper/paste/mask)')
        require(all(close(p['mask'],.03) for p in pads),'ST reviewed solder mask margin differs from 0.03mm policy')
        # Figure 55 specifies a D1/4 x E1/4 identification zone. Locate that
        # corner from pin 1 in Figure 56's orientation, not from the recipe dot.
        x1,y1,x2,y2=metrics['body_bounds_mm'];w=x2-x1;h=y2-y1
        corner=min(((x1,y1),(x1,y2),(x2,y1),(x2,y2)),key=lambda c:math.dist(c,pd['1']['at']))
        zx1,zx2=(x1,x1+w/4) if corner[0]==x1 else (x2-w/4,x2)
        zy1,zy2=(y1,y1+h/4) if corner[1]==y1 else (y2-h/4,y2)
        marks=[g for g in graphics if g['layer']==LAYER['F.SilkS'] and g['kind']=='circle']
        require(len(marks)==1,'ST pin 1 marker is missing or ambiguous')
        mark=marks[0];cx,cy=mark['center'];radius=mark['radius']+mark['width']/2
        require(zx1-TOL<=cx-radius and cx+radius<=zx2+TOL and zy1-TOL<=cy-radius and cy+radius<=zy2+TOL,'ST pin 1 marker is outside the pin 1 corner zone')
        return dict(profile=pr['profile'],manufacturer_pin_count=48,pitch_mm=.5,body_nominal_mm=[7,7],land_size_mm=[.3,1.2],pin1=[-2.75,-4.25],
                    assembly_body_geometry=body_geometry,
                    pin1_marker_center_mm=mark['center'],pin1_zone_mm=[zx1,zy1,zx2,zy2],mask_margin_policy_mm=.03,
                    marker_note='Silkscreen dot is this library policy, aligned to the rotated Figure 55 identification zone; dot diameter is not a manufacturer land requirement.')
    if pr['profile']=='C62715':
        require(len(pads)==4 and set(pd)=={'1','2','3','4'} and all(p['type']=='thru_hole' for p in pads),'LTV817 requires four through holes')
        coordinates={'1':(-1.27,-3.81),'2':(1.27,-3.81),'3':(1.27,3.81),'4':(-1.27,3.81)}
        for pin,xy in coordinates.items():
            p=pd[pin]
            require(math.dist(p['at'],xy)<TOL,'LTV817 pin mapping or formed installation pitch differs, pin '+pin)
            require(p['shape']=='oval' and math.dist(p['size'],[1.524,2.0])<TOL and close(p['drill'],1.0) and close(p['angle']%180,0),
                'LTV817 reviewed oval pad/hole/orientation differs, pin '+pin)
        require(all(set(p['layers'])=={'*.Cu','*.Mask'} for p in pads),'LTV817 reviewed pad layers differ')
        require(all(close(p['mask'],.05) for p in pads),'LTV817 reviewed solder mask margin differs')
        bounds=[-2.55,-3.5,2.55,3.5]
        require(all(close(a,b) for a,b in zip(metrics['body_bounds_mm'],bounds)),'LTV817 maximum body envelope differs')
        body_geometry=reviewed_body_rectangle(graphics,bounds)
        marks=[g for g in graphics if g['layer']==LAYER['F.SilkS'] and g['kind']=='circle']
        require(len(marks)==1,'LTV817 pin 1 marker is missing or ambiguous')
        mark=marks[0];x,y=mark['center'];radius=mark['radius']+mark['width']/2
        require(-2.55-TOL<=x-radius and x+radius<=-1.275+TOL and -3.5-TOL<=y-radius and y+radius<=-1.75+TOL,
            'LTV817 marker is outside the pin 1 / anode corner')
        return dict(profile='C62715',manufacturer_pin_count=4,pin_mapping={'1':'Anode','2':'Cathode','3':'Emitter','4':'Collector'},
            pitch_nominal_mm=2.54,formed_installation_row_mm=7.62,body_nominal_mm=[4.6,6.5],body_maximum_mm=[5.1,7.0],
            assembly_body_geometry=body_geometry,pin1_marker_center_mm=mark['center'],pin1_zone_library_policy_mm=[-2.55,-3.5,-1.275,-1.75],
            nominal_drill_mm=1.0,**{geometry_origin+'_minimum_drill_mm':min(p['drill'] for p in pads)},
            pad_size_policy_mm=[1.524,2.0],mask_margin_policy_mm=.05,lead_width_max_mm=.6,lead_thickness_nominal_mm=.26,
            lead_thickness_tolerance_mm=None,width_max_thickness_nominal_diagonal_mm=.65391131,
            fully_toleranced_lead_diagonal_mm=None,manufacturer_recommended_pcb_hole_mm=None,
            formed_lead_fit_verified=False,manufactured_fit_verified=False,
            note='January 2010 standard DIP4 outline and anode corner. Hole, pad, mask and silk zone are library choices. Lead thickness tolerance is unspecified; free lead span is 7.62 to 9.98mm. No full worst-case fit or physical insertion claim.')
    require(len(pads)==2 and all(p['type']=='thru_hole' for p in pads),'DO41 requires two through holes')
    require(math.dist(pd['1']['at'],[-4.35,0])<TOL and math.dist(pd['2']['at'],[4.35,0])<TOL,'DO41 center/pin mapping mismatch')
    require(all(close(p['drill'],1.0) and math.dist(p['size'],[1.8,1.8])<TOL for p in pads),'DO41 hole/copper mismatch')
    require(all(p['shape']=='circle' for p in pads),'DO41 reviewed pads must be circular')
    require(all(set(p['layers'])=={'*.Cu','*.Mask'} for p in pads),'DO41 reviewed pad layers differ')
    require(all(close(p['mask'],.05) for p in pads),'DO41 reviewed solder mask margin differs from 0.05mm policy')
    require(all(close(a,b) for a,b in zip(metrics['body_bounds_mm'],[-2.605,-1.35,2.605,1.35])),'DO41 body outline differs from 5.21x2.70mm envelope of controlling inch maximum')
    body_geometry=reviewed_body_rectangle(graphics,[-2.605,-1.35,2.605,1.35])
    # onsemi identifies cathode by the band. Detect an internal full-height band
    # independently of the converter's chosen -1.90mm drawing coordinate.
    x1,y1,x2,y2=metrics['body_bounds_mm'];bands=[]
    for g in graphics:
        if g['layer']!=LAYER['F.SilkS'] or g['kind']!='line':continue
        a,b=g['start'],g['end'];x=(a[0]+b[0])/2
        if close(a[0],b[0]) and x1+TOL<x<x2-TOL and min(a[1],b[1])<=y1+TOL and max(a[1],b[1])>=y2-TOL:bands.append(x)
    require(len(bands)==1,'Cathode band is missing or ambiguous')
    require(x1-TOL<=bands[0]<=x1+(x2-x1)/4+TOL and pd['1']['at'][0]<x1,'Cathode band is not at the pin 1 / K end')
    allowance=min(p['drill'] for p in pads)-.8636
    require(allowance>=.10-TOL,'Insufficient nominal hole/lead allowance')
    return dict(profile=pr['profile'],pitch_mm=8.7,nominal_drill_mm=1.0,max_lead_diameter_mm=.8636,nominal_diametral_allowance_mm=.1364,
                **{geometry_origin+'_minimum_drill_mm':min(p['drill'] for p in pads),geometry_origin+'_minimum_diametral_allowance_mm':round(allowance,8)},pin_mapping={'1':'K / cathode band','2':'A'},
                assembly_body_geometry=body_geometry,
                body_maximum_mm=[5.207,2.6924],assembly_envelope_mm=[5.21,2.70],controlling_unit='inch',
                lead_diameter_uncontrolled_within_F_mm=1.27,manufactured_fit_verified=False,
                cathode_band_x_mm=bands[0],mask_margin_policy_mm=.05,
                note='Maximums converted from controlling inches; assembly envelope rounded outward to 0.01mm. 8.70mm pitch, 1.00mm nominal hole, 1.80mm copper and mask/band are library choices. Actual formed-lead position, uncontrolled diameter near body and finished-hole tolerances are not fit-verified.')

def preview(pads,graphics,title,path,texts=()):
    # Engineering vector preview; geometry comes from independently checked native readback.
    silktexts=[t for t in texts if t['layer']=='REF DES/SILKSCREEN_TOP']
    geos=[pad_polygon(p) for p in pads]+[geometry_path(g) for g in graphics]+[box(*t['bounds']) for t in silktexts]
    x1,y1,x2,y2=unary_union(geos).bounds;margin=max(1,(x2-x1)*.08)
    width=x2-x1+2*margin;height=y2-y1+2*margin
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x1-margin} {-y2-margin} {width} {height}" width="900" height="700">',
           '<rect x="-1000" y="-1000" width="2000" height="2000" fill="#111827"/>',f'<title>{html.escape(title)}</title>','<g transform="scale(1,-1)">']
    for g in graphics:
        color={'PACKAGE GEOMETRY/SILKSCREEN_TOP':'#f8fafc','PACKAGE GEOMETRY/ASSEMBLY_TOP':'#22d3ee','PACKAGE GEOMETRY/PLACE_BOUND_TOP':'#a855f7'}[g['layer']]
        if g['kind']=='line':parts.append(f'<path d="M {g["start"][0]} {g["start"][1]} L {g["end"][0]} {g["end"][1]}" fill="none" stroke="{color}" stroke-width="{g["width"]}"/>')
        else:parts.append(f'<circle cx="{g["center"][0]}" cy="{g["center"][1]}" r="{g["radius"]}" fill="none" stroke="{color}" stroke-width="{g["width"]}"/>')
    for p in pads:
        polygon=pad_polygon(p);pts=' '.join(f'{x},{y}' for x,y in polygon.exterior.coords)
        parts.append(f'<polygon points="{pts}" fill="#ef4444"/>')
        if p['drill']:parts.append(f'<circle cx="{p["at"][0]}" cy="{p["at"][1]}" r="{p["drill"]/2}" fill="#111827" stroke="#fbbf24" stroke-width=".04"/>')
    parts.append('<path d="M -.25 0 L .25 0 M 0 -.25 L 0 .25" stroke="#34d399" stroke-width=".04"/></g>')
    for p in pads:
        x,y=p['at'];parts.append(f'<text x="{x}" y="{-y}" text-anchor="middle" dominant-baseline="central" font-size=".23" fill="white" font-family="Arial">{html.escape(p["number"])}</text>')
    for t in silktexts:
        x,y=t['at'];parts.append(f'<text x="{x}" y="{-y}" text-anchor="middle" font-size="{t["font_height"]}" fill="white" font-family="Arial">{html.escape(t["text"])}</text>')
    parts.append('</svg>');path.write_text('\n'.join(parts),encoding='utf-8')

def checked_output(path):
    """Check spelling before resolution so directory aliases cannot redirect writes."""
    root=(ROOT/'outputs').absolute();path=Path(path).absolute()
    require('..' not in path.parts and path!=root and path.is_relative_to(root),'Output path escapes the output root')
    require(not ntpath.isreserved(str(path)),'Output path contains a Windows reserved component')
    for node in [*reversed(path.parents),path]:
        require(not node.is_symlink() and not node.is_junction(),'Output link/junction refused: '+str(node))
        if node.is_file():require(node.stat().st_nlink==1,'Output hardlink refused: '+str(node))
    require(path.resolve().is_relative_to(root.resolve()),'Resolved output path escapes the output root')
    return path


def output_write(path,payload):
    """Atomically replace one ordinary owned file; never write through a link."""
    path=checked_output(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.lc2a-write-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as file:file.write(payload)
        checked_output(path);os.replace(tmp,path)
    finally:
        if Path(tmp).is_file():Path(tmp).unlink()


def output_json(path,value):output_write(path,json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'))


def output_directory_identity(out):
    info=out.stat(follow_symlinks=False)
    return dict(device=info.st_dev,inode=info.st_ino)


def same_job(value,job):
    return isinstance(value,str) and Path(value).is_absolute() and Path(value)==job


def owned_output(job,out,previous=None):
    job=Path(job).resolve();out=checked_output(out)
    require(out.is_dir(),'Output directory is missing')
    if previous is not None:require(same_job(previous.get('job'),job),'Output report belongs to another job')
    owner_path=checked_output(out/'任务路径.txt')
    require(owner_path.is_file() and same_job(owner_path.read_text(encoding='utf-8-sig').strip(),job),'Output belongs to another job or has no owner')
    manifest=checked_output(out/'.lc2a-output.json');modern=manifest.is_file()
    if modern:
        record=load(manifest)
        require(record.get('schema')=='lc2a_output_owner_v1' and same_job(record.get('job'),job),'Output owner manifest belongs to another job')
        require(record.get('directory_identity')==output_directory_identity(out) and same_job(record.get('output_directory'),out),
                'Output directory identity differs from its ownership record')
    doc=checked_output(out/'docs/verification.json')
    require(modern or doc.is_file(),'Legacy output has no matching verification report')
    if doc.is_file():
        report=load(doc)
        require(same_job(report.get('job'),job),'Output verification report belongs to another job')
        if report.get('output_directory'):require(same_job(report['output_directory'],out),'Output verification directory differs')
    return out


def check_output_tree(out):
    for child in out.iterdir():
        checked_output(child)
        if child.is_dir():check_output_tree(child)


def claim_output(job,out):
    job=Path(job).resolve();out=checked_output(out)
    try:out.mkdir(parents=True,exist_ok=False)
    except FileExistsError:
        owned_output(job,out);check_output_tree(out)
    else:
        output_json(out/'.lc2a-output.json',dict(schema='lc2a_output_owner_v1',job=str(job),output_directory=str(out),directory_identity=output_directory_identity(out)))
        output_write(out/'任务路径.txt',(str(job)+'\n').encode('utf-8'))
    return out


def invalidate_output(job,result,previous):
    # A clone retains the previous job's report. It must not mark that job's
    # output failed, even when the clone itself passed identical geometry.
    candidate=result.get('output_directory') or previous.get('output_directory')
    if not candidate:return
    modified=[]
    try:
        owner=result if result.get('output_directory') else previous
        out=owned_output(job,candidate,owner)
        marker=checked_output(out/'FAILED_请勿使用.txt');checked_output(out/'docs/verification.json')
        result['output_directory']=str(out)
        result['output_invalidation']={'status':'marked_failed','output_directory':str(out)}
        output_write(marker,'重新测试失败，此输出不再作为通过结果。详情见docs/verification.json。'.encode('utf-8'))
        modified.append(str(marker))
        output_json(out/'docs/verification.json',result)
    except Exception as exc:
        result.pop('output_directory',None)
        result['output_invalidation']={'status':'failed_to_mark' if modified else 'not_modified','candidate':str(candidate),'reason':str(exc),'modified_files':modified}


def export_passed(job,s,report):
    label=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',s['label']).strip(' .')[:70] or s['name']
    dirname=label+'_'+job.name.rsplit('_',1)[-1]
    # A suffix after a dot does not disarm device stems such as NUL.txt.
    # This changes only the filesystem label, never the native name or text.
    if ntpath.isreserved(dirname):dirname='FP_'+dirname
    out=claim_output(job,ROOT/'outputs'/dirname)
    report['output_directory']=str(out)
    for f in (job/'native').iterdir():
        if f.suffix in ('.dra','.psm'):output_write(out/f.name,f.read_bytes())
        elif f.suffix=='.pad':
            output_write(out/'pad'/f.name,f.read_bytes())
    doc=checked_output(out/'docs');doc.mkdir(exist_ok=True)
    for f in ('status.json','model.json'):output_write(doc/f,(job/f).read_bytes())
    output_write(doc/'封装预览.svg',(job/'verification/preview.svg').read_bytes())
    output_write(doc/'转换输入.kicad_mod',safe_rel(job,s['source_file']).read_bytes())
    if s.get('review_profile'):
        for f in (job/'reviewed').rglob('*'):
            if f.is_file():output_write(doc/'器件资料'/f.relative_to(job/'reviewed'),f.read_bytes())
    output_write(out/'说明.txt',('转换和独立验收测试均已通过。原生文件使用ASCII名称；本目录、资料和说明支持中文。\n'
        '预览：红色铜盘、黄色孔、白色丝印、青色装配、紫色放置边界，绿色十字为(0,0)。\n'
        '测试包括实际PSM/PAD加载、放置、旋转、移动与坐标/铜盘/钻孔/图形读回。\n'
        '制造限制见verification.json与器件修订说明；不等于板厂工艺确认或电路功能验证。\n').encode('utf-8'))
    return out

def verify_job(job, export=True):
    job=job.resolve();result={'status':'failed','verifier_version':'0.2.0','job':str(job),'created_unix':time.time(),'checks':{}}
    previous={}
    try:
        if (job/'verification.json').is_file():previous=load(job/'verification.json')
        require(isinstance(previous,dict),'Previous verification report is not an object')
    except Exception as exc:
        previous={};result['previous_report_error']=str(exc)
    try:
        s,model=check_build(job);result['status_sha256']=sha(job/'status.json')
        result['checks']['native_build_journals']=s.get('native_journal_revision')==1
        result['build_journal_origin']='recorded_and_checked' if s.get('native_journal_revision')==1 else 'legacy_unrecorded'
        if export:require(result['checks']['native_build_journals'],'Native build journals are absent; reconvert before exporting')
        result['manufacturer_evidence']=check_manufacturer_evidence(job,s)
        if s.get('review_profile'):result['checks']['manufacturer_document_binding']=True
        result['source_identity']=check_fetch_evidence(job,s)
        if 'lcsc' in s:result['checks']['download_request_source_identity']=True
        if 'lcsc' in s and s.get('review_profile'):result['checks']['manufacturer_catalog_binding']=True
        from verify_upstream import check as check_raw_source
        result['raw_source_geometry']=check_raw_source(job,s)
        if 'lcsc' in s:result['checks']['raw_easyeda_pad_geometry']=True
        if 'lcsc' in s:result['checks']['raw_easyeda_graphic_inventory']=True
        if 'lcsc' in s and not s.get('review_profile'):result['checks']['raw_easyeda_graphic_geometry']=True
        from verify_graphics_source import profile_source
        result['profile_original_graphics']=profile_source(job,s)
        if s.get('review_profile'):result['checks']['profile_source_graphics_safe']=True
        expected,graphics=read_expected(job,s)
        result['source_pin_identity']=check_pin_identity(expected,model)
        result['pad_quantization']=check_pad_quantization(job,s,expected)
        result['checks']['immutable_conversion_manifest']=True
        # Preliminary tests also reject poor upstream layer mappings before Cadence is launched.
        metrics=design_rules(expected,graphics);result['metrics']=metrics
        result['manufacturer_profile']=profile_checks(s,expected,metrics,graphics)
        result['checks']['source_geometry_rules']=True
        def preflight_dra(path):
            drawing=read_native(path)
            check_default_view(path.parent/'default_view.tsv')
            compare_pins(expected,drawing['pins'],model)
            compare_graphics(graphics,drawing['graphics'])
            compare_placement_boundary(graphics,drawing['bounds'])
            for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
                texts=[t for t in drawing['texts'] if t['layer']==layer]
                require(len(texts)==1 and texts[0]['text']==s['refdes']+'*','Missing/misplaced true RefDes label on '+layer)
        def preflight_pads(stage):
            check_pads(expected,model,stage/'drawing_pads.txt')
            result['raw_pad_summary']=check_raw_pads(expected,model,stage/'raw_pads')
        native_tests(job,s,preflight_dra,preflight_pads);native=job/'verification/native'
        result['native_journal_sessions']=native_journal.audit(native,('read.scr','seed_cleanup.scr','placement.scr','saved.scr'))
        result['checks']['native_journal_sessions']=True
        result['native_library_paths']=check_library_paths(native/'library_paths.tsv')
        result['checks']['native_library_search_paths']=True
        result['test_board_cleanup']=check_clean_seed(native)
        result['checks']['fresh_test_board_without_cached_symbols']=True
        result['checks']['fresh_test_board_without_cached_padstacks']=True
        result['checks']['raw_pad_all_layers_thermal_antipad']=True
        dra=read_native(native/'drawing.tsv')
        require('millimeters' in dra['units'],'Native DRA units are not millimeters')
        compare_pins(expected,dra['pins'],model);compare_graphics(graphics,dra['graphics'])
        drawing_layers=native_pad_layers(expected,dra['pins'],model,pad_definitions(native/'drawing_pads.txt',model))
        raw_layers=native_pad_layers(expected,dra['pins'],model,result['raw_pad_summary'])
        result['native_clearance']={'drawing':native_layer_rules(drawing_layers,dra['graphics']),
                                    'raw_pad_at_dra_coordinates':native_layer_rules(raw_layers,dra['graphics'])}
        result['native_enclosure']={'drawing':native_enclosure(drawing_layers,dra),
                                    'raw_pad_at_dra_coordinates':native_enclosure(raw_layers,dra)}
        check_pads(expected,model,native/'disk_pads.txt')
        disk_definitions=pad_definitions(native/'disk_pads.txt',model)
        for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
            texts=[t for t in dra['texts'] if t['layer']==layer]
            require(len(texts)==1 and texts[0]['text']==s['refdes']+'*','Missing/misplaced true RefDes label on '+layer)
        result['checks']['dra_all_pins_graphics_layers_refdes']=True
        result['default_view']=check_default_view(native/'default_view.tsv')
        result['checks']['default_view_background_contrast']=True
        if s.get('presentation_revision',0)>=1:
            result['presentation']=check_presentation(native/'default_view.tsv',dra['texts'],drawing_layers['TOP'],dra['graphics'],s['refdes'])
            result['checks']['default_view_refdes_font_clearance']=True
        result['placed_refdes']={};result['placement_boundary']={'drawing':compare_placement_boundary(graphics,dra['bounds'])}
        for f,anchor,ang in NATIVE_STATES:
            placed=read_native(native/f)
            require(placed['anchor'] is not None and math.dist(placed['anchor'][:2],anchor)<TOL and angleclose(placed['anchor'][2],ang),'Placement anchor/rotation differs')
            compare_pins(expected,placed['pins'],model,anchor,ang)
            compare_graphics(graphics,placed['graphics'],anchor,ang)
            placed_layers=native_pad_layers(expected,placed['pins'],model,disk_definitions)
            result['native_clearance'][f]=native_layer_rules(placed_layers,placed['graphics'])
            result['native_enclosure'][f]=native_enclosure(placed_layers,placed)
            result['placed_refdes'][f]=compare_refdes(dra['texts'],placed['texts'],expected,graphics,anchor,ang,actual_pads=placed_layers['TOP'])
            result['placement_boundary'][f]=compare_placement_boundary(graphics,placed['bounds'],anchor,ang)
        result['actual_board_layers']={};result['bottom_placement']={}
        for f,anchor,ang,mirror in BOARD_STATES:
            placed=read_native(native/f)
            result['actual_board_layers'][f]=board_sides.check_layers(expected,placed['pins'],native/(f+'.layers'),anchor,ang,mirror)
            if mirror:
                normalized=board_sides.bottom_to_local(placed,anchor,ang)
                compare_pins(expected,normalized['pins'],model)
                compare_graphics(graphics,normalized['graphics'])
                layers=native_pad_layers(expected,normalized['pins'],model,disk_definitions)
                result['bottom_placement'][f]=dict(
                    comparison_frame='Inverse of bottom board rotation/mirror/translation; source local coordinates',
                    clearance=native_layer_rules(layers,normalized['graphics']),enclosure=native_enclosure(layers,normalized),
                    refdes=compare_refdes(dra['texts'],normalized['texts'],expected,graphics,actual_pads=layers['TOP']),
                    boundary=compare_placement_boundary(graphics,normalized['bounds']))
        result['checks']['psm_top_bottom_actual_layers']=True
        result['checks']['psm_loaded_placed_rotated90_moved']=True
        result['checks']['compiled_refdes_orientation']=True
        result['checks']['psm_all_quarter_turns_saved']=True
        result['checks']['source_pin_lexical_identity']=True
        result['checks']['compiled_placement_boundary_geometry']=True
        result['checks']['disk_pad_copper_internal_masks_paste_drill_plating_offsets']=True
        result['checks']['analytic_curved_clearance']=True
        result['checks']['native_actual_clearance']=True
        result['checks']['native_placement_enclosure']=True
        result['checks']['assembly_all_geometry_enclosure']=True
        result['checks']['native_pad_quantization']=True
        # Apply geometric rules to the checked native linework as a final consistency check.
        native_metrics=design_rules(drawing_layers['TOP'],dra['graphics'])
        result['manufacturer_profile_native']=profile_checks(s,drawing_layers['TOP'],native_metrics,dra['graphics'],geometry_origin='native')
        if s.get('review_profile'):
            result['checks']['manufacturer_profile_body_polarity']=True
            result['checks']['manufacturer_profile_body_geometry']=True
        preview(drawing_layers['TOP'],dra['graphics'],s['label'],job/'verification/preview.svg',dra['texts'])
        result['checks']['body_origin_courtyard_silk_clearance']=True
        result['checks']['native_sessions_without_errors_or_crash_files']=True
        result['pin_count']=len(expected);result['padstack_count']=len({p['padstack'] for p in model['pads']})
        result['limitations']=['未进行制板、实物试插/焊接和板厂孔公差确认。','未生成Capture符号；使用时仍须让Capture引脚编号与封装一致。','规则采用本工具明确的几何阈值，不是IPC全套认证；不验证电气性能/高压安规。','位号按原生包围盒检查本封装的避让，不替代整板放置后文字与其他器件的DRC；SVG预览字体是近似示意。']
        result['limitations'].append('独立非逻辑测试板关闭交互式DRC，以避免16.6批量API移动时的DRC回调崩溃；本测试不声称整板原生DRC通过。')
        result['limitations'].append('放入PCB后字体可能按板文件文字设置匹配；报告记录实际变化并检查实际尺寸/避让，不保证任意目标板保持DRA原字号。')
        if not result['checks']['native_build_journals']:result['limitations'].append('旧构建没有逐会话journal，仅允许隔离几何复查；导出或入库须重新转换，不能补通过标志。')
        result['status']='passed'
        program=job/'verification/program';program.mkdir(exist_ok=True)
        result['native_runtime_revision']=1
        result['source_pin_identity_revision']=1
        for script in ('lc2a.py','verify.py','board_sides.py','pad_summary.py','native_runtime.py','native_journal.py','kicad_source.py','verify_pin_ids.py','reviewed_profiles.py','easyeda_raw.py','verify_upstream.py','source_graphics.py','verify_graphics_source.py'):
            shutil.copy2(ROOT/'src'/script,program/script)
        result['evidence_sha256']={str(p.relative_to(job)):sha(p) for p in (job/'verification').rglob('*') if p.is_file()}
        if export:
            out=export_passed(job,s,result)
            marker=checked_output(out/'FAILED_请勿使用.txt')
            if marker.is_file():marker.unlink()
            output_json(out/'docs/verification.json',result)
        save(job/'verification.json',result)
    except Exception as exc:
        result['status']='failed';result['error']=str(exc)
        invalidate_output(job,result,previous)
        save(job/'verification.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser(description='Independent Allegro 16.6 acceptance test')
    ap.add_argument('--job',type=Path,required=True)
    sys.exit(verify_job(ap.parse_args().job))
