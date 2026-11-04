"""Explicit review recipes. Never silently apply to an arbitrary C-number.

Original source remains immutable. Every changed pad/graphic is recorded.
These profiles correct source library deficiencies; they are NOT neutral conversion.
"""
import hashlib, json, math
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sexpdata as sx
import kicad_source

def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def node(n,k):return next((s for s in n if tag(s)==k),None)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def line(a,b,layer,w=.15):
    return sx.loads(f'(fp_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer {layer}) (width {w}))')
def rect(bounds,layer,w):
    a,b,c,d=bounds;pts=[(a,b),(c,b),(c,d),(a,d)]
    return [line(pts[i],pts[(i+1)%4],layer,w) for i in range(4)]
def ensure(ok,msg):
    if not ok:raise ValueError(msg)

def reviewed_document(profile):
    # These are reviewed byte identities, not "whatever the same URL serves".
    ensure(profile in ('C8734','C53568','C62715'),'Unknown manufacturer profile')
    if profile=='C62715':
        return dict(schema='bound_manufacturer_v1',mpn='LTV-817-A',package='LTV-817 standard DIP4',
            publisher='LITE-ON',document_id='LTV-817 / 827 / 847 series',document_revision=None,document_date='2010-01',
            filename='C62715_LTV817A.pdf',sha256='616ee1f8582185fc8562fb98e868051147a00d58ac8c76baf75251fdbbb689ce',
            source_kind='manufacturer_pdf_via_lcsc',pdf_pages_1_based=[2,11],references=['LTV-817 OUTLINE DIMENSIONS','RANK TABLE A'],
            url_role='pinned_file_origin',policy_id='C62715_DIP4_REVIEW_1')
    if profile=='C8734':
        return dict(schema='bound_manufacturer_v1',mpn='STM32F103C8T6',package='LQFP48 (5B)',
            publisher='STMicroelectronics',document_id='DS5319',document_revision='20',document_date='2025-07',
            filename='STM32F103C8.pdf',sha256='1e54ce18e6c34c5b4a78986f26e245e143748c721009fc9af5953795c30686bc',
            source_kind='manufacturer_pdf',pdf_pages_1_based=[98,99,100],references=['Figure 55','Table 62','Figure 56'],
            url_role='manufacturer_reference',policy_id='C8734_LQFP48_REVIEW_1')
    return dict(schema='bound_manufacturer_v1',mpn='1N4007RLG',package='DO41 / CASE 59-10 ISSUE U',
        publisher='onsemi',document_id='1N4001/D',document_revision='13',document_date='2012-10',
        filename='1N4007.pdf',sha256='3967a81f94842515b1ff4258fc779a87ba9c7a11f464cd974dd0eba1941c84b7',
        source_kind='user_supplied_manufacturer_pdf',pdf_pages_1_based=[1,7],references=['Polarity band','CASE 59-10 ISSUE U'],
        url_role='current_manufacturer_reference_not_pinned_file_origin',policy_id='C53568_DO41_REVIEW_2')

def dimensional_basis(profile):
    if profile=='C62715':
        return dict(drawing_dimension_unit='mm',body_nominal_mm=[4.6,6.5],body_tolerance_mm=[.5,.5],
            assembly_envelope_mm=[5.1,7.0],pitch_nominal_mm=2.54,pitch_tolerance_mm=.25,row_nominal_mm=7.62,
            row_at_body_tolerance_mm=.30,free_lead_span_mm=[7.62,9.98],
            lead_width_nominal_mm=.5,lead_width_tolerance_mm=.1,lead_width_max_mm=.6,
            lead_thickness_nominal_mm=.26,lead_thickness_tolerance_mm=None,
            width_max_thickness_nominal_diagonal_mm=round(math.hypot(.6,.26),8),
            fully_toleranced_lead_diagonal_mm=None,nominal_hole_mm=1.0,manufacturer_recommended_pcb_hole_mm=None,
            formed_lead_fit_verified=False,manufactured_fit_verified=False)
    if profile!='C53568':return None
    # CASE 59-10 note 2 makes INCH controlling. Its mm column is rounded.
    inch={k:Decimal(v) for k,v in {'A':'.205','B':'.106','D':'.034','F':'.050'}.items()}
    mm={k:v*Decimal('25.4') for k,v in inch.items()}
    envelope=[float(mm[k].quantize(Decimal('.01'),rounding=ROUND_CEILING)) for k in ('A','B')]
    return dict(controlling_unit='inch',mm_per_inch=25.4,maximum_dimensions_inch={k:float(v) for k,v in inch.items()},
        maximum_dimensions_mm={k:float(v) for k,v in mm.items()},assembly_envelope_mm=envelope,
        envelope_policy='round each maximum body dimension outward to 0.01 mm',
        nominal_hole_mm=1.0,nominal_diametral_allowance_mm=float(Decimal('1')-mm['D']),
        nominal_allowance_minimum_policy_mm=.10,lead_diameter_uncontrolled_within_F=True,
        manufactured_fit_verified=False)

