"""Local, fail-closed EasyEDA/KiCad -> Allegro 16.6 footprint pipeline."""
from __future__ import annotations
import argparse, hashlib, importlib.metadata, json, math, ntpath, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import sexpdata
import native_runtime
import native_journal
import kicad_source
from source_graphics import text_layer

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.2.0'
LAYERS = {'F.SilkS':'PACKAGE GEOMETRY/SILKSCREEN_TOP', 'F.Fab':'PACKAGE GEOMETRY/ASSEMBLY_TOP', 'F.CrtYd':'PACKAGE GEOMETRY/PLACE_BOUND_TOP'}

def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def key(x): return str(x[0]) if isinstance(x, list) and x else ''
def child(x, tag, default=None): return next((y for y in x if key(y)==tag), default)
def num(x):
    v=float(x)
    if not math.isfinite(v): raise ValueError('Non-finite dimension')
    return v
def point(node):
    require(isinstance(node,list) and len(node)==3,'Expected exactly two coordinates')
    return [num(node[1]),num(node[2])]
def require(ok, message):
    if not ok: raise ValueError(message)

def validate_native_name(name):
    require(isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,30}',name)),
            'Use an ASCII name of 1-31 characters for the default Allegro 16.6 flow; use --label for Chinese display text')
    require(not ntpath.isreserved(name),'Windows reserved device name cannot be used for a native footprint: '+name)

def validate_refdes_prefix(prefix):
    require(isinstance(prefix,str) and bool(re.fullmatch(r'[A-Za-z]{1,4}',prefix)),'Invalid reference prefix; use 1-4 uppercase ASCII letters')
    require(prefix==prefix.upper(),'Allegro 16.6 uppercases RefDes text; use an uppercase reference prefix explicitly: '+prefix.upper())
def skstr(s): return json.dumps(str(s), ensure_ascii=True)
def pt(p): return f'{p[0]:.6f}:{p[1]:.6f}'

def database_dimension(value):
    rounded=Decimal(str(value)).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
    require(rounded.is_finite() and rounded>0,'Dimension is below the positive 0.0001mm database grid')
    return float(rounded)

def native_pad_spec(p,margin=0):
    values=[Decimal(str(v))+2*Decimal(str(margin)) for v in p['size']]
    w,h=map(database_dimension,values)
    if p['shape']=='rect':shape='SQUARE' if w==h else 'RECTANGLE'
    elif p['shape']=='oval':shape='CIRCLE' if w==h else ('OBLONG_X' if w>h else 'OBLONG_Y')
    else:
        require(p['shape']=='circle' and w==h,'Circular pad has unequal database dimensions');shape='CIRCLE'
    return dict(shape=shape,size=[w,h])

def pad_quantization_record(pads):
    rows=[]
    for p in pads:
        masks=any(layer in p['layers'] for layer in ('*.Mask','F.Mask','B.Mask'))
        rows.append(dict(number=p['number'],source_shape=p['shape'],source_size_mm=p['size'],source_mask_mm=p['mask'],source_drill_mm=p['drill'] or 0,
                         copper=native_pad_spec(p),mask=native_pad_spec(p,p['mask']) if masks else None,
                         paste=native_pad_spec(p) if 'F.Paste' in p['layers'] else None,drill_mm=database_dimension(p['drill']) if p['drill'] else 0))
    return dict(schema='pad_dimensions_4dp_half_up_v1',units='mm',step_mm=.0001,pads=rows)
def fields(node,allowed,start=1):
    seen=set()
    for n in node[start:]:
        if not isinstance(n,list):
            require(str(n)=='locked','Unsupported scalar field: '+str(n));continue
        k=key(n);require(k in allowed,'Unsupported field: '+k)
        require(k not in seen,'Duplicate field: '+k);seen.add(k)
    return seen

def graphic_width(node,kind):
    accepted={'end','layer','stroke','width','fill','locked','tstamp','uuid'}|({'center'} if kind=='fp_circle' else {'start'})
    fields(node,accepted)
    stroke=child(node,'stroke');legacy=child(node,'width')
    require(not (stroke is not None and legacy is not None),'Ambiguous legacy width and stroke definitions')
    if stroke is not None:
        fields(stroke,{'width','type'})
        style=child(stroke,'type')
        require(style is not None and len(style)==2 and str(style[1]) in ('default','solid'),'Unsupported stroke style; dashed/dotted lines cannot become solid')
        width=child(stroke,'width')
    else:width=legacy
    require(width is not None and len(width)==2,'Missing/malformed graphic width')
    w=num(width[1]);require(w>0,'Graphic width must be positive')
    fill=child(node,'fill',[None,'none'])
    require(len(fill)==2 and str(fill[1]) in ('none','no'),'Filled graphics not supported')
    layer=child(node,'layer')
    require(layer is not None and len(layer)==2,'Malformed graphic layer')
    return w

