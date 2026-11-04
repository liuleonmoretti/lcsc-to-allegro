"""Physical pin-number field: native 31-character boundary, not a filename rule."""
import argparse, contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a, verify, kicad_source
from test_pin_identity import fixture


class PinNumberLength(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='引脚长度_');self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name);self.source=self.job/'source.kicad_mod'
        self.state=dict(source_file=self.source.name,source_center_mm=[0,0],conversion_options={'mask_margin':0})

    def write(self,numbers,quoted=False):
        self.source.write_text(fixture([json.dumps(n) if quoted else n for n in numbers]),encoding='utf-8')

    def test_31_character_numbers_and_distinct_final_character_are_preserved(self):
        names=['P'*31,'A'*30+'1','A'*30+'2']
        for quoted in (False,True):
            self.write(names,quoted)
            model=lc2a.parse(self.source);expected,_=verify.read_expected(self.job,self.state)
            self.assertEqual([p['number'] for p in model['pads']],names)
            self.assertEqual([p['number'] for p in expected],names)

    def test_oversize_conversion_fails_before_build_and_does_not_truncate(self):
        for names in (['P'*32],['P'*64],['A'*31+'1','A'*31+'2']):
            self.write(names);original=self.source.read_bytes()
            opts=argparse.Namespace(name='TEST_PIN_LENGTH',input=self.source,lcsc=None,profile=None,
                refdes='J',label='引脚长度',center=None,body_bounds=None,mask_margin=0,prepare_only=False)
            out=io.StringIO()
            with patch.object(lc2a,'ROOT',self.job),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(out):
                self.assertEqual(lc2a.convert(opts),1);native.assert_not_called()
            result=json.loads(out.getvalue());failed=Path(result['job'])
            self.assertIn('pin number must be 1-31',result['error'])
            self.assertFalse((failed/'build.il').exists());self.assertFalse((failed/'native').exists())
            self.assertEqual(self.source.read_bytes(),original)
            self.assertEqual((failed/'source.kicad_mod').read_bytes(),original)

    def test_independent_reader_rejects_32_and_64_without_producer(self):
        for length in (32,64):
            for quoted in (False,True):
                self.write(['P'*length],quoted)
                with patch.object(kicad_source,'load',side_effect=AssertionError('producer called')):
                    with self.assertRaisesRegex(ValueError,'pin number.*31'):
                        verify.read_expected(self.job,self.state)

    def test_direct_native_entry_rejects_length_before_configuration_or_io(self):
        for length in (32,64):
            with patch.object(lc2a,'ROOT',self.job),patch.object(lc2a,'run') as native:
                with self.assertRaisesRegex(ValueError,'pin number must be 1-31'):
                    lc2a.native_build(self.job,{'pads':[{'number':'P'*length}]},'TEST_PIN_LENGTH','J')
                native.assert_not_called()
            self.assertEqual(list(self.job.iterdir()),[])

    def test_legacy_release_flag_cannot_skip_pin_number_length_check(self):
        self.write(['P'*32])
        checks=('immutable_conversion_manifest','source_geometry_rules','dra_all_pins_graphics_layers_refdes',
            'psm_loaded_placed_rotated90_moved','disk_pad_copper_internal_masks_paste_drill_plating_offsets',
            'raw_pad_all_layers_thermal_antipad','body_origin_courtyard_silk_clearance',
            'native_sessions_without_errors_or_crash_files','analytic_curved_clearance','native_actual_clearance',
            'native_placement_enclosure','assembly_all_geometry_enclosure','native_pad_quantization')
        verify.save(self.job/'verification.json',dict(status='passed',checks={name:True for name in checks}))
        with patch.object(verify,'check_build',return_value=(self.state,{'pads':[{'number':'P'*32}]})),patch.object(verify,'run') as native:
            with self.assertRaisesRegex(ValueError,'pin number.*31'):verify.require_verified(self.job)
            native.assert_not_called()

    def test_pin_identifiers_are_not_filtered_as_windows_paths_or_devices(self):
        names=['CON','NUL','COM1','.','..','0','0000']
        for quoted in (False,True):
            self.write(names,quoted)
            model=lc2a.parse(self.source);expected,_=verify.read_expected(self.job,self.state)
            self.assertEqual([p['number'] for p in model['pads']],names)
            self.assertEqual([p['number'] for p in expected],names)


if __name__=='__main__':unittest.main()