def prepare(src,profile,job):
    ensure(profile in ('C8734','C53568','C62715'),'Unknown manufacturer profile')
    basis=reviewed_document(profile);document=Path(__file__).resolve().parents[1]/'docs/datasheets'/basis['filename']
    ensure(document.is_file(),'Manufacturer evidence: reviewed datasheet is missing')
    document_bytes=document.read_bytes()
    ensure(hashlib.sha256(document_bytes).hexdigest()==basis['sha256'],'Manufacturer evidence: datasheet bytes/version changed; review again')
    tree=kicad_source.load(src.read_text(encoding='utf-8-sig'))
    identities=[str(n[2]) for n in tree if tag(n)=='property' and len(n)>=3 and str(n[1])=='LCSC Part']
    ensure(identities==[profile],'Profile/C-number mismatch or ambiguity')
    from source_graphics import profile_inventory
    replaced_inventory=profile_inventory(tree)
    pads=[n for n in tree if tag(n)=='pad']
    before={str(n[1]):n for n in pads}
    changes=[]
    if profile=='C8734':
        ensure(set(before)=={str(i) for i in range(1,49)},'C8734 source pin set changed')
        for i in range(1,49):
            p=before[str(i)];group=(i-1)//12;j=(i-1)%12
            expected=[(-2.75+.5*j,4.25),(4.25,2.75-.5*j),(2.75-.5*j,-4.25),(-4.25,-2.75+.5*j)][group]
            at=node(p,'at');size=node(p,'size')
            ensure(str(p[2])=='smd' and str(p[3])=='oval','C8734 source pad type changed; review again')
            ensure(math.dist([float(at[1]),float(at[2])],expected)<.001 and abs(float(at[3]))<.001,'C8734 source orientation/pitch changed')
            oldsize=(.270,1.500) if group in (0,2) else (1.500,.270)
            ensure(math.dist([float(size[1]),float(size[2])],oldsize)<.001,'C8734 source pad dimensions changed')
            p[3]=sx.Symbol('rect');size[1:]=[.30,1.20] if group in (0,2) else [1.20,.30]
        body=[-3.5,-3.5,3.5,3.5];courtyard=[-5.15,-5.15,5.15,5.15];mask=.03
        graphics=rect(body,'F.Fab',.10)+rect([-3.3,-3.3,3.3,3.3],'F.SilkS',.15)+rect(courtyard,'F.CrtYd',.05)
        graphics.append(sx.loads('(fp_circle (center -3.25 3.25) (end -3.10 3.25) (layer F.SilkS) (width 0.12))'))
        manufacturer='ST STM32F103C8T6, DS5319 Rev 20, Figure 55/56'
        url='https://www.st.com/resource/en/datasheet/stm32f103c8.pdf';pdf='STM32F103C8.pdf'
        changes=['保留源库48脚编号、中心、0.50mm脚距及4.25mm焊盘中心偏移。',
                 '铜盘由源库0.27×1.50mm长圆改为ST Figure 56的0.30×1.20mm矩形；这是有记录的设计修订。',
                 '重建7×7mm装配本体、避让焊盘的丝印与1脚标记；将只包本体的Courtyard修正为10.30×10.30mm。',
                 '替换源库全部图形（含近整圆弧、Cmts.User装饰、未闭合/重叠丝印），原件保留。',
                 '阻焊单边扩展0.03mm、丝印0.15mm、边界外扩是本库设计选值，非ST制造认证。']
    elif profile=='C53568':
        ensure(set(before)=={'1','2'},'C53568 source pin set changed')
        for i,x in ((1,-4.35),(2,4.35)):
            p=before[str(i)];at=node(p,'at');size=node(p,'size');drill=node(p,'drill')
            ensure(str(p[2])=='thru_hole' and str(p[3])=='circle','C53568 source pad type changed')
            ensure(math.dist([float(at[1]),float(at[2])],(x,0))<.001 and abs(float(at[3]))<.001,'C53568 source pitch/orientation changed')
            ensure(math.dist([float(size[1]),float(size[2])],(1.8,1.8))<.001 and len(drill)==2 and abs(float(drill[1])-1)<.001,'C53568 source hole/pad changed')
        envelope=dimensional_basis(profile)['assembly_envelope_mm'];a,b=(v/2 for v in envelope)
        body=[-a,-b,a,b];courtyard=[-5.60,-1.75,5.60,1.75];mask=.05
        graphics=rect(body,'F.Fab',.10)+rect([-2.70,-1.45,2.70,1.45],'F.SilkS',.15)+rect(courtyard,'F.CrtYd',.05)
        graphics.append(line((-1.90,-1.45),(-1.90,1.45),'F.SilkS'))
        manufacturer='onsemi 1N4007RLG, CASE 59-10 ISSUE U; user supplied datasheet page 7'
        url='https://www.onsemi.com/pdf/datasheet/1n4001-d.pdf';pdf='1N4007.pdf'
        changes=['保留源库8.70mm孔距、1.00mm圆形金属化孔、1.80mm铜盘及1/2脚号。',
                 '尺寸以厂家控制单位INCH换算：本体上限5.207×2.6924mm，装配包络分别向外取到0.01mm为5.21×2.70mm。',
                 '重建外框和左侧阴极带（1=K，2=A），Courtyard包住铜盘与本体。',
                 '阻焊单边扩展0.05mm、铜环0.40mm、8.70mm弯脚安装距为库设计选值，非厂家指定PCB孔距。',
                 '最大受控引脚直径0.034英寸=0.8636mm，名义孔径1.00mm，名义直径余量0.1364mm。',
                 '厂家注明本体附近F=0.050英寸=1.27mm内引脚直径不受D尺寸控制；未验证实际弯脚、装配位置及板厂成品孔公差，不能据名义余量保证实物可装配。']
    else:
        positions={'1':(-1.27,3.81),'2':(1.27,3.81),'3':(1.27,-3.81),'4':(-1.27,-3.81)}
        ensure(len(pads)==4 and set(before)==set(positions),'C62715 source pin set changed')
        for number,xy in positions.items():
            p=before[number];at=node(p,'at');size=node(p,'size');drill=node(p,'drill')
            ensure(str(p[2])=='thru_hole' and str(p[3])=='oval','C62715 source pad type changed')
            ensure(math.dist([float(at[1]),float(at[2])],xy)<.00021 and abs(float(at[3]))<.00021,'C62715 source pin orientation/pitch changed')
            ensure(math.dist([float(size[1]),float(size[2])],(1.524,2.0))<.00021 and len(drill)==2 and abs(float(drill[1])-1)<.00021,'C62715 source hole/pad changed')
        body=[-2.55,-3.5,2.55,3.5];courtyard=[-3.05,-5.15,3.05,5.15];mask=.05
        graphics=rect(body,'F.Fab',.10)+rect(courtyard,'F.CrtYd',.05)
        for x in (-2.70,2.70):graphics.append(line((x,-3.55),(x,3.55),'F.SilkS'))
        for y in (-3.55,3.55):
            for a,b in ((-2.70,-2.40),(2.40,2.70)):graphics.append(line((a,y),(b,y),'F.SilkS'))
        graphics.append(sx.loads('(fp_circle (center -1.8 2.35) (end -1.65 2.35) (layer F.SilkS) (width 0.12))'))
        manufacturer='LITE-ON LTV-817-A, January 2010, LTV-817 outline page 2 and rank A page 11'
        url='https://datasheet.lcsc.com/datasheet/pdf/02b3eef263084ccda653b96b967d41b5.pdf?productCode=C62715';pdf='C62715_LTV817A.pdf'
        changes=['保留原始四脚编号、2.54mm同排脚距、7.62mm安装排距、1.524×2.00mm长圆铜盘及1.00mm名义金属化圆孔。',
                 '厂家本体4.6±0.5×6.5±0.5mm，补建5.10×7.00mm最大装配包络；中心为(0,0)。',
                 '重建避开阻焊开窗的断开式丝印、1脚标记和6.10×10.30mm占位框；原始框没有包住焊盘，原始装配本体缺失。',
                 '阻焊单边0.05mm、孔径、铜盘、丝印及占位框属于库选值；厂家未提供本图的推荐PCB孔径。',
                 '引脚宽度上限0.60mm，厚度只给0.26mm且未给公差；sqrt(0.60²+0.26²)不是完整最坏公差对角线。',
                 '自由引脚跨度7.62—9.98mm；7.62mm安装排距需要成形适配，未完成成品孔、引脚厚度公差和实物装配验证。']
    # Only the graphics listed above are replaced; unexpected electrical fields survive
    # and are rejected by the normal parser instead of being silently removed.
    oldgraphics=[n for n in tree if tag(n).startswith('fp_')]
    tree=[n for n in tree if not tag(n).startswith('fp_') and tag(n) not in ('model','descr','attr')]
    tree.append([sx.Symbol('descr'),manufacturer+'；中文路径与说明测试'])
    tree.append([sx.Symbol('attr'),sx.Symbol('smd' if profile=='C8734' else 'through_hole')])
    tree.extend(graphics)
    mask_revisions=[]
    for p in pads:
        layers=[str(x) for x in node(p,'layers')[1:]]
        ensure(set(layers)==({'F.Cu','F.Paste','F.Mask'} if profile=='C8734' else {'*.Cu','*.Mask'}),'Source pad layer set changed')
        masks=[n for n in p if tag(n)=='solder_mask_margin']
        ensure(len(masks)<=1,'Source local mask margin conflicts: duplicate fields')
        local_mask=masks[0] if masks else None
        if local_mask is not None:
            raw_mask=.1969*.254 if profile=='C8734' else .2*.254
            value=float(local_mask[1]) if len(local_mask)==2 else float('nan')
            ensure(math.isfinite(value) and (abs(value-mask)<1e-8 or abs(value-raw_mask)<1e-8),'Source local mask margin conflicts with reviewed profile policy; review again')
            mask_revisions.append(dict(pin=str(p[1]),from_mm=value,to_mm=mask))
            local_mask[1]=mask
    if mask_revisions:changes.append('逐脚记录原始阻焊扩展到本库选值的明确修订；不把任意扩展当默认值覆盖。')
    out=job/'reviewed'/('已复核封装_'+profile+'.kicad_mod');out.parent.mkdir(exist_ok=True)
    out.write_text(sx.dumps(tree)+'\n',encoding='utf-8')
    record=dict(profile=profile,revision={'C8734':4,'C53568':5,'C62715':1}[profile],review_basis=basis,original_source=str(src.relative_to(job)),original_sha256=digest(src),
        reviewed_source=str(out.relative_to(job)),reviewed_sha256=digest(out),body_bounds_mm=body,courtyard_bounds_mm=courtyard,
        mask_margin_mm=mask,mask_revisions=mask_revisions,manufacturer=manufacturer,url=url,changes=changes,replaced_graphic_count=len(oldgraphics),replaced_graphic_inventory=replaced_inventory)
    evidence=job/'reviewed'/pdf
    ensure(pdf==basis['filename'],'Manufacturer evidence: recipe/datasheet identity mismatch')
    evidence.write_bytes(document_bytes)
    record['datasheet']=str(evidence.relative_to(job));record['datasheet_sha256']=digest(evidence)
    if profile in ('C53568','C62715'):record['dimensional_basis']=dimensional_basis(profile)
    (job/'reviewed/review.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return out,record