def placement_contour(graphics):
    """Assemble one explicit courtyard without allowing compiler bbox fallback."""
    gs=[g for g in graphics if g['layer']=='F.CrtYd']
    require(gs,'Missing placement courtyard')
    if any(g['kind']=='circle' for g in gs):
        require(len(gs)==1 and gs[0]['kind']=='circle','Placement courtyard must be one circle or one closed line contour')
        return dict(kind='circle',center=gs[0]['center'],radius=gs[0]['radius'])
    require(all(g['kind']=='line' for g in gs),'Unsupported placement contour geometry')
    neighbours={};edges=set()
    for g in gs:
        a,b=tuple(g['start']),tuple(g['end']);edge=tuple(sorted((a,b)))
        require(a!=b and edge not in edges,'Duplicate/zero placement contour edge')
        edges.add(edge);neighbours.setdefault(a,[]).append(b);neighbours.setdefault(b,[]).append(a)
    require(len(neighbours)>=3 and all(len(v)==2 for v in neighbours.values()),'Open or branching placement contour')
    start=min(neighbours);points=[start];previous=None;current=start
    while True:
        next_point=next(v for v in neighbours[current] if v!=previous)
        if next_point==start:break
        require(next_point not in points,'Invalid placement contour traversal')
        points.append(next_point);previous,current=current,next_point
    require(len(points)==len(edges),'Disconnected placement contours are unsupported')
    from shapely.geometry import Polygon
    region=Polygon(points)
    require(region.is_valid and region.area>0,'Self-intersecting or empty placement contour')
    return dict(kind='polygon',points=[list(p) for p in points]+[list(start)])
def run(args, cwd, timeout=120):
    si=None
    if os.name=='nt':
        si=subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow=0
    command=[str(a) for a in args];base=Path(cwd)/(Path(args[0]).stem+'_last_process');child_env=None
    journal_context=native_journal.begin(command,cwd);journal_report=None
    def record(stdout=b'',stderr=b'',code=None,timed_out=False,error=None):
        nonlocal journal_report
        base.with_suffix('.stdout.bin').write_bytes(stdout);base.with_suffix('.stderr.bin').write_bytes(stderr)
        def decode(data):
            try:return data.decode('utf-8')
            except UnicodeDecodeError:return data.decode('gb18030',errors='replace')
        text=decode(stdout)+decode(stderr)
        if error:text+='\nPROCESS ERROR: '+error+'\n'
        base.with_suffix('.txt').write_text(text,encoding='utf-8')
        dump(base.with_suffix('.json'),dict(command=command,timeout_seconds=timeout,timed_out=timed_out,returncode=code,error=error,
             working_directory=str(Path(cwd).resolve()),native_temp_directory=child_env.get('TEMP') if child_env is not None else None))
        journal_report=native_journal.finish(journal_context,stdout,stderr,code,timed_out,error)
        return text
    try:
        child_env=native_runtime.process_environment(command[0],cwd)
        result=subprocess.run(command,cwd=cwd,capture_output=True,timeout=timeout,startupinfo=si,env=child_env)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and waits for only its own child on timeout.
        record(exc.stdout or b'',exc.stderr or b'',timed_out=True,error=str(exc));raise
    except (OSError,ValueError) as exc:record(error=str(exc));raise
    log=record(result.stdout,result.stderr,result.returncode)
    if result.returncode: raise RuntimeError(f'Command failed ({result.returncode}): {args[0]}\n{log}')
    require(not re.search(r'(?im)^\s*E-|\*Error\*|encountered a problem|fatal error',log),'Native command reported an error despite exit code 0')
    require(not list(Path(cwd).glob('*.dmp')) and not list(Path(cwd).glob('*.SAV')),'Native crash/recovery artifact detected')
    native_journal.require_clean(journal_report)
    return log

def catalog_profile_identity(data,profile):
    """Check the catalog assertions before choosing a reviewed device recipe.

    These are explicitly reviewed legacy-API labels, not aliases guessed from
    a package family. A vendor label change requires review, not auto-mapping.
    """
    require(profile in ('C8734','C53568','C62715'),'Manufacturer catalog: unsupported profile')
    mpn,manufacturer,package={
        'C8734':('STM32F103C8T6','ST(意法半导体)','LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL'),
        'C53568':('1N4007RLG','onsemi(安森美)','DO-41_BD2.4-L4.7-P8.70-D0.9-RD'),
        'C62715':('LTV-817-A','LITEON(光宝)','DIP-4_L4.6-W6.5-P2.54-LS7.6-BL')
    }[profile]
    def value(keys,wanted):
        cur=data
        for key in keys:
            require(isinstance(cur,dict) and key in cur,'Manufacturer catalog: missing '+'.'.join(keys));cur=cur[key]
        require(isinstance(cur,str) and cur.strip()==wanted,'Manufacturer catalog: reviewed '+'.'.join(keys)+' differs')
        return cur.strip()
    base=('result','dataStr','head','c_para');pbase=('result','packageDetail','dataStr','head','c_para')
    return dict(schema='reviewed_catalog_v1',profile=profile,
        title=value(('result','title'),mpn),name=value(base+('name',),mpn),mpn=value(base+('Manufacturer Part',),mpn),
        manufacturer=value(base+('Manufacturer',),manufacturer),supplier=value(base+('Supplier',),'LCSC'),
        supplier_part=value(base+('Supplier Part',),profile),package=value(base+('package',),package),
        package_title=value(('result','packageDetail','title'),package),package_parameter=value(pbase+('package',),package))

