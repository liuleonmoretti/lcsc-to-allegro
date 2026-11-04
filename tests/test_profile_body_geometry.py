"""Match full reviewed assembly envelope; source/output agreement isn't enough."""
import copy,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def field(n,k):return next((v for v in n if tag(v)==k),None)
def line(a,b):
    return sx.loads(f'(fp_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer F.Fab) (width 0.1))')

class ReviewedBodyGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples={}
        for key in ('f103','pth'):
            p=lc2a.ROOT/'work'/(key+'_v2.json')
            if not p.is_file():raise unittest.SkipTest('Real integration samples required')
            job=Path(verify.load(p)['job']);s,_=verify.check_build(job)
            tree=sx.loads((job/s['source_file']).read_text(encoding='utf-8'));cls.samples[key]=(job,s,tree)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def tree(self,key):return copy.deepcopy(self.samples[key][2])
    def body(self,tree):return [n for n in tree if tag(n)=='fp_line' and str(field(n,'layer')[1])=='F.Fab']
    def replacement(self,tree,points):
        for n in self.body(tree):tree.remove(n)
        tree.extend(line(points[i],points[(i+1)%len(points)]) for i in range(len(points)))
        return tree
    def read_source(self,key,tree):
        s=copy.deepcopy(self.samples[key][1]);s['source_file']='wrong.kicad_mod';src=self.root/s['source_file']
        src.write_text(sx.dumps(tree),encoding='utf-8')
        lc2a.parse(src,body_bounds=s['conversion_options']['body_bounds'],mask=s['conversion_options']['mask_margin'])
        pads,graphics=verify.read_expected(self.root,s)
        return s,pads,graphics
    def check(self,key,tree):
        s,pads,graphics=self.read_source(key,tree);metrics=verify.design_rules(pads,graphics)
        return s,pads,metrics,graphics
    def reject(self,key,tree,error='assembly body'):
        args=self.check(key,tree)
        with self.assertRaisesRegex(ValueError,error):verify.profile_checks(*args)
    def test_f103_same_bbox_triangle_is_not_rectangular_body(self):
        self.reject('f103',self.replacement(self.tree('f103'),[(-3.5,-3.5),(3.5,-3.5),(0,3.5)]))
    def test_do41_same_bbox_triangle_is_not_rectangular_body(self):
        self.reject('pth',self.replacement(self.tree('pth'),[(-2.605,-1.35),(2.605,-1.35),(0,1.35)]))
    def test_same_bbox_notch_cannot_remove_body_region(self):
        for key,a,b in (('f103',3.5,3.5),('pth',2.605,1.35)):
            with self.subTest(key=key):
                self.reject(key,self.replacement(self.tree(key),[(-a,-b),(a,-b),(a,b),(a/2,b),(a/2,b/2),(-a/2,b/2),(-a/2,b),(-a,b)]))
    def test_repeated_edge_is_rejected(self):
        for key in self.samples:
            with self.subTest(key=key):
                tree=self.tree(key);tree.append(copy.deepcopy(self.body(tree)[0]));self.reject(key,tree,'assembly body.*duplicated')
    def test_dangling_line_is_not_discarded(self):
        for key,b in (('f103',3.5),('pth',1.35)):
            with self.subTest(key=key):
                tree=self.tree(key);tree.append(line((0,b),(0,b-.3)));self.reject(key,tree,'assembly body.*closed')
    def test_inner_contour_is_not_silently_ignored(self):
        for key in self.samples:
            with self.subTest(key=key):
                tree=self.tree(key);pts=[(-.2,-.2),(.2,-.2),(.2,.2),(-.2,.2)]
                tree.extend(line(pts[i],pts[(i+1)%4]) for i in range(4));self.reject(key,tree)
    def test_disconnected_smaller_body_is_not_silently_ignored(self):
        for key,a in (('f103',3.5),('pth',2.605)):
            with self.subTest(key=key):
                tree=self.tree(key);pts=[(a+.1,0),(a+.2,0),(a+.2,.1),(a+.1,.1)]
                tree.extend(line(pts[i],pts[(i+1)%4]) for i in range(4))
                s,pads,graphics=self.read_source(key,tree)
                # All-assembly center rules now detect this before the profile.
                # Also exercise the independent full-rectangle guard directly.
                with self.assertRaisesRegex(ValueError,'Assembly geometry bounding-box center'):
                    verify.design_rules(pads,graphics)
                with self.assertRaisesRegex(ValueError,'assembly body'):
                    verify.reviewed_body_rectangle(graphics,s['review_profile']['body_bounds_mm'])
    def test_diagonal_inside_rectangle_is_rejected(self):
        tree=self.tree('f103');tree.append(line((-3.5,-3.5),(3.5,3.5)));self.reject('f103',tree)
    def test_reversed_scrambled_edge_order_is_valid(self):
        for key,area in (('f103',49),('pth',14.067)):
            with self.subTest(key=key):
                tree=self.tree(key)
                for edge in self.body(tree):
                    a,b=field(edge,'start'),field(edge,'end');a[1:],b[1:]=b[1:],a[1:]
                tree[2:]=reversed(tree[2:])
                r=verify.profile_checks(*self.check(key,tree));self.assertAlmostEqual(r['assembly_body_geometry']['area_mm2'],area)
    def test_collinear_split_edges_are_valid(self):
        for key in self.samples:
            with self.subTest(key=key):
                tree=self.tree(key);edge=self.body(tree)[0];a=field(edge,'start')[1:];b=field(edge,'end')[1:]
                middle=[(x+y)/2 for x,y in zip(a,b)];tree.remove(edge);tree.extend([line(a,middle),line(middle,b)])
                r=verify.profile_checks(*self.check(key,tree));self.assertLess(r['assembly_body_geometry']['maximum_boundary_error_mm'],verify.TOL)
    def test_native_readback_checked_independently(self):
        for key in self.samples:
            with self.subTest(key=key):
                job,s,_=self.samples[key];pads,g=verify.read_expected(job,s);native=verify.read_native(job/'verification/native/drawing.tsv')
                r=verify.profile_checks(s,pads,verify.design_rules(pads,native['graphics']),native['graphics'])
                self.assertLess(r['assembly_body_geometry']['maximum_boundary_error_mm'],verify.TOL)
    def test_old_profile_report_requires_geometry_check(self):
        job=self.root/'old';shutil.copytree(self.samples['f103'][0],job)
        report=verify.load(job/'verification.json');report['checks'].pop('manufacturer_profile_body_geometry',None);verify.save(job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'assembly body geometry'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
