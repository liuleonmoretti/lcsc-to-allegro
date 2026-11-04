"""Invalid names must fail before tasks, downloads or native processes exist."""
import argparse,contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify,library


class NativeNamePreflight(unittest.TestCase):
    def rejected_before_side_effects(self,name,error='1-31 characters'):
        with tempfile.TemporaryDirectory(prefix='中文名称规则_') as folder:
            root=Path(folder)
            with patch.object(lc2a,'ROOT',root),patch.object(lc2a,'run') as run,patch.object(lc2a,'native_build') as native:
                with self.assertRaisesRegex(ValueError,error):
                    lc2a.convert(argparse.Namespace(name=name,refdes='U',lcsc='C8734'))
                run.assert_not_called();native.assert_not_called();self.assertEqual(list(root.iterdir()),[])

    def test_32_character_name_rejected_before_download(self):self.rejected_before_side_effects('A'*32)

    def test_former_46_character_limit_rejected_early(self):self.rejected_before_side_effects('A'*46)

    def test_chinese_basename_has_clear_label_guidance(self):self.rejected_before_side_effects('中文封装')

    def test_path_or_shell_characters_are_not_accepted_as_names(self):
        for name in ('../ABC','ABC/DEF','A$(x)','A;B','A"B'):
            with self.subTest(name=name):self.rejected_before_side_effects(name)

    def test_windows_devices_rejected_before_download(self):
        for name in ('CON','con','NUL','AUX','PRN','COM1','com9','LPT1','Lpt9'):
            with self.subTest(name=name):self.rejected_before_side_effects(name,'Windows reserved device')

    def test_direct_native_entry_rejects_devices_before_seed_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(lc2a,'ROOT',Path(folder)),patch.object(lc2a.shutil,'copy2') as copy,patch.object(lc2a,'run') as native:
                with self.assertRaisesRegex(ValueError,'Windows reserved device'):
                    lc2a.native_build(Path(folder),{},'NUL','U')
                copy.assert_not_called();native.assert_not_called();self.assertEqual(list(Path(folder).iterdir()),[])

    def test_independent_reader_rejects_device_before_reading_manifest_files(self):
        with tempfile.TemporaryDirectory() as folder:
            job=Path(folder);verify.save(job/'status.json',dict(status='built_and_geometry_checked',program_version='0.2.0',name='COM1'))
            with self.assertRaisesRegex(ValueError,'Windows reserved device'):verify.check_build(job)

    def test_publication_plan_rejects_device_before_any_library_write(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);job=base/'job';job.mkdir();root=base/'library';(root/'footprint/MCU').mkdir(parents=True)
            verify.save(job/'model.json',dict(pads=[]));verify.save(job/'status.json',dict(name='LPT1',files={'model.json':verify.sha(job/'model.json')}))
            accepted=dict(status_sha256=verify.sha(job/'status.json'),evidence_sha256={});before=library.snapshot(root);dirs=library.directory_snapshot(root)
            with patch.object(verify,'require_verified',return_value=accepted):
                with self.assertRaisesRegex(ValueError,'Windows reserved device'):library.plan_publication(job,root,'MCU',False,'device-name test','test',True)
            self.assertEqual(library.snapshot(root),before);self.assertEqual(library.directory_snapshot(root),dirs)

    def test_nearby_ordinary_names_are_not_rejected_as_device_prefixes(self):
        for name in ('CON_PART','NUL_PAD','COM10','LPT10','AUX_1','prn-part'):
            with self.subTest(name=name):lc2a.validate_native_name(name)

    def test_valid_31_character_name_and_chinese_label_prepare_without_truncation(self):
        with tempfile.TemporaryDirectory(prefix='中文名称边界_') as folder:
            root=Path(folder);source=root/'中文输入.kicad_mod'
            source.write_text('''(module T (layer F.Cu)
            (fp_rect (start -2 -2) (end 2 2) (layer F.Fab) (width .1))
            (fp_rect (start -3 -3) (end 3 3) (layer F.CrtYd) (width .05))
            (fp_rect (start -2.3 -2.3) (end 2.3 2.3) (layer F.SilkS) (width .1))
            (pad 1 smd circle (at 0 0) (size .8 .8) (layers F.Cu F.Paste F.Mask)))''',encoding='utf-8')
            name='A'*31;label='中文说明保留'
            args=argparse.Namespace(name=name,input=source,lcsc=None,profile=None,refdes='U',label=label,center=None,body_bounds=None,mask_margin=0,prepare_only=True)
            stream=io.StringIO()
            with patch.object(lc2a,'ROOT',root),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(stream):
                self.assertEqual(lc2a.convert(args),0);native.assert_not_called()
            record=json.loads(stream.getvalue());self.assertEqual(record['name'],name);self.assertEqual(record['label'],label)
            self.assertEqual(record['status'],'prepared_only');self.assertTrue((Path(record['job'])/'build.il').is_file())


if __name__=='__main__':unittest.main()
