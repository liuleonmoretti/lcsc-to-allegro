"""Geometry goldens exercise round features without polygon-distance oracles."""
import copy,math,sys,unittest
from pathlib import Path
from shapely.geometry import box
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

def pad(shape='circle',size=(2,2),at=(0,0),angle=0,mask=0):
    return dict(number='1',shape=shape,size=list(size),at=list(at),angle=angle,mask=mask,drill=0,
                type='smd',layers=['F.Cu','F.Mask'])
def rectangle(layer,a,b):
    pts=[(-a,-b),(a,-b),(a,b),(-a,b)]
    return [dict(kind='line',layer=verify.LAYER[layer],start=list(pts[i]),end=list(pts[(i+1)%4]),width=.1) for i in range(4)]
def outlines(silk=None):
    return rectangle('F.Fab',40,15)+rectangle('F.CrtYd',42,17)+(silk or rectangle('F.SilkS',41,16))
def silk_circle(r=3,width=.2):return dict(kind='circle',center=[0,0],radius=r,width=width,layer=verify.LAYER['F.SilkS'])

class AnalyticClearance(unittest.TestCase):
    def test_round_pad_gap_is_independent_of_polygon_vertex_direction(self):
        for diameter in (.3,2,20,80):
            for i in range(64):
                angle=(i+.5)*math.pi/32;distance=diameter+.1495
                a=pad(size=(diameter,diameter));b=pad(size=(diameter,diameter),at=(distance*math.cos(angle),distance*math.sin(angle)))
                self.assertAlmostEqual(verify.copper_gap(a,b),.1495,places=10)
    def test_horizontal_capsule_endcaps_use_true_radius(self):
        theta=math.pi/512;distance=20+.1495
        a=pad('oval',(30,20));b=pad('oval',(30,20),(10+distance*math.cos(theta),distance*math.sin(theta)))
        self.assertAlmostEqual(verify.copper_gap(a,b),.1495,places=10)
    def test_vertical_and_rotated_capsules_are_equivalent(self):
        a=pad('oval',(30,20));b=pad('oval',(30,20),(0,20.37))
        c=pad('oval',(20,30),angle=90);d=pad('oval',(20,30),(0,20.37),angle=90)
        self.assertAlmostEqual(verify.copper_gap(a,b),.37);self.assertAlmostEqual(verify.copper_gap(c,d),.37)
    def test_rectangle_circle_corner_distance(self):
        self.assertAlmostEqual(verify.copper_gap(pad('rect',(2,4)),pad(at=(4,6))),4.)
    def test_rectangle_rotation_changes_edge_distance(self):
        self.assertAlmostEqual(verify.copper_gap(pad('rect',(2,4)),pad('rect',(2,4),(5,0),90)),2.)
    def test_capsule_rectangle_gap(self):
        self.assertAlmostEqual(verify.copper_gap(pad('oval',(6,2)),pad('rect',(2,2),(6,0))),2.)
    def test_overlapping_shapes_have_zero_gap(self):
        for shape in ('circle','rect','oval'):
            for other in ('circle','rect','oval'):
                self.assertEqual(verify.copper_gap(pad(shape),pad(other,at=(.5,0))),0.)
    def test_mask_growth_and_shrink_change_gap_by_explicit_margin(self):
        line=dict(kind='line',start=[1.25,-2],end=[1.25,2],width=.1)
        for margin,wanted in ((-.1,.3),(0,.2),(.1,.1)):
            self.assertAlmostEqual(verify.silk_to_pad_gap(line,pad(mask=margin)),wanted)
    def test_circle_silk_surrounding_pad_is_not_treated_as_filled_disk(self):
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk_circle(),pad()),1.9)
    def test_pad_containing_silk_circle_intersects_it(self):
        self.assertEqual(verify.silk_to_pad_gap(silk_circle(),pad(size=(8,8))),0.)
    def test_circle_silk_outside_pad_radial_gap(self):
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk_circle(),pad(at=(7,0))),2.9)
    def test_circle_silk_crosses_pad(self):
        self.assertEqual(verify.silk_to_pad_gap(silk_circle(),pad(at=(3,0))),0.)
    def test_circle_silk_surrounding_rectangle_uses_farthest_corner(self):
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk_circle(),pad('rect')),3-math.sqrt(2)-.1)
    def test_circle_silk_surrounding_capsule_uses_farthest_endcap(self):
        self.assertAlmostEqual(verify.silk_to_pad_gap(silk_circle(5),pad('oval',(6,2))),1.9)
    def test_line_endpoint_to_circle_includes_half_stroke(self):
        line=dict(kind='line',start=[0,0],end=[0,1],width=.2)
        self.assertAlmostEqual(verify.silk_to_pad_gap(line,pad(at=(3,4))),math.sqrt(18)-1-.1)
    def test_refdes_box_to_circle_uses_actual_round_edge(self):
        self.assertAlmostEqual(verify.solid_to_pad_gap(box(4,6,5,7),pad()),math.sqrt(52)-1)
    def test_four_native_regressions_rejected_by_source_geometry(self):
        theta=math.pi/512;c,s=math.cos(theta),math.sin(theta)
        for shape,w in (('circle',20),('oval',30)):
            dx=(w-20)+(20+.1495)*c;dy=(20+.1495)*s
            pads=[pad(shape,(w,20),(-dx/2,-dy/2)),pad(shape,(w,20),(dx/2,dy/2))]
            with self.assertRaisesRegex(ValueError,'Copper spacing below'):verify.design_rules(pads,outlines())
        d=15+.075+.1496
        with self.assertRaisesRegex(ValueError,'Silkscreen-to-mask'):
            verify.design_rules([pad(size=(20,20),at=(d*c,d*s))],outlines([silk_circle(5,.15)]))
        d=10+.075+.1497;line=dict(kind='line',start=[d*c-2*s,d*s+2*c],end=[d*c+2*s,d*s-2*c],width=.15,layer=verify.LAYER['F.SilkS'])
        with self.assertRaisesRegex(ValueError,'Silkscreen-to-mask'):verify.design_rules([pad(size=(20,20))],outlines([line]))
    def test_control_above_threshold_still_passes(self):
        theta=math.pi/512;d=20+.151
        pads=[pad(size=(20,20),at=(-d*math.cos(theta)/2,-d*math.sin(theta)/2)),pad(size=(20,20),at=(d*math.cos(theta)/2,d*math.sin(theta)/2))]
        result=verify.design_rules(pads,outlines());self.assertEqual(result['copper_clearance_mm'],.151)
    def test_exact_boundary_does_not_silently_expand_tolerance(self):
        threshold=.15-verify.TOL
        for delta,ok in ((-1e-8,False),(1e-8,True)):
            pads=[pad(at=(-1-threshold/2-delta/2,0)),pad(at=(1+threshold/2+delta/2,0))]
            if ok:verify.design_rules(pads,outlines())
            else:
                with self.assertRaisesRegex(ValueError,'Copper spacing below'):verify.design_rules(pads,outlines())
    def test_current_reviewed_parts_pass_exact_rules(self):
        for key in ('f103','pth'):
            job=Path(verify.load(lc2a.ROOT/'work'/(key+'_v2.json'))['job']);s=verify.load(job/'status.json');p,g=verify.read_expected(job,s)
            result=verify.design_rules(p,g);self.assertEqual(result['clearance_method'],'analytic_capsule_v1')

if __name__=='__main__':unittest.main()