def downloaded_identity(job,source,requested,profile=None):
    """Bind the requested SKU to cached legacy API data and its KiCad export."""
    require(bool(re.fullmatch(r'C[0-9]+',requested)),'Expected ASCII C-number')
    raw=job/'source/.easyeda_cache'/(requested+'.json')
    require(raw.is_file(),'Download response evidence is missing')
    def unique(pairs):
        obj={}
        for k,v in pairs:
            require(k not in obj,'Download JSON has duplicate key: '+k);obj[k]=v
        return obj
    def constant(value):raise ValueError('Download JSON has non-finite value: '+value)
    data=json.loads(raw.read_text(encoding='utf-8-sig'),object_pairs_hook=unique,parse_constant=constant)
    require(isinstance(data,dict) and data.get('success') is True and type(data.get('code')) is int and data['code']==0,
            'Download response is not successful legacy API data')
    def obj(parent,key):
        value=parent.get(key);require(isinstance(value,dict),'Download response missing object '+key);return value
    result=obj(data,'result');catalog=obj(result,'lcsc');symbol=obj(obj(result,'dataStr'),'head')
    parameters=obj(symbol,'c_para');package=obj(result,'packageDetail');package_head=obj(obj(package,'dataStr'),'head')
    require(catalog.get('number')==requested,'Download response C-number differs from request')
    if result.get('szlcsc') is not None:
        require(obj(result,'szlcsc').get('number')==requested,'Download szlcsc C-number differs from request')
    if parameters.get('Supplier Part') not in (None,''):
        require(parameters['Supplier Part']==requested,'Download symbol C-number differs from request')
    component_uuid=result.get('uuid');package_uuid=package.get('uuid')
    require(isinstance(component_uuid,str) and component_uuid and isinstance(package_uuid,str) and package_uuid,
            'Download component/package identity is missing')
    require(symbol.get('puuid')==package_uuid,'Download component/package UUID link differs')
    for head,identity in ((symbol,component_uuid),(package_head,package_uuid)):
        if head.get('uuid') not in (None,''):require(head['uuid']==identity,'Download embedded UUID differs')
    require(str(symbol.get('docType'))=='2' and str(package_head.get('docType'))=='4','Download document types are not symbol/package')
    tree=sexpdata.loads(source.read_text(encoding='utf-8-sig'))
    require(key(tree) in ('module','footprint'),'Download did not produce a KiCad footprint')
    identities=[str(n[2]) for n in tree if key(n)=='property' and len(n)>=3 and str(n[1])=='LCSC Part']
    require(identities==[requested],'Downloaded KiCad C-number is missing, ambiguous or different')
    reviewed=None
    if profile:
        require(profile==requested,'Manufacturer catalog: profile/request mismatch')
        reviewed=catalog_profile_identity(data,profile)
    evidence=dict(schema='easyeda_legacy_v1',requested_lcsc=requested,response_file=raw.relative_to(job).as_posix(),
                response_sha256=sha(raw),footprint_file=source.relative_to(job).as_posix(),footprint_sha256=sha(source),
                component_uuid=component_uuid,package_uuid=package_uuid)
    if reviewed:evidence['manufacturer_catalog']=reviewed
    return evidence

