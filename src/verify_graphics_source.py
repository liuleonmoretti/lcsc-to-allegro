"""Independent inventory of original graphics, including local profile inputs."""
import collections,hashlib,json,math,re
from pathlib import Path
import sexpdata
from shapely.geometry import LinearRing,Polygon

def need(ok,msg):
    if not ok:raise ValueError('Reference source graphics: '+msg)
def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def check_text_layer(node):
    """Independent check, including modern drawable property fields."""
    kind=tag(node)
    if kind not in {'fp_text','property'}:return None
    layer_nodes=[];positioned=False
    for item in node[3:]:
        if tag(item)=='layer':layer_nodes.append(item)
        elif tag(item) in ('at','effects','render_cache'):positioned=True
        elif not isinstance(item,list) and str(item) in ('hide','unlocked'):positioned=True
    if kind=='property' and not positioned and not layer_nodes:return None
    need(len(layer_nodes)==1,'source text must have exactly one explicit layer')
    need(len(layer_nodes[0])==2,'source text layer is malformed')
    actual=str(layer_nodes[0][1])
    annotations=('F.SilkS','B.SilkS','F.Fab','B.Fab','F.CrtYd','B.CrtYd','Cmts.User','Dwgs.User','Eco1.User','Eco2.User')
    need(actual in annotations,'source text on physical or unsupported layer cannot be omitted: '+actual)
    return actual

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def check_linear_path(text):
    # Every segment must be one explicit absolute command. Lowercase/curves,
    # implicit repeated coordinates and multiple subpaths are not approximated.
    commands=re.findall(r'([MLHVZ])([^MLHVZ]*)',text.strip())
    need(commands and ''.join(a+b for a,b in commands)==text.strip(),'invalid/unsupported courtyard graphic path')
    vertices=[]
    for i,(command,body) in enumerate(commands):
        need((command=='M')==(i==0) and (command=='Z')==(i==len(commands)-1),'open/multiple courtyard graphic subpaths')
        cells=body.replace(',',' ').split();n={'M':2,'L':2,'H':1,'V':1,'Z':0}[command]
        need(len(cells)==n,'implicit/repeated courtyard graphic coordinates')
        values=list(map(float,cells));need(all(math.isfinite(v) for v in values),'non-finite graphic point')
        if command=='M' or command=='L':vertices.append(tuple(values))
        elif command=='H':vertices.append((values[0],vertices[-1][1]))
        elif command=='V':vertices.append((vertices[-1][0],values[0]))
    need(len(vertices)>=3,'too few courtyard graphic points')
    ring=LinearRing(vertices);need(ring.is_simple and Polygon(ring).area>0,'invalid courtyard graphic polygon')
    return vertices

def value(text):
    try:result=float(text)
    except (TypeError,ValueError):raise ValueError('Reference source graphics: invalid numeric graphic field')
    need(math.isfinite(result),'non-finite graphic value');return result

def outline_values(c):
    """Independent validation before trusting upstream safe_float/minimums."""
    if c[0]=='CIRCLE':
        need(len(c)>=8 and not any(c[8:]),'unreviewed circle graphic fields')
        cx,cy,r,w=map(value,c[1:5]);need(r>0,'graphic radius must be positive')
        need(c[7] in {'0','1'},'invalid circle graphic lock');pts=[(cx,cy),(cx+r,cy)]
    elif c[0]=='RECT':
        need(len(c)>=9 and not any(c[9:]),'unreviewed rectangle graphic fields')
        x,y,dx,dy=map(value,c[1:5]);need(dx>0 and dy>0,'graphic rectangle dimensions must be positive')
        need(c[7] in {'0','1'},'invalid rectangle graphic lock');w=value(c[8]);pts=[(x,y),(x+dx,y),(x+dx,y+dy),(x,y+dy),(x,y)]
    else:
        need(c[0]=='TRACK' and len(c)>=7 and not any(c[7:]),'unreviewed track graphic fields')
        nums=[value(t) for t in c[4].split()];need(len(nums)>=4 and len(nums)%2==0,'invalid graphic polyline')
        need(c[6] in {'0','1'},'invalid track graphic lock');pts=list(zip(nums[::2],nums[1::2]));w=value(c[1])
    need(w*.254>=.01-1e-12,'graphic stroke below supported 0.01mm')
    need(all(math.dist(a,b)>0 for a,b in zip(pts,pts[1:])),'degenerate graphic segment')
    return pts,w*.254

