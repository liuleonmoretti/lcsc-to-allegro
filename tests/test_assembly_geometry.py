"""Keep all assembly geometry; small details may exceed a valid larger body."""
import math,shutil,sys,tempfile,unittest
from pathlib import Path
from shapely.geometry import Point,GeometryCollection,LineString,box
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify
from test_enclosure_exact import lines,rectangle,circle,pad,slanted


def rect_bounds(x1,y1,x2,y2,layer='F.Fab'):
    return lines([[x1,y1],[x2,y1],[x2,y2],[x1,y2]],layer)


def detail(a=(-1,0),b=(1,0)):
    return dict(kind='line',start=list(a),end=list(b),width=.1,layer=verify.LAYER['F.Fab'])


class AllAssemblyGeometry(unittest.TestCase):
    def graphics(self,extra):return rectangle(3,3)+rectangle(2,2,'F.Fab')+extra

    def disjoint(self):return rect_bounds(-4,-2,0,2)+rect_bounds(2,-1,4,1)

    def test_small_rectangle_outside_largest_body_is_not_ignored(self):
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([],self.graphics(rect_bounds(5,-.5,6,.5)))

    def test_small_circle_outside_largest_body_is_not_ignored(self):
        extra=dict(circle(.5,'F.Fab'),center=[5.5,0])
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([],self.graphics([extra]))

    def test_open_line_extension_is_not_discarded(self):
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([],self.graphics([detail((0,0),(6,0))]))

    def test_circle_with_internal_line_still_uses_exact_radius(self):
        graphics=lines(slanted(10-.00035))+[circle(10,'F.Fab'),detail()]
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([],graphics)

    def test_second_circle_also_uses_exact_radius(self):
        first=dict(circle(.5,'F.Fab'),center=[-12,0])
        graphics=lines(slanted(10-.00035))+[first,circle(10,'F.Fab'),detail()]
        with self.assertRaisesRegex(ValueError,'excludes body'):
            verify.check_placement_enclosure([],graphics)

    def test_valid_disjoint_body_is_centered_on_all_geometry(self):
        graphics=rectangle(5,3)+self.disjoint()+rectangle(4.5,2.5,'F.SilkS')
        result=verify.design_rules([pad(size=(.8,.8),at=(-2,0)) | {'drill':0}],graphics)
        self.assertEqual(result['body_bounds_mm'],[-4,-2,4,2])
        self.assertEqual(result['placement_enclosure']['assembly_object_count'],8)

    def test_disjoint_faces_are_supported_inside_circular_bound(self):
        result=verify.check_placement_enclosure([],self.disjoint()+[circle(6)])
        self.assertEqual(result['boundary_kind'],'circle')
        self.assertEqual(result['body_bounds_mm'],[-4,-2,4,2])

    def test_valid_open_detail_in_circular_bound(self):
        graphics=rectangle(1,1,'F.Fab')+[detail((0,0),(2,0)),circle(3)]
        self.assertEqual(verify.check_placement_enclosure([],graphics)['body_bounds_mm'],[-1,-1,2,1])

    def test_geometry_collection_farthest_vertex_checks_every_member(self):
        region=dict(region=Point(0,0).buffer(3),circle=dict(center=[0,0],radius=3))
        self.assertFalse(verify.core_inside_domain(region,GeometryCollection([box(-1,-1,1,1),Point(4,0)])))
        self.assertTrue(verify.core_inside_domain(region,GeometryCollection([box(-1,-1,1,1),Point(2,0)])))

    def test_empty_geometry_cannot_pass_circle_enclosure(self):
        self.assertFalse(verify.core_inside_domain(dict(region=None,circle=dict(center=[0,0],radius=3)),GeometryCollection()))

    def test_nested_contours_and_internal_detail_preserve_outer_envelope(self):
        graphics=rectangle(3,3)+rectangle(2,2,'F.Fab')+rectangle(1,1,'F.Fab')+[detail()]
        region=verify.body_region(graphics)
        self.assertEqual(region.area,16)
        self.assertEqual(verify.check_placement_enclosure([],graphics)['body_bounds_mm'],[-2,-2,2,2])

    def test_overlapping_faces_use_union_not_largest_polygonized_face(self):
        graphics=rectangle(4,3)+rect_bounds(-3,-2,1,2)+rect_bounds(-1,-1,3,1)
        body=verify.body_region(graphics)
        self.assertEqual(body.area,20)
        self.assertEqual(verify.check_placement_enclosure([],graphics)['body_bounds_mm'],[-3,-2,3,2])

    def test_open_detail_crossing_notch_is_rejected_with_endpoints_inside(self):
        court=[[-3,-3],[3,-3],[3,3],[1,3],[1,1],[-1,1],[-1,3],[-3,3]]
        graphics=lines(court)+rect_bounds(-.5,-2,.5,-1)+[detail((-2,2),(2,2))]
        with self.assertRaisesRegex(ValueError,'excludes body'):verify.check_placement_enclosure([],graphics)

    def test_only_open_assembly_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'Missing or open contour'):
            verify.check_placement_enclosure([],rectangle(3,3)+[detail()])

    def test_extra_detail_changes_center_even_if_largest_body_is_centered(self):
        graphics=rectangle(4,3)+rectangle(2,2,'F.Fab')+[detail((0,0),(3,0))]+rectangle(2.3,2.3,'F.SilkS')
        with self.assertRaisesRegex(ValueError,'Assembly geometry bounding-box center'):
            verify.design_rules([],graphics)


class AssemblyRelease(unittest.TestCase):
    def test_old_report_needs_all_assembly_reverification(self):
        original=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job'])
        with tempfile.TemporaryDirectory(prefix='全部装配验收_') as folder:
            job=Path(folder)/'job';shutil.copytree(original,job);verify.require_verified(job)
            report=verify.load(job/'verification.json');report['checks'].pop('assembly_all_geometry_enclosure');verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'All assembly geometry'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
