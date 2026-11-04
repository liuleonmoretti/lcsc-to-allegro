import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a

class Geometry(unittest.TestCase):
    def test_center_and_pitch(self):
        m=lc2a.parse(Path(__file__).parent/'header_1x2.kicad_mod')
        self.assertEqual(m['source_center'],[1.27,0.0])
        self.assertEqual([p['at'] for p in m['pads']],[[-1.27,0.0],[1.27,0.0]])
        self.assertEqual(m['pads'][0]['drill'],1.0)
    def test_rotation_and_y(self):
        m=lc2a.parse(Path(__file__).parent/'sot23_5.kicad_mod')
        self.assertEqual(m['pads'][0]['at'],[-.95,-1.3])
        self.assertEqual(m['pads'][0]['angle'],270)
    def test_unsupported_shapes_do_not_generate(self):
        t=(Path(__file__).parent/'sot23_5.kicad_mod').read_text()
        for replacement in ('roundrect','custom'):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'bad.kicad_mod';p.write_text(t.replace('smd rect','smd '+replacement))
                with self.assertRaisesRegex(ValueError,'Unsupported pad shape'):lc2a.parse(p)
    def test_missing_center(self):
        t='(footprint "test" (layer F.Cu) (pad "1" smd rect (at 0 0) (size 1 1) (layers F.Cu F.Mask)))'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.kicad_mod';p.write_text(t)
            with self.assertRaisesRegex(ValueError,'No F.Fab'):lc2a.parse(p)
    def test_duplicate_numbers(self):
        t=(Path(__file__).parent/'header_1x2.kicad_mod').read_text().replace('pad "2"','pad "1"')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.kicad_mod';p.write_text(t)
            with self.assertRaisesRegex(ValueError,'Duplicate'):lc2a.parse(p)

if __name__=='__main__':unittest.main()
