"""Real side-map counterexamples, bottom transforms, and publication evidence gates."""
import copy,math,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import board_sides,verify

FIX=Path(__file__).parent/'fixtures/board_sides'

def job(key):return Path(verify.load(verify.ROOT/'work'/(key+'_v2.json'))['job'])

def fixture(name):return verify.load(FIX/(name+'.json')),verify.read_native(FIX/(name+'.tsv'))

class BoardSides(unittest.TestCase):
    def test_three_real_parts_have_sixteen_states_with_actual_layer_evidence(self):
        for key,count in [('f103',48),('pth',2),('opto',4)]:
            source=job(key);r=verify.load(source/'verification.json');self.assertTrue(r['checks']['psm_top_bottom_actual_layers'])
            self.assertEqual(len(r['actual_board_layers']),16);self.assertEqual(len(r['bottom_placement']),8)
            baseline=verify.read_native(source/'verification/native/drawing.tsv');pins={p['number']:p for p in baseline['pins']}
            for saved in (False,True):
                for i,angle in enumerate((0,90,180,270)):
                    tag=('moved' if angle==0 else f'saved{angle}') if saved else f'placed{angle}'
                    name='bottom_'+tag+'.tsv';anchor=(20+30*i+(10 if saved else 0),75 if saved else 60)
                    actual=verify.read_native(source/'verification/native'/name)
                    self.assertEqual(actual['anchor'],[*anchor,angle]);self.assertEqual(len(actual['pins']),count)
                    self.assertEqual(r['actual_board_layers'][name]['pin_layer_slots'],count*6)
                    for p in actual['pins']:
                        x,y=pins[p['number']]['at'];x=-x
                        u,v={0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[angle]
                        self.assertLess(math.dist(p['at'],(anchor[0]+u,anchor[1]+v)),.00021)
                    for t in actual['texts']:self.assertTrue(t['mirrored']);self.assertTrue(t['layer'].endswith('_BOTTOM'))

    def test_real_geometry_only_mirror_rejected_for_wrong_copper_side(self):
        f,p=fixture('geometry_only')
        with self.assertRaisesRegex(ValueError,'Unexpected actual pad on ETCH/TOP'):
            board_sides.check_layers(f['pads'],p['pins'],FIX/'geometry_only.layers',(20,60),0,True)

    def test_real_geometry_only_mirror_rejected_for_wrong_graphic_side(self):
        _,p=fixture('geometry_only')
        with self.assertRaisesRegex(ValueError,'wrong side'):board_sides.bottom_to_local(p,(20,60),0)

    def test_native_oblong_bottom90_normalizes_pins_graphics_and_boundary(self):
        f,p=fixture('bottom_pth90')
        r=board_sides.check_layers(f['pads'],p['pins'],FIX/'bottom_pth90.layers',(50,20),90,True)
        self.assertEqual(r['populated_polygons'],16)
        normalized=board_sides.bottom_to_local(p,(50,20),90)
        verify.compare_pins(f['pads'],normalized['pins'],f['model'])
        verify.compare_graphics(f['graphics'],normalized['graphics'])
        verify.compare_placement_boundary(f['graphics'],normalized['bounds'])

    def test_missing_pth_bottom_copper_is_rejected(self):
        f,p=fixture('bottom_pth90');rows=(FIX/'bottom_pth90.layers').read_text(encoding='ascii').splitlines()
        pin=p['pins'][0]['number'];changed=[]
        for line in rows:
            r=line.split('\t')
            if r[0]=='POLY' and r[1:3]==[pin,'ETCH/BOTTOM']:continue
            if r[0]=='PAD' and r[1:3]==[pin,'ETCH/BOTTOM']:line=f'PAD\t{pin}\tETCH/BOTTOM\t"NULL"\t0'
            changed.append(line)
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'bad.layers';target.write_text('\n'.join(changed)+'\n',encoding='ascii')
            with self.assertRaisesRegex(ValueError,'Missing/multiple actual pad polygons on ETCH/BOTTOM'):
                board_sides.check_layers(f['pads'],p['pins'],target,(50,20),90,True)

    def test_nonfinite_actual_layer_geometry_is_rejected(self):
        rows=(FIX/'bottom_smd.layers').read_text(encoding='ascii').splitlines()
        i=next(i for i,r in enumerate(rows) if r.startswith('POLY\t'));r=rows[i].split('\t');r[7]='NaN';rows[i]='\t'.join(r)
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'bad.layers';target.write_text('\n'.join(rows)+'\n',encoding='ascii')
            with self.assertRaisesRegex(ValueError,'Nonfinite actual board-layer'):board_sides.read_layers(target)

    def test_optional_mask_and_paste_absence_is_not_assumed_present(self):
        f,p=fixture('bottom_smd');pads=copy.deepcopy(f['pads'])
        for pin in pads:pin['layers']=['F.Cu']
        rows=[]
        for line in (FIX/'bottom_smd.layers').read_text(encoding='ascii').splitlines():
            r=line.split('\t')
            if r[0]=='POLY' and r[2].startswith('PIN/'):continue
            if r[0]=='PAD' and r[2].startswith('PIN/'):line='\t'.join(r[:3]+['"NULL"','0'])
            rows.append(line)
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'copper_only.layers';target.write_text('\n'.join(rows)+'\n',encoding='ascii')
            result=board_sides.check_layers(pads,p['pins'],target,(20,20),0,True)
            self.assertEqual(result['populated_polygons'],48)

    def test_unmirrored_bottom_refdes_is_rejected_before_normalization(self):
        _,p=fixture('bottom_pth90');p['texts'][0]['mirrored']=False
        with self.assertRaisesRegex(ValueError,'Bottom RefDes is not mirrored'):board_sides.bottom_to_local(p,(50,20),90)

    def test_old_report_without_side_coverage_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'job';shutil.copytree(job('f103'),target);r=verify.load(target/'verification.json')
            r['checks'].pop('psm_top_bottom_actual_layers',None);verify.save(target/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Top/bottom PSM placement'):verify.require_verified(target)

    def test_missing_bottom_saved_evidence_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'job';shutil.copytree(job('f103'),target)
            (target/'verification/native/bottom_saved270.tsv.layers').unlink()
            with self.assertRaisesRegex(ValueError,'Test evidence changed/missing: .*bottom_saved270.tsv.layers'):verify.require_verified(target)

    def test_forged_success_and_digest_cannot_hide_wrong_bottom_refdes_mirror(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'job';shutil.copytree(job('f103'),target);path=target/'verification/native/bottom_saved90.tsv'
            rows=path.read_text(encoding='ascii').splitlines();changed=False
            for i,line in enumerate(rows):
                if line.startswith('T\tREF DES/SILKSCREEN_BOTTOM\t'):
                    fields=line.split('\t');fields[13]='nil';rows[i]='\t'.join(fields);changed=True
            self.assertTrue(changed);path.write_text('\n'.join(rows)+'\n',encoding='ascii')
            r=verify.load(target/'verification.json');key=next(k for k in r['evidence_sha256'] if k.replace('\\','/')=='verification/native/bottom_saved90.tsv')
            r['evidence_sha256'][key]=verify.sha(path);verify.save(target/'verification.json',r)
            with self.assertRaisesRegex(ValueError,'Bottom RefDes is not mirrored'):verify.require_verified(target)


if __name__=='__main__':unittest.main()
