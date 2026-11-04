"""Enclosure goldens cover curved pads, large circular bounds and concavities."""
import copy,math,shutil,sys,tempfile,unittest
from pathlib import Path
from shapely.geometry import Point,LineString,Polygon,box
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify


def lines(points,layer='F.CrtYd'):
    return [dict(kind='line',start=a,end=b,width=.05,layer=verify.LAYER[layer]) for a,b in zip(points,points[1:]+points[:1])]
def rectangle(a=2,b=2,layer='F.CrtYd'):return lines([[-a,-b],[a,-b],[a,b],[-a,b]],layer)
def circle(radius,layer='F.CrtYd'):return dict(kind='circle',center=[0,0],radius=radius,width=.05,layer=verify.LAYER[layer])
def pad(shape='circle',size=(2,2),at=(0,0)):
    return dict(number='1',shape=shape,size=list(size),at=list(at),angle=0,drill=1,mask=0,type='thru_hole',layers=['*.Cu','*.Mask'])
def shape(points):
    return dict(layer=verify.LAYER['F.CrtYd'],bounds=list(Polygon(points).bounds),voids=0,segment_count=len(points),
                segments=[dict(kind='line',start=a,end=b) for a,b in zip(points,points[1:]+points[:1])])
def slanted(right,left=-16):
    theta=math.pi/512;n=(math.cos(theta),math.sin(theta));t=(-n[1],n[0])
    return [[u*n[0]+v*t[0],u*n[1]+v*t[1]] for u,v in ((left,-11),(right,-11),(right,11),(left,11))]


class Enclosure(unittest.TestCase):
    def test_circle_protrusion_between_polygon_vertices_is_rejected(self):
        graphics=lines(slanted(10-.00035))+rectangle(1,1,'F.Fab')
        with self.assertRaisesRegex(ValueError,'excludes pads'):verify.check_placement_enclosure([pad(size=(20,20))],graphics)

    def test_capsule_end_protrusion_uses_true_round_radius(self):
        right=10+5*math.cos(math.pi/512)-.00035
        with self.assertRaisesRegex(ValueError,'excludes pads'):
            verify.check_placement_enclosure([pad('oval',(30,20))],lines(slanted(right))+rectangle(1,1,'F.Fab'))

    def test_round_body_protrusion_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([pad()],lines(slanted(10-.00035))+[circle(10,'F.Fab')])

    def test_valid_square_near_large_circle_is_not_rejected_by_chord(self):
        theta=math.pi/4+math.pi/512;at=((50-.0004)*math.cos(theta)-.9,(50-.0004)*math.sin(theta)-.9)
        result=verify.check_placement_enclosure([pad('rect',(1.8,1.8),at)],rectangle(1,1,'F.Fab')+[circle(50)])
        self.assertEqual(result['boundary_kind'],'circle')

    def test_square_outside_true_large_circle_is_rejected(self):
        theta=math.pi/4+math.pi/512;at=((50+.0003)*math.cos(theta)-.9,(50+.0003)*math.sin(theta)-.9)
        with self.assertRaisesRegex(ValueError,'excludes pads'):
            verify.check_placement_enclosure([pad('rect',(1.8,1.8),at)],rectangle(1,1,'F.Fab')+[circle(50)])

    def test_circle_in_circle_uses_center_offset_plus_radius(self):
        domain=verify.enclosure_domain([circle(10)])
        self.assertTrue(verify.core_inside_domain(domain,Point(3,4),5))
        self.assertFalse(verify.core_inside_domain(domain,Point(3,4),5.001))

    def test_circle_tolerance_boundary_is_not_expanded(self):
        domain=verify.enclosure_domain([circle(10)])
        self.assertTrue(verify.core_inside_domain(domain,Point(0,0),10+verify.TOL-1e-8))
        self.assertFalse(verify.core_inside_domain(domain,Point(0,0),10+verify.TOL+1e-8))

    def test_rectangle_crossing_notch_rejected_even_with_corners_inside(self):
        region=Polygon([[-3,-3],[3,-3],[3,3],[1,3],[1,1],[-1,1],[-1,3],[-3,3]])
        core=box(-2,0,2,2)
        self.assertTrue(all(region.covers(Point(v)) for v in core.exterior.coords))
        self.assertFalse(verify.core_inside_domain(dict(region=region,circle=None),core))

    def test_capsule_crossing_notch_is_rejected(self):
        region=Polygon([[-3,-3],[3,-3],[3,3],[1,3],[1,1],[-1,1],[-1,3],[-3,3]])
        self.assertFalse(verify.core_inside_domain(dict(region=region,circle=None),LineString([[-2,2],[2,2]]),.2))
        self.assertTrue(verify.core_inside_domain(dict(region=region,circle=None),LineString([[-2,-1],[2,-1]]),.9))

    def test_reversed_shuffled_edges_preserve_enclosure(self):
        g=rectangle()+rectangle(1,1,'F.Fab');original=verify.check_placement_enclosure([pad()],g)
        g.reverse()
        for item in g:item['start'],item['end']=item['end'],item['start']
        self.assertEqual(verify.check_placement_enclosure([pad()],g),original)

    def test_actual_shape_offset_is_not_replaced_by_courtyard_line(self):
        g=rectangle(1,2)+rectangle(.5,.5,'F.Fab');p=pad(at=(.0001,0))
        verify.check_placement_enclosure([p],g)
        actual=[shape([[-1,-2],[.9998,-2],[.9998,2],[-1,2]])]
        verify.compare_placement_boundary(g,actual)
        with self.assertRaisesRegex(ValueError,'excludes pads'):verify.check_placement_enclosure([p],g,actual)

    def test_actual_circle_radius_is_used(self):
        g=[circle(1)]+rectangle(.5,.5,'F.Fab');p=pad(at=(.0001,0))
        verify.check_placement_enclosure([p],g)
        r=.9998;actual=[dict(layer=verify.LAYER['F.CrtYd'],bounds=[-r,-r,r,r],voids=0,segment_count=1,
            segments=[dict(kind='arc',start=[r,0],end=[r,0],center=[0,0],radius=r,clockwise=False,circle=True)])]
        verify.compare_placement_boundary(g,actual)
        with self.assertRaisesRegex(ValueError,'excludes pads'):verify.check_placement_enclosure([p],g,actual)

    def test_all_physical_copper_layers_are_enclosed(self):
        g=rectangle(1,2)+rectangle(.5,.5,'F.Fab');drawing=dict(graphics=g,bounds=[shape([[-1,-2],[1,-2],[1,2],[-1,2]])])
        top=pad(size=(1.9998,1.9998),at=(.0002,0));inner=pad(size=(2.0002,2.0002),at=(.0002,0))
        verify.native_enclosure({'TOP':[top]},drawing)
        with self.assertRaisesRegex(ValueError,'excludes pads'):verify.native_enclosure({'TOP':[top],'INNER':[inner]},drawing)

    def test_sub_tolerance_radius_does_not_force_center_inside(self):
        domain=dict(region=box(-1,-1,1,1),circle=None)
        self.assertTrue(verify.core_inside_domain(domain,Point(1.0001,0),.00001))
        self.assertFalse(verify.core_inside_domain(domain,Point(1.001,0),.00001))


