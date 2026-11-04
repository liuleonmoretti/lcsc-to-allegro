"""Physical-layer text must never disappear behind the annotation omission policy."""
import argparse, contextlib, io, json, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a, source_graphics, verify, verify_graphics_source


class TextLayers(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'中文源.kicad_mod'
        self.base=(lc2a.ROOT/'tests/header_1x2.kicad_mod').read_text(encoding='utf-8')
        self.state=dict(source_file=self.path.name,source_center_mm=[1.27,0],conversion_options={'mask_margin':0})

    def write(self,node):
        self.path.write_text(self.base.rstrip()[:-1]+'\n'+node+'\n)',encoding='utf-8')

    def calls(self):
        return [('converter',lambda:lc2a.parse(self.path)),('independent',lambda:verify.read_expected(self.root,self.state))]

    def reject(self,node):
        self.write(node)
        for label,call in self.calls():
            with self.subTest(implementation=label),self.assertRaisesRegex(ValueError,'text.*layer|layer.*text'):call()

    def test_copper_masks_paste_edges_and_unknown_text_layers_rejected(self):
        for layer in ('F.Cu','B.Cu','In1.Cu','*.Cu','F.Mask','B.Mask','F.Paste','B.Paste','F.Adhes','Edge.Cuts','Margin','Unknown.Layer'):
            for prefix in ('fp_text user','fp_text reference','fp_text value','property "LABEL"'):
                with self.subTest(layer=layer,prefix=prefix):
                    self.reject(f'({prefix} "LAYERTEST" (at 1.27 0) (layer {layer}) (effects (font (size 0.5 0.5) (thickness 0.1))))')

    def test_hidden_flag_does_not_allow_physical_text(self):
        self.reject('(fp_text user "hidden copper" (at 0 0) (layer F.Cu) hide (effects (font (size 1 1))))')
        self.reject('(property "MASK" "hidden mask" (at 0 0) (layer F.Mask) (effects (font (size 1 1)) hide))')

    def test_missing_duplicate_and_malformed_layers_rejected(self):
        for layers in ('','(layer F.SilkS) (layer F.Cu)','(layer)','(layer F.SilkS extra)'):
            for prefix in ('fp_text user','property "DRAWN"'):
                with self.subTest(layers=layers,prefix=prefix):self.reject(f'({prefix} "text" (at 0 0) {layers})')

    def test_annotation_omissions_are_allowed_and_recorded(self):
        for layer in ('F.SilkS','B.SilkS','F.Fab','B.Fab','F.CrtYd','B.CrtYd','Cmts.User','Dwgs.User','Eco1.User','Eco2.User'):
            self.write(f'(fp_text user "中文说明" (at 0 0) (layer {layer})) (property "Value" "说明" (at 0 0) (layer {layer}))')
            with self.subTest(layer=layer):
                model=lc2a.parse(self.path);expected,_=verify.read_expected(self.root,self.state)
                self.assertEqual(model['omitted'],['fp_text','property_text'])
                self.assertEqual([p['number'] for p in expected],['1','2'])

    def test_plain_metadata_does_not_require_a_drawing_layer(self):
        self.write('(property "LCSC Part" "C123") (property "说明" "中文")')
        model=lc2a.parse(self.path);verify.read_expected(self.root,self.state)
        self.assertNotIn('property_text',model['omitted'])

    def test_independent_reader_never_calls_producer_text_guard(self):
        self.write('(fp_text user "copper" (at 0 0) (layer F.Cu))')
        with patch.object(source_graphics,'text_layer',side_effect=AssertionError('producer called')):
            with self.assertRaisesRegex(ValueError,'source text'):verify.read_expected(self.root,self.state)

    def test_profile_original_property_is_checked_before_replacement(self):
        tree=sx.loads(self.base)
        good=source_graphics.profile_inventory(tree)
        tree.append(sx.loads('(property "MASK" "TEST" (at 0 0) (layer F.Mask))'))
        self.path.write_text(sx.dumps(tree),encoding='utf-8')
        state={'review_profile':dict(original_source=self.path.name,original_sha256=verify.sha(self.path),replaced_graphic_inventory=good),
               'files':{self.path.name:verify.sha(self.path)}}
        with self.assertRaisesRegex(ValueError,'text'):source_graphics.profile_inventory(tree)
        with self.assertRaisesRegex(ValueError,'text'):verify_graphics_source.profile_source(self.root,state)

    def test_conversion_rejects_before_calling_native_builder(self):
        self.write('(fp_text user "copper" (at 0 0) (layer F.Cu))')
        args=argparse.Namespace(name='TEST_TEXT_REJECT',refdes='J',label='',lcsc=None,input=self.path,profile=None,
                                center=None,body_bounds=None,mask_margin=0,prepare_only=False)
        stream=io.StringIO()
        with patch.object(lc2a,'ROOT',self.root),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(stream):
            self.assertEqual(lc2a.convert(args),1)
        native.assert_not_called();self.assertIn('text',json.loads(stream.getvalue())['error'])

    def test_old_passed_report_cannot_release_physical_text_after_rehash(self):
        source=Path(verify.load(lc2a.ROOT/'work/f103_v2.json')['job'])
        job=self.root/'job';shutil.copytree(source,job)
        state=verify.load(job/'status.json');path=verify.safe_rel(job,state['source_file'])
        text=path.read_text(encoding='utf-8').rstrip()
        path.write_text(text[:-1]+'\n(fp_text user "DROPPED" (at 0 0) (layer F.Cu))\n)',encoding='utf-8')
        state['source_sha256']=verify.sha(path)
        key=next(k for k in state['files'] if Path(k).as_posix().casefold()==path.relative_to(job).as_posix().casefold())
        state['files'][key]=verify.sha(path);verify.save(job/'status.json',state)
        with self.assertRaisesRegex(ValueError,'source text'):verify.require_verified(job)


if __name__=='__main__':unittest.main()
