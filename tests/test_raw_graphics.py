"""Electrical artwork must survive the upstream export and review recipe boundary."""
import copy,json,math,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import source_graphics as producer
import verify_graphics_source as independent
import easyeda_raw,verify_upstream,reviewed_profiles,verify,lc2a

def data(shapes):return {'result':{'packageDetail':{'dataStr':{'head':{'x':0,'y':0},'shape':shapes}}}}
def solid(layer=99,kind='solid',path='M 0 0 L 4 0 L 4 2 L 0 2 Z'):
    return f'SOLIDREGION~{layer}~~{path}~{kind}~test_region~~~~0'
def track(layer=3):return f'TRACK~0.5~{layer}~~0 0 4 0 4 2~test_line~0'
def svg(layer=19,nested=None):
    v={'layerid':layer,'attrs':{'layerid':layer,'c_etype':'outline3D'}}
    if nested is not None:v['children']=[{'layerid':nested}]
    return 'SVGNODE~'+json.dumps(v)

class GraphicInventory(unittest.TestCase):
    def both(self,shapes,profile=None):
        p=producer.inventory(data(shapes),profile);q=independent.inventory(data(shapes),profile)
        self.assertEqual(p,q);return p
    def reject(self,shapes,profile=None,pattern='graphic|courtyard'):
        for fn in (producer.inventory,independent.inventory):
            with self.subTest(reader=fn.__module__),self.assertRaisesRegex(ValueError,pattern):fn(data(shapes),profile)
    def test_copper_regions_cannot_disappear(self):
        for layer in (1,2,11,21,22):
            with self.subTest(layer=layer):self.reject([solid(layer)],'C53568')
    def test_copper_tracks_cannot_be_profile_deleted(self):
        for profile in (None,'C53568','C8734'):
            with self.subTest(profile=profile):self.reject([track(1)],profile)
    def test_mask_and_paste_regions_not_ignored(self):
        for layer in (5,6,7,8):
            with self.subTest(layer=layer):self.reject([solid(layer)],'C8734')
    def test_board_outline_and_mechanical_cutout_rejected(self):
        for layer,kind in ((10,'npth'),(3,'npth'),(99,'cutout'),(100,'npth')):
            with self.subTest(layer=layer,kind=kind):self.reject([solid(layer,kind)],'C53568')
    def test_unknown_command_cannot_disappear(self):self.reject(['NEW_SHAPE~1~0~0'],'C53568')
    def test_unknown_and_bottom_layers_do_not_default_to_fab(self):
        for layer in (4,9,14,15,18,102,999):
            with self.subTest(layer=layer):self.reject([track(layer)],'C53568')
    def test_each_supported_command_checks_its_own_layer_field(self):
        records=['ARC~1~1~~M 0 0 A 1 1 0 0 1 2 2~id~0',
                 'CIRCLE~0~0~1~0.1~1~id~0','RECT~0~0~4~2~1~id~0',
                 'TEXT~N~0~0~1~0~none~1~~1~TEST~path~show~id~0',
                 'DIMENSION~1~ignored','PROTRACTOR~1~ignored']
        for record in records:
            with self.subTest(record=record):self.reject([record],'C8734')
    def test_svg_must_be_identified_model_layer(self):
        for layer in (1,3,10):
            with self.subTest(layer=layer):self.reject([svg(layer)],'C53568',pattern='graphic|SVG')
    def test_nested_svg_copper_rejected(self):self.reject([svg(nested=1)],'C53568',pattern='graphic|SVG')
    def test_svg_missing_model_identity_rejected(self):
        self.reject(['SVGNODE~'+json.dumps({'layerid':19,'attrs':{'layerid':19}})],'C53568',pattern='graphic|SVG')
    def test_duplicate_svg_keys_rejected(self):
        self.reject(['SVGNODE~{"layerid":1,"layerid":19,"attrs":{"layerid":19,"c_etype":"outline3D"}}'],'C53568',pattern='graphic|SVG')
    def test_known_model_omission_is_recorded(self):
        result=self.both([svg(nested=19)]);self.assertEqual(result['counts'],{'SVGNODE:19:model_omitted':1})
    def test_explicit_profile_replacement_and_nonphysical_layers_recorded(self):
        result=self.both([track(),solid(100,'cutout'),solid(12),solid(99)],'C53568')
        self.assertEqual(result['shape_count'],4)
        self.assertEqual(result['counts']['SOLIDREGION:100:profile_replaced'],1)
        self.assertEqual(result['counts']['SOLIDREGION:12:document_nonphysical'],1)
    def test_generic_filled_silk_not_downgraded_to_outline(self):self.reject([solid(3)])
    def test_generic_arc_rejected_before_upstream_chord_approximation(self):
        self.reject(['ARC~1~3~~M 0 0 A 1 1 0 0 1 2 2~id~0'])
    def test_explicit_concave_linear_courtyard_allowed(self):
        self.both([solid(path='M 0 0 H 4 V 4 H 3 V 1 H 0 Z')])
    def test_curved_relative_implicit_open_courtyard_rejected(self):
        paths=['M 0 0 A 4 4 0 0 1 4 4 L 0 4 Z',
               'm 0 0 l 4 0 l 4 4 z','M 0 0 L 4 0 4 4 0 4 Z',
               'M 0 0 L 4 0 L 4 4','M 0 0 L 4 0 L 4 4 Z M 6 6 L 7 6 L 7 7 Z',
               'M 0 0 L 4 4 L 0 4 L 4 0 Z','M 0 0 L NaN 2 L 4 4 Z']
        for path in paths:
            with self.subTest(path=path):
                for fn in (producer.inventory,independent.inventory):
                    with self.assertRaises(ValueError):fn(data([solid(path=path)]),None)
    def test_inventory_metadata_tamper_rejected_after_coherent_hashes(self):
        # Reuse a valid on-disk job: the independent reader must recompute counts.
        info=verify.load(lc2a.ROOT/'work/pth_v2.json');old=Path(info['job']);state=verify.load(old/'status.json')
        with tempfile.TemporaryDirectory(prefix='图形清单篡改_') as tmp:
            job=Path(tmp);ev=state['fetch_evidence']
            for rel in (ev['response_file'],ev['footprint_file']):
                target=job/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/rel,target)
            audited,audit=easyeda_raw.normalize(job,job/ev['footprint_file'],ev,'C53568')
            source,profile=reviewed_profiles.prepare(audited,'C53568',job)
            audit['graphic_inventory']['shape_count']+=1
            verify.save(job/'raw_pad_audit.json',audit)
            s=dict(lcsc='C53568',fetch_evidence=ev,raw_pad_audit=audit,review_profile=profile,source_file=source.relative_to(job).as_posix(),
                   files={str(p.relative_to(job)):verify.sha(p) for p in job.rglob('*') if p.is_file()})
            with self.assertRaisesRegex(ValueError,'inventory'):verify_upstream.check(job,s)

