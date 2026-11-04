import argparse, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a

class Publication(unittest.TestCase):
    def test_conflict_prevents_partial_publish(self):
        report=lc2a.ROOT/'work/f103_v2.json'
        if not report.exists():self.skipTest('Run real C17414 integration build first')
        info=json.loads(report.read_text(encoding='utf-8-sig'))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cat=root/'footprint/MCU';cat.mkdir(parents=True)
            conflict=cat/(info['name']+'.psm');conflict.write_bytes(b'existing unrelated footprint')
            args=argparse.Namespace(job=info['job'],library=str(root),category='MCU',specific=False,review_note='test only')
            with self.assertRaisesRegex(ValueError,'Existing different definition'):lc2a.publish(args)
            self.assertFalse((cat/(info['name']+'.dra')).exists())
            self.assertEqual(conflict.read_bytes(),b'existing unrelated footprint')
            self.assertFalse((root/'pad').exists())

if __name__=='__main__':unittest.main()
