"""Wrong API success data must stop before native generation and independent release."""
import argparse,contextlib,copy,io,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sexpdata as sx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import lc2a,verify

class FetchIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lc2a_中文来源_');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.data={'success':True,'code':0,'result':{'uuid':'component-id','lcsc':{'number':'C1234'},'szlcsc':{'number':'C1234'},
            'dataStr':{'head':{'docType':'2','uuid':'component-id','puuid':'package-id','c_para':{'Supplier Part':'C1234'}}},
            'packageDetail':{'uuid':'package-id','dataStr':{'head':{'docType':'4','uuid':'package-id'}}}}}
        self.tree=sx.loads((lc2a.ROOT/'tests/header_1x2.kicad_mod').read_text())
        self.tree.append(sx.loads('(property "LCSC Part" "C1234")'))
        self.job=self.root/'sample';self.source=self.job/'source/library.pretty/中文排针.kicad_mod';self.source.parent.mkdir(parents=True)
        self.raw=self.job/'source/.easyeda_cache/C1234.json';self.raw.parent.mkdir()
    def write(self,data=None,tree=None,raw=None):
        self.source.write_text(sx.dumps(self.tree if tree is None else tree),encoding='utf-8')
        self.raw.write_text(json.dumps(self.data if data is None else data,ensure_ascii=False) if raw is None else raw,encoding='utf-8')
    def status(self):
        e=lc2a.downloaded_identity(self.job,self.source,'C1234')
        return dict(lcsc='C1234',fetch_evidence=e,source_file=self.source.relative_to(self.job).as_posix(),files={str(p.relative_to(self.job)):verify.sha(p) for p in (self.source,self.raw)})
    def rehash(self,s):
        e=s['fetch_evidence'];e['response_sha256']=verify.sha(self.raw);e['footprint_sha256']=verify.sha(self.source)
        s['files']={str(p.relative_to(self.job)):verify.sha(p) for p in (self.source,self.raw)}
    def reject_both(self,data=None,tree=None,raw=None):
        self.write();s=self.status();self.write(data,tree,raw);self.rehash(s)
        for name,call in [('convert',lambda:lc2a.downloaded_identity(self.job,self.source,'C1234')),('verify',lambda:verify.check_fetch_evidence(self.job,s))]:
            with self.subTest(implementation=name),self.assertRaises((ValueError,KeyError)):call()
    def test_valid_response_and_chinese_source(self):
        self.write();s=self.status();r=verify.check_fetch_evidence(self.job,s)
        self.assertEqual(r['requested_lcsc'],'C1234');self.assertEqual(r['package_uuid'],'package-id')
    def test_optional_corroboration_can_be_absent(self):
        del self.data['result']['szlcsc'];del self.data['result']['dataStr']['head']['c_para']['Supplier Part']
        self.write();verify.check_fetch_evidence(self.job,self.status())
    def test_catalog_number_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['lcsc']['number']='C5678';self.reject_both(data)
    def test_regional_catalog_number_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['szlcsc']['number']='C5678';self.reject_both(data)
    def test_symbol_supplier_number_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['dataStr']['head']['c_para']['Supplier Part']='C5678';self.reject_both(data)
    def test_package_link_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['dataStr']['head']['puuid']='other-package';self.reject_both(data)
    def test_embedded_component_uuid_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['dataStr']['head']['uuid']='different';self.reject_both(data)
    def test_embedded_package_uuid_mismatch(self):
        data=copy.deepcopy(self.data);data['result']['packageDetail']['dataStr']['head']['uuid']='different';self.reject_both(data)
    def test_missing_or_wrong_document_kind(self):
        for value in ('2',None,'PCB'):
            with self.subTest(value=value):
                data=copy.deepcopy(self.data);data['result']['packageDetail']['dataStr']['head']['docType']=value;self.reject_both(data)
    def test_success_must_be_true_boolean(self):
        for value in (False,1,'true',None):
            with self.subTest(value=value):
                data=copy.deepcopy(self.data);data['success']=value;self.reject_both(data)
    def test_error_code_must_be_integer_zero(self):
        for value in (False,'0',1,None):
            with self.subTest(value=value):
                data=copy.deepcopy(self.data);data['code']=value;self.reject_both(data)
    def test_duplicate_json_keys_cannot_hide_wrong_identity(self):
        raw=json.dumps(self.data).replace('"number": "C1234"','"number": "C5678", "number": "C1234"',1)
        self.reject_both(raw=raw)
    def test_nonfinite_json_response_rejected(self):
        data=copy.deepcopy(self.data);data['result']['bad_metric']=float('nan');self.reject_both(data)
    def test_truncated_or_html_response_rejected(self):
        for raw in ('<html>service unavailable</html>','{"success":true,'):
            with self.subTest(raw=raw):self.reject_both(raw=raw)
    def test_missing_wrong_or_duplicate_kicad_identity(self):
        for kind in ('missing','wrong','duplicate'):
            with self.subTest(kind=kind):
                tree=copy.deepcopy(self.tree);p=next(n for n in tree if lc2a.key(n)=='property' and str(n[1])=='LCSC Part')
                if kind=='missing':tree.remove(p)
                elif kind=='wrong':p[2]='C5678'
                else:tree.append(copy.deepcopy(p))
                self.reject_both(tree=tree)
    def test_missing_cache_is_not_valid_provenance(self):
        self.write();s=self.status();self.raw.unlink()
        with self.assertRaisesRegex(ValueError,'evidence is missing'):lc2a.downloaded_identity(self.job,self.source,'C1234')
        with self.assertRaisesRegex(ValueError,'missing/changed'):verify.check_fetch_evidence(self.job,s)
    def test_missing_manifest_entry_rejected(self):
        self.write();s=self.status();s['files'].pop(str(self.raw.relative_to(self.job)))
        with self.assertRaisesRegex(ValueError,'unmanifested'):verify.check_fetch_evidence(self.job,s)
    def test_response_edit_after_conversion_rejected(self):
        self.write();s=self.status();self.raw.write_text(self.raw.read_text()+' ',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'missing/changed'):verify.check_fetch_evidence(self.job,s)
    def test_original_source_path_cannot_escape(self):
        self.write();s=self.status();s['fetch_evidence']['footprint_file']='source/../../outside.kicad_mod'
        with self.assertRaisesRegex(ValueError,'escapes'):verify.check_fetch_evidence(self.job,s)
    def test_recorded_request_and_ids_must_match(self):
        for field in ('requested_lcsc','package_uuid','component_uuid'):
            with self.subTest(field=field):
                self.write();s=self.status();s['fetch_evidence'][field]='wrong'
                with self.assertRaisesRegex(ValueError,'recorded'):verify.check_fetch_evidence(self.job,s)
    def test_reviewed_source_identity_also_checked(self):
        self.write();s=self.status();p=self.job/'reviewed.kicad_mod';p.write_text(self.source.read_text().replace('C1234','C5678'),encoding='utf-8');s['source_file']=p.name
        with self.assertRaisesRegex(ValueError,'KiCad C-number'):verify.check_fetch_evidence(self.job,s)
    def test_local_input_has_no_online_identity_claim(self):
        r=verify.check_fetch_evidence(self.job,{'source_file':self.source.name});self.assertEqual(r['mode'],'local_file')
    def attempt(self,case):
        calls=[];req='C１２３４' if case=='unicode_request' else 'C1234'
        def fetch(args,cwd,timeout=120):
            calls.append(args);out=Path(cwd);(out/'library.pretty').mkdir();(out/'library.pretty/part.kicad_mod').write_text(sx.dumps(self.tree),encoding='utf-8')
            if case=='two_files':(out/'library.pretty/extra.kicad_mod').write_text(sx.dumps(self.tree),encoding='utf-8')
            if case!='missing_cache':
                data=copy.deepcopy(self.data)
                if case=='wrong_part':data['result']['lcsc']['number']='C5678'
                (out/'.easyeda_cache').mkdir();(out/'.easyeda_cache/C1234.json').write_text(json.dumps(data),encoding='utf-8')
            return 'Simulated successful downloader; no network request.'
        args=argparse.Namespace(name='INPUT_SOURCE_TEST',refdes='J',lcsc=req,input=None,label='中文来源',profile=None,center=None,body_bounds=None,mask_margin=0,prepare_only=False)
        stream=io.StringIO()
        with patch.object(lc2a,'ROOT',self.root),patch.object(lc2a,'run',side_effect=fetch),patch.object(lc2a,'native_build') as native,contextlib.redirect_stdout(stream):
            code=lc2a.convert(args);native.assert_not_called()
        result=json.loads(stream.getvalue());self.assertEqual(code,1);self.assertEqual(result['status'],'failed')
        self.assertTrue((Path(result['job'])/'status.json').is_file());return calls,result
    def test_bad_fetch_stops_before_native_and_preserves_failed_status(self):
        for case in ('wrong_part','missing_cache','two_files'):
            with self.subTest(case=case):self.attempt(case)
    def test_nonascii_request_digits_rejected_before_fetch(self):
        calls,result=self.attempt('unicode_request');self.assertFalse(calls);self.assertIn('ASCII',result['error'])
    def test_legacy_online_report_needs_new_check(self):
        pointer=lc2a.ROOT/'work/f103_v2.json'
        if not pointer.exists():self.skipTest('Real integration required')
        src=Path(verify.load(pointer)['job']);job=self.root/'legacy';shutil.copytree(src,job)
        r=verify.load(job/'verification.json');r['checks'].pop('download_request_source_identity',None);verify.save(job/'verification.json',r)
        with self.assertRaisesRegex(ValueError,'Download request/source identity'):verify.require_verified(job)

if __name__=='__main__':unittest.main()