class ProfileSource(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='修订前图形_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        old=Path(verify.load(lc2a.ROOT/'work/pth_v2.json')['job']);s=verify.load(old/'status.json')
        self.tree=sx.loads((old/s['review_profile']['original_source']).read_text(encoding='utf-8-sig'))
        self.source=self.job/'source.kicad_mod'
    def write(self):self.source.write_text(sx.dumps(self.tree),encoding='utf-8')
    def review(self):
        self.write();_,record=reviewed_profiles.prepare(self.source,'C53568',self.job)
        state={'review_profile':record,'files':{'source.kicad_mod':verify.sha(self.source)}}
        return record,state
    def test_normal_local_profile_records_replacement_inventory(self):
        record,state=self.review();result=independent.profile_source(self.job,state)
        self.assertEqual(record['revision'],5);self.assertEqual(result['replaced'],record['replaced_graphic_inventory'])
    def test_local_copper_mask_paste_edge_graphics_rejected_in_both_paths(self):
        for layer in ('F.Cu','B.Cu','In1.Cu','F.Mask','F.Paste','Edge.Cuts','Dwgs.User'):
            with self.subTest(layer=layer):
                self.tree.append(sx.loads(f'(fp_line (start 0 0) (end 1 1) (layer {layer}) (width 0.2))'));self.write()
                state={'review_profile':{'original_source':self.source.name,'original_sha256':verify.sha(self.source),'replaced_graphic_inventory':{}},'files':{self.source.name:verify.sha(self.source)}}
                with self.assertRaisesRegex(ValueError,'physical graphic'):reviewed_profiles.prepare(self.source,'C53568',self.job)
                with self.assertRaisesRegex(ValueError,'physical graphic'):independent.profile_source(self.job,state)
                self.tree.pop()
    def test_unknown_profile_primitive_rejected(self):
        self.tree.append(sx.loads('(fp_new_shape (layer F.SilkS))'));self.write()
        with self.assertRaisesRegex(ValueError,'unknown KiCad graphic'):reviewed_profiles.prepare(self.source,'C53568',self.job)
    def test_duplicate_profile_layer_rejected(self):
        self.tree.append(sx.loads('(fp_line (start 0 0) (end 1 1) (layer F.SilkS) (layer F.Cu) (width 0.2))'));self.write()
        with self.assertRaisesRegex(ValueError,'missing/ambiguous'):reviewed_profiles.prepare(self.source,'C53568',self.job)
    def test_missing_original_replacement_inventory_blocks_independent_check(self):
        record,state=self.review();record.pop('replaced_graphic_inventory')
        with self.assertRaisesRegex(ValueError,'inventory missing'):independent.profile_source(self.job,state)
    def test_local_profile_original_change_is_detected(self):
        _,state=self.review();self.source.write_text(self.source.read_text(encoding='utf-8')+' ',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'unmanifested/changed'):independent.profile_source(self.job,state)

class PadPoints(unittest.TestCase):
    def record(self,shape='RECT',angle=0):
        # 20 raw fields, 4 x 2 legacy units, center (10,20). Hand-calculated
        # rectangular and capsule points; do not call the producer for fixtures.
        pts=[(-2,-1),(2,-1),(2,1),(-2,1)] if shape=='RECT' else [(-1,0),(1,0)]
        a=math.radians(angle);xy=[(10+u*math.cos(a)-v*math.sin(a),20+u*math.sin(a)+v*math.cos(a)) for u,v in pts]
        values=' '.join(str(v) for p in xy for v in p)
        return f'PAD~{shape}~10~20~4~2~1~~1~0~{values}~{angle}~p1~0~~Y~0~0~0.2~10,20'
    def both(self,record):
        a=easyeda_raw.raw_pads(data([record]));b=verify_upstream.expectation(data([record]))
        pin=record.split('~')[8];self.assertEqual(a[pin]['shape'],b[pin]['shape'])
    def reject(self,record):
        for fn in (easyeda_raw.raw_pads,verify_upstream.expectation):
            with self.subTest(reader=fn.__module__),self.assertRaisesRegex(ValueError,'point|finite'):fn(data([record]))
    def change(self,record,fn):
        f=record.split('~');points=list(map(float,f[10].split()));points=fn(points);f[10]=' '.join(map(str,points));return '~'.join(f)
    def test_non_square_rectangle_rotations_and_cyclic_orders(self):
        for angle in (0,90,180,270,-90,450):
            for shift in range(4):
                with self.subTest(angle=angle,shift=shift):
                    r=self.change(self.record(angle=angle),lambda p:p[shift*2:]+p[:shift*2]);self.both(r)
    def test_capsule_rotated_endpoints_and_reverse_order(self):
        for angle in (0,90,180,270):
            with self.subTest(angle=angle):
                self.both(self.record('OVAL',angle));self.both(self.change(self.record('OVAL',angle),lambda p:p[2:]+p[:2]))
    def test_vendor_rotated_pad_examples(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/easyeda_vendor_rotated_points.json').read_text(encoding='utf-8'))
        tested=0
        for record in fixture['pads']:
            f=record.split('~')
            if f[17]!='0':continue  # Negative paste is a separate unsupported feature.
            with self.subTest(pin=f[8],shape=f[1],angle=f[11]):self.both(record)
            tested+=1
        self.assertGreaterEqual(tested,15)
    def test_shifted_rectangle_points_rejected(self):
        self.reject(self.change(self.record(),lambda p:[v+2 if i%2==0 else v for i,v in enumerate(p)]))
    def test_shifted_oval_points_rejected(self):
        self.reject(self.change(self.record('OVAL'),lambda p:[v+2 if i%2==0 else v for i,v in enumerate(p)]))
    def test_crossed_and_duplicate_rectangle_corners_rejected(self):
        for fn in (lambda p:p[:2]+p[4:6]+p[2:4]+p[6:],lambda p:p[:6]+p[:2]):
            self.reject(self.change(self.record(),fn))
    def test_empty_point_cache_is_not_invented(self):
        f=self.record().split('~');f[10]='';self.both('~'.join(f))
    def test_closed_rectangle_point_cache_allowed(self):self.both(self.change(self.record(),lambda p:p+p[:2]))
    def test_point_count_and_nonfinite_rejected(self):
        for fn in (lambda p:p[:-1],lambda p:p+[1,2],lambda p:[float('nan')]+p[1:]):
            self.reject(self.change(self.record(),fn))
    def test_euclidean_point_tolerance_not_per_axis(self):
        for shape in ('RECT','OVAL'):
            with self.subTest(shape=shape):
                bad=self.change(self.record(shape),lambda p:[v+.00015/.254 if i<2 else v for i,v in enumerate(p)])
                self.reject(bad)  # sqrt(2)*0.00015 > 0.0002 mm.
                good=self.change(self.record(shape),lambda p:[v+.0001/.254 if i<2 else v for i,v in enumerate(p)])
                self.both(good)

class GraphicReleaseGate(unittest.TestCase):
    def copy_job(self):
        tmp=tempfile.TemporaryDirectory(prefix='图形发布门槛_');self.addCleanup(tmp.cleanup)
        job=Path(tmp.name)/'job';source=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job']);shutil.copytree(source,job)
        return job
    def test_online_release_requires_raw_graphic_inventory_check(self):
        job=self.copy_job();r=verify.load(job/'verification.json');r['checks'].pop('raw_easyeda_graphic_inventory',None);verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'Raw EasyEDA graphic inventory'):verify.require_verified(job)
    def test_profile_release_requires_original_graphic_check(self):
        job=self.copy_job();r=verify.load(job/'verification.json');r['checks'].pop('profile_source_graphics_safe',None);verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'Original profile graphics'):verify.require_verified(job)
    def test_graphic_reader_snapshots_are_mandatory_evidence(self):
        job=self.copy_job();r=verify.load(job/'verification.json')
        for k in list(r['evidence_sha256']):
            if k.replace('\\','/').endswith('/verify_graphics_source.py'):del r['evidence_sha256'][k]
        verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'mandatory acceptance evidence'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
