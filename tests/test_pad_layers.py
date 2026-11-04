"""Regression evidence captured from actual 16.6 PAD files, including faults."""
import copy,re,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import verify

FIX=Path(__file__).with_name('fixtures')
PTH=dict(number='1',type='thru_hole',shape='oval',size=[2.4,1.8],drill=1.,mask=.05,layers=['*.Cu','*.Mask'])
SMD=dict(number='1',type='smd',shape='rect',size=[.3,1.2],drill=None,mask=.03,layers=['F.Cu','F.Mask','F.Paste'])

class RawPadLayers(unittest.TestCase):
    def setUp(self):
        self.pth=(FIX/'pad_pth_oval_summary.txt').read_text(encoding='ascii')
        self.smd=(FIX/'pad_smd_summary.txt').read_text(encoding='ascii')
    def test_actual_through_report_checks_all_inner_layers(self):
        result=verify.check_raw_pad(PTH,self.pth)
        self.assertTrue({'PLANE','INNER','DEFAULT INTERNAL','END LAYER'}<=set(result['layers']))
    def test_actual_smd_report_has_no_back_or_drill(self):
        self.assertFalse(verify.check_raw_pad(SMD,self.smd)['through_hole'])
    def test_actual_inner_override_hidden_by_two_layer_board_rejected(self):
        p=dict(PTH,shape='circle',size=[1.8,1.8])
        with self.assertRaisesRegex(ValueError,'PLANE'):
            verify.check_raw_pad(p,(FIX/'pad_pth_inner_fault.txt').read_text(encoding='ascii'))
    def test_thermal_and_antipad_are_not_ignored(self):
        for kind in ['THERMAL-PAD','ANTI-PAD']:
            with self.subTest(kind=kind):
                bad=re.sub(kind+r'\s+Not Defined',kind+' Circle 0.4 0.4 0.0000/0.0000',self.pth,count=1)
                with self.assertRaisesRegex(ValueError,'anti/thermal'):verify.check_raw_pad(PTH,bad)
    def test_missing_default_internal_section_is_incomplete(self):
        bad=re.sub(r'DEFAULT INTERNAL\n.*?(?=TOP SOLDERMASK)','',self.pth,flags=re.S)
        with self.assertRaisesRegex(ValueError,'Missing raw PAD layer'):verify.check_raw_pad(PTH,bad)
    def test_truncated_reports_never_pass(self):
        for text in [self.pth.split('NCDRILL')[0],self.pth.split('DRILL SYMBOL')[0]+'DRILL SYMBOL\n']:
            with self.subTest(length=len(text)):
                with self.assertRaisesRegex(ValueError,'Incomplete'):verify.check_raw_pad(PTH,text)
    def test_units_and_precision_must_be_explicit(self):
        for text in [self.pth.replace('MILLIMETERS','MILS'),self.pth.replace('Decimal places:\t4','Decimal places:\t2')]:
            with self.assertRaisesRegex(ValueError,'units/precision'):verify.check_raw_pad(PTH,text)
    def test_duplicate_layer_is_not_last_value_wins(self):
        text=self.pth.replace('INNER\n','PLANE\n',1)
        with self.assertRaisesRegex(ValueError,'Duplicate raw PAD layer'):verify.check_raw_pad(PTH,text)
    def test_unknown_flash_shape_is_not_approximated(self):
        with self.assertRaisesRegex(ValueError,'Unknown raw PAD shape'):
            verify.check_raw_pad(PTH,self.pth.replace('Oblong_X','FLASH',1))
    def test_diameter_plating_and_drill_offset_checked(self):
        edits=[('1.0000  Circle-Drill','0.8000  Circle-Drill','diameter differs'),
               ('Circle-Drill  Plated','Circle-Drill  Non-Plated','not circular/plated'),
               ('Offset: 0.0000/0.0000','Offset: 0.2000/0.0000','offset/tolerance differs')]
        for old,new,error in edits:
            with self.subTest(field=old):
                self.assertIn(old,self.pth)
                with self.assertRaisesRegex(ValueError,error):verify.check_raw_pad(PTH,self.pth.replace(old,new))
    def test_smd_cannot_acquire_bottom_mask(self):
        text=re.sub(r'(BOTTOM SOLDERMASK\s+REGULAR-PAD\s+)Not Defined',r'\g<1>Rectangle 0.36 1.26 0.0000/0.0000',self.smd)
        with self.assertRaisesRegex(ValueError,'BOTTOM SOLDERMASK'):verify.check_raw_pad(SMD,text)
    def test_embedded_dra_extra_inner_layer_checked(self):
        p=dict(PTH,shape='circle',size=[1.8,1.8]);model={'pads':[dict(p,padstack='lc_c900dba0a3d761d4')]}
        with self.assertRaisesRegex(ValueError,'PLANE'):verify.check_pads([p],model,FIX/'extracta_inner_fault.txt')
    def test_embedded_dra_antipad_checked(self):
        p=dict(PTH,shape='circle',size=[1.8,1.8]);model={'pads':[dict(p,padstack='lc_c900dba0a3d761d4')]}
        with self.assertRaisesRegex(ValueError,'anti/thermal'):verify.check_pads([p],model,FIX/'extracta_antipad_fault.txt')

if __name__=='__main__':unittest.main()