class TestBoardExtents(unittest.TestCase):
    def drawing(self):return dict(graphics=[circle(50)],texts=[dict(bounds=[-.5,50.5,.5,52])],bounds=[dict(bounds=[-50,-50,50,50])],pins=[])
    def test_rotated_large_label_fits_without_shrinking_footprint(self):
        d=self.drawing();limit=verify.test_drawing_limit(d,{'pads':[]})
        # The saved 270-degree symbol is now at x=120; its label reaches x=172.
        self.assertEqual(limit,182);self.assertGreaterEqual(limit,120+52+10)

    def test_large_pin_and_mask_extent_are_included(self):
        d=self.drawing();d['pins']=[dict(number='1',at=[80,0],angle=0)]
        model={'pads':[dict(number='1',size=[30,40],mask=2)]}
        self.assertGreaterEqual(verify.test_drawing_limit(d,model),80+25+2+120+10)

    def test_unknown_pin_is_not_sized_from_a_default(self):
        d=self.drawing();d['pins']=[dict(number='1',at=[0,0],angle=0)]
        with self.assertRaisesRegex(ValueError,'Unknown native pin'):verify.test_drawing_limit(d,{'pads':[]})

    def test_nonfinite_bounds_are_rejected(self):
        d=self.drawing();d['texts'][0]['bounds'][0]=float('inf')
        with self.assertRaisesRegex(ValueError,'Invalid test drawing'):verify.test_drawing_limit(d,{'pads':[]})


class EnclosureRelease(unittest.TestCase):
    def test_legacy_enclosure_requires_actual_native_reverification(self):
        original=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job'])
        with tempfile.TemporaryDirectory(prefix='占位包络发布_') as d:
            job=Path(d)/'job';shutil.copytree(original,job);verify.require_verified(job)
            report=verify.load(job/'verification.json');report['checks'].pop('native_placement_enclosure');verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'Actual native placement enclosure'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
