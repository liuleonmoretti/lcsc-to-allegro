"""Real DIP4 source plus deliberately coherent wrong geometry and fit claims."""
import copy,math,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify,reviewed_profiles

class OptoProfile(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='光耦通孔中文_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        original=Path(verify.load(lc2a.ROOT/'work/opto_v2.json')['job']);self.s=verify.load(original/'status.json')
        self.original=original
        for rel in self.s['files']:
            if rel.replace('\\','/').startswith('native/'):continue
            dst=self.job/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original/rel,dst)
        self.source=self.job/self.s['source_file'];self.refresh()
    def refresh(self):
        self.s['source_sha256']=verify.sha(self.source);self.s['review_profile']['reviewed_sha256']=verify.sha(self.source)
        verify.save(self.job/'reviewed/review.json',self.s['review_profile'])
        self.s['files']={p.relative_to(self.job).as_posix():verify.sha(p) for p in self.job.rglob('*') if p.is_file()}
    def tree(self):return sx.loads(self.source.read_text(encoding='utf-8'))
    def write(self,tree):
        self.source.write_text(sx.dumps(tree),encoding='utf-8');self.refresh()
        # Producer can represent this geometry. Matching that model is not the oracle.
        lc2a.parse(self.source,body_bounds=self.s['conversion_options']['body_bounds'],mask=.05)
    def check(self):
        pads,graphics=verify.read_expected(self.job,self.s)
        return verify.profile_checks(self.s,pads,verify.design_rules(pads,graphics),graphics)
    def marker(self,tree):return next(n for n in tree if verify.tag(n)=='fp_circle')
    def test_real_native_readback_matches_documented_pinout(self):
        r=verify.load(self.original/'verification.json');self.assertEqual(r['status'],'passed')
        n=r['manufacturer_profile_native'];self.assertEqual(n['pin_mapping'],{'1':'Anode','2':'Cathode','3':'Emitter','4':'Collector'})
        self.assertEqual(n['native_minimum_drill_mm'],1.);self.assertNotIn('source_minimum_drill_mm',n)
        for gate in ('default_view_refdes_font_clearance','raw_easyeda_pad_geometry','psm_loaded_placed_rotated90_moved'):
            self.assertTrue(r['checks'][gate])
    def test_body_includes_maximum_tolerance_not_just_nominal(self):
        r=self.check();self.assertEqual(r['body_maximum_mm'],[5.1,7.0])
        self.assertAlmostEqual(r['assembly_body_geometry']['area_mm2'],35.7)
    def test_source_y_flip_preserves_anode_corner(self):
        pads,_=verify.read_expected(self.job,self.s);coords={p['number']:p['at'] for p in pads}
        self.assertEqual(coords,{'1':[-1.27,-3.81],'2':[1.27,-3.81],'3':[1.27,3.81],'4':[-1.27,3.81]})
        self.assertEqual(self.check()['pin1_marker_center_mm'],[-1.8,-2.35])
    def test_swapped_numbers_do_not_pass_manufacturer_check(self):
        tree=self.tree();pads=[n for n in tree if verify.tag(n)=='pad'];pd={str(n[1]):n for n in pads}
        pd['2'][1],pd['3'][1]='3','2';self.write(tree)
        with self.assertRaisesRegex(ValueError,'pin mapping'):self.check()
    def test_source_body_shrunk_to_nominal_is_rejected(self):
        tree=self.tree()
        for n in tree:
            if verify.tag(n)=='fp_line' and str(verify.get(n,'layer')[1])=='F.Fab':
                for key in ('start','end'):
                    p=verify.get(n,key);p[1]*=4.6/5.1;p[2]*=6.5/7.
        self.write(tree)
        with self.assertRaisesRegex(ValueError,'maximum body envelope'):self.check()
    def test_wrong_marker_corner_is_rejected(self):
        tree=self.tree();mark=self.marker(tree)
        for key in ('center','end'):
            p=verify.get(mark,key);p[1]*=-1;p[2]*=-1
        self.write(tree)
        with self.assertRaisesRegex(ValueError,'pin 1 / anode corner'):self.check()
    def test_missing_marker_is_rejected(self):
        tree=self.tree();tree.remove(self.marker(tree));self.write(tree)
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):self.check()
    def test_duplicate_marker_is_rejected(self):
        tree=self.tree();tree.append(copy.deepcopy(self.marker(tree)));self.write(tree)
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):self.check()
    def test_wrong_hole_is_rejected_even_with_plenty_of_copper_ring(self):
        tree=self.tree()
        for p in tree:
            if verify.tag(p)=='pad':verify.get(p,'drill')[1]=.70
        self.write(tree)
        with self.assertRaisesRegex(ValueError,'oval pad/hole/orientation'):self.check()
    def test_oval_rotated_90_degrees_is_rejected(self):
        tree=self.tree()
        for p in tree:
            if verify.tag(p)=='pad':verify.get(p,'at')[3]=90
        self.write(tree)
        with self.assertRaisesRegex(ValueError,'Silkscreen-to-mask clearance'):self.check()
        pads,g=verify.read_expected(self.job,self.s)
        with self.assertRaisesRegex(ValueError,'oval pad/hole/orientation'):
            verify.profile_checks(self.s,pads,{'body_bounds_mm':[-2.55,-3.5,2.55,3.5]},g)
    def test_missing_bottom_copper_is_rejected(self):
        tree=self.tree()
        for p in tree:
            if verify.tag(p)=='pad':verify.get(p,'layers')[1:]=list(map(sx.Symbol,('F.Cu','F.Mask')))
        self.source.write_text(sx.dumps(tree),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'missing copper layers'):verify.read_expected(self.job,self.s)
        pads,g=verify.read_expected(self.original,verify.load(self.original/'status.json'))
        for p in pads:p['layers']=['F.Cu','F.Mask']
        with self.assertRaisesRegex(ValueError,'pad layers'):verify.profile_checks(self.s,pads,verify.design_rules(pads,g),g)
    def test_mask_policy_cannot_be_changed_with_source_and_output(self):
        tree=self.tree()
        for p in tree:
            if verify.tag(p)=='pad':verify.get(p,'solder_mask_margin')[1]=.10
        self.write(tree)
        with self.assertRaisesRegex(ValueError,'mask margin'):self.check()
    def test_unknown_thickness_tolerance_stays_unknown(self):
        r=self.check();self.assertIsNone(r['lead_thickness_tolerance_mm']);self.assertIsNone(r['fully_toleranced_lead_diagonal_mm'])
        self.assertAlmostEqual(r['width_max_thickness_nominal_diagonal_mm'],math.hypot(.6,.26),8)
        self.assertFalse(r['manufactured_fit_verified']);self.assertFalse(r['formed_lead_fit_verified'])
    def test_invented_thickness_tolerance_is_rejected(self):
        self.s['review_profile']['dimensional_basis']['lead_thickness_tolerance_mm']=.05;self.refresh()
        with self.assertRaisesRegex(ValueError,'unverified fit limits'):verify.check_manufacturer_evidence(self.job,self.s)
    def test_invented_fully_toleranced_diagonal_is_rejected(self):
        self.s['review_profile']['dimensional_basis']['fully_toleranced_lead_diagonal_mm']=.65391131;self.refresh()
        with self.assertRaisesRegex(ValueError,'unverified fit limits'):verify.check_manufacturer_evidence(self.job,self.s)
    def test_invented_manufacturer_recommended_hole_is_rejected(self):
        self.s['review_profile']['dimensional_basis']['manufacturer_recommended_pcb_hole_mm']=1.;self.refresh()
        with self.assertRaisesRegex(ValueError,'unverified fit limits'):verify.check_manufacturer_evidence(self.job,self.s)
    def test_invented_fit_approval_is_rejected(self):
        for field in ('manufactured_fit_verified','formed_lead_fit_verified'):
            with self.subTest(field=field):
                self.s['review_profile']['dimensional_basis'][field]=True;self.refresh()
                with self.assertRaisesRegex(ValueError,'unverified fit limits'):verify.check_manufacturer_evidence(self.job,self.s)
                self.s['review_profile']['dimensional_basis'][field]=False
    def test_pdf_version_is_pinned_even_if_record_and_hash_agree(self):
        p=self.job/self.s['review_profile']['datasheet'];p.write_bytes(b'%PDF-fake replacement\n')
        self.s['review_profile']['datasheet_sha256']=verify.sha(p);self.refresh()
        with self.assertRaisesRegex(ValueError,'independently reviewed document'):verify.check_manufacturer_evidence(self.job,self.s)
    def test_document_page_cannot_silently_use_wide_body_variant(self):
        self.s['review_profile']['review_basis']['pdf_pages_1_based']=[3,11];self.refresh()
        with self.assertRaisesRegex(ValueError,'review basis'):verify.check_manufacturer_evidence(self.job,self.s)
    def test_wrong_catalog_rank_does_not_reuse_profile(self):
        rawfile=self.job/self.s['fetch_evidence']['response_file'];raw=verify.load(rawfile)
        raw['result']['dataStr']['head']['c_para']['Manufacturer Part']='LTV-817-B';verify.save(rawfile,raw)
        self.s['fetch_evidence']['response_sha256']=verify.sha(rawfile);self.refresh()
        with self.assertRaisesRegex(ValueError,'Manufacturer catalog'):lc2a.catalog_profile_identity(raw,'C62715')
        with self.assertRaisesRegex(ValueError,'Manufacturer catalog'):verify.check_fetch_evidence(self.job,self.s)
    def test_unknown_profile_does_not_get_diode_datasheet(self):
        with self.assertRaisesRegex(ValueError,'Unknown manufacturer'):reviewed_profiles.reviewed_document('C00000')
    def test_saved_review_and_independent_evidence_agree(self):
        r=verify.check_manufacturer_evidence(self.job,self.s)
        self.assertEqual(r['review_basis']['document_date'],'2010-01')
        self.assertIsNone(r['review_basis']['document_revision'])

if __name__=='__main__':unittest.main()
