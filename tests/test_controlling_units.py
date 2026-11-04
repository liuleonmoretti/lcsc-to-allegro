"""Do not let a rounded secondary dimension table certify an undersized body."""
import copy,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,reviewed_profiles,verify

class ControllingUnits(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='英寸控制尺寸_');self.addCleanup(self.tmp.cleanup);self.job=Path(self.tmp.name)
        original=Path(verify.load(lc2a.ROOT/'work/pth_v2.json')['job']);self.state=verify.load(original/'status.json')
        source=self.job/'original.kicad_mod';shutil.copy2(original/self.state['review_profile']['original_source'],source)
        final,record=reviewed_profiles.prepare(source,'C53568',self.job)
        self.state.update(source_file=final.relative_to(self.job).as_posix(),review_profile=record,source_sha256=verify.sha(final))
        self.state['conversion_options'].update(body_bounds=record['body_bounds_mm'],mask_margin=record['mask_margin_mm'])
        self.source=final;self.refresh()
    def refresh(self):
        verify.save(self.job/'reviewed/review.json',self.state['review_profile'])
        self.state['files']={str(p.relative_to(self.job)):verify.sha(p) for p in self.job.rglob('*') if p.is_file()}
    def check(self):
        pads,graphics=verify.read_expected(self.job,self.state)
        return verify.profile_checks(self.state,pads,verify.design_rules(pads,graphics),graphics)
    def set_body_length(self,value):
        tree=sx.loads(self.source.read_text(encoding='utf-8'))
        for item in tree:
            if verify.tag(item)=='fp_line' and str(verify.get(item,'layer')[1])=='F.Fab':
                for field in ('start','end'):
                    point=verify.get(item,field);point[1]=(-1 if point[1]<0 else 1)*value/2
        self.source.write_text(sx.dumps(tree),encoding='utf-8')
        # Coherent producer conversion is possible, but manufacturer acceptance
        # must reject this geometry irrespective of an updated model/hash.
        lc2a.parse(self.source,mask=.05)
    def test_maximums_follow_inch_not_secondary_metric_column(self):
        r=self.check()
        self.assertEqual(r['body_maximum_mm'],[5.207,2.6924])
        self.assertEqual(r['max_lead_diameter_mm'],.8636)
        self.assertEqual(r['nominal_diametral_allowance_mm'],.1364)
        self.assertAlmostEqual(r['source_minimum_diametral_allowance_mm'],r['source_minimum_drill_mm']-.8636)
        self.assertEqual(r['controlling_unit'],'inch')
    def test_outward_envelope_covers_true_maximum(self):
        record=self.state['review_profile'];x1,y1,x2,y2=record['body_bounds_mm']
        self.assertAlmostEqual(x2-x1,5.21);self.assertAlmostEqual(y2-y1,2.70)
        self.assertGreaterEqual(x2-x1,5.207);self.assertGreaterEqual(y2-y1,2.6924)
        self.assertEqual([x1+x2,y1+y2],[0,0]);self.assertAlmostEqual(self.check()['assembly_body_geometry']['area_mm2'],14.067)
    def test_native_drill_evidence_is_not_labelled_source(self):
        pads,graphics=verify.read_expected(self.job,self.state)
        for p in pads:p['drill']=1.
        report=verify.profile_checks(self.state,pads,verify.design_rules(pads,graphics),graphics,geometry_origin='native')
        self.assertEqual(report['native_minimum_drill_mm'],1.);self.assertEqual(report['native_minimum_diametral_allowance_mm'],.1364)
        self.assertNotIn('source_minimum_drill_mm',report)
    def test_old_metric_5p20_body_rejected_even_with_coherent_conversion(self):
        self.set_body_length(5.20)
        with self.assertRaisesRegex(ValueError,'controlling inch maximum'):self.check()
    def test_just_under_controlling_maximum_is_rejected(self):
        self.set_body_length(5.2068)
        with self.assertRaisesRegex(ValueError,'controlling inch maximum'):self.check()
    def test_wrong_units_or_conversion_factor_not_hidden_by_hashes(self):
        original=copy.deepcopy(self.state['review_profile']['dimensional_basis'])
        for field,value in (('controlling_unit','mm'),('mm_per_inch',25),('envelope_policy','round to nearest')):
            with self.subTest(field=field):
                self.state['review_profile']['dimensional_basis']=dict(original,**{field:value});self.refresh()
                with self.assertRaisesRegex(ValueError,'controlling-unit dimensional basis'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_rounded_metric_maximums_cannot_replace_controlling_limits(self):
        original=copy.deepcopy(self.state['review_profile']['dimensional_basis'])
        for dimension,value in (('A',5.2),('B',2.7),('D',.86),('F',1.2)):
            with self.subTest(dimension=dimension):
                self.state['review_profile']['dimensional_basis']=copy.deepcopy(original)
                self.state['review_profile']['dimensional_basis']['maximum_dimensions_mm'][dimension]=value;self.refresh()
                with self.assertRaisesRegex(ValueError,'controlling-unit dimensional basis'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_nominal_allowance_is_not_manufacturing_fit_approval(self):
        r=self.check();self.assertFalse(r['manufactured_fit_verified']);self.assertEqual(r['lead_diameter_uncontrolled_within_F_mm'],1.27)
        self.state['review_profile']['dimensional_basis']['manufactured_fit_verified']=True;self.refresh()
        with self.assertRaisesRegex(ValueError,'controlling-unit dimensional basis'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_uncontrolled_near_body_region_cannot_be_omitted(self):
        self.state['review_profile']['dimensional_basis'].pop('lead_diameter_uncontrolled_within_F');self.refresh()
        with self.assertRaisesRegex(ValueError,'controlling-unit dimensional basis'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_missing_unit_basis_rejected(self):
        self.state['review_profile'].pop('dimensional_basis');self.refresh()
        with self.assertRaisesRegex(ValueError,'controlling-unit dimensional basis'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_old_do41_recipe_revision_requires_regeneration(self):
        self.state['review_profile']['revision']=4;self.refresh()
        with self.assertRaisesRegex(ValueError,'unreviewed recipe revision'):verify.check_manufacturer_evidence(self.job,self.state)
    def test_producer_document_and_independent_acceptance_agree(self):
        r=verify.check_manufacturer_evidence(self.job,self.state)
        self.assertEqual(r['review_basis']['policy_id'],'C53568_DO41_REVIEW_2')
        self.assertEqual(self.state['review_profile']['dimensional_basis']['nominal_diametral_allowance_mm'],self.check()['nominal_diametral_allowance_mm'])

if __name__=='__main__':unittest.main()
