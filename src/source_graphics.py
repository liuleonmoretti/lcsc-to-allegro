"""Producer-side inventory: unsupported physical graphics cannot disappear upstream."""
import collections,json,math,re
from shapely.geometry import Polygon

def require(ok,msg):
    if not ok:raise ValueError('Raw graphics: '+msg)

def text_layer(node):
    """Only known annotation layers may lose text under the documented policy."""
    def tag(v):return str(v[0]) if isinstance(v,list) and v else ''
    kind=tag(node)
    if kind not in ('fp_text','property'):return None
    parts=node[3:];layers=[v for v in parts if tag(v)=='layer']
    drawing=any(tag(v) in {'at','effects','render_cache'} or not isinstance(v,list) and str(v) in {'hide','unlocked'} for v in parts)
    if kind=='property' and not layers and not drawing:return None  # Plain key/value metadata.
    require(len(layers)==1 and len(layers[0])==2,'text layer missing, malformed or ambiguous')
    layer=str(layers[0][1])
    require(layer in {'F.SilkS','B.SilkS','F.Fab','B.Fab','F.CrtYd','B.CrtYd','Cmts.User','Dwgs.User','Eco1.User','Eco2.User'},
            'cannot discard text on physical or unsupported layer '+layer)
    return layer

def linear_courtyard(text):
    tokens=re.findall(r'[A-Za-z]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',text)
    residue=re.sub(r'[A-Za-z]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[\s,]','',text)
    require(not residue,'invalid courtyard path characters')
    points=[];i=0;closed=False
    while i<len(tokens):
        cmd=tokens[i];i+=1;require(cmd in {'M','L','H','V','Z'},'unsupported courtyard path command '+cmd)
        require(not closed and ((cmd=='M')== (not points)),'multiple/open courtyard subpaths')
        size={'M':2,'L':2,'H':1,'V':1,'Z':0}[cmd];args=tokens[i:i+size];i+=size
        require(len(args)==size,'truncated courtyard path')
        values=list(map(float,args));require(all(math.isfinite(v) for v in values),'non-finite courtyard')
        if cmd=='Z':closed=True;continue
        if cmd in ('M','L'):points.append(values)
        elif cmd=='H':points.append([values[0],points[-1][1]])
        else:points.append([points[-1][0],values[0]])
    require(closed and len(points)>=3,'courtyard path must explicitly close')
    region=Polygon(points);require(region.is_valid and region.area>0,'invalid courtyard region')
    return points

def numeric(value):
    try:n=float(value)
    except (TypeError,ValueError):raise ValueError('Raw graphics: invalid numeric graphic field')
    require(math.isfinite(n),'non-finite graphic field');return n

def primitive(cells):
    """Known legacy outline primitives. Reject exporter defaults/clamping."""
    kind=cells[0];count={'TRACK':7,'RECT':9,'CIRCLE':8}[kind]
    require(len(cells)>=count and all(v=='' for v in cells[count:]),'unknown graphic schema fields')
    if kind=='TRACK':
        require(cells[6] in ('0','1'),'invalid graphic locked flag')
        coords=list(map(numeric,cells[4].split()));require(len(coords)>=4 and len(coords)%2==0,'graphic track point count')
        points=list(zip(coords[::2],coords[1::2]));width=numeric(cells[1])*.254
    elif kind=='RECT':
        require(cells[7] in ('0','1'),'invalid graphic locked flag')
        x,y,w,h=map(numeric,cells[1:5]);require(w>0 and h>0,'non-positive graphic rectangle size')
        points=[(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)];width=numeric(cells[8])*.254
    else:
        require(cells[7] in ('0','1'),'invalid graphic locked flag')
        x,y,r,width=map(numeric,cells[1:5]);require(r>0,'non-positive graphic circle radius')
        width*=.254;points=[(x,y),(x+r,y)]
    require(width>=.01-1e-12,'graphic stroke below supported 0.01mm; exporter would clamp')
    require(all(a!=b for a,b in zip(points,points[1:])),'zero-length graphic segment')
    return points,width

def model_only(text):
    def unique(items):
        require(len(dict(items))==len(items),'duplicate SVG JSON key');return dict(items)
    data=json.loads(text,object_pairs_hook=unique)
    require(isinstance(data,dict) and str(data.get('layerid'))=='19','SVG is not on model layer 19')
    attrs=data.get('attrs');require(isinstance(attrs,dict) and attrs.get('c_etype')=='outline3D' and str(attrs.get('layerid'))=='19','SVG is not an identified 3D outline')
    def walk(value):
        if isinstance(value,dict):
            if 'layerid' in value:require(str(value['layerid'])=='19','SVG contains a non-model graphic layer')
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(data)

