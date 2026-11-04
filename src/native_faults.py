"""Build deliberately WRONG real Allegro data; the independent verifier must reject it.
Only isolated copies are touched. Mutants are evidence, never library candidates.
"""
import argparse,copy,contextlib,io,json,os,shutil,sys,time
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from pathlib import Path
import lc2a,verify
ROOT=Path(__file__).resolve().parents[1]

def main():
    supported=['pin_swap','mirror_y','wrong_drill','wrong_graphic_layer','missing_pin','origin_offset','hidden_refdes','tiny_refdes']
    ap=argparse.ArgumentParser();ap.add_argument('--case',choices=['all']+supported,default='all');args=ap.parse_args()
    dest=ROOT/'outputs/故障实验'/(time.strftime('%Y%m%d_%H%M%S')+'_原生错误注入_'+os.urandom(2).hex());dest.mkdir(parents=True)
    cases=supported if args.case=='all' else [args.case]
    summary={'status':'running','results':[],'directory':str(dest)}
    for case in cases:
        key='pth' if case in ('wrong_drill','wrong_graphic_layer') else 'f103'
        original=Path(verify.load(ROOT/'work'/(key+'_v2.json'))['job']);verify.require_verified(original)
        source_status=verify.load(original/'status.json');job=dest/case;job.mkdir()
        result={'case':case,'conversion_self_check':False,'independent_rejected':False,'job':str(job)}
        try:
            for rel in source_status['files']:
                src=(original/rel).resolve();verify.require(src.is_relative_to(original.resolve()),'Invalid original source path')
                target=job/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
            model=verify.load(job/'model.json')
            if case=='pin_swap':model['pads'][0]['number'],model['pads'][1]['number']=model['pads'][1]['number'],model['pads'][0]['number']
            elif case=='mirror_y':
                for p in model['pads']:p['at'][1]*=-1;p['angle']=(-p['angle'])%360
                for g in model['graphics']:
                    for field in ('start','end','center'):
                        if field in g:g[field][1]*=-1
            elif case=='wrong_drill':
                for p in model['pads']:p['drill']=.8
            elif case=='wrong_graphic_layer':
                next(g for g in model['graphics'] if g['layer']=='F.SilkS')['layer']='F.Fab'
            elif case=='missing_pin':model['pads'].pop()
            elif case=='origin_offset':
                for p in model['pads']:p['at'][0]+=3;p['at'][1]+=4
                for g in model['graphics']:
                    for field in ('start','end','center'):
                        if field in g:g[field][0]+=3;g[field][1]+=4
            verify.save(job/'model.json',model)
            original_builder=lc2a.skill_build
            def injected_builder(*params):
                text=original_builder(*params)
                if case=='tiny_refdes':text=text.replace('?width 0.50 ?height 0.80','?width 0.05 ?height 0.08')
                return text
            preset=lc2a.VIEW_PRESET
            if case=='hidden_refdes':preset=preset.replace('axlVisibleLayer(name t)','axlVisibleLayer(name nil)')
            with patch.object(lc2a,'skill_build',side_effect=injected_builder),patch.object(lc2a,'VIEW_PRESET',preset):
                (job/'build.il').write_text(lc2a.skill_build(model,source_status['name'],source_status['refdes']),encoding='ascii')
                checks=lc2a.native_build(job,model,source_status['name'],source_status['refdes'])
            result['conversion_self_check']=True
            s=copy.deepcopy(source_status);s['checks']=checks;s['fault_injection']={'case':case,'original_job':str(original),'test_only':True}
            s['files']={str(p.relative_to(job)):verify.sha(p) for p in job.rglob('*') if p.is_file() and p.name!='status.json'}
            verify.save(job/'status.json',s)
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream):code=verify.verify_job(job,export=False)
            (job/'independent_verify.txt').write_text(stream.getvalue(),encoding='utf-8')
            outcome=verify.load(job/'verification.json');result['verification_error']=outcome.get('error')
            # An infrastructure error is NOT evidence that the intended fault was detected.
            expected={'pin_swap':'Pin coordinate/rotation','mirror_y':'Pin coordinate/rotation','wrong_drill':'Hole diameter',
                'wrong_graphic_layer':'Graphic layer','missing_pin':'Pin count','origin_offset':'Pin coordinate/rotation',
                'hidden_refdes':'Default layer visibility','tiny_refdes':'font too small'}[case]
            result['independent_rejected']=code!=0 and expected in outcome.get('error','')
        except Exception as exc:result['experiment_error']=str(exc)
        result['status']='passed' if result['conversion_self_check'] and result['independent_rejected'] else 'failed'
        summary['results'].append(result);verify.save(dest/'summary.json',summary)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    summary['status']='passed' if all(x['status']=='passed' for x in summary['results']) else 'failed'
    verify.save(dest/'summary.json',summary)
    bj=datetime.now(timezone(timedelta(hours=8)))
    lines=[f'# 原生错误注入：{bj:%Y-%m-%d %H:%M:%S} 北京时间','',
        '故意把错误写入真实 .pad/.dra/.psm。转换阶段仍与自己的错误模型比对，因此通过；独立验收必须对照原始来源拒绝。',
        '原始成功样本及用户打开的设计没有被修改，错误样本禁止入库。','',
        '| 错误 | 转换自检 | 独立发现预期错误 | 结果/原因 |','|---|---|---|---|']
    for r in summary['results']:lines.append(f'| {r["case"]} | {r["conversion_self_check"]} | {r["independent_rejected"]} | {r.get("verification_error",r.get("experiment_error",""))} |')
    lines+=['',f'本轮结果：**{summary["status"]}**。完整原生输入输出、SKILL、日志和读回记录均在各案例目录。']
    (dest/'实验记录.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (ROOT/'outputs/测试记录.md').open('a',encoding='utf-8') as f:f.write('\n'+ '\n'.join(lines).replace('# 原生','## 原生',1)+f'\n\n路径：`{dest}`\n')
    return 0 if summary['status']=='passed' else 1

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    sys.exit(main())
