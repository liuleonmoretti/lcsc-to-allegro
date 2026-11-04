"""Coherent wrong source/output still needs manufacturer and reviewed-policy rejection."""
import copy,shutil,sys,tempfile,unittest
from pathlib import Path
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify,reviewed_profiles

def node(n,k):return next((v for v in n if isinstance(v,list) and v and str(v[0])==k),None)
def tag(n):return str(n[0]) if isinstance(n,list) and n else ''
def set_mask(p,value):
    prior=node(p,'solder_mask_margin')
    if prior is None:p.append([sx.Symbol('solder_mask_margin'),value])
    else:prior[1]=value

class ProfileIndependence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples={}
        for key in ('f103','pth'):
            pointer=lc2a.ROOT/'work'/(key+'_v2.json')
            if not pointer.is_file():raise unittest.SkipTest('Real integration samples required')
            job=Path(verify.load(pointer)['job']);status,_=verify.check_build(job)
            tree=sx.loads((job/status['source_file']).read_text(encoding='utf-8'))
            cls.samples[key]=(job,status,tree)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def tree(self,key):return copy.deepcopy(self.samples[key][2])
    def parse(self,key,tree,status_change=None):
        _,s,_=self.samples[key];s=copy.deepcopy(s);s['source_file']='coherent_source.kicad_mod'
        if status_change:status_change(s)
        source=self.root/s['source_file'];source.write_text(sx.dumps(tree),encoding='utf-8')
        # The producer accepts these geometries; matching its output alone is insufficient.
        lc2a.parse(source,body_bounds=s['conversion_options']['body_bounds'],mask=s['conversion_options']['mask_margin'])
        pads,graphics=verify.read_expected(self.root,s)
        return s,pads,graphics
    def rejected(self,key,tree,message):
        s,p,g=self.parse(key,tree);metrics=verify.design_rules(p,g)
        with self.assertRaisesRegex(ValueError,message):verify.profile_checks(s,p,metrics,g)
    def marker(self,tree):return next(n for n in tree if tag(n)=='fp_circle')
    def band(self,tree):
        return next(n for n in tree if tag(n)=='fp_line' and str(node(n,'layer')[1])=='F.SilkS' and abs(float(node(n,'start')[1])+1.9)<.001)
    def test_pin1_mark_on_opposite_corner(self):
        tree=self.tree('f103');mark=self.marker(tree)
        for k in ('center','end'):
            n=node(mark,k);n[1]=-n[1];n[2]=-n[2]
        self.rejected('f103',tree,'pin 1 corner zone')
    def test_pin1_mark_missing(self):
        tree=self.tree('f103');tree.remove(self.marker(tree));self.rejected('f103',tree,'marker is missing')
    def test_pin1_marker_ambiguous(self):
        tree=self.tree('f103');tree.append(copy.deepcopy(self.marker(tree)));self.rejected('f103',tree,'marker is missing or ambiguous')
    def test_whole_marker_must_fit_pin1_zone(self):
        tree=self.tree('f103');mark=self.marker(tree)
        node(mark,'center')[1:]=[-1.75,2.75];node(mark,'end')[1:]=[-1.60,2.75]
        self.rejected('f103',tree,'pin 1 corner zone')
    def test_marker_allowed_elsewhere_in_identification_zone(self):
        tree=self.tree('f103');mark=self.marker(tree)
        node(mark,'center')[1:]=[-2.75,2.75];node(mark,'end')[1:]=[-2.60,2.75]
        s,p,g=self.parse('f103',tree);r=verify.profile_checks(s,p,verify.design_rules(p,g),g)
        self.assertEqual(r['pin1_marker_center_mm'],[-2.75,-2.75])
    def test_do41_small_body(self):
        tree=self.tree('pth')
        for n in tree:
            if tag(n)=='fp_line' and str(node(n,'layer')[1])=='F.Fab':
                for key in ('start','end'):
                    pt=node(n,key);pt[1]*=.6;pt[2]*=.6
        self.rejected('pth',tree,'body outline differs')
    def test_do41_cathode_band_at_anode(self):
        tree=self.tree('pth');band=self.band(tree)
        for key in ('start','end'):node(band,key)[1]=1.9
        self.rejected('pth',tree,'Cathode band is not')
    def test_do41_cathode_band_missing(self):
        tree=self.tree('pth');tree.remove(self.band(tree));self.rejected('pth',tree,'Cathode band is missing')
    def test_do41_both_ends_marked(self):
        tree=self.tree('pth');other=copy.deepcopy(self.band(tree))
        for key in ('start','end'):node(other,key)[1]=1.9
        tree.append(other);self.rejected('pth',tree,'Cathode band is missing or ambiguous')
    def test_do41_band_not_tied_to_recipe_coordinate(self):
        tree=self.tree('pth');band=self.band(tree)
        for key in ('start','end'):node(band,key)[1]=-2.2
        s,p,g=self.parse('pth',tree);r=verify.profile_checks(s,p,verify.design_rules(p,g),g)
        self.assertEqual(r['cathode_band_x_mm'],-2.2)
    def test_profile_mask_policy_cannot_be_overridden(self):
        for key in ('f103','pth'):
            with self.subTest(profile=key):
                tree=self.tree(key)
                for p in [n for n in tree if tag(n)=='pad']:set_mask(p,-.10)
                self.rejected(key,tree,'solder mask margin differs')
    def test_f103_paste_must_exist(self):
        tree=self.tree('f103')
        for p in [n for n in tree if tag(n)=='pad']:
            layers=node(p,'layers');layers[:]=[v for v in layers if str(v)!='F.Paste']
        self.rejected('f103',tree,'pad layers differ')
    def test_profile_c_number_must_be_unique_and_match_source(self):
        for case in ('missing','duplicate','wrong'):
            with self.subTest(case=case):
                tree=self.tree('f103');identity=next(n for n in tree if tag(n)=='property' and str(n[1])=='LCSC Part')
                if case=='missing':tree.remove(identity)
                elif case=='duplicate':tree.append(copy.deepcopy(identity))
                else:identity[2]='C99999'
                with self.assertRaisesRegex(ValueError,'profile/source C-number'):self.parse('f103',tree)
    def test_requested_c_number_must_match(self):
        with self.assertRaisesRegex(ValueError,'profile/requested C-number'):
            self.parse('f103',self.tree('f103'),lambda s:s.update(lcsc='C53568'))
    def test_unknown_profile_not_assumed_to_be_do41(self):
        s,p,g=self.parse('pth',self.tree('pth'));s['review_profile']['profile']='UNKNOWN'
        with self.assertRaisesRegex(ValueError,'Unknown manufacturer profile'):verify.profile_checks(s,p,verify.design_rules(p,g),g)
        with self.assertRaisesRegex(ValueError,'Unknown manufacturer profile'):reviewed_profiles.prepare(self.root/'absent.kicad_mod','UNKNOWN',self.root)
    def original(self,key):
        job,s,_=self.samples[key]
        return sx.loads((job/s['review_profile']['original_source']).read_text(encoding='utf-8'))
    def prepare(self,key,tree):
        job=self.root/key;job.mkdir(exist_ok=True);src=job/'original.kicad_mod';src.write_text(sx.dumps(tree),encoding='utf-8')
        return reviewed_profiles.prepare(src,self.samples[key][1]['review_profile']['profile'],job)
    def test_recipe_rejects_conflicting_local_mask(self):
        for key in ('f103','pth'):
            with self.subTest(profile=key):
                tree=self.original(key);set_mask(next(n for n in tree if tag(n)=='pad'),-.10)
                with self.assertRaisesRegex(ValueError,'local mask margin conflicts'):self.prepare(key,tree)
    def test_recipe_preserves_matching_local_mask(self):
        for key,value in (('f103',.03),('pth',.05)):
            with self.subTest(profile=key):
                tree=self.original(key);set_mask(next(n for n in tree if tag(n)=='pad'),value)
                output,_=self.prepare(key,tree);p=next(n for n in sx.loads(output.read_text(encoding='utf-8')) if tag(n)=='pad')
                self.assertEqual(node(p,'solder_mask_margin')[1],value)
    def test_recipe_rejects_duplicate_identity(self):
        tree=self.original('f103');tree.append(copy.deepcopy(next(n for n in tree if tag(n)=='property' and str(n[1])=='LCSC Part')))
        with self.assertRaisesRegex(ValueError,'ambiguity'):self.prepare('f103',tree)
    def copy_job(self):
        job=self.root/'job';shutil.copytree(self.samples['f103'][0],job);return job
    def test_old_profile_acceptance_requires_new_check(self):
        job=self.copy_job();report=verify.load(job/'verification.json');report['checks'].pop('manufacturer_profile_body_polarity',None)
        verify.save(job/'verification.json',report)
        with self.assertRaisesRegex(ValueError,'body/polarity policy'):verify.require_verified(job)
    def test_intentional_fault_sample_is_never_publishable(self):
        job=self.copy_job();s=verify.load(job/'status.json');s['fault_injection']={'test_only':True}
        verify.save(job/'status.json',s)
        with self.assertRaisesRegex(ValueError,'Intentional fault experiment'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
