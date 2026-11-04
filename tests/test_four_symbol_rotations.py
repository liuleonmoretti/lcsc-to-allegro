"""Verify real saved quarter-turn states and reject incomplete release evidence."""
import math,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

STATES=(('placed0.tsv',0,(20,20)),('placed90.tsv',90,(50,20)),('placed180.tsv',180,(80,20)),
        ('placed270.tsv',270,(110,20)),('moved.tsv',0,(30,35)),('saved90.tsv',90,(60,35)),
        ('saved180.tsv',180,(90,35)),('saved270.tsv',270,(120,35)))

def transform(point,angle,anchor):
    x,y=point;u,v={0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[angle]
    return [anchor[0]+u,anchor[1]+v]

def real_job(key):return Path(verify.load(lc2a.ROOT/'work'/(key+'_v2.json'))['job'])

class FourSymbolRotations(unittest.TestCase):
    def test_three_real_packages_keep_pins_and_text_at_all_eight_states(self):
        for key,count in [('f103',48),('pth',2),('opto',4)]:
            job=real_job(key);report=verify.load(job/'verification.json')
            self.assertTrue(report['checks']['psm_all_quarter_turns_saved'])
            source=verify.read_native(job/'verification/native/drawing.tsv')
            for filename,angle,anchor in STATES:
                state=verify.read_native(job/'verification/native'/filename)
                self.assertEqual(state['anchor'],[*anchor,angle]);self.assertEqual(len(state['pins']),count)
                pins={p['number']:p for p in state['pins']}
                for pin in source['pins']:
                    actual=pins[pin['number']]
                    self.assertLess(math.dist(actual['at'],transform(pin['at'],angle,anchor)),.00021)
                    self.assertEqual(actual['angle'],(pin['angle']+angle)%360)
                    self.assertEqual(actual['through'],pin['through']);self.assertEqual(actual['padstack'],pin['padstack'])
                texts={t['layer']:t for t in state['texts']}
                for text in source['texts']:
                    actual=texts[text['layer']]
                    self.assertEqual(actual['text'],text['text']);self.assertFalse(actual['mirrored'])
                    self.assertLess(math.dist(actual['at'],transform(text['at'],angle,anchor)),.00021)
                    self.assertEqual(actual['angle'],(text['angle']+angle)%360)
                self.assertIn(filename,report['placement_boundary']);self.assertIn(filename,report['native_enclosure'])
    def test_missing_saved270_evidence_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            job=Path(temporary)/'job';shutil.copytree(real_job('f103'),job)
            missing=job/'verification/native/saved270.tsv';self.assertTrue(missing.is_file());missing.unlink()
            with self.assertRaisesRegex(ValueError,'Test evidence changed/missing: .*saved270.tsv'):verify.require_verified(job)
    def test_old_report_without_saved_quarter_turn_coverage_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            job=Path(temporary)/'job';shutil.copytree(real_job('f103'),job);report=verify.load(job/'verification.json')
            report['checks'].pop('psm_all_quarter_turns_saved',None);verify.save(job/'verification.json',report)
            with self.assertRaisesRegex(ValueError,'All four PSM orientations'):verify.require_verified(job)
    def test_drawing_extent_covers_large_rotated_text_at_far_saved_anchor(self):
        drawing=dict(pins=[],graphics=[],texts=[{'bounds':[-2,80,2,82]}],bounds=[{'bounds':[-50,-80,50,80]}])
        # At 270 degrees the top of this label becomes x=82; the saved symbol is at x=120.
        self.assertGreaterEqual(verify.test_drawing_limit(drawing,{'pads':[]}),212)


if __name__=='__main__':unittest.main()