def parse(path, center=None, body_bounds=None, mask=0.0):
    tree=kicad_source.load(Path(path).read_text(encoding='utf-8-sig'))
    require(key(tree) in ('footprint','module'), 'Expected .kicad_mod footprint/module')
    pads=[]; graphics=[]; omitted=[]
    allowed={'layer','tedit','tstamp','version','generator','generator_version','descr','tags','attr','property','fp_text','model','uuid','path'}
    for n in tree[2:]:
        if not isinstance(n,list):
            require(str(n) in ('locked','placed'),'Unsupported footprint scalar: '+str(n));omitted.append('editing_state');continue
        k=key(n)
        if k=='pad':
            fields(n,{'at','size','layers','drill','solder_mask_margin','tstamp','uuid','net','pinfunction','pintype','locked'},4)
            p={'number':str(n[1]),'type':str(n[2]),'shape':str(n[3])}
            require(p['type'] in ('smd','thru_hole'), f'Unsupported pad type {p["type"]}')
            require(p['shape'] in ('rect','circle','oval'), f'Unsupported pad shape {p["shape"]}; no silent approximation')
            require(bool(re.fullmatch(r'[A-Za-z0-9_+.-]+',p['number'])), 'Unsupported/empty pin number')
            # 16.6 algrostart Table 2-1: physical pin number, not logical pin name.
            require(len(p['number'])<=31,'Allegro 16.6 pin number must be 1-31 ASCII characters; got '+str(len(p['number'])))
            require(p['number']==p['number'].upper(),'Allegro 16.6 uppercases pin numbers; lowercase source identifier cannot be preserved: '+p['number'])
            a=child(n,'at');require(a is not None and len(a) in (3,4),'Missing/malformed pad position')
            p['at']=[num(a[1]),num(a[2])];p['angle']=num(a[3]) if len(a)>3 else 0.0
            require(abs(p['angle']/90-round(p['angle']/90))<1e-8, 'V0.1 supports 90-degree pad rotations only')
            p['size']=point(child(n,'size'))
            require(min(p['size'])>0, 'Pad sizes must be positive')
            if p['shape']=='circle': require(abs(p['size'][0]-p['size'][1])<1e-6,'Circle sizes differ')
            ls=[str(v) for v in child(n,'layers',[])[1:]]
            require(len(set(ls))==len(ls),'Duplicate pad layers')
            require(set(ls)<=({'F.Cu','F.Paste','F.Mask'} if p['type']=='smd' else {'*.Cu','*.Mask','F.Mask','B.Mask'}), 'Unsupported pad layers')
            require('F.Cu' in ls if p['type']=='smd' else '*.Cu' in ls, 'Missing copper layers')
            p['layers']=ls; p['drill']=0.0
            if p['type']=='thru_hole':
                d=child(n,'drill',[])
                require(len(d)==2 and isinstance(d[1],(int,float)), 'Only centered circular plated drills supported')
                p['drill']=num(d[1]); require(0<p['drill']<min(p['size']), 'Invalid drill or non-positive annular ring')
            require(p['type']!='smd' or child(n,'drill') is None,'SMD with drill unsupported')
            masknode=child(n,'solder_mask_margin',[None,mask]);require(len(masknode)==2,'Malformed mask margin')
            p['mask']=num(masknode[1])
            require(min(p['size'])+2*p['mask']>0,'Invalid mask opening')
            pads.append(p)
        elif k in ('fp_line','fp_rect','fp_circle'):
            layer=str(child(n,'layer',[None,''])[1]); require(layer in LAYERS,f'Unsupported graphics layer {layer}')
            width=graphic_width(n,k)
            if k=='fp_circle':
                c=point(child(n,'center')); end=point(child(n,'end')); radius=math.dist(c,end)
                require(radius>0,'Zero circle radius'); graphics.append(dict(kind='circle',center=c,radius=radius,width=width,layer=layer))
            else:
                a=point(child(n,'start')); b=point(child(n,'end'))
                require(math.dist(a,b)>0,'Zero-length graphic')
                if k=='fp_rect':require(a[0]!=b[0] and a[1]!=b[1],'Zero-area rectangle')
                pairs=[(a,b)] if k=='fp_line' else [(a,[b[0],a[1]]),([b[0],a[1]],b),(b,[a[0],b[1]]),([a[0],b[1]],a)]
                graphics.extend(dict(kind='line',start=u,end=v,width=width,layer=layer) for u,v in pairs)
        elif k in allowed:
            if k in ('fp_text','property'):
                annotation_layer=text_layer(n)
                if k=='property' and annotation_layer is not None:omitted.append('property_text')
            if k in ('fp_text','model'): omitted.append(k)
            if k=='layer': require(str(n[1])=='F.Cu','Bottom-side footprint unsupported')
        else: raise ValueError(f'Unsupported footprint element: {k}')
    require(pads,'No pads'); require(len({p['number'] for p in pads})==len(pads),'Duplicate pin numbers unsupported')
    bounds=[]
    for g in graphics:
        if g['layer']!='F.Fab': continue
        if g['kind']=='line': bounds += [g['start'],g['end']]
        else:
            x,y=g['center']; r=g['radius']; bounds += [[x-r,y-r],[x+r,y+r]]
    if body_bounds:
        x1,y1,x2,y2=map(num,body_bounds); require(x1<x2 and y1<y2,'Invalid body bounds')
        center=[(x1+x2)/2,(y1+y2)/2]; basis='explicit body bounds'
    elif center is not None: center=list(map(num,center)); basis='explicit center'
    elif bounds:
        center=[(min(p[i] for p in bounds)+max(p[i] for p in bounds))/2 for i in (0,1)]; basis='F.Fab geometry bounding box; verify main body'
    else: raise ValueError('No F.Fab body outline: supply --center X Y or --body-bounds xmin ymin xmax ymax in source mm')
    def xy(p): return [round(p[0]-center[0],6), round(-(p[1]-center[1]),6)]
    for p in pads:
        p['at']=xy(p['at']); p['angle']=(-p['angle'])%360
        spec={k:p[k] for k in ('type','shape','size','layers','drill','mask')}
        p['padstack']='lc_'+hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:16]
    for g in graphics:
        if g['kind']=='line': g['start']=xy(g['start']);g['end']=xy(g['end'])
        else:g['center']=xy(g['center'])
    placement_contour(graphics)
    return dict(source_name=str(tree[1]),units='mm',pads=pads,graphics=graphics,source_center=center,center_basis=basis,body_bounds=body_bounds,omitted=sorted(set(omitted)),notes=['Annotation-layer value/user/property text and 3D associations not transferred; true RefDes labels regenerated; physical-layer text is rejected','Default mask expansion is explicit CLI policy, not inferred from PCB design settings'])

