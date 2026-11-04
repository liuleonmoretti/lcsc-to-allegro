"""Goldens for actual native copper, hole and mask dimensions after rounding."""
import copy,math,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify


def pad(number='1',at=(0,0),diameter=1.8,drill=1):
    return dict(number=number,type='thru_hole',shape='circle',size=[diameter,diameter],at=list(at),
                angle=0,drill=drill,mask=0,layers=['*.Cu','*.Mask'])


def definitions(diameter=1.8,drill=1):
    geom=dict(shape='CIRCLE',size=[diameter,diameter],offset=[0,0])
    return {'a':dict(geometry={k:copy.deepcopy(geom) for k in ('TOP','BOTTOM','internal_pad_def','~TSM','~BSM')},
                     drill=dict(diameter=drill,offset=[0,0]))}


def layers(pads,defs,pins=None):
    model={'pads':[dict(number=p['number'],padstack='a') for p in pads]}
    if pins is None:pins=[dict(number=p['number'],at=p['at'],angle=p['angle']) for p in pads]
    return verify.native_pad_layers(pads,pins,model,defs)


def silk(x=4):return [dict(kind='line',layer=verify.LAYER['F.SilkS'],start=[x,-2],end=[x,2],width=.15)]


class ActualGeometry(unittest.TestCase):
    def test_source_gap_passes_but_actual_rounded_gap_fails(self):
        source=[pad(at=(.000051,0),diameter=1.80006),pad('2',(1.949949,0),1.80006)]
        self.assertAlmostEqual(verify.copper_metrics(source)['copper_clearance_mm'],.149838)
        pins=[dict(number='1',at=[.0001,0],angle=0),dict(number='2',at=[1.9499,0],angle=0)]
        actual=layers(source,definitions(1.8001),pins)
        self.assertAlmostEqual(verify.copper_gap(*actual['TOP']),.1497)
        with self.assertRaisesRegex(ValueError,'Copper spacing below'):verify.native_layer_rules(actual,silk())

    def test_source_ring_passes_but_actual_rounded_ring_fails(self):
        source=[pad(diameter=1.999949,drill=1.700351)]
        self.assertAlmostEqual(verify.copper_metrics(source)['minimum_annular_ring_mm'],.149799)
        actual=layers(source,definitions(1.9999,1.7004))
        self.assertAlmostEqual(verify.annular_ring(actual['TOP'][0]),.14975)
        with self.assertRaisesRegex(ValueError,'Annular ring below'):verify.native_layer_rules(actual,silk())

    def test_source_silk_passes_but_actual_rounded_mask_fails(self):
        source=[pad(at=(.000051,0),diameter=1.80006)]
        self.assertAlmostEqual(verify.clearance_metrics(source,silk(1.1249))['silk_to_mask_mm'],.149819)
        actual=layers(source,definitions(1.8001),[dict(number='1',at=[.0001,0],angle=0)])
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk(1.1249)[0],actual['TOP'][0]),.14975)
        with self.assertRaisesRegex(ValueError,'Silkscreen-to-mask clearance below'):verify.native_layer_rules(actual,silk(1.1249))

    def test_copper_mask_and_hole_offsets_rotate_about_the_pin(self):
        data=definitions();data['a']['geometry']['TOP']['offset']=[.5,.6]
        data['a']['geometry']['~TSM']['offset']=[.1,.2];data['a']['drill']['offset']=[.3,-.4]
        p=layers([pad()],data,[dict(number='1',at=[10,20],angle=90)])['TOP'][0]
        for a,b in zip(p['at'],[9.4,20.5]):self.assertAlmostEqual(a,b)
        for a,b in zip(p['top_mask']['at'],[9.8,20.1]):self.assertAlmostEqual(a,b)
        for a,b in zip(p['hole_at'],[10.4,20.3]):self.assertAlmostEqual(a,b)

    def test_mask_uses_separate_x_and_y_dimensions(self):
        data=definitions();data['a']['geometry']['~TSM']=dict(shape='RECTANGLE',size=[2.2,1.9],offset=[.05,-.1])
        p=layers([pad()],data)['TOP'][0]
        self.assertEqual(verify.mask_pad(p)['size'],[2.2,1.9])
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk(2)[0],p),.775)

    def test_explicit_absent_mask_does_not_fall_back_to_source(self):
        data=definitions();data['a']['geometry']['~TSM']=None;p=layers([pad()],data)['TOP'][0]
        self.assertFalse(verify.has_top_mask(p))
        with self.assertRaisesRegex(ValueError,'Missing actual top mask'):verify.mask_pad(p)

    def test_circle_ring_accounts_for_hole_offset(self):
        p=dict(pad(diameter=2),hole_at=[.1,0]);self.assertAlmostEqual(verify.annular_ring(p),.4)

    def test_rectangle_ring_uses_nearest_edge(self):
        p=dict(pad(drill=.5),shape='rect',size=[4,2],hole_at=[1.2,.3])
        self.assertAlmostEqual(verify.annular_ring(p),.45)

    def test_capsule_ring_uses_distance_from_rounded_end(self):
        p=dict(pad(drill=.4),shape='oval',size=[4,2],hole_at=[1.3,.4])
        self.assertAlmostEqual(verify.annular_ring(p),.3)

    def test_hole_outside_copper_has_negative_ring(self):
        p=dict(pad(diameter=2),hole_at=[1.1,0]);self.assertAlmostEqual(verify.annular_ring(p),-.6)
        p['shape']='rect';self.assertAlmostEqual(verify.annular_ring(p),-.6)

    def test_only_inner_layer_ring_failure_blocks_acceptance(self):
        data=definitions(1.2996);data['a']['geometry']['IN1']=dict(shape='CIRCLE',size=[1.2994,1.2994],offset=[0,0])
        actual=layers([pad(diameter=1.2996)],data)
        verify.copper_metrics(actual['TOP'])
        with self.assertRaisesRegex(ValueError,'Native IN1: Annular ring below'):verify.native_layer_rules(actual,silk())

    def test_default_inner_geometry_is_used_on_other_pad_explicit_layer(self):
        data=definitions();data['b']=copy.deepcopy(data['a']);data['a']['geometry']['IN1']=copy.deepcopy(data['a']['geometry']['TOP'])
        source=[pad(),pad('2',(4,0))];model={'pads':[dict(number='1',padstack='a'),dict(number='2',padstack='b')]}
        pins=[dict(number=p['number'],at=p['at'],angle=0) for p in source]
        actual=verify.native_pad_layers(source,pins,model,data)
        self.assertEqual([p['number'] for p in actual['IN1']],['1','2'])
        self.assertAlmostEqual(verify.copper_gap(*actual['IN1']),2.2)

    def test_surface_pad_does_not_acquire_default_inner_copper(self):
        source=[dict(pad(drill=0),type='smd',layers=['F.Cu','F.Mask'])];data=definitions(drill=0);data['a']['drill']=None
        data['a']['geometry']['BOTTOM']=None;data['a']['geometry']['internal_pad_def']=None
        actual=layers(source,data);self.assertEqual(actual['BOTTOM'],[]);self.assertEqual(actual['internal_pad_def'],[])
        self.assertIsNone(verify.copper_metrics(actual['TOP'])['minimum_annular_ring_mm'])

    def test_native_copper_offsets_change_clearance(self):
        source=[pad(),pad('2',(2,0))];data=definitions();data['b']=copy.deepcopy(data['a'])
        data['b']['geometry']['TOP']['offset']=[-.1,0]
        model={'pads':[dict(number='1',padstack='a'),dict(number='2',padstack='b')]}
        actual=verify.native_pad_layers(source,[dict(number=p['number'],at=p['at'],angle=0) for p in source],model,data)
        self.assertAlmostEqual(verify.copper_gap(*actual['TOP']),.1)

    def test_invalid_actual_round_geometry_is_rejected(self):
        data=definitions();data['a']['geometry']['TOP']['size'][1]+= .0001
        with self.assertRaisesRegex(ValueError,'asymmetric'):layers([pad()],data)
        data=definitions();data['a']['drill']['diameter']=float('nan')
        with self.assertRaisesRegex(ValueError,'actual hole'):layers([pad()],data)

    def test_missing_and_duplicate_native_pins_are_rejected(self):
        for pins in ([],[dict(number='1',at=[0,0],angle=0)]*2):
            with self.assertRaisesRegex(ValueError,'missing/duplicate'):layers([pad()],definitions(),pins)

    def test_compiled_label_clearance_uses_actual_world_mask(self):
        source=[pad(diameter=1.80006)];actual=layers(source,definitions(1.8001),[dict(number='1',at=[.0001,0],angle=0)])['TOP']
        text=dict(text='J*',at=[1.0499,0],bounds=[1.0499,-1,2.1,1],font_width=.5,font_height=.8,stroke=.12,angle=0,mirrored=False)
        labels=[dict(text,layer='REF DES/'+layer) for layer in ('SILKSCREEN_TOP','ASSEMBLY_TOP')]
        outlines=[dict(kind='line',start=[-3,-5],end=[3,-5],width=.1,layer=verify.LAYER['F.SilkS'])]
        verify.compare_refdes(labels,labels,source,outlines)
        with self.assertRaisesRegex(ValueError,'Compiled RefDes-to-mask clearance below'):
            verify.compare_refdes(labels,labels,source,outlines,actual_pads=actual)


