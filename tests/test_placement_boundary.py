"""Compare occupied areas, not just their identical outside bounding boxes."""
import copy,math,shutil,sys,tempfile,unittest
from pathlib import Path
from shapely.geometry import Polygon
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

SQUARE=[[-5,-5],[5,-5],[5,5],[-5,5]]
def graphics(points=SQUARE):
    return [dict(kind='line',layer=verify.LAYER['F.CrtYd'],width=.05,start=a,end=b) for a,b in zip(points,points[1:]+points[:1])]
def bound(points=SQUARE):
    return dict(layer=verify.LAYER['F.CrtYd'],bounds=list(Polygon(points).bounds),voids=0,segment_count=len(points),segments=[dict(kind='line',start=a,end=b) for a,b in zip(points,points[1:]+points[:1])])
def model_graphics(gs):return [dict(g,layer='F.CrtYd') for g in copy.deepcopy(gs)]

class BoundaryGeometry(unittest.TestCase):
    def test_triangle_with_same_box_is_rejected(self):
        bad=bound([[-5,-5],[5,-5],[0,5]])
        self.assertEqual(bad['bounds'],bound()['bounds'])
        with self.assertRaisesRegex(ValueError,'geometry differs'):verify.compare_placement_boundary(graphics(),[bad])
    def test_concave_shape_is_not_replaced_by_box(self):
        pts=[[-5,-5],[5,-5],[5,5],[2,5],[2,3],[-2,3],[-2,5],[-5,5]];g=graphics(pts)
        verify.compare_placement_boundary(g,[bound(pts)])
        with self.assertRaisesRegex(ValueError,'geometry differs'):verify.compare_placement_boundary(g,[bound()])
    def test_same_area_same_box_different_region_rejected(self):
        a=[[-5,-5],[5,-5],[5,5],[2,5],[2,3],[-2,3],[-2,5],[-5,5]]
        b=[[-5,-5],[-2,-5],[-2,-3],[2,-3],[2,-5],[5,-5],[5,5],[-5,5]]
        self.assertEqual(Polygon(a).area,Polygon(b).area)
        with self.assertRaisesRegex(ValueError,'geometry differs'):verify.compare_placement_boundary(graphics(a),[bound(b)])
    def test_circle_does_not_become_rectangle(self):
        g=[dict(kind='circle',center=[0,0],radius=5,width=.05,layer=verify.LAYER['F.CrtYd'])]
        with self.assertRaisesRegex(ValueError,'geometry differs'):verify.compare_placement_boundary(g,[bound()])
    def test_void_is_rejected_even_when_outer_shape_matches(self):
        b=bound();b['voids']=1
        with self.assertRaisesRegex(ValueError,'unexpected voids'):verify.compare_placement_boundary(graphics(),[b])
    def test_source_dangling_edge_rejected_by_both_implementations(self):
        g=graphics()+[dict(kind='line',layer=verify.LAYER['F.CrtYd'],width=.05,start=[5,5],end=[6,5])]
        with self.assertRaisesRegex(ValueError,'closed non-branching'):verify.source_placement_region(g)
        with self.assertRaisesRegex(ValueError,'Open or branching'):lc2a.placement_contour(model_graphics(g))
    def test_duplicate_edge_rejected_by_both_implementations(self):
        g=graphics();g.append(copy.deepcopy(g[0]))
        with self.assertRaisesRegex(ValueError,'duplicated/overlapping'):verify.source_placement_region(g)
        with self.assertRaisesRegex(ValueError,'Duplicate'):lc2a.placement_contour(model_graphics(g))
    def test_disconnected_contours_rejected_by_both(self):
        g=graphics()+graphics([[10,10],[12,10],[12,12],[10,12]])
        with self.assertRaisesRegex(ValueError,'one closed'):verify.source_placement_region(g)
        with self.assertRaisesRegex(ValueError,'Disconnected'):lc2a.placement_contour(model_graphics(g))
    def test_self_intersection_rejected_by_both(self):
        g=graphics([[-5,-5],[5,5],[5,-5],[-5,5]])
        with self.assertRaisesRegex(ValueError,'one closed'):verify.source_placement_region(g)
        with self.assertRaisesRegex(ValueError,'Self-intersecting'):lc2a.placement_contour(model_graphics(g))
    def test_shuffled_and_reversed_segments_preserve_region(self):
        g=graphics();g=[g[i] for i in (2,0,3,1)]
        for line in g:line['start'],line['end']=line['end'],line['start']
        c=lc2a.placement_contour(model_graphics(g));self.assertEqual(Polygon(c['points']).area,100)
        verify.compare_placement_boundary(g,[bound()])
    def test_split_collinear_segments_preserve_region(self):
        b=bound([[-5,-5],[0,-5],[5,-5],[5,5],[-5,5]])
        self.assertEqual(verify.compare_placement_boundary(graphics(),[b])['maximum_boundary_error_mm'],0)
    def test_rotated_translated_region(self):
        points=[[-5,-5],[5,-5],[5,5],[2,5],[2,3],[-2,3],[-2,5],[-5,5]]
        for degrees in (0,90,180,270):
            angle=math.radians(degrees);moved=[[30+x*math.cos(angle)-y*math.sin(angle),35+x*math.sin(angle)+y*math.cos(angle)] for x,y in points]
            with self.subTest(rotation=degrees):verify.compare_placement_boundary(graphics(points),[bound(moved)],(30,35),degrees)
    def test_circle_and_quarter_arc_readbacks(self):
        g=[dict(kind='circle',center=[0,0],radius=3.5,width=.05,layer=verify.LAYER['F.CrtYd'])]
        whole=dict(layer=verify.LAYER['F.CrtYd'],bounds=[-3.5,-3.5,3.5,3.5],voids=0,segment_count=1,
                   segments=[dict(kind='arc',start=[3.5,0],end=[3.5,0],center=[0,0],radius=3.5,clockwise=False,circle=True)])
        verify.compare_placement_boundary(g,[whole])
        for clockwise in (False,True):
            points=[[3.5,0],[0,3.5],[-3.5,0],[0,-3.5]]
            if clockwise:points.reverse()
            segments=[dict(kind='arc',start=a,end=b,center=[0,0],radius=3.5,clockwise=clockwise,circle=False) for a,b in zip(points,points[1:]+points[:1])]
            part=dict(whole,segments=segments,segment_count=4);verify.compare_placement_boundary(g,[part])
    def test_arc_radius_must_agree_with_endpoints(self):
        b=bound();b['segments'][0]=dict(kind='arc',start=[-5,-5],end=[5,-5],center=[0,0],radius=4,clockwise=False,circle=False)
        with self.assertRaisesRegex(ValueError,'radius/endpoints'):verify.compare_placement_boundary(graphics(),[b])
    def test_report_bounds_do_not_replace_edge_geometry(self):
        b=bound();b['bounds']=[-4,-5,5,5]
        with self.assertRaisesRegex(ValueError,'bounds disagree'):verify.compare_placement_boundary(graphics(),[b])

