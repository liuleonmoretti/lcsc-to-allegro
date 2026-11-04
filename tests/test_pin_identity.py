"""Pin identities survive numeric-looking lexical forms; verifier must catch coherent renaming."""
import argparse,contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sexpdata
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify,kicad_source,verify_pin_ids,easyeda_raw,verify_upstream


def fixture(tokens):
    body='''(module PINS (layer F.Cu)
    (fp_rect (start -20 -2) (end 20 2) (layer F.Fab) (width .1))
    (fp_rect (start -20.2 -2.2) (end 20.2 2.2) (layer F.SilkS) (width .1))
    (fp_rect (start -20.5 -2.5) (end 20.5 2.5) (layer F.CrtYd) (width .05))'''
    for i,token in enumerate(tokens):body+=f'\n(pad {token} thru_hole circle (at {i*3} 0) (size 1.8 1.8) (drill 1) (layers *.Cu *.Mask))'
    return body+')'


class PinIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='编号核对_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        self.source=self.job/'source.kicad_mod'
        self.state=dict(source_file=self.source.name,source_center_mm=[0,0],conversion_options={'mask_margin':0})
    def read_both(self,text):
        self.source.write_text(text,encoding='utf-8')
        model=lc2a.parse(self.source);expected,_=verify.read_expected(self.job,self.state)
        return model['pads'],expected
    def test_bare_and_quoted_numeric_looking_ids_preserved(self):
        names=['01','002','0000','1E3','2.00','+3','-0']
        for quoted in (False,True):
            with self.subTest(quoted=quoted):
                tokens=[json.dumps(n) if quoted else n for n in names]
                for rows in self.read_both(fixture(tokens)):self.assertEqual([p['number'] for p in rows],names)
    def test_one_and_leading_zero_are_distinct(self):
        for rows in self.read_both(fixture(['1','01','"001"'])):self.assertEqual([p['number'] for p in rows],['1','01','001'])
    def test_exact_duplicate_in_mixed_quote_syntax_is_rejected(self):
        self.source.write_text(fixture(['01','"01"']),encoding='utf-8')
        for call in (lambda:lc2a.parse(self.source),lambda:verify.read_expected(self.job,self.state)):
            with self.assertRaisesRegex(ValueError,'[Dd]uplicate'):call()
    def test_non_numeric_punctuation_and_uppercase_letters_preserved(self):
        names=['A_1','A-1','A.1','+','-','NIL','T']
        for rows in self.read_both(fixture(names)):self.assertEqual([p['number'] for p in rows],names)
    def test_nil_and_t_never_become_list_or_boolean_identifiers(self):
        for loader in (kicad_source.load,verify_pin_ids.load):
            nodes=[n for n in loader(fixture(['nil','t'])) if isinstance(n,list) and n and str(n[0])=='pad']
            self.assertEqual([n[1] for n in nodes],['nil','t'])
    def test_numeric_geometry_keeps_numeric_types(self):
        for loader in (kicad_source.load,verify_pin_ids.load):
            node=next(n for n in loader(fixture(['01'])) if isinstance(n,list) and n and str(n[0])=='pad')
            self.assertEqual(node[1],'01');self.assertIsInstance(next(n for n in node if isinstance(n,list) and str(n[0])=='drill')[1],int)
            self.assertIsInstance(next(n for n in node if isinstance(n,list) and str(n[0])=='size')[1],float)
    def test_text_comments_nested_metadata_do_not_create_pins(self):
        text=fixture(['01']).replace('(layer F.Cu)','''(layer F.Cu)
        ; (pad 999 smd circle)
        (descr "not a (pad 666) and \\\"quoted\\\" text")
        (property "metadata" (pad 888))''',1)
        for rows in self.read_both(text):self.assertEqual([p['number'] for p in rows],['01'])
    def test_independent_reader_never_calls_producer_loader(self):
        self.source.write_text(fixture(['01','+3']),encoding='utf-8')
        with patch.object(kicad_source,'load',side_effect=AssertionError('producer must not supply oracle')):
            expected,_=verify.read_expected(self.job,self.state)
        self.assertEqual([p['number'] for p in expected],['01','+3'])
    def test_independent_reader_recovers_id_even_if_numeric_atom_coerces(self):
        original=sexpdata.Parser.atom
        def corrupt(parser,token):return 777 if token=='001' else original(parser,token)
        self.source.write_text(fixture(['001']),encoding='utf-8')
        with patch.object(sexpdata.Parser,'atom',corrupt):expected,_=verify.read_expected(self.job,self.state)
        self.assertEqual(expected[0]['number'],'001')
    def test_malformed_pin_lists_or_missing_tokens_rejected(self):
        for text in ('(module X (pad))','(module X (pad (number 1) smd circle))','(module X (pad "unterminated))'):
            for loader in (kicad_source.load,verify_pin_ids.load):
                with self.subTest(text=text,loader=loader.__module__),self.assertRaises(Exception):loader(text)
    def test_empty_or_unsupported_number_rejected(self):
        for token in ('""','"引脚1"','"A B"','"A/B"'):
            self.source.write_text(fixture([token]),encoding='utf-8')
            for call in (lambda:lc2a.parse(self.source),lambda:verify.read_expected(self.job,self.state)):
                with self.subTest(token=token),self.assertRaises(ValueError):call()
    def test_escaped_identifier_is_not_silently_reinterpreted(self):
        for token in ('"\\u0031"','"\\x31"'):
            self.source.write_text(fixture([token]),encoding='utf-8')
            for call in (lambda:lc2a.parse(self.source),lambda:verify.read_expected(self.job,self.state)):
                with self.subTest(token=token),self.assertRaises(ValueError):call()
    def test_lowercase_and_case_collision_stop_before_native(self):
        for tokens in (['1e3'],['a1'],['A1','a1'],['t'],['nil']):
            self.source.write_text(fixture(tokens),encoding='utf-8')
            opts=argparse.Namespace(name='TEST_PIN_PREFLIGHT',input=self.source,lcsc=None,profile=None,refdes='J',label='编号',center=None,body_bounds=None,mask_margin=0,prepare_only=False)
            out=io.StringIO()
            with patch.object(lc2a,'ROOT',self.job),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(out):
                self.assertEqual(lc2a.convert(opts),1);native.assert_not_called()
            self.assertIn('uppercases pin numbers',json.loads(out.getvalue())['error'])
            with self.assertRaisesRegex(ValueError,'cannot be preserved'):verify.read_expected(self.job,self.state)
    def test_direct_native_entry_does_not_coerce_lowercase_or_number_type(self):
        for number in ('a1',1,'A B'):
            with patch.object(lc2a,'ROOT',self.job),patch.object(lc2a,'run') as native:
                with self.assertRaisesRegex(ValueError,'no numeric/case coercion'):lc2a.native_build(self.job,{'pads':[{'number':number}]},'TEST_PIN','J')
                native.assert_not_called()
    def test_old_coherently_renamed_model_and_quantization_rejected(self):
        rows,_=self.read_both(fixture(['01']))
        renamed=[dict(rows[0],number='1')]
        verify.save(self.job/'pad_quantization.json',lc2a.pad_quantization_record(renamed))
        self.state['pad_quantization_revision']=1
        self.state['files']={'pad_quantization.json':verify.sha(self.job/'pad_quantization.json')}
        with self.assertRaisesRegex(ValueError,'Model pin identifiers differ'):verify.check_pin_identity(rows,{'pads':renamed})
        with self.assertRaisesRegex(ValueError,'quantization record differs'):verify.check_pad_quantization(self.job,self.state,rows)
    def test_legacy_publication_gate_rechecks_literal_identity(self):
        rows,_=self.read_both(fixture(['01']))
        state=dict(self.state,pad_quantization_revision=0)
        names=('immutable_conversion_manifest','source_geometry_rules','dra_all_pins_graphics_layers_refdes','psm_loaded_placed_rotated90_moved',
            'disk_pad_copper_internal_masks_paste_drill_plating_offsets','raw_pad_all_layers_thermal_antipad','body_origin_courtyard_silk_clearance',
            'native_sessions_without_errors_or_crash_files','analytic_curved_clearance','native_actual_clearance','native_placement_enclosure',
            'assembly_all_geometry_enclosure','native_pad_quantization','compiled_placement_boundary_geometry')
        verify.save(self.job/'verification.json',dict(status='passed',checks={name:True for name in names}))
        with patch.object(verify,'check_build',return_value=(state,{'pads':[dict(rows[0],number='1')]})):
            with self.assertRaisesRegex(ValueError,'Model pin identifiers differ'):verify.require_verified(self.job)


class RawPinIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        self.raw=self.job/'raw.json';self.source=self.job/'original.kicad_mod'
        self.evidence=dict(response_file='raw.json',footprint_file=self.source.name)
    def prepare(self,name,quoted=False):
        raw={'result':{'packageDetail':{'dataStr':{'head':{'x':0,'y':0},'shape':[
            'PAD~ELLIPSE~0~0~8~8~11~~'+name+'~2~~0~id1~0~~Y~0~0~0.2~0,0']}}}}
        verify.save(self.raw,raw)
        token=json.dumps(name) if quoted else name
        self.source.write_text(f'(module X (layer F.Cu) (pad {token} thru_hole circle (at 0 0 0) (size 2.032 2.032) (drill 1.016) (layers *.Cu *.Mask)))',encoding='utf-8')
        out,audit=easyeda_raw.normalize(self.job,self.source,self.evidence)
        state=dict(lcsc='C123',fetch_evidence=self.evidence,raw_pad_audit=audit,source_file=out.name)
        self.rehash(state);return out,state
    def rehash(self,state):
        audit=state['raw_pad_audit'];audit['normalized_sha256']=verify.sha(self.job/audit['normalized_file'])
        verify.save(self.job/'raw_pad_audit.json',audit)
        state['files']={str(p.relative_to(self.job)):verify.sha(p) for p in self.job.rglob('*') if p.is_file()}
    def test_raw_export_normalization_preserves_original_pin_text(self):
        for name in ('01','+3','2.00','1E3'):
            for quoted in (False,True):
                with self.subTest(name=name,quoted=quoted):
                    out,state=self.prepare(name,quoted);self.assertEqual(verify_upstream.check(self.job,state)['pad_count'],1)
                    self.assertEqual(verify_pin_ids.identifiers(out.read_text(encoding='utf-8')),[name])
    def test_coherent_normalized_pin_renaming_rejected(self):
        out,state=self.prepare('01');out.write_text(out.read_text(encoding='utf-8').replace('(pad "01"','(pad "1"'),encoding='utf-8');self.rehash(state)
        with self.assertRaisesRegex(ValueError,'normalized pin count/identity differs'):verify_upstream.check(self.job,state)
    def test_export_already_lost_identifier_rejected(self):
        out,state=self.prepare('01');self.source.write_text(self.source.read_text(encoding='utf-8').replace('(pad 01','(pad 1'),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'exported pin set differs'):easyeda_raw.normalize(self.job,self.source,self.evidence)


if __name__=='__main__':unittest.main()
