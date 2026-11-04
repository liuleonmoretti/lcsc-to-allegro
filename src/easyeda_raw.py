"""Audit legacy EasyEDA PAD fields before trusting the pinned KiCad exporter.

The original export is retained. A separate source restores raw precision and
explicit mask expansion; unsupported holes/paste expansion stop conversion.
This module is a producer, never the independent verification oracle.
"""
import hashlib, json, math, re
from pathlib import Path
import sexpdata as sx
import kicad_source

def require(ok, message):
    if not ok: raise ValueError('Raw EasyEDA: '+message)

def finite(value):
    number=float(value);require(math.isfinite(number),'non-finite PAD value');return number

def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def one(n,k):
    values=[v for v in n if tag(v)==k]
    require(len(values)==1,'missing or duplicate exported '+k)
    return values[0]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def pad_points(f,center,size,angle):
    if not f[10].strip():return
    values=[finite(x)*.254 for x in f[10].split()]
    require(len(values)%2==0,'malformed PAD point array')
    points=list(zip(values[::2],values[1::2]));x,y=[v*.254 for v in center];w,h=size
    require(f[1] in ('RECT','OVAL'),'unexpected point array on circular pad')
    if f[1]=='RECT':
        if len(points)==5 and math.dist(points[0],points[-1])<=.0002:points.pop()
        base=[(-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)]
    else:
        a,b=max(w-h,0)/2,max(h-w,0)/2;base=[(-a,-b),(a,b)]
    require(len(points)==len(base),'PAD point array count differs')
    cs,sn=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    expected=[(x+u*cs-v*sn,y+u*sn+v*cs) for u,v in base]
    cycles=[expected[i:]+expected[:i] for i in range(len(expected))]
    cycles += [list(reversed(c)) for c in cycles]
    require(any(all(math.dist(a,b)<=.0002 for a,b in zip(points,c)) for c in cycles),'PAD point array conflicts with center/size/rotation')

def raw_pads(data):
    package=data['result']['packageDetail']['dataStr'];head=package['head']
    origin=[finite(head[k]) for k in ('x','y')]
    shapes=package['shape'];require(isinstance(shapes,list),'missing shape list')
    pads={}
    for item in shapes:
        require(isinstance(item,str),'non-string shape')
        f=item.split('~')
        require(f[0] not in ('HOLE','VIA'),'standalone HOLE/VIA is unsupported; cannot drop it')
        if f[0]!='PAD':continue
        require(len(f)==20,'PAD requires the observed 20-field legacy schema')
        number=f[8];require(bool(re.fullmatch(r'[A-Za-z0-9_+.-]+',number)) and number not in pads,'invalid or duplicate pin number')
        require(f[1] in ('RECT','ELLIPSE','OVAL'),'unsupported copper shape '+f[1])
        center=[finite(f[2]),finite(f[3])];size=[finite(f[4])*.254,finite(f[5])*.254]
        radius=finite(f[9]);angle=finite(f[11])%360
        require(min(size)>0 and radius>=0,'invalid pad size/hole radius')
        require(abs(angle/90-round(angle/90))<1e-8,'nonorthogonal PAD rotation')
        pad_points(f,center,size,angle)
        require(f[15] in ('Y','N'),'unknown plating flag')
        require(f[16] in ('0','1'),'unknown locked flag')
        require(finite(f[13])==0 and not f[14].strip(),'slotted hole is unsupported')
        paste=finite(f[17])*.254;mask=finite(f[18])*.254
        require(paste==0,'nonzero paste expansion is unsupported')
        require(min(size)+2*mask>0,'invalid solder mask opening')
        if radius:
            require(f[6]=='11' and f[15]=='Y','only multilayer plated round holes are supported')
            hc=f[19].split(',');require(len(hc)==2,'missing/malformed hole center')
            offset=[(finite(hc[i])-center[i])*.254 for i in (0,1)]
            # Source x/y are often rounded to 0.001 legacy units. Limit the
            # tolerated disagreement to 0.0002 mm, below native readback TOL.
            require(max(map(abs,offset))<=.0002,'offset hole is unsupported')
            drill=2*radius*.254;require(drill<min(size),'drill exceeds copper')
            kind='thru_hole';layers=['*.Cu','*.Mask']
        else:
            require(f[6]=='1','only top SMD or multilayer PTH pads supported')
            offset=[0.,0.];drill=0.;kind='smd';layers=['F.Cu','F.Paste','F.Mask']
        shape={'RECT':'rect','ELLIPSE':'circle','OVAL':'oval'}[f[1]]
        require(shape!='circle' or abs(size[0]-size[1])<1e-8,'elliptical copper cannot become circular')
        pads[number]=dict(number=number,type=kind,shape=shape,at=[(center[i]-origin[i])*.254 for i in (0,1)],
            angle=angle,size=size,layers=layers,drill=drill,mask=mask,paste=paste,hole_center_rounding_mm=offset)
    require(pads,'no PAD records')
    return pads