def skill_build(model,name,refdes):
    code=['; Generated by lc2a, run only on isolated seed copy',
          'axlVisibleDesign(t)','axlSetFindFilter(?enabled list("alltypes") ?onButtons list("alltypes"))','axlAddSelectAll()',
          'axlDeleteObject(axlGetSelSet())','axlClearSelSet()',
          # Deleting pins leaves cached definitions, including same-name pads.
          # Purge only this private seed copy before creating the required pads.
          "axlPurgePadstacks('padstacks nil)",
          'axlDBChangeDesignUnits("millimeters" 4)', 'axlDBChangeDesignExtents(list(-100.0:-100.0 100.0:100.0))',
          'pinBlock=axlDBTextBlockCreate(1 ?width 0.16 ?height 0.25 ?lineSpace 0.35 ?charSpace 0.04 ?photoWidth 0.035)',
          'refBlock=axlDBTextBlockCreate(1 ?width 0.50 ?height 0.80 ?lineSpace 1.0 ?charSpace 0.15 ?photoWidth 0.12)',
          'unless(pinBlock && refBlock error("Text block creation failed"))',
          'to=make_axlTextOrientation(?textBlock sprintf(nil "%d" pinBlock) ?justify "center")',
          'refTo=make_axlTextOrientation(?textBlock sprintf(nil "%d" refBlock) ?justify "center")']
    def check(expr): code.append(f'unless({expr} error("lc2a native operation failed"))')
    for p in {p['padstack']:p for p in model['pads']}.values():
        w,h=p['size']
        layers=['TOP'] if p['type']=='smd' else ['TOP','DEFAULT INTERNAL','BOTTOM']
        entries=[]
        def pad(layer,margin=0):
            native=native_pad_spec(p,margin);width,height=native['size'];shape=native['shape']
            entries.append(f'make_axlPadStackPad(?layer {skstr(layer)} ?type \'REGULAR ?figure \'{shape} ?figureSize {width:.4f}:{height:.4f})')
        for l in layers:pad(l)
        if 'F.Mask' in p['layers'] or '*.Mask' in p['layers']:pad('SOLDERMASK_TOP',p['mask'])
        if 'B.Mask' in p['layers'] or '*.Mask' in p['layers']:pad('SOLDERMASK_BOTTOM',p['mask'])
        if 'F.Paste' in p['layers']:pad('PASTEMASK_TOP')
        drill_size=database_dimension(p['drill']) if p['drill'] else 0
        drill='nil' if not p['drill'] else f'make_axlPadStackDrill(?drillDiameter {drill_size:.4f} ?figure \'CIRCLE ?figureSize {drill_size:.4f}:{drill_size:.4f} ?plating \'PLATED)'
        check(f'axlDBCreatePadStack({skstr(p["padstack"])} {drill} list({" ".join(entries)}) t)')
        check(f'axlPadstackToDisk({skstr(p["padstack"])} {skstr(p["padstack"])})')
    for p in model['pads']:
        check(f'axlDBCreatePin({skstr(p["padstack"])} {pt(p["at"])} make_axlPinText(?number {skstr(p["number"])} ?offset 0:0 ?text to) {p["angle"]:.6f})')
    for g in model['graphics']:
        layer=skstr(LAYERS[g['layer']])
        if g['kind']=='line': check(f'axlDBCreateLine(list({pt(g["start"])} {pt(g["end"])}) {g["width"]:.6f} {layer})')
        else: check(f'axlDBCreateCircle(list({pt(g["center"])} {g["radius"]:.6f}) {g["width"]:.6f} {layer})')
    boundary=placement_contour(model['graphics'])
    if boundary['kind']=='circle':
        code.append(f'boundPath=axlPathStartCircle(list({pt(boundary["center"])} {boundary["radius"]:.6f}) 0.0)')
    else:
        code.append('boundPath=axlPathStart(list('+' '.join(pt(p) for p in boundary['points'])+'))')
    check('boundPath')
    check('axlDBCreateShape(boundPath t "PACKAGE GEOMETRY/PLACE_BOUND_TOP")')
    tops=[p['at'][1]+max(p['size'])/2+max(p['mask'],0) for p in model['pads']]
    for g in model['graphics']:
        top=max(g['start'][1],g['end'][1]) if g['kind']=='line' else g['center'][1]+g['radius']
        tops.append(top+g['width']/2)
    y=max(tops)+1.0
    for layer in ('REF DES/SILKSCREEN_TOP','REF DES/ASSEMBLY_TOP'):
        label_y=y if layer.endswith('SILKSCREEN_TOP') else 0.0
        check(f'axlDBCreateText({skstr(refdes+"*")} 0:{label_y:.6f} refTo {skstr(layer)})')
    code.extend(VIEW_PRESET.splitlines())
    check(f'axlSaveDesign(?design {skstr(name+".dra")} ?noMru t ?noConfirm t)')
    code += ['f=outfile("native_done.txt")','fprintf(f "OK\\n")','close(f)']
    return '\n'.join(code)+'\n'

