"""Discrete shape identity follows explicit four-decimal database dimensions."""
import copy,json,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify
FIX=Path(__file__).with_name('fixtures')/'quantized_pads'

def pad(shape='rect',size=(1.8001,1.8002),mask=.05):
    return dict(number='1',type='thru_hole',shape=shape,size=list(size),at=[0,0],angle=0,drill=1.,mask=mask,layers=['*.Cu','*.Mask'])


class Quantization(unittest.TestCase):
    def both(self,p,expected,margin=0):
        self.assertEqual(lc2a.native_pad_spec(p,margin),expected)
        self.assertEqual(verify.reference_native_dimensions(p,margin),expected)

    def test_nearly_square_rectangle_remains_rectangle(self):
        self.both(pad(),dict(shape='RECTANGLE',size=[1.8001,1.8002]))

    def test_nearly_circular_oval_retains_axis(self):
        self.both(pad('oval',(2.0001,2.0002)),dict(shape='OBLONG_Y',size=[2.0001,2.0002]))
        self.both(pad('oval',(2.0002,2.0001)),dict(shape='OBLONG_X',size=[2.0002,2.0001]))

    def test_rectangle_equal_on_database_grid_is_square(self):
        self.both(pad(size=(1.800001,1.800002)),dict(shape='SQUARE',size=[1.8,1.8]))

    def test_oval_equal_on_database_grid_is_circle(self):
        self.both(pad('oval',(2.000001,2.000002)),dict(shape='CIRCLE',size=[2.,2.]))

    def test_mask_can_be_square_while_copper_is_rectangle(self):
        p=pad(size=(1.800049,1.800051),mask=.00001)
        self.both(p,dict(shape='RECTANGLE',size=[1.8,1.8001]))
        self.both(p,dict(shape='SQUARE',size=[1.8001,1.8001]),p['mask'])

    def test_mask_can_be_rectangle_while_copper_is_square(self):
        p=pad(size=(1.80001,1.80004),mask=.00001)
        self.both(p,dict(shape='SQUARE',size=[1.8,1.8]))
        self.both(p,dict(shape='RECTANGLE',size=[1.8,1.8001]),p['mask'])

    def test_half_up_ties_are_explicit(self):
        self.both(pad(size=(1.80005,1.80015)),dict(shape='RECTANGLE',size=[1.8001,1.8002]))
        self.assertEqual(lc2a.database_dimension(.00005),.0001)

    def test_dimensions_rounding_to_zero_are_rejected(self):
        p=pad(size=(.00004,.5))
        with self.assertRaisesRegex(ValueError,'positive.*grid'):lc2a.native_pad_spec(p)
        with self.assertRaisesRegex(ValueError,'positive.*grid'):verify.reference_native_dimensions(p)

    def test_circle_cannot_have_different_quantized_diameters(self):
        p=pad('circle',(1.800049,1.800051))
        with self.assertRaisesRegex(ValueError,'unequal database'):lc2a.native_pad_spec(p)
        with self.assertRaisesRegex(ValueError,'unequal database'):verify.reference_native_dimensions(p)

    def test_source_values_are_preserved_and_drill_rounding_recorded(self):
        p=pad(size=(1.800001,1.800002));p['drill']=.999998;original=copy.deepcopy(p)
        report=lc2a.pad_quantization_record([p]);self.assertEqual(p,original)
        self.assertEqual(report['pads'][0]['source_size_mm'],[1.800001,1.800002]);self.assertEqual(report['pads'][0]['source_drill_mm'],.999998)
        self.assertEqual(report['pads'][0]['drill_mm'],1.)

    def test_independent_audit_rejects_rewritten_native_size(self):
        p=pad();report=lc2a.pad_quantization_record([p]);report['pads'][0]['copper']['size'][0]+=.1
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);verify.save(path/'pad_quantization.json',report)
            with self.assertRaisesRegex(ValueError,'independently calculated'):
                verify.check_pad_quantization(path,dict(pad_quantization_revision=1,files={'pad_quantization.json':'unused'}),[p])

    def test_new_record_requires_manifest_and_known_revision(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'manifest entry'):verify.check_pad_quantization(Path(d),dict(pad_quantization_revision=1,files={}),[pad()])
            with self.assertRaisesRegex(ValueError,'Unknown pad quantization'):verify.check_pad_quantization(Path(d),dict(pad_quantization_revision=2),[pad()])

    def test_old_native_can_be_reverified_with_derived_policy(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(verify.check_pad_quantization(Path(d),{},[pad()])['record_origin'],'derived_from_legacy_source')


class QuantizedNativeReports(unittest.TestCase):
    def check(self,case,p):
        result=verify.check_raw_pad(p,(FIX/(case+'.txt')).read_text(encoding='ascii'))
        self.assertTrue(result['through_hole']);return result

    def test_actual_near_rectangle_report(self):self.check('rectangle',pad())
    def test_actual_near_oval_report(self):self.check('oval',pad('oval',(2.0001,2.0002)))
    def test_actual_rounded_rectangle_report(self):self.check('rounded_rectangle',pad(size=(1.800001,1.800002)))
    def test_actual_rounded_oval_report(self):self.check('rounded_oval',pad('oval',(2.000001,2.000002)))
    def test_actual_distinct_copper_and_mask_shape(self):
        r=self.check('mask_rectangle',pad(size=(1.800049,1.800051),mask=.00001))
        self.assertEqual(r['geometry']['TOP']['shape'],'Rectangle');self.assertEqual(r['geometry']['~TSM']['shape'],'Square')


class QuantizationRelease(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='数据库舍入发布_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)/'job'
        original=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job']);shutil.copytree(original,self.job);verify.require_verified(self.job)

    def test_legacy_report_must_be_reverified(self):
        report=verify.load(self.job/'verification.json');report['checks'].pop('native_pad_quantization');verify.save(self.job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'Native pad dimension quantization'):verify.require_verified(self.job)

    def test_modified_rounding_record_cannot_be_fixed_by_self_hash(self):
        path=self.job/'pad_quantization.json';record=verify.load(path);record['pads'][0]['copper']['shape']='CIRCLE';verify.save(path,record)
        state=verify.load(self.job/'status.json');state['files']['pad_quantization.json']=verify.sha(path);verify.save(self.job/'status.json',state)
        report=verify.load(self.job/'verification.json');report['status_sha256']=verify.sha(self.job/'status.json');verify.save(self.job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'independently calculated'):verify.require_verified(self.job)


if __name__=='__main__':unittest.main()