def normalize(job,source,evidence,profile=None):
    from source_graphics import inventory,normalize_geometry
    raw=job/evidence['response_file'];data=json.loads(raw.read_text(encoding='utf-8-sig'));pads=raw_pads(data)
    graphics=inventory(data,profile)
    tree=kicad_source.load(source.read_text(encoding='utf-8-sig'));exported=[n for n in tree if tag(n)=='pad']
    require(len(exported)==len(pads) and {str(n[1]) for n in exported}==pads.keys(),'exported pin set differs')
    position_error=0.;size_error=0.
    for n in exported:
        p=pads[str(n[1])]
        require(str(n[2])==p['type'] and str(n[3])==p['shape'],'exported pad type/shape differs')
        at=one(n,'at');size=one(n,'size');layers=one(n,'layers')
        require(len(at)==4 and len(size)==3,'malformed exported pad geometry')
        pos_delta=max(abs(finite(at[i+1])-p['at'][i]) for i in (0,1))
        dim_delta=max(abs(finite(size[i+1])-p['size'][i]) for i in (0,1))
        require(pos_delta<=.00500001 and dim_delta<=.00050001,'exported coordinates/size exceed documented decimal rounding')
        require(abs((finite(at[3])-p['angle']+180)%360-180)<1e-8,'exported angle differs')
        require(len(layers[1:])==len(p['layers']) and set(map(str,layers[1:]))==set(p['layers']),'exported copper/mask/paste layers differ')
        d=[v for v in n if tag(v)=='drill']
        require((not d) if not p['drill'] else (len(d)==1 and len(d[0])==2 and abs(finite(d[0][1])-p['drill'])<1e-8),'exported drill differs')
        margins=[v for v in n if tag(v)=='solder_mask_margin']
        require(len(margins)<=1 and (not margins or len(margins[0])==2 and abs(finite(margins[0][1])-p['mask'])<1e-8),'exported mask expansion contradicts raw source')
        require(not any(tag(v) in ('solder_paste_margin','solder_paste_margin_ratio') for v in n),'unexpected exported paste override')
        position_error=max(position_error,pos_delta);size_error=max(size_error,dim_delta)
        at[1:]=[*p['at'],p['angle']];size[1:]=p['size']
        if d:d[0][1]=p['drill']
        n[:]=[v for v in n if tag(v)!='solder_mask_margin']
        n.append([sx.Symbol('solder_mask_margin'),p['mask']])
    graphic_geometry=normalize_geometry(data,tree,profile)
    out=job/'source_audited.kicad_mod';out.write_text(sx.dumps(tree)+'\n',encoding='utf-8')
    record=dict(schema='easyeda_pad_v3',normalized_file=out.name,normalized_sha256=sha(out),pad_count=len(pads),graphic_inventory=graphics,graphic_geometry=graphic_geometry,
        original_export_sha256=sha(source),raw_response_sha256=sha(raw),max_export_position_rounding_mm=position_error,
        max_export_size_rounding_mm=size_error,hole_center_tolerance_mm=.0002,
        action='Preserve original; restore PAD/ordinary-outline precision and explicit mask margins in a separate source.',
        limitations='Supported ordinary outlines checked; profile replacements and recorded omitted graphics do not claim raw geometry equivalence.')
    (job/'raw_pad_audit.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return out,record
