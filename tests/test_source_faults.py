"""Fault injection against BOTH implementations; sources need not be compiled to prove rejection."""
import copy,math,random,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class SourceFaults(unittest.TestCase):
    def setUp(self):
        self.tree=sx.loads((lc2a.ROOT/'tests/header_1x2.kicad_mod').read_text())
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.settings={'source_file':'fault.kicad_mod','source_center_mm':[1.27,0],'conversion_options':{'mask_margin':0}}
    def check(self,tree):
        p=self.root/'fault.kicad_mod';p.write_text(sx.dumps(tree),encoding='utf-8')
        for name,call in [('converter',lambda:lc2a.parse(p)),('verifier',lambda:verify.read_expected(self.root,self.settings))]:
            with self.subTest(implementation=name),self.assertRaises((ValueError,KeyError)):call()
    def test_dashed_and_dotted_graphic_rejected(self):
        for style in ('dash','dash_dot','dash_dot_dot','dot'):
            t=copy.deepcopy(self.tree);g=next(n for n in t if lc2a.key(n)=='fp_rect')
            stroke=lc2a.child(g,'stroke');lc2a.child(stroke,'type')[1]=sx.Symbol(style)
            with self.subTest(style=style):self.check(t)
    def test_stroke_opacity_not_silently_discarded(self):
        t=copy.deepcopy(self.tree);g=next(n for n in t if lc2a.key(n)=='fp_rect')
        lc2a.child(g,'stroke').append(sx.loads('(color 0 0 0 0)'));self.check(t)
    def test_unknown_geometry_rejected(self):
        t=copy.deepcopy(self.tree);next(n for n in t if lc2a.key(n)=='fp_rect').append(sx.loads('(offset 8 9)'));self.check(t)
    def test_conflicting_width_forms_rejected(self):
        t=copy.deepcopy(self.tree);next(n for n in t if lc2a.key(n)=='fp_rect').append(sx.loads('(width 5)'));self.check(t)
    def test_duplicate_pad_geometry_rejected(self):
        for field in ('(at 20 20)','(size 10 10)','(drill 0.1)','(layers F.Cu)'):
            t=copy.deepcopy(self.tree);next(n for n in t if lc2a.key(n)=='pad').append(sx.loads(field))
            with self.subTest(field=field):self.check(t)
    def test_global_mask_expansion_not_ignored(self):
        t=copy.deepcopy(self.tree);t.append(sx.loads('(solder_mask_margin 0.8)'));self.check(t)
    def test_nonfinite_pad_coordinates_rejected(self):
        for value in ('nan','inf','-inf'):
            t=copy.deepcopy(self.tree);p=next(n for n in t if lc2a.key(n)=='pad');lc2a.child(p,'at')[1]=sx.Symbol(value)
            with self.subTest(value=value):self.check(t)
    def test_filled_outline_rejected(self):
        t=copy.deepcopy(self.tree);g=next(n for n in t if lc2a.key(n)=='fp_rect');lc2a.child(g,'fill')[1]=sx.Symbol('solid');self.check(t)
    def test_zero_area_outline_rejected(self):
        t=copy.deepcopy(self.tree);g=next(n for n in t if lc2a.key(n)=='fp_rect');lc2a.child(g,'end')[1]=lc2a.child(g,'start')[1];self.check(t)
    def test_pad_extra_coordinates_rejected(self):
        t=copy.deepcopy(self.tree);p=next(n for n in t if lc2a.key(n)=='pad');lc2a.child(p,'at').extend([90,888]);self.check(t)
    def test_missing_coordinate_not_assumed_zero(self):
        t=copy.deepcopy(self.tree);p=next(n for n in t if lc2a.key(n)=='pad');p.remove(lc2a.child(p,'at'));self.check(t)
    def test_slot_and_offset_drill_rejected(self):
        for drill in ('(drill oval 0.8 1.8)','(drill 1 (offset 0.4 0))'):
            t=copy.deepcopy(self.tree);p=next(n for n in t if lc2a.key(n)=='pad');p.remove(lc2a.child(p,'drill'));p.append(sx.loads(drill));self.check(t)
    def test_unknown_scalar_rejected(self):
        t=copy.deepcopy(self.tree);t.append(sx.Symbol('unknown_geometry'));self.check(t)
    def test_translation_invariance_for_nonzero_centers(self):
        # Moving every source point cannot change centered pad locations/pitch.
        rng=random.Random(10930)
        for trial in range(30):
            dx,dy=rng.uniform(-500,500),rng.uniform(-500,500);t=copy.deepcopy(self.tree)
            for n in t:
                if not isinstance(n,list):continue
                for field in ('start','end','center','at'):
                    point=lc2a.child(n,field)
                    if point:point[1]+=dx;point[2]+=dy
            p=self.root/'translated.kicad_mod';p.write_text(sx.dumps(t),encoding='utf-8')
            m=lc2a.parse(p)
            self.assertAlmostEqual(m['pads'][0]['at'][0],-1.27,places=5)
            self.assertAlmostEqual(m['pads'][1]['at'][0],1.27,places=5)
            self.assertAlmostEqual(m['pads'][0]['at'][1],0,places=5)
            self.assertAlmostEqual(m['source_center'][0],dx+1.27,places=5)

if __name__=='__main__':unittest.main()
