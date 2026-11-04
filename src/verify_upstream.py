"""Independent raw API PAD -> export -> audited source -> reviewed source check.

Does not import easyeda_raw or easyeda2kicad. Recomputes the expected fields from
the retained API response, rather than trusting the producer's normalized model.
"""
import hashlib,json,math,re
from pathlib import Path
import sexpdata
import verify_pin_ids

def need(ok,msg):
    if not ok:raise ValueError('Reference raw PAD: '+msg)
def number(value):
    n=float(value);need(math.isfinite(n),'non-finite value');return n
def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def path(job,rel):
    p=(job/rel).resolve();need(p.is_relative_to(job.resolve()),'path escapes job');return p
def field(node,name,optional=False):
    values=[v for v in node if tag(v)==name]
    need(len(values)<=1 and (optional or len(values)==1),'missing/duplicate '+name)
    return values[0] if values else None

def check_points(form,text,x,y,w,h,angle):
    if not text.strip():return
    coords=list(map(number,text.split()));need(len(coords)%2==0,'malformed pad point list')
    points=[((coords[i]-x)*.254,(coords[i+1]-y)*.254) for i in range(0,len(coords),2)]
    # Inverse rotate the point cache, independently of the producer's forward
    # expected-cycle comparison. Orthogonal angles avoid curved approximation.
    a=math.radians(angle);points=[(u*math.cos(a)+v*math.sin(a),-u*math.sin(a)+v*math.cos(a)) for u,v in points]
    w*=.254;h*=.254
    if form=='RECT':
        if len(points)==5 and math.dist(points[0],points[-1])<=.0002:points=points[:-1]
        need(len(points)==4,'rectangle point count')
        corners=set()
        for u,v in points:
            need(math.hypot(abs(u)-w/2,abs(v)-h/2)<=.0002,'pad point conflicts with rectangle dimensions/rotation')
            corners.add((u>0,v>0))
        need(len(corners)==4,'duplicate rectangle corner point')
        for (u,v),(a,b) in zip(points,points[1:]+points[:1]):
            need(((u>0)!=(a>0))+((v>0)!=(b>0))==1,'crossing rectangle point order')
    elif form=='OVAL':
        need(len(points)==2,'oval endpoint count')
        i=0 if w>=h else 1;j=1-i;half=abs(w-h)/2
        ordered=sorted(points,key=lambda p:p[i])
        need(all(math.hypot(p[j],p[i]-end)<=.0002 for p,end in zip(ordered,(-half,half))),'pad point conflicts with oval centerline')
    else:raise ValueError('Reference raw PAD: unexpected circular pad point list')

def expectation(data):
    document=data['result']['packageDetail']['dataStr'];head=document['head']
    ox,oy=number(head['x']),number(head['y']);expected={}
    need(isinstance(document.get('shape'),list),'shape list missing')
    for text in document['shape']:
        need(isinstance(text,str),'invalid shape record')
        cells=text.split('~');command=cells[0]
        need(command not in {'HOLE','VIA'},'unsupported separate hole/via')
        if command!='PAD':continue
        need(len(cells)==20,'unknown PAD schema')
        _,form,x,y,w,h,layer,net,pin,hole,points,angle,identifier,slot,slot_points,plated,locked,paste,mask,hole_center=cells
        need(re.fullmatch(r'[A-Za-z0-9_+.-]+',pin) is not None and pin not in expected,'invalid/duplicate pin')
        x,y,w,h,hole,angle,slot,paste,mask=map(number,(x,y,w,h,hole,angle,slot,paste,mask))
        need(form in {'RECT','OVAL','ELLIPSE'} and min(w,h)>0 and hole>=0,'unsupported/invalid copper or hole')
        need(plated in {'Y','N'} and locked in {'0','1'},'invalid plating/locked flags')
        need(slot==0 and slot_points.strip()=='','unsupported slot geometry')
        need(paste==0,'paste expansion would be lost')
        need(min(w,h)+2*mask>0,'invalid mask opening')
        need(abs(angle/90-round(angle/90))<1e-8,'unsupported angle')
        check_points(form,points,x,y,w,h,angle)
        need(form!='ELLIPSE' or abs(w-h)<1e-8,'ellipse cannot become circle')
        if hole>0:
            need(plated=='Y' and layer=='11','nonplated or non-multilayer hole')
            pair=hole_center.split(',');need(len(pair)==2,'hole center missing/malformed')
            dx,dy=(number(pair[0])-x)*.254,(number(pair[1])-y)*.254
            need(max(abs(dx),abs(dy))<=.0002,'offset hole unsupported')
            need(2*hole<min(w,h),'hole exceeds copper')
            kind='thru_hole';layers={'*.Cu','*.Mask'}
        else:
            need(layer=='1','non-top SMD pad');kind='smd';layers={'F.Cu','F.Paste','F.Mask'}
        expected[pin]={'type':kind,'shape':{'RECT':'rect','OVAL':'oval','ELLIPSE':'circle'}[form],
            'xy':((x-ox)*.254,(y-oy)*.254),'angle':angle%360,'size':(w*.254,h*.254),
            'drill':hole*.508,'layers':layers,'mask':mask*.254}
    need(expected,'no raw pads')
    return expected

