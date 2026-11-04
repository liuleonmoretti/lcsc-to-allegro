import copy,json,re,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class Presentation(unittest.TestCase):
    def setUp(self):
        info=verify.load(lc2a.ROOT/'work/f103_v2.json');self.job=Path(info['job'])
        self.status,self.model=verify.check_build(self.job)
        self.pads,self.graphics=verify.read_expected(self.job,self.status)
        self.texts=verify.read_native(self.job/'verification/native/drawing.tsv')['texts']
        self.view=(self.job/'verification/native/default_view.tsv').read_text(encoding='ascii')
    def check(self,texts=None,view=None):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'view.tsv';path.write_text(view if view is not None else self.view,encoding='ascii')
            return verify.check_presentation(path,texts if texts is not None else self.texts,self.pads,self.graphics,'U')
    def test_fresh_gui_opens_with_visible_silkscreen_refdes(self):
        result=self.check();self.assertGreater(result['refdes_to_mask_mm'],.15)
    def test_hidden_silkscreen_rejected(self):
        with self.assertRaisesRegex(ValueError,'visibility'):
            self.check(view=self.view.replace('REF DES/SILKSCREEN_TOP\tt','REF DES/SILKSCREEN_TOP\tnil'))
    def test_black_label_rejected(self):
        rows=[]
        for row in self.view.splitlines():
            v=row.split('\t')
            if v[1]=='REF DES/SILKSCREEN_TOP':v[3:]=['0','0','0']
            rows.append('\t'.join(v))
        with self.assertRaisesRegex(ValueError,'contrast'):self.check(view='\n'.join(rows))
    def test_zero_width_font_rejected(self):
        texts=copy.deepcopy(self.texts);texts[0]['stroke']=0
        with self.assertRaisesRegex(ValueError,'zero-width'):self.check(texts)
    def test_missing_or_generic_layer_label_rejected(self):
        texts=copy.deepcopy(self.texts);texts[0]['layer']='PACKAGE GEOMETRY/SILKSCREEN_TOP'
        with self.assertRaisesRegex(ValueError,'true RefDes'):self.check(texts)
    def test_assembly_text_must_use_body_center(self):
        texts=copy.deepcopy(self.texts)
        next(t for t in texts if t['layer']=='REF DES/ASSEMBLY_TOP')['at']=[0,4]
        with self.assertRaisesRegex(ValueError,'centered at origin'):self.check(texts)
    def test_silk_label_inside_outline_rejected(self):
        texts=copy.deepcopy(self.texts)
        t=next(t for t in texts if t['layer']=='REF DES/SILKSCREEN_TOP');t['at']=[0,0];t['bounds']=[-.7,-.3,.7,.9]
        with self.assertRaisesRegex(ValueError,'overlaps'):self.check(texts)
    def test_board_font_mapping_checks_effective_clearance(self):
        placed=verify.read_native(self.job/'verification/native/placed90.tsv')
        result=verify.compare_refdes(self.texts,placed['texts'],self.pads,self.graphics,(50,20),90)
        self.assertGreater(result['refdes_to_mask_mm'],.15)
        bad=copy.deepcopy(placed['texts']);bad[0]['at'][0]+=.5
        with self.assertRaisesRegex(ValueError,'position differs'):
            verify.compare_refdes(self.texts,bad,self.pads,self.graphics,(50,20),90)
    def test_board_mapped_zero_stroke_rejected(self):
        placed=verify.read_native(self.job/'verification/native/placed0.tsv');placed['texts'][0]['stroke']=0
        with self.assertRaisesRegex(ValueError,'zero-width'):
            verify.compare_refdes(self.texts,placed['texts'],self.pads,self.graphics,(20,20),0)

class TallBody(unittest.TestCase):
    def test_body_taller_than_pad_extent_keeps_refdes_above_outline(self):
        # A tall radial package exposed the old pad-only label placement rule.
        source='''(footprint "TALL_TEST" (layer "F.Cu")
          (fp_rect (start -3 -4) (end 3 4) (layer "F.Fab") (width 0.1))
          (fp_rect (start -3.1 -4.1) (end 3.1 4.1) (layer "F.SilkS") (width 0.15))
          (fp_rect (start -3.3 -4.3) (end 3.3 4.3) (layer "F.CrtYd") (width 0.05))
          (pad "1" thru_hole circle (at -1 0) (size 1.5 1.5) (drill .8) (layers "*.Cu" "*.Mask"))
          (pad "2" thru_hole circle (at 1 0) (size 1.5 1.5) (drill .8) (layers "*.Cu" "*.Mask")))'''
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'高本体中文.kicad_mod';path.write_text(source,encoding='utf-8')
            model=lc2a.parse(path);script=lc2a.skill_build(model,'TALL_TEST','C')
            match=re.search(r'axlDBCreateText\("C\*" 0:([\d.]+).*?SILKSCREEN_TOP',script)
            self.assertIsNotNone(match);self.assertGreater(float(match.group(1)),4.5)

if __name__=='__main__':unittest.main()