VIEW_PRESET='''; Explicit default presentation, only changes this generated design.
axlColorSet('background list(0 0 0))
axlColorSet(1 list(245 245 245))
axlColorSet(2 list(0 210 255))
foreach(name list("REF DES/SILKSCREEN_TOP" "PACKAGE GEOMETRY/SILKSCREEN_TOP")
 layer=axlLayerGet(name)
 layer->color=1
 axlLayerSet(layer)
 axlVisibleLayer(name t)
)
foreach(name list("REF DES/ASSEMBLY_TOP" "PACKAGE GEOMETRY/ASSEMBLY_TOP")
 layer=axlLayerGet(name)
 layer->color=2
 axlLayerSet(layer)
 axlVisibleLayer(name nil)
)
axlVisibleUpdate(t)
'''

INSPECT='''axlVisibleDesign(t)
axlSetFindFilter(?enabled list("noall" "pins") ?onButtons list("pins"))
axlAddSelectAll()
f=outfile("pins.tsv")
foreach(p axlGetSelSet() fprintf(f "%s\\t%.6f\\t%.6f\\t%.6f\\n" p->number car(p->xy) cadr(p->xy) p->rotation))
close(f)
'''

def native_build(job,model,name,refdes):
    validate_native_name(name)
    validate_refdes_prefix(refdes)
    for pin in model['pads']:
        number=pin.get('number')
        require(isinstance(number,str) and bool(re.fullmatch(r'[A-Z0-9_+.-]+',number)),
                'Native pin number must preserve uppercase ASCII source text; no numeric/case coercion')
        require(len(number)<=31,'Allegro 16.6 pin number must be 1-31 ASCII characters; got '+str(len(number)))
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    cad=Path(cfg['cadence_bin']); seed=Path(cfg['seed'])
    require(seed.is_file(),'Seed unavailable; run doctor')
    # Old Cadence only receives ASCII paths; final results remain under user root.
    with native_runtime.temporary_stage(prefix='lc2a_',configured=cfg.get('native_temp_root')) as tmp:
        stage=Path(tmp); require(str(stage).isascii(),'Native staging path must be ASCII')
        # Work on fresh writable bytes; copy2 carries Windows READONLY from the
        # source template and can stall the native build/save.
        # The original template's attributes and contents are never changed.
        shutil.copyfile(seed,stage/(name+'.dra'))
        (stage/'build.il').write_text(skill_build(model,name,refdes),encoding='ascii')
        (stage/'run.scr').write_text('skill errset(load("build.il") t)\nskill axlSaveDesign(?noMru t ?noConfirm t)\nexit\n',encoding='ascii')
        try:
            run([cad/'allegro.exe','-nograph','-s','run.scr',name+'.dra'],stage)
            require((stage/'native_done.txt').exists(),'Native build failed; inspect build journal')
            # Commit presentation in an actual graphics session (hidden), not only -nograph.
            (stage/'view.il').write_text(VIEW_PRESET+'\naxlSaveDesign(?noMru t ?noConfirm t)\n',encoding='ascii')
            (stage/'view.scr').write_text('skill errset(load("view.il") t)\nexit\n',encoding='ascii')
            run([cad/'allegro.exe','-s','view.scr',name+'.dra'],stage)
            log=run([cad/'create_sym.exe','-p',name+'.dra',name+'.psm'],stage)
            (stage/'compile_stdout.txt').write_text(log,encoding='utf-8')
            require((stage/(name+'.log')).is_file() and 'Create symbol completed.' in (stage/(name+'.log')).read_text(errors='replace'), 'Symbol compiler did not confirm success')
            (stage/'inspect.il').write_text(INSPECT,encoding='ascii')
            (stage/'inspect.scr').write_text('skill load("inspect.il")\nexit\n',encoding='ascii')
            run([cad/'allegro.exe','-nograph','-s','inspect.scr',name+'.dra'],stage)
            rows=[r.split('\t') for r in (stage/'pins.tsv').read_text().splitlines()]
            require(len(rows)==len(model['pads']),'Readback pin count differs')
            actual={r[0]:list(map(float,r[1:])) for r in rows}
            require(len(actual)==len(rows),'Duplicate readback pin numbers')
            for p in model['pads']:
                require(p['number'] in actual,'Missing pin '+p['number'])
                x,y,a=actual[p['number']]
                require(math.dist((x,y),p['at'])<=.0002 and abs(((a-p['angle']+180)%360)-180)<.01,'Readback position/rotation differs: '+p['number'])
            view=Path(cfg['pad_report_view'])
            run([cad/'extracta.exe','-q',name+'.dra',view,'pads_report.txt'],stage)
            text=(stage/'pads_report.txt').read_text(errors='replace')
            require('S!' in text,'No native pad geometry report')
            native={}
            for row in text.splitlines():
                if row.startswith('S!'):
                    parts=row.split('!'); native[(parts[1].lower(),parts[3])]=parts
            for p in model['pads']:
                w,h=p['size']
                shape=native_pad_spec(p)['shape']
                for layer in (['TOP'] if p['type']=='smd' else ['TOP','BOTTOM','internal_pad_def']):
                    r=native.get((p['padstack'],layer)); require(r is not None,'Missing native copper pad: '+layer)
                    require(abs(float(r[7])-p['size'][0])<.0002 and abs(float(r[8])-p['size'][1])<.0002,'Copper pad size differs: '+layer)
                    require(r[6]==shape and abs(float(r[9]))<.0002 and abs(float(r[10]))<.0002,'Copper shape/offset differs: '+layer)
                # Explicit inner overrides can disappear when a PAD is later
                # loaded into a two-layer test board. Check every native row here.
                for (padname,layer),r in native.items():
                    if padname!=p['padstack'] or layer.startswith('~'):continue
                    require(len(r)>=27,'Malformed native pad layer row')
                    needed=layer=='TOP' or p['type']=='thru_hole'
                    if needed:
                        require(r[6]==shape and abs(float(r[7])-w)<.0002 and abs(float(r[8])-h)<.0002 and abs(float(r[9]))<.0002 and abs(float(r[10]))<.0002,'Copper geometry differs on '+layer)
                    else:require(not r[6] and all(abs(float(r[i] or 0))<.0002 for i in range(7,11)),'Unexpected copper on '+layer)
                    require(not r[11] and not r[12],'Unexpected pad flash/shape reference on '+layer)
                    require(all(not r[i] for i in (13,18,19,20,25,26)) and all(abs(float(r[i] or 0))<.0002 for i in (14,15,16,17,21,22,23,24)),'Unexpected anti/thermal definition on '+layer)
                for layer,needed,margin in [('~TSM','F.Mask' in p['layers'] or '*.Mask' in p['layers'],p['mask']),('~BSM','B.Mask' in p['layers'] or '*.Mask' in p['layers'],p['mask']),('~TPM','F.Paste' in p['layers'],0.0)]:
                    r=native.get((p['padstack'],layer))
                    if needed:
                        require(r is not None and r[6]==native_pad_spec(p,margin)['shape'] and abs(float(r[7])-(w+2*margin))<.0002 and abs(float(r[8])-(h+2*margin))<.0002,'Mask/paste geometry differs')
                    else: require(r is None or (abs(float(r[7]))<.0002 and abs(float(r[8]))<.0002),'Unexpected mask/paste opening')
                if p['drill']:
                    r=native.get((p['padstack'],'~DRILL')); require(r is not None,'Missing drill report')
                    require(abs(float(r[7])-p['drill'])<.0002,'Drill diameter differs')
                    require(r[12]=='P','Hole must be plated')
                    require(abs(float(r[9]))<.0002 and abs(float(r[10]))<.0002,'Drill offset differs')
            native_journal.audit(stage,('run.scr','view.scr','inspect.scr'))
            return {'native_16_6':True,'fresh_session_pin_readback':True,'pad_geometry_report':True,'mask_paste_checked':True,'drill_plating_checked':True,'native_build_journals':True,'manufacturing_review':False}
        finally:
            dest=job/'native'; dest.mkdir(exist_ok=True)
            for f in stage.iterdir():
                if f.is_file(): shutil.copy2(f,dest/f.name)