def compare(tree,expected,mode,profile=None):
    pads=[n for n in tree if tag(n)=='pad']
    need(len(pads)==len(expected) and {str(p[1]) for p in pads}==expected.keys(),mode+' pin count/identity differs')
    max_position=max_dimension=0.
    for node in pads:
        pin=str(node[1]);p=expected[pin];shape=p['shape'];size=p['size'];mask=p['mask']
        if mode=='final' and profile:
            need(profile in {'C8734','C53568','C62715'},'unknown reviewed profile')
            known_mask=.1969*.254 if profile=='C8734' else .2*.254
            need(abs(mask-known_mask)<1e-8,'source mask changed outside reviewed policy')
            mask=.03 if profile=='C8734' else .05
            if profile=='C8734':
                shape='rect';group=(int(pin)-1)//12;size=(.30,1.20) if group in (0,2) else (1.20,.30)
        need(str(node[2])==p['type'] and str(node[3])==shape,mode+' type/shape differs')
        at=field(node,'at');dim=field(node,'size');ls=field(node,'layers');drill=field(node,'drill',True)
        need(len(at)==4 and len(dim)==3,mode+' malformed dimensions')
        dp=max(abs(number(at[i+1])-p['xy'][i]) for i in (0,1))
        ds=max(abs(number(dim[i+1])-size[i]) for i in (0,1))
        need(dp<=(.00500001 if mode=='original' else 1e-8),mode+' raw position differs')
        need(ds<=(.00050001 if mode=='original' else 1e-8),mode+' raw copper size differs')
        need(abs((number(at[3])-p['angle']+180)%360-180)<1e-8,mode+' raw rotation differs')
        need(len(ls)-1==len(p['layers']) and set(map(str,ls[1:]))==p['layers'],mode+' raw layers differ')
        need((drill is None) if p['drill']==0 else (drill is not None and len(drill)==2 and abs(number(drill[1])-p['drill'])<1e-8),mode+' raw drill differs')
        margin=field(node,'solder_mask_margin',mode=='original')
        if margin is not None:need(len(margin)==2 and abs(number(margin[1])-mask)<1e-8,mode+' raw mask expansion differs')
        need(field(node,'solder_paste_margin',True) is None and field(node,'solder_paste_margin_ratio',True) is None,mode+' unexpected paste override')
        max_position=max(max_position,dp);max_dimension=max(max_dimension,ds)
    return dict(max_position_delta_mm=max_position,max_size_delta_mm=max_dimension)

def check(job,status):
    if 'lcsc' not in status:return {'mode':'local_file','note':'No raw EasyEDA audit asserted.'}
    evidence=status['fetch_evidence'];raw=path(job,evidence['response_file']);original=path(job,evidence['footprint_file'])
    raw_data=json.loads(raw.read_text(encoding='utf-8-sig'));expected=expectation(raw_data)
    from verify_graphics_source import inventory,check_geometry
    profile=status.get('review_profile')
    graphics=inventory(raw_data,profile.get('profile') if profile else None)
    audit=status.get('raw_pad_audit');need(isinstance(audit,dict) and audit.get('schema')=='easyeda_pad_v3','audit missing; regenerate online source')
    need(audit.get('graphic_inventory')==graphics,'raw graphic inventory differs')
    need(audit.get('normalized_file')=='source_audited.kicad_mod','unexpected normalized source path')
    normalized=path(job,audit['normalized_file']);report=job/'raw_pad_audit.json'
    manifest={str(Path(k)).replace('\\','/').casefold():v for k,v in status['files'].items()}
    for p in (normalized,report):
        need(p.is_file() and digest(p)==manifest.get(p.relative_to(job).as_posix().casefold()),'unmanifested/changed audited source or report')
    need(json.loads(report.read_text(encoding='utf-8'))==audit,'audit record differs from status')
    need(audit.get('raw_response_sha256')==digest(raw) and audit.get('original_export_sha256')==digest(original) and audit.get('normalized_sha256')==digest(normalized),'audit source hashes differ')
    need(audit.get('pad_count')==len(expected),'recorded raw pin count differs')
    trees=[verify_pin_ids.load(p.read_text(encoding='utf-8-sig')) for p in (original,normalized,path(job,status['source_file']))]
    outline=check_geometry(raw_data,trees,profile)
    recorded=audit.get('graphic_geometry',{})
    for key,val in outline.items():
        need(key in recorded and (abs(val-recorded[key])<1e-9 if isinstance(val,float) else val==recorded[key]),'raw graphic geometry audit mismatch')
    export_error=compare(trees[0],expected,'original');compare(trees[1],expected,'normalized')
    if profile:
        need(profile.get('original_source')==normalized.name and profile.get('original_sha256')==digest(normalized),'review profile does not start at audited source')
    else:need(path(job,status['source_file'])==normalized,'final source bypasses raw audit')
    compare(trees[2],expected,'final',profile.get('profile') if profile else None)
    return dict(mode='easyeda_pad_v3',pad_count=len(expected),original_export_rounding=export_error,graphic_inventory=graphics,graphic_geometry=outline,
        raw_mask_margins_mm=sorted({round(p['mask'],10) for p in expected.values()}),
        normalized_precision_restored=True,raw_plating_and_hole_center_checked=True,pad_point_consistency_checked=True,
        limitations='Supported ordinary outlines checked; reviewed replacements/explicit omissions are not raw equivalence or manufacturer approval.')
