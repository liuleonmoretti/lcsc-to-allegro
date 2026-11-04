"""Raw numeric meaning and full ordinary outline precision, before native compilation."""
import copy,math,sys,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import source_graphics as producer
import verify_graphics_source as independent

def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def field(n,k):return next(v for v in n if tag(v)==k)

class RawGraphicValues(unittest.TestCase):
    def setUp(self):
        self.shapes=[
            'TRACK~0.4~3~~100.012 -50.012 104.012 -50.012 104.012 -48.012~track~0',
            'CIRCLE~100~-50~1.234~0.4~3~circle~0~~',
            'RECT~100~-50~2~1~13~rect~0~0.4',
            'SOLIDREGION~99~~M 98 -52 L 106 -52 L 106 -46 L 98 -46 Z~solid~boundary~~~~0']
        self.data={'result':{'packageDetail':{'dataStr':{'head':{'x':100,'y':-50},'shape':self.shapes}}}}
        # Hand-calculated upstream 0.01mm serialized result. The fixture is not
        # made with either normalization or independent geometry builder.
        self.tree=sx.loads('''(footprint "原始图形"
          (fp_text reference REF** (at 0 0) (layer F.SilkS))
          (fp_line (start 0 0) (end 1.02 0) (layer F.SilkS) (width 0.10))
          (fp_line (start 1.02 0) (end 1.02 0.50) (layer F.SilkS) (width 0.10))
          (fp_circle (center 0 0) (end 0.31 0) (layer F.SilkS) (width 0.10))
          (fp_line (start 0 0) (end 0.51 0) (layer F.Fab) (width 0.10))
          (fp_line (start 0.51 0) (end 0.51 0.25) (layer F.Fab) (width 0.10))
          (fp_line (start 0.51 0.25) (end 0 0.25) (layer F.Fab) (width 0.10))
          (fp_line (start 0 0.25) (end 0 0) (layer F.Fab) (width 0.10))
          (fp_line (start -0.51 -0.51) (end 1.52 -0.51) (layer F.CrtYd) (width 0.05))
          (fp_line (start 1.52 -0.51) (end 1.52 1.02) (layer F.CrtYd) (width 0.05))
          (fp_line (start 1.52 1.02) (end -0.51 1.02) (layer F.CrtYd) (width 0.05))
          (fp_line (start -0.51 1.02) (end -0.51 -0.51) (layer F.CrtYd) (width 0.05)))''')
    def replace(self,shape,index,value):
        cells=self.shapes[shape].split('~');cells[index]=str(value);self.shapes[shape]='~'.join(cells)
    def both_reject_raw(self):
        for fn in (producer.inventory,independent.inventory):
            with self.subTest(reader=fn.__module__),self.assertRaisesRegex(ValueError,'graphic'):fn(self.data,None)
    def prepared(self):
        producer.inventory(self.data,None);original=copy.deepcopy(self.tree)
        result=producer.normalize_geometry(self.data,self.tree,None)
        independent.inventory(self.data,None);check=independent.check_geometry(self.data,[original,self.tree],None)
        self.assertEqual(result.keys(),check.keys())
        for k,v in result.items():
            if isinstance(v,float):self.assertAlmostEqual(v,check[k],places=10)
            else:self.assertEqual(v,check[k])
        return original,result
    def reject_export(self):
        for call in (lambda:producer.normalize_geometry(self.data,copy.deepcopy(self.tree),None),
                     lambda:independent.check_geometry(self.data,[self.tree,self.tree],None)):
            with self.assertRaisesRegex(ValueError,'graphic'):call()
    def test_restores_raw_origin_coordinates_radius_and_stroke(self):
        old,result=self.prepared();line=next(n for n in self.tree if tag(n)=='fp_line')
        self.assertAlmostEqual(field(line,'start')[1],.003048,places=10)
        self.assertAlmostEqual(field(line,'start')[2],-.003048,places=10)
        self.assertAlmostEqual(field(line,'width')[1],.1016,places=10)
        circle=next(n for n in self.tree if tag(n)=='fp_circle')
        self.assertAlmostEqual(field(circle,'end')[1],.313436,places=10)
        self.assertEqual(result['graphic_count'],11);self.assertTrue(result['raw_equivalence'])
        self.assertLessEqual(result['max_export_coordinate_rounding_mm'],.005)
        self.assertNotEqual(old,self.tree)
    def test_zero_and_negative_strokes_are_not_clamped(self):
        for width in ('0','-1'):
            with self.subTest(width=width):self.replace(0,1,width);self.both_reject_raw()
    def test_text_stroke_does_not_become_default(self):self.replace(0,1,'bad');self.both_reject_raw()
    def test_nonfinite_graphic_values_rejected(self):
        saved=self.shapes.copy()
        for shape,index,value in ((0,1,'NaN'),(1,3,'Infinity'),(2,1,'NaN'),(0,4,'100 -50 101 NaN')):
            with self.subTest(shape=shape,index=index):
                self.shapes[:]=saved;self.replace(shape,index,value);self.both_reject_raw()
    def test_subminimum_stroke_not_silently_enlarged(self):self.replace(0,1,.002/.254);self.both_reject_raw()
    def test_negative_or_zero_radius_not_absolute_value(self):
        for radius in ('-1','0'):
            with self.subTest(radius=radius):self.replace(1,3,radius);self.both_reject_raw()
    def test_rectangle_dimensions_must_be_positive(self):
        for index,v in ((3,'-2'),(3,'0'),(4,'-1'),(4,'0')):
            with self.subTest(index=index,value=v):
                self.shapes[2]='RECT~100~-50~2~1~13~rect~0~0.4';self.replace(2,index,v);self.both_reject_raw()
    def test_track_points_even_and_nondegenerate(self):
        for points in ('100 -50 101','100 -50','100 -50 100 -50'):
            with self.subTest(points=points):self.replace(0,4,points);self.both_reject_raw()
    def test_unknown_graphic_tail_not_silently_zipped_away(self):
        self.shapes[0]+='~unrecognized';self.both_reject_raw()
    def test_unknown_locked_flag_rejected(self):self.replace(1,7,'UNKNOWN');self.both_reject_raw()
    def test_raw_invalid_values_rejected_even_if_profile_would_replace_them(self):
        self.replace(1,3,-1)
        for fn in (producer.inventory,independent.inventory):
            with self.assertRaisesRegex(ValueError,'graphic'):fn(self.data,'C53568')
    def test_original_export_position_change_rejected(self):
        n=next(n for n in self.tree if tag(n)=='fp_line');field(n,'start')[1]=.1;self.reject_export()
    def test_original_export_stroke_change_rejected(self):
        n=next(n for n in self.tree if tag(n)=='fp_line');field(n,'width')[1]=.2;self.reject_export()
    def test_missing_graphic_rejected(self):
        self.tree.remove(next(n for n in self.tree if tag(n)=='fp_circle'));self.reject_export()
    def test_extra_graphic_rejected(self):
        self.tree.append(copy.deepcopy(next(n for n in self.tree if tag(n)=='fp_circle')));self.reject_export()
    def test_wrong_layer_rejected(self):
        n=next(n for n in self.tree if tag(n)=='fp_circle');field(n,'layer')[1]=sx.Symbol('F.Fab');self.reject_export()
    def test_wrong_circle_end_rejected(self):
        n=next(n for n in self.tree if tag(n)=='fp_circle');field(n,'end')[1]=.4;self.reject_export()
    def test_normalized_positions_cannot_use_export_rounding_tolerance(self):
        old,_=self.prepared();n=next(n for n in self.tree if tag(n)=='fp_line');field(n,'start')[1]+=.001
        with self.assertRaisesRegex(ValueError,'graphic geometry'):independent.check_geometry(self.data,[old,self.tree],None)
    def test_normalized_stroke_cannot_revert_to_rounded_width(self):
        old,_=self.prepared();n=next(n for n in self.tree if tag(n)=='fp_line');field(n,'width')[1]=.10
        with self.assertRaisesRegex(ValueError,'graphic geometry'):independent.check_geometry(self.data,[old,self.tree],None)
    def test_agreeing_export_and_normalized_files_still_must_match_raw(self):
        for n in self.tree:
            if tag(n)=='fp_circle':field(n,'center')[1]+=.1;field(n,'end')[1]+=.1
        self.reject_export()
    def test_other_nonpad_metadata_cannot_be_changed(self):
        old,_=self.prepared();next(n for n in self.tree if tag(n)=='fp_text')[2]='changed'
        with self.assertRaisesRegex(ValueError,'non-pad metadata'):independent.check_geometry(self.data,[old,self.tree],None)
    def test_segment_order_and_direction_are_irrelevant(self):
        self.tree[3:]=reversed(self.tree[3:])
        for n in self.tree:
            if tag(n)=='fp_line':
                a,b=field(n,'start'),field(n,'end');a[1:],b[1:]=b[1:],a[1:]
        self.prepared()
    def test_ambiguous_export_resolution_not_guessed(self):
        # An additional almost coincident line collapses to the same rounded
        # export. A unique restoration is impossible, so both paths must reject.
        self.shapes.append('TRACK~0.4~3~~100.013 -50.013 104.013 -50.013~close_track~0')
        self.tree.append(copy.deepcopy(next(n for n in self.tree if tag(n)=='fp_line')));self.reject_export()
    def test_profile_replacement_is_not_claimed_as_raw_equivalence(self):
        original=copy.deepcopy(self.tree);a=producer.normalize_geometry(self.data,self.tree,'C53568')
        b=independent.check_geometry(self.data,[original,self.tree],{'profile':'C53568'})
        self.assertEqual(a,b);self.assertFalse(a['raw_equivalence']);self.assertEqual(original,self.tree)

if __name__=='__main__':unittest.main()