class ActualNativeEvidence(unittest.TestCase):
    def setUp(self):
        self.job=Path(verify.load(lc2a.ROOT/'work/pth_v2.json')['job']);self.state,self.model=verify.check_build(self.job)
        self.pads,self.graphics=verify.read_expected(self.job,self.state)
        self.drawing=verify.read_native(self.job/'verification/native/drawing.tsv')

    def test_pad_def_and_full_raw_summary_both_supply_real_geometry(self):
        raw=verify.check_raw_pads(self.pads,self.model,self.job/'verification/native/raw_pads')
        embedded=verify.pad_definitions(self.job/'verification/native/drawing_pads.txt',self.model)
        for data in (raw,embedded):
            actual=verify.native_pad_layers(self.pads,self.drawing['pins'],self.model,data)
            self.assertEqual(actual['TOP'][0]['size'],[1.8,1.8]);self.assertEqual(actual['TOP'][0]['drill'],1.)
            self.assertEqual(actual['TOP'][0]['top_mask']['size'],[1.9,1.9])
            self.assertAlmostEqual(verify.copper_gap(*actual['TOP']),6.9)
            self.assertAlmostEqual(verify.annular_ring(actual['BOTTOM'][0]),.4)

    def test_actual_placement_clearances_survive_rotation_and_move(self):
        data=verify.pad_definitions(self.job/'verification/native/disk_pads.txt',self.model)
        for name in ('placed0.tsv','placed90.tsv','moved.tsv'):
            read=verify.read_native(self.job/'verification/native'/name)
            actual=verify.native_pad_layers(self.pads,read['pins'],self.model,data)
            metrics=verify.native_layer_rules(actual,read['graphics'])
            self.assertAlmostEqual(metrics['TOP']['copper_clearance_mm'],6.9)
            self.assertAlmostEqual(metrics['TOP']['minimum_annular_ring_mm'],.4)


class ActualGeometryRelease(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='实际尺寸发布_');self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name)/'job';original=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job'])
        shutil.copytree(original,self.job);verify.require_verified(self.job)

    def missing(self,key,message):
        report=verify.load(self.job/'verification.json');report['checks'].pop(key);verify.save(self.job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,message):verify.require_verified(self.job)

    def test_legacy_polygon_clearance_report_requires_reverification(self):
        self.missing('analytic_curved_clearance','Analytic curved clearance')

    def test_legacy_source_pad_clearance_report_requires_reverification(self):
        self.missing('native_actual_clearance','Actual native pad/hole/mask clearance')


if __name__=='__main__':unittest.main()
