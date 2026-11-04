"""Actual board-layer geometry and bottom-side transforms for 16.6 acceptance."""
from __future__ import annotations
import copy,json,math
from pathlib import Path

LAYERS=('ETCH/TOP','ETCH/BOTTOM','PIN/SOLDERMASK_TOP','PIN/SOLDERMASK_BOTTOM','PIN/PASTEMASK_TOP','PIN/PASTEMASK_BOTTOM')

PAD_LAYER_READER=r'''
procedure(lcReadPadLayers(s filename)
 let((f p layer pad polys aa bb)
  f=outfile(filename)
  fprintf(f "S\t%L\t%.6f\t%.6f\t%.6f\n" s->isMirrored s->rotation car(s->xy) cadr(s->xy))
  foreach(p s->pins
   foreach(layer list("ETCH/TOP" "ETCH/BOTTOM" "PIN/SOLDERMASK_TOP" "PIN/SOLDERMASK_BOTTOM" "PIN/PASTEMASK_TOP" "PIN/PASTEMASK_BOTTOM")
    pad=axlDBGetPad(p layer "REGULAR")
    polys=axlPolyFromDB(p ?layer layer ?padType 'REGULAR)
    fprintf(f "PAD\t%s\t%s\t%L\t%d\n" p->number layer if(pad then pad->figureName else nil) length(polys))
    foreach(poly polys
     aa=car(poly->bBox) bb=cadr(poly->bBox)
     fprintf(f "POLY\t%s\t%s\t%.6f\t%.6f\t%.6f\t%.6f\t%.8f\n" p->number layer car(aa) cadr(aa) car(bb) cadr(bb) poly->area)
    )
   )
  )
  close(f)
 )
)
'''

def require(condition,message):
    if not condition:raise ValueError(message)