def convert(args):
    validate_native_name(args.name)
    validate_refdes_prefix(args.refdes)
    job=ROOT/'jobs'/(time.strftime('%Y%m%d_%H%M%S')+'_'+args.name+'_'+os.urandom(3).hex())
    job.mkdir(parents=True)
    status={'program_version':VERSION,'native_journal_revision':1,'presentation_revision':2,'boundary_revision':1,'pad_quantization_revision':1,'pin_identity_revision':1,'name':args.name,'label':getattr(args,'label','') or args.name,'refdes':args.refdes,'status':'started','created_unix':time.time()}
    try:
        if args.lcsc:
            require(bool(re.fullmatch(r'C[0-9]+',args.lcsc)),'Expected ASCII C-number')
            status['lcsc']=args.lcsc
            out=job/'source';out.mkdir()
            log=run([sys.executable,'-X','utf8','-m','easyeda2kicad','--lcsc_id',args.lcsc,'--footprint','--output',out/'library','--use-cache'],out)
            (out/'fetch.log').write_text(log,encoding='utf-8')
            files=list(out.rglob('*.kicad_mod'));require(len(files)==1,'Expected one downloaded footprint')
            src=files[0];status['fetch_evidence']=downloaded_identity(job,src,args.lcsc,getattr(args,'profile',None))
            from easyeda_raw import normalize
            src,status['raw_pad_audit']=normalize(job,src,status['fetch_evidence'],getattr(args,'profile',None))
        else:
            src=job/'source.kicad_mod';shutil.copy2(args.input,src)
        profile=getattr(args,'profile',None)
        if profile:
            from reviewed_profiles import prepare
            src,profile_record=prepare(src,profile,job)
            status['review_profile']=profile_record
            require(args.center is None and args.body_bounds is None,'Reviewed profile supplies its own body center; do not override it')
            args.body_bounds=profile_record['body_bounds_mm']
            args.mask_margin=profile_record['mask_margin_mm']
        status['source_sha256']=sha(src)
        status['source_file']=str(src.relative_to(job))
        status['conversion_options']={'center':args.center,'body_bounds':args.body_bounds,'mask_margin':args.mask_margin}
        model=parse(src,args.center,args.body_bounds,args.mask_margin)
        dump(job/'model.json',model)
        dump(job/'pad_quantization.json',pad_quantization_record(model['pads']))
        (job/'build.il').write_text(skill_build(model,args.name,args.refdes),encoding='ascii')
        status['center_basis']=model['center_basis'];status['source_center_mm']=model['source_center']
        if not args.prepare_only:
            status['checks']=native_build(job,model,args.name,args.refdes)
            status['status']='built_and_geometry_checked'
        else:status['status']='prepared_only'
        status['files']={str(f.relative_to(job)):sha(f) for f in job.rglob('*') if f.is_file()}
    except Exception as exc:
        status['status']='failed';status['error']=str(exc);dump(job/'status.json',status)
        print(json.dumps({'job':str(job),**status},ensure_ascii=False));return 1
    dump(job/'status.json',status)
    print(json.dumps({'job':str(job),**status},ensure_ascii=False,indent=2));return 0