def svg_is_only_model(text):
    def pairs(items):
        seen={}
        for key,value in items:
            need(key not in seen,'duplicate SVG graphic JSON key');seen[key]=value
        return seen
    node=json.loads(text,object_pairs_hook=pairs)
    need(isinstance(node,dict) and str(node.get('layerid'))=='19','SVG graphic is not a model')
    attrs=node.get('attrs',{});need(isinstance(attrs,dict) and attrs.get('c_etype')=='outline3D' and str(attrs.get('layerid'))=='19','unknown SVG graphic semantics')
    pending=[node]
    while pending:
        item=pending.pop()
        if isinstance(item,dict):
            need('layerid' not in item or str(item['layerid'])=='19','nested physical SVG graphic layer')
            pending.extend(item.values())
        elif isinstance(item,list):pending.extend(item)

def inventory(data,profile):
    need(profile in {None,'C8734','C53568','C62715'},'unknown replacement profile')
    primitives=data['result']['packageDetail']['dataStr']['shape'];need(isinstance(primitives,list),'no primitive list')
    tally=collections.Counter()
    for item in primitives:
        need(isinstance(item,str),'invalid primitive');cmd=item.partition('~')[0]
        if cmd=='PAD':tally['PAD:pad_verified']+=1;continue
        if cmd=='SVGNODE':svg_is_only_model(item.partition('~')[2]);tally['SVGNODE:19:model_omitted']+=1;continue
        cells=item.split('~')
        if cmd in {'TRACK','ARC'}:index=2
        elif cmd in {'RECT','CIRCLE'}:index=5
        elif cmd=='TEXT':index=7
        elif cmd in {'SOLIDREGION','DIMENSION','PROTRACTOR'}:index=1
        else:raise ValueError('Reference source graphics: unsupported graphic command '+cmd)
        need(len(cells)>index and re.fullmatch(r'[0-9]+',cells[index]) is not None,'invalid graphic layer')
        layer=int(cells[index])
        need(layer in {3,12,13,99,100,101},f'unsupported physical graphic layer {layer}; lost copper/mask/paste/board geometry')
        if cmd in {'TRACK','RECT','CIRCLE'}:outline_values(cells)
        if cmd in {'DIMENSION','PROTRACTOR'}:need(layer==12,'non-document dimension graphic')
        if cmd=='SOLIDREGION':
            need(len(cells)>=6 and (cells[4]=='solid' or layer==100 and cells[4]=='cutout'),'unsupported physical graphic cutout/npth')
        if layer==12:disposition='document_nonphysical'
        elif profile:disposition='profile_replaced'
        elif cmd=='TEXT':disposition='text_omitted'
        elif layer==100:disposition='virtual_lead_geometry'
        else:
            need(cmd!='ARC','unreviewed arc graphic')
            if cmd=='SOLIDREGION':
                need(layer==99,'filled graphic would lose its region');check_linear_path(cells[3])
            disposition='export_geometry_checked'
        tally[f'{cmd}:{layer}:{disposition}']+=1
    return dict(schema='raw_graphics_v1',profile=profile,shape_count=len(primitives),counts=dict(sorted(tally.items())),limitation='Inventory/layer semantics, not full raw non-pad shape equivalence.')

def profile_source(job,status):
    record=status.get('review_profile')
    if not record:return {'mode':'no_profile'}
    source=(job/record['original_source']).resolve();need(source.is_relative_to(job.resolve()),'profile source path escapes job')
    files={str(Path(k)).replace('\\','/').casefold():v for k,v in status['files'].items()}
    need(source.is_file() and sha(source)==record.get('original_sha256')==files.get(source.relative_to(job).as_posix().casefold()),'unmanifested/changed profile original')
    tree=sexpdata.loads(source.read_text(encoding='utf-8-sig'));count=collections.Counter()
    for node in tree:
        if tag(node)=='property':check_text_layer(node)
        if not tag(node).startswith('fp_'):continue
        kind=tag(node);need(kind in {'fp_line','fp_rect','fp_circle','fp_arc','fp_poly','fp_text'},'unreviewed profile graphic kind')
        layers=[part for part in node if tag(part)=='layer']
        need(len(layers)==1 and len(layers[0])==2,'ambiguous profile graphic layer')
        layer=str(layers[0][1]);need(layer in {'F.SilkS','F.Fab','F.CrtYd','Cmts.User'},'profile removed physical graphic layer '+layer)
        count[layer+':'+kind]+=1
    need(record.get('replaced_graphic_inventory')==dict(sorted(count.items())),'profile replacement inventory missing or inconsistent; regenerate')
    return {'mode':'reviewed_profile','source_sha256':sha(source),'replaced':dict(sorted(count.items()))}