def quarter(point,angle):
    x,y=point;require(angle%360 in (0,90,180,270),'Board-side audit requires quarter-turn placement')
    return {0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[angle%360]

def read_layers(path):
    rows={};state=None
    def floats(values):
        nums=list(map(float,values));require(all(math.isfinite(n) for n in nums),'Nonfinite actual board-layer geometry');return nums
    for line in Path(path).read_text(encoding='ascii').splitlines():
        r=line.split('\t')
        if r[0]=='S':
            require(len(r)==5 and state is None and r[1] in ('t','nil'),'Malformed/duplicate board-layer symbol state')
            values=floats(r[2:]);state=dict(mirrored=r[1]=='t',angle=values[0],anchor=values[1:])
        elif r[0]=='PAD':
            require(len(r)==5 and (r[1],r[2]) not in rows and r[2] in LAYERS,'Malformed/duplicate actual pad layer')
            figure=json.loads(r[3]) if r[3]!='nil' else None;count=int(r[4])
            require((figure is None or isinstance(figure,str)) and count in (0,1),'Unsupported actual pad layer figure/count')
            rows[(r[1],r[2])]=dict(figure=figure,count=count,polygons=[])
        elif r[0]=='POLY':
            require(len(r)==8 and (r[1],r[2]) in rows,'Malformed/unowned actual pad polygon')
            values=floats(r[3:]);require(values[0]<values[2] and values[1]<values[3] and values[4]>0,'Invalid actual pad polygon extent/area')
            rows[(r[1],r[2])]['polygons'].append(dict(bounds=values[:4],area=values[4]))
        else:raise ValueError('Unexpected board-layer record: '+r[0])
    require(state is not None,'Missing board-layer symbol state')
    return state,rows

def check_layers(expected,actual_pins,path,anchor,angle,mirrored):
    # Import lazily: verify owns independent source parsing, quantization and tolerance.
    import verify
    state,rows=read_layers(path)
    require(state['mirrored']==mirrored and verify.angleclose(state['angle'],angle) and math.dist(state['anchor'],anchor)<verify.TOL,
            'Actual board-layer symbol side/anchor/angle differs')
    require(rows.keys()=={(p['number'],layer) for p in expected for layer in LAYERS},'Actual board-layer pin/layer inventory differs')
    placed={p['number']:p for p in actual_pins}
    require(len(placed)==len(actual_pins)==len(expected) and placed.keys()=={p['number'] for p in expected},'Actual board-layer pin identity differs')
    populated=0
    for pin in expected:
        actual=placed[pin['number']]
        require(all(math.isfinite(v) for v in actual['at']) and math.isfinite(actual['angle']),'Nonfinite board-layer pin transform')
        require(verify.angleclose(actual['angle'],round(actual['angle']/90)*90),'Actual board-layer pin is not orthogonal')
        for layer in LAYERS:
            row=rows[(pin['number'],layer)];top=(layer.endswith('TOP')!=mirrored);side='F' if top else 'B'
            copper=layer.startswith('ETCH/');mask='SOLDERMASK' in layer
            if copper:exists=pin['type']=='thru_hole' or top
            elif mask:exists=side+'.Mask' in pin['layers'] or '*.Mask' in pin['layers']
            else:exists=side+'.Paste' in pin['layers']
            if not exists:
                require(row['figure'] in (None,'NULL') and row['count']==0 and not row['polygons'],f'Unexpected actual pad on {layer}, pin {pin["number"]}')
                continue
            require(row['count']==len(row['polygons'])==1,f'Missing/multiple actual pad polygons on {layer}, pin {pin["number"]}')
            dims=verify.reference_native_dimensions(pin,pin['mask'] if mask else 0);w,h=dims['size']
            require(row['figure']==dims['shape'],f'Actual pad figure differs on {layer}, pin {pin["number"]}')
            dx,dy=(w,h) if round(actual['angle']/90)%2==0 else (h,w);x,y=actual['at']
            bounds=[x-dx/2,y-dy/2,x+dx/2,y+dy/2];poly=row['polygons'][0]
            require(all(verify.close(a,b) for a,b in zip(bounds,poly['bounds'])),f'Actual pad extent differs on {layer}, pin {pin["number"]}')
            area=w*h if pin['shape']=='rect' else (math.pi*w*w/4 if pin['shape']=='circle' else min(w,h)*(max(w,h)-min(w,h))+math.pi*min(w,h)**2/4)
            # A length tolerance becomes area tolerance through the perimeter bound.
            require(abs(poly['area']-area)<=verify.TOL*(2*(w+h)+1),f'Actual pad area differs on {layer}, pin {pin["number"]}')
            populated+=1
    return dict(mirrored=mirrored,pin_layer_slots=len(rows),populated_polygons=populated,
                scope='Actual regular copper/mask/paste layer, figure, extents and area; disk/raw PAD separately verifies drills, internals, thermal/anti pads.')

def bottom_to_local(placed,anchor,angle):
    """Undo R(angle) Mx + anchor without discarding incorrect mirror/layer data."""
    import verify
    require(placed['anchor'] is not None and math.dist(placed['anchor'][:2],anchor)<verify.TOL and verify.angleclose(placed['anchor'][2],angle),'Bottom placement anchor/rotation differs')
    result=copy.deepcopy(placed)
    def point(p):
        require(len(p)==2 and all(math.isfinite(v) for v in p),'Invalid bottom-side coordinate')
        x,y=quarter((p[0]-anchor[0],p[1]-anchor[1]),-angle);return [-x,y]
    def bounds(b):
        x1,y1,x2,y2=b;p=[point(v) for v in ((x1,y1),(x1,y2),(x2,y1),(x2,y2))]
        return [min(v[0] for v in p),min(v[1] for v in p),max(v[0] for v in p),max(v[1] for v in p)]
    def layer(name):
        require(name.endswith('_BOTTOM'),'Bottom package graphic/RefDes remained on wrong side: '+name)
        return name.removesuffix('_BOTTOM')+'_TOP'
    for pin in result['pins']:
        pin['at']=point(pin['at']);pin['angle']=(angle-pin['angle'])%360
    for graphic in result['graphics']:
        graphic['layer']=layer(graphic['layer'])
        for key in ('start','end','center'):
            if key in graphic:graphic[key]=point(graphic[key])
    for text in result['texts']:
        require(text.get('mirrored') is True,'Bottom RefDes is not mirrored')
        text['layer']=layer(text['layer']);text['at']=point(text['at']);text['bounds']=bounds(text['bounds'])
        text['angle']=(angle-text['angle'])%360;text['mirrored']=False
    for boundary in result['bounds']:
        boundary['layer']=layer(boundary['layer']);boundary['bounds']=bounds(boundary['bounds'])
        for segment in boundary.get('segments',[]):
            for key in ('start','end','center'):
                if key in segment:segment[key]=point(segment[key])
            if 'clockwise' in segment:segment['clockwise']=not segment['clockwise']
    result['anchor']=[0,0,0]
    return result