def publish(args):
    from library import publish_job
    receipt=publish_job(args.job,args.library,args.category,args.specific,args.review_note)
    print(json.dumps({'status':'published','receipt':str(receipt)},ensure_ascii=False))
    return 0

def main():
    ap=argparse.ArgumentParser(description='LCSC/KiCad to Allegro 16.6; explicit unsupported-feature errors')
    sub=ap.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    c=sub.add_parser('convert');g=c.add_mutually_exclusive_group(required=True);g.add_argument('--lcsc');g.add_argument('--input',type=Path)
    c.add_argument('--name',required=True);c.add_argument('--refdes',default='U');cg=c.add_mutually_exclusive_group();cg.add_argument('--center',nargs=2,type=float);cg.add_argument('--body-bounds',nargs=4,type=float)
    c.add_argument('--mask-margin',type=float,default=0.0);c.add_argument('--prepare-only',action='store_true')
    c.add_argument('--label',default='',help='Chinese display label; native file basename stays ASCII')
    c.add_argument('--profile',choices=['C8734','C53568','C62715'],help='Explicit, versioned manufacturer-reviewed geometry corrections')
    v=sub.add_parser('verify');v.add_argument('--job',required=True)
    p=sub.add_parser('publish');p.add_argument('--job',required=True);p.add_argument('--category',required=True);p.add_argument('--library',default='E:/Allegro_Lib');p.add_argument('--specific',action='store_true');p.add_argument('--review-note',required=True)
    t=sub.add_parser('library-test',help='Publish verified sample, read it back, remove only this test, compare whole library hashes')
    t.add_argument('--job',required=True);t.add_argument('--category',required=True);t.add_argument('--library',default='E:/Allegro_Lib');t.add_argument('--specific',action='store_true');t.add_argument('--review-note',required=True)
    u=sub.add_parser('cleanup-test');u.add_argument('--receipt',required=True,help='Receipt from an explicit test transaction only')
    u=sub.add_parser('recover-test',help='Resume completed test cleanup after proving a stale lock owner has exited')
    u.add_argument('--receipt',required=True)
    args=ap.parse_args()
    try:
        if args.command=='doctor':
            cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
            checks={k:Path(cfg[k]).exists() for k in ('cadence_bin','seed','pad_report_view','board_seed')}
            with native_runtime.temporary_stage(prefix='lc2a_doctor_',configured=cfg.get('native_temp_root')) as probe:
                checks['ascii_native_temp_writable']=True;native_temp_parent=str(probe.parent)
            checks.update({x: (Path(cfg['cadence_bin'])/x).is_file() for x in ('allegro.exe','create_sym.exe','extracta.exe','pad_designer.exe')})
            print(json.dumps({'version':VERSION,'root':str(ROOT),'output_directory':str(ROOT/'outputs'),'checks':checks,'native_temp_parent':native_temp_parent,'easyeda2kicad':importlib.metadata.version('easyeda2kicad'),'sexpdata':importlib.metadata.version('sexpdata'),'shapely':importlib.metadata.version('shapely'),'encoding':sys.stdout.encoding},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
        if args.command=='verify':
            from verify import verify_job
            return verify_job(Path(args.job))
        if args.command=='library-test':
            from library import trial_job
            print(json.dumps(trial_job(args.job,args.library,args.category,args.specific,args.review_note),ensure_ascii=False,indent=2))
            return 0
        if args.command=='cleanup-test':
            from library import cleanup_test
            result=cleanup_test(args.receipt)
            print(json.dumps({'status':result['state'],'transaction_id':result['transaction_id']},ensure_ascii=False))
            return 0
        if args.command=='recover-test':
            from library import recover_test
            result=recover_test(args.receipt)
            print(json.dumps({'status':result['state'],'transaction_id':result['transaction_id']},ensure_ascii=False))
            return 0
        return convert(args) if args.command=='convert' else publish(args)
    except Exception as exc: print('ERROR: '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    sys.exit(main())