def raw_geometry(data):
    document=data['result']['packageDetail']['dataStr'];base=(value(document['head']['x']),value(document['head']['y']));geometries=[]
    def relative(p):return ((p[0]-base[0])*.254,(p[1]-base[1])*.254)
    mapping={3:'F.SilkS',12:'Cmts.User',13:'F.Fab',99:'F.CrtYd',100:'F.Fab',101:'F.SilkS'}
    for item in document['shape']:
        c=item.split('~');kind=c[0]
        if kind in {'PAD','TEXT','SVGNODE','DIMENSION','PROTRACTOR'}:continue
        if kind=='SOLIDREGION':
            layer=int(c[1])
            if layer in {12,100}:continue  # Explicit nonphysical omission in inventory.
            need(layer==99,'unsupported generic filled graphic')
            pts=check_linear_path(c[3]);stroke=.05
            if pts[0]!=pts[-1]:pts=pts+[pts[0]]
        else:
            need(kind in {'TRACK','RECT','CIRCLE'},'unsupported generic outline graphic')
            pts,stroke=outline_values(c);layer=int(c[2] if kind=='TRACK' else c[5])
        pairs=[(pts[0],pts[1])] if kind=='CIRCLE' else list(zip(pts,pts[1:]))
        for a,b in pairs:geometries.append(('circle' if kind=='CIRCLE' else 'line',mapping[layer],(*relative(a),*relative(b),stroke)))
    return geometries

def exported_geometry(tree):
    result=[]
    for node in tree:
        kind=tag(node)
        if not kind.startswith('fp_') or kind=='fp_text':continue
        need(kind in {'fp_line','fp_circle'},'unexpected exported graphic kind')
        def item(name,count):
            found=[n for n in node if tag(n)==name];need(len(found)==1 and len(found[0])==count,'malformed exported graphic '+name);return found[0][1:]
        a=list(map(value,item('center' if kind=='fp_circle' else 'start',3)));b=list(map(value,item('end',3)))
        layer=str(item('layer',2)[0]);width=value(item('width',2)[0])
        result.append(('circle' if kind=='fp_circle' else 'line',layer,tuple(a+b+[width])))
    return result

def geometry_delta(expected,tree,tolerance):
    obtained=exported_geometry(tree);need(len(expected)==len(obtained),'raw/exported graphic count mismatch');used=set();coord=width=0.
    for typ,layer,vector in obtained:
        candidates=[]
        for index,(shape,other,reference) in enumerate(expected):
            if index in used or (shape,other)!=(typ,layer):continue
            variants=[reference]
            if shape=='line':variants.append((*reference[2:4],*reference[:2],reference[4]))
            for v in variants:
                dc=max(abs(a-b) for a,b in zip(vector[:4],v[:4]));dw=abs(vector[4]-v[4])
                if max(dc,dw)<=tolerance:candidates.append((index,dc,dw));break
        need(len(candidates)==1,'raw/exported graphic geometry differs or ambiguous')
        index,dc,dw=candidates[0];used.add(index);coord=max(coord,dc);width=max(width,dw)
    return coord,width

def check_geometry(data,trees,profile):
    if profile:
        need([n for n in trees[0] if tag(n)!='pad']==[n for n in trees[1] if tag(n)!='pad'],'profile raw normalization changed non-pad elements')
        return {'mode':'reviewed_profile_replacement','raw_equivalence':False}
    expected=raw_geometry(data);dc,dw=geometry_delta(expected,trees[0],.00500001)
    geometry_delta(expected,trees[1],1e-8)
    # Only the two supported outline types may change for precision restoration.
    ignore={'pad','fp_line','fp_circle'}
    need([n for n in trees[0] if tag(n) not in ignore]==[n for n in trees[1] if tag(n) not in ignore],'normalization unexpectedly changed non-pad metadata')
    return dict(mode='raw_outline_geometry',raw_equivalence=True,graphic_count=len(expected),max_export_coordinate_rounding_mm=dc,max_export_width_rounding_mm=dw)