def inventory(data,profile=None):
    require(profile in (None,'C8734','C53568','C62715'),'unknown graphic replacement profile')
    shapes=data['result']['packageDetail']['dataStr']['shape'];require(isinstance(shapes,list),'missing shape list')
    count=collections.Counter()
    positions={'TRACK':2,'ARC':2,'CIRCLE':5,'RECT':5,'TEXT':7,'SOLIDREGION':1,'DIMENSION':1,'PROTRACTOR':1}
    for text in shapes:
        require(isinstance(text,str),'non-string raw graphic')
        cmd,_,payload=text.partition('~');f=text.split('~')
        if cmd=='PAD':count['PAD:pad_verified']+=1;continue
        if cmd=='SVGNODE':model_only(payload);count['SVGNODE:19:model_omitted']+=1;continue
        require(cmd in positions,'unsupported graphic command '+cmd)
        index=positions[cmd];require(len(f)>index and re.fullmatch('[0-9]+',f[index]) is not None,'malformed graphic layer')
        layer=int(f[index])
        require(layer in {3,12,13,99,100,101},f'unsupported physical graphic layer {layer} ({cmd}); cannot discard')
        if cmd in ('TRACK','RECT','CIRCLE'):primitive(f)
        if cmd in ('DIMENSION','PROTRACTOR'):require(layer==12,'dimension/protractor outside document layer')
        if cmd=='SOLIDREGION':
            require(len(f)>=6,'truncated solid region')
            require(f[4]=='solid' or layer==100 and f[4]=='cutout','unsupported physical cutout/npth/region type')
        if layer==12:action='document_nonphysical'
        elif profile:action='profile_replaced'
        elif cmd=='TEXT':action='text_omitted'
        elif layer==100:
            # Virtual lead decoration is not the package body or real copper.
            # Only explicitly identified virtual geometry may be omitted.
            action='virtual_lead_geometry'
        else:
            if cmd=='SOLIDREGION':
                require(layer==99,'filled marking/body graphics are unsupported without reviewed replacement')
                linear_courtyard(f[3])
            require(cmd!='ARC','arc needs supported conversion or explicit reviewed replacement')
            action='export_geometry_checked'
        count[f'{cmd}:{layer}:{action}']+=1
    return dict(schema='raw_graphics_v1',profile=profile,shape_count=len(shapes),counts=dict(sorted(count.items())),
                limitation='Inventory/layer semantics, not full raw non-pad shape equivalence.')

def profile_inventory(tree):
    counts=collections.Counter()
    for n in tree:
        tag=str(n[0]) if isinstance(n,list) and n else ''
        if tag=='property':text_layer(n)
        if not tag.startswith('fp_'):continue
        require(tag in {'fp_line','fp_rect','fp_circle','fp_arc','fp_poly','fp_text'},'unknown KiCad graphic in profile')
        layers=[v for v in n if isinstance(v,list) and v and str(v[0])=='layer']
        require(len(layers)==1 and len(layers[0])==2,'profile graphic layer missing/ambiguous')
        layer=str(layers[0][1])
        require(layer in {'F.SilkS','F.Fab','F.CrtYd','Cmts.User'},'profile cannot remove physical graphic layer '+layer)
        counts[layer+':'+tag]+=1
    return dict(sorted(counts.items()))

def expected_geometry(data):
    doc=data['result']['packageDetail']['dataStr'];ox,oy=map(numeric,(doc['head']['x'],doc['head']['y']));out=[]
    layers={3:'F.SilkS',12:'Cmts.User',13:'F.Fab',99:'F.CrtYd',100:'F.Fab',101:'F.SilkS'}
    def point(p):return [(p[0]-ox)*.254,(p[1]-oy)*.254]
    for shape in doc['shape']:
        f=shape.split('~');cmd=f[0]
        if cmd in ('PAD','TEXT','SVGNODE','DIMENSION','PROTRACTOR'):continue
        if cmd=='SOLIDREGION':
            if int(f[1]) in (12,100):continue
            require(int(f[1])==99,'unsupported graphic region')
            points=linear_courtyard(f[3]);points=points+[points[0]] if points[-1]!=points[0] else points
            width=.05;layer='F.CrtYd'
        else:
            require(cmd in ('TRACK','RECT','CIRCLE'),'unsupported generic graphic '+cmd)
            points,width=primitive(f);layer=layers[int(f[2] if cmd=='TRACK' else f[5])]
        if cmd=='CIRCLE':out.append(dict(kind='fp_circle',layer=layer,a=point(points[0]),b=point(points[1]),width=width))
        else:
            for a,b in zip(points,points[1:]):out.append(dict(kind='fp_line',layer=layer,a=point(a),b=point(b),width=width))
    return out

def normalize_geometry(data,tree,profile):
    if profile:return {'mode':'reviewed_profile_replacement','raw_equivalence':False}
    expected=expected_geometry(data);actual=[n for n in tree if isinstance(n,list) and n and str(n[0]).startswith('fp_') and str(n[0])!='fp_text']
    require(len(actual)==len(expected),'exported graphic count differs from raw')
    def field(n,k):
        values=[v for v in n if isinstance(v,list) and v and str(v[0])==k]
        require(len(values)==1,'missing/duplicate graphic '+k);return values[0]
    available=list(range(len(actual)));max_coord=max_width=0.
    for e in expected:
        candidates=[]
        for i in available:
            n=actual[i]
            if str(n[0])!=e['kind'] or str(field(n,'layer')[1])!=e['layer']:continue
            a=field(n,'center' if e['kind']=='fp_circle' else 'start');b=field(n,'end');w=field(n,'width')
            require(len(a)==len(b)==3 and len(w)==2,'malformed exported graphic')
            av=list(map(numeric,a[1:]));bv=list(map(numeric,b[1:]));dw=abs(numeric(w[1])-e['width'])
            for reverse in ([False,True] if e['kind']=='fp_line' else [False]):
                ea,eb=(e['b'],e['a']) if reverse else (e['a'],e['b'])
                dp=max(abs(u-v) for u,v in zip(av+bv,ea+eb))
                if dp<=.00500001 and dw<=.00500001:candidates.append((i,dp,dw,ea,eb));break
        require(len(candidates)==1,'exported graphic differs or is ambiguous versus raw geometry')
        i,dp,dw,ea,eb=candidates[0];available.remove(i);n=actual[i]
        field(n,'center' if e['kind']=='fp_circle' else 'start')[1:]=ea;field(n,'end')[1:]=eb;field(n,'width')[1]=e['width']
        max_coord=max(max_coord,dp);max_width=max(max_width,dw)
    return dict(mode='raw_outline_geometry',raw_equivalence=True,graphic_count=len(expected),max_export_coordinate_rounding_mm=max_coord,max_export_width_rounding_mm=max_width)