class BoundaryReadback(unittest.TestCase):
    def read(self,body):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'native.tsv';path.write_text(body,encoding='ascii');return verify.read_native(path)
    def test_truncated_edge_record_and_count_are_rejected(self):
        header='B\tPACKAGE GEOMETRY/PLACE_BOUND_TOP\t0\t0\t2\t2\t0\t4\n'
        for tail in ('','BL\t0\t0\t2\t0\n','BL\t0\t0\t2\t0\nBE\n','S\t0\t0\t0\n'):
            with self.subTest(tail=tail),self.assertRaisesRegex(ValueError,'Truncated|Incomplete'):self.read(header+tail)
    def test_legacy_box_only_evidence_not_accepted(self):
        record=self.read('B\tPACKAGE GEOMETRY/PLACE_BOUND_TOP\t-5\t-5\t5\t5\n')
        with self.assertRaisesRegex(ValueError,'complete edge evidence'):verify.compare_placement_boundary(graphics(),record['bounds'])
    def test_nonfinite_boundary_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Non-finite'):self.read('B\tPACKAGE GEOMETRY/PLACE_BOUND_TOP\tnan\t-5\t5\t5\t0\t4\n')
    def test_missing_courtyard_is_rejected_before_native_generation(self):
        import sexpdata as sx
        source=sx.loads((lc2a.ROOT/'tests/header_1x2.kicad_mod').read_text())
        source=[n for n in source if not (isinstance(n,list) and (layer:=lc2a.child(n,'layer')) is not None and str(layer[1])=='F.CrtYd')]
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'source.kicad_mod';file.write_text(sx.dumps(source),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Missing placement courtyard'):lc2a.parse(file)
    def test_old_acceptance_without_full_boundary_check_cannot_publish(self):
        pointer=lc2a.ROOT/'work/f103_v2.json'
        if not pointer.exists():self.skipTest('Run real integration first')
        original=Path(verify.load(pointer)['job'])
        with tempfile.TemporaryDirectory() as d:
            job=Path(d)/'job';shutil.copytree(original,job);report=verify.load(job/'verification.json')
            report['checks'].pop('compiled_placement_boundary_geometry',None);verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'placement boundary geometry has not'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
