"""Native full-CSV DC extraction, using exactly the final grading fixtures.

The old QPOST sample list omitted a saturation endpoint. Every candidate here
is exported and graded before cleanup. Best selection requires all previous PASS.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import bjt_compare_candidates as compare
import bjt_temperature as temp
import bjt_transient_cap as cap
from qspice_io import SuiteError, read_text, sha256, find_tools, command

base=temp.base;power=temp.power;core=temp.core;resume=temp.resume
BLOCKS=[['VEF','BF','IK','IHC','AXI'],
        ['VGB','DVGBF','AEPI','AB'],
        ['RCV','SCRCV','BRI','IBR','RBC']]


def render(params,nk_keyword=None):
    return temp.render_temperature(params,nk_keyword).replace(
        '* Charge/transit/avalanche/thermal parameters are NOT extracted here.',
        '* CJC comes from a separate native TRAN admittance calibration.\n'
        '* Other charge/transit/avalanche/thermal parameters remain unextracted.')


def targets(root, case):
    refs=core.reference(root)
    kind=case['kind'];temperature=case['temperature_C']
    if kind=='output':
        groups=[('output',f'IB{round(case["parameters"]["IB_TEST"]*1000)}mA',1,2)]
    elif kind=='gain':groups=[('gain',f'T{temperature:+d}',1,3)]
    else:groups=[('vcesat',f'T{temperature:+d}',1,3),('vbesat',f'T{temperature:+d}',1,4)]
    out=[]
    for family,curve,xcol,ycol in groups:
        reference=refs[family,temperature,curve]
        physical=reference
        if kind=='saturation':
            # Exact physical bounds; no arbitrary 0.100001 A endpoint exclusion.
            physical=reference[(reference[:,0]>=case['parameters']['IC_START']) &
                               (reference[:,0]<=case['parameters']['IC_STOP'])]
        out.append((family,curve,xcol,ycol,reference,physical))
    return out


def fitting_cases(root,cfg,fine=False):
    cases=core.plan(cfg,['output','gain','saturation'])
    # Same parameters, sweep spacing and VBE limit as final static_validation.
    for case in cases:
        case['fit_targets']=[{'kind':family,'x':float(x),'reference':float(y)}
            for family,_,_,_,_,ref in targets(root,case) for x,y in ref]
    return cases


def trace_residual(rx,ry,mx,my):
    """Fixed reference vector; use the final metric's interpolation convention."""
    valid=np.isfinite(mx)&np.isfinite(my)&(mx>0)
    mx,my=mx[valid],my[valid]
    if len(mx)<3 or np.any(np.diff(mx)<-1e-10):raise SuiteError('Invalid physical curve axis')
    xx,ix=np.unique(mx,return_index=True);yy=my[ix]
    inside=(rx>=xx[0])&(rx<=xx[-1])
    residual=np.full(len(rx),50.,dtype=float)
    return residual,inside,xx,yy


class FullCurveFit(base.NativeFit):
    def __init__(self,*args,protected,**kwargs):
        self.protected=protected
        super().__init__(*args,**kwargs,renderer=render,
                         model_name=power.NAME,case_factory=fitting_cases)
        self.counts={kind:sum(t['kind']==kind for c in self.cases for t in c['fit_targets'])
                     for kind in ('output','gain','vcesat','vbesat')}
        self.nres+=19

    def run_cases(self,p,folder,cases=None,nk_keyword=None):
        cases=copy.deepcopy(cases or self.cases)
        self.curves=[];self.pieces=[];self.full_cases=cases
        results={}
        for case in cases:
            path=Path(folder)/case['id'];path.mkdir(parents=True,exist_ok=True)
            try:
                deck,names=core.compile_deck(self.sources[case['id']],case,self.cfg,self.renderer(p))
                (path/'case.cir').write_text(deck,encoding='utf-8')
                if self.prepare:continue
                self.native_counts['engine_runs_started']+=1
                command([self.tools['engine'],path/'case.cir'],path,path/'simulator.stdout.log',
                        path/'simulator.stderr.log',self.cfg['timeout_seconds'])
                if base.logs_have_unknown_parameter(path):raise SuiteError('Unknown/ignored model parameter')
                # One QPOST table query, then full QUX export. No thousands of FIND queries.
                array=core.postprocess(path,case,self.tools,self.cfg,names)
                self.native_counts['qpost_runs_completed']+=1
                core.capture_case(self.root,path,case,self.cfg,array)
                core.dump(path/'parameters.json',case['parameters'])
                self.curves.extend(case['curve_metrics'])
                results[case['id']]=case['measurements']
                for family,_,xcol,ycol,_,ref in targets(self.root,case):
                    residual,inside,xx,yy=trace_residual(ref[:,0],ref[:,1],array[:,xcol],array[:,ycol])
                    predict=(np.interp(ref[inside,0],xx,yy) if family=='output' else
                             np.interp(np.log10(ref[inside,0]),np.log10(xx),yy))
                    if np.any(predict<=0) or np.any(ref[:,1]<=0):raise SuiteError('Nonpositive fit ordinate')
                    residual[inside]=np.log(predict/ref[inside,1])
                    self.pieces.append(residual/math.sqrt(self.counts[family]))
            finally:case['cleanup']=core.cleanup_large_files(path)
        return results

    def measure_objective(self,cases,measurements):
        data={'cases':self.full_cases,'config':self.cfg,'curve_comparison':self.curves}
        core.evaluate_limits(data)
        self.assessment=compare.assess(data,self.protected,self.cfg)
        ratios=[row['p95_relative']/self.cfg['curve_tolerance_p95_relative'][row['kind']] for row in self.curves]
        residual=np.r_[np.concatenate(self.pieces),[2*max(0.,r-.9) for r in ratios]]
        if len(residual)!=self.nres:raise SuiteError('Full-reference residual contract changed')
        guards={row['name']:row['value'] for row in data['limits']}
        return residual,copy.deepcopy(self.curves),guards,self.assessment['table_limits_pass']

    def should_replace_best(self,result):
        row=result['row'];assessment=copy.deepcopy(self.assessment)
        row.update(full_curve_guard_pass=assessment['eligible_dc'],full_curve_assessment=assessment,
                   grading_basis='Full CSV before cleanup; identical final metric_curve')
        if not assessment['eligible_dc']:return False
        if self.best is None:return True
        previous=self.best['row']['full_curve_assessment']['summary']['PASS']
        current=assessment['summary']['PASS']
        return current>previous or (current==previous and result['cost']<self.best['cost'])


def checkpoint(root,repair=False):
    folder=root/'calibration'/('mextram_v12_repair' if repair else 'mextram_v11_selected')
    state=json.loads(read_text(folder/'checkpoint.json'))
    for name,value in state['original_hashes'].items():
        if sha256(root/name)!=value:raise SuiteError('Original input changed: '+name)
    if sha256(folder/'selected_model.txt')!=state['selected_model_sha256']:raise SuiteError('Selected card changed')
    if sha256(folder/'baseline_validation.json')!=state['baseline_sha256']:raise SuiteError('Native baseline changed')
    if repair:
        for name,key in [('seed_model.txt','seed_model_sha256'),('seed_row.json','seed_row_sha256')]:
            if sha256(folder/name)!=state[key]:raise SuiteError('Native repair seed changed: '+name)
    return state,power.parse_model(read_text(folder/'selected_model.txt'))


def save_report(run,state):
    rows=['# FJP5027: подбор по полным CSV с обязательным сохранением PASS','',f'**{state["result"]}**','',
          'Старт: сохранённый нативный вариант без потери прежних PASS, с CJC, уточнённой через TRAN.',
          'Каждая оценка использует полный CSV и тот же metric_curve, что итоговая проверка.',
          'Весь допустимый диапазон насыщения включает первую точку около 0.1 A.',
          'Прежние 10 PASS, четыре табличных предела и правильный IB обязательны для сохранения best.',
          'Промежуточные варианты могут нарушать ограничения; они не становятся best и не устанавливаются.',
          '',f'Оценок: {state.get("evaluations",0)} / {state["max_evals"]}. Установка: **False**.']
    for key in ('starting','best_fine'):
        if key in state:
            row=state[key]
            rows+=['',f'{key}: {row.get("full_curve_assessment",{})}; cost={row.get("cost")}.']
    if state.get('repair_seed'):
        row=state['repair_seed']
        rows+=['',f'Повторная нативная оценка исследовательского старта: {row.get("full_curve_assessment",{})}; cost={row.get("cost")}.',
               f'Максимальное отличие P95 от сохранённой оценки: {state.get("repair_seed_max_p95_delta")}.',
               'Этот старт допускается к поиску с нарушениями; для сохранения best все прежние PASS обязательны.']
    if state.get('validation_summary'):
        rows+=['',f'Финальные полные кривые: {state["validation_summary"]}.',
               f'Потери прежних PASS: {state.get("protected_regressions",[])}.',
               '', '[Полный отчёт](validation/report.md)',
               '', '![Кривые](validation/figures/datasheet_comparison.png)']
    if state.get('transient_cap'):rows+=['',f'Cobo TRAN: {state["transient_cap"]["status"]}; [отчёт](transient_cap/report.md).']
    if state.get('repair_optimizer'):
        rows+=['',f'Совместная коррекция: {state["repair_optimizer"]}.',
               'Оптимизатор получил ограничения на прежние P95 явно. Для сохранения best по-прежнему требуется нативная полная проверка.']
    if 'new_curve_passes' in state:rows+=['',f'Новых PASS относительно проверенного старта: {state["new_curve_passes"]}.']
    if state.get('error'):rows+=['',state['error']]
    rows+=['','Нативный AC не объявляется исправленным. fT, переключение, нагрев, SOA и весь разброс приборов этим подбором не принимаются.']
    (run/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    core.dump(run/'full_curve_fit_state.json',state)


def main(root,argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true');parser.add_argument('--qspice-dir')
    parser.add_argument('--engine',choices=['64','80'],default='64')
    parser.add_argument('--max-evals',type=int,default=60)
    parser.add_argument('--repair',action='store_true')
    args=parser.parse_args(argv);root=Path(root).resolve();run=None;state=None
    try:
        if args.max_evals<8:raise SuiteError('Need at least 8 evaluations including baseline and final check')
        old,params=checkpoint(root,args.repair);cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg)
        tools=None if args.prepare else find_tools(args.qspice_dir,args.engine)
        run=root/'full_curve_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
        state={'result':'PREPARE_ONLY' if args.prepare else 'RUNNING','applied':False,'max_evals':args.max_evals,
               'original_hashes':old['original_hashes'],'protected_curves':old['protected_curves'],'repair':args.repair,
               'tool_hashes':{} if tools is None else {k:sha256(v) for k,v in tools.items()},
               'script_hashes':{name:sha256(core.COMMON/name) for name in ('bjt_full_guard.py','bjt_calibrate.py','bjt_suite.py')}}
        state['checkpoint_metadata']=old
        if args.repair:state['script_hashes']['bjt_feasible_repair.py']=sha256(core.COMMON/'bjt_feasible_repair.py')
        fit=FullCurveFit(root,run,cfg,tools,args.prepare,args.max_evals-1,protected=old['protected_curves'])
        core.dump(run/'targets.json',fit.cases)
        if args.prepare:
            fit.run_cases(params,run/'prepared_cases')
            cap.prepare(run/'prepared_cap',root,params,cfg)
            save_report(run,state);print('Prepared:',run/'report.md');return 0
        start=fit.evaluate(params,'fresh_feasible_checkpoint' if args.repair else 'native_v11_selected_baseline')
        state['starting']=start['row']
        if fit.best is None:raise SuiteError('Fresh full-CSV baseline did not preserve previous PASS; no optimization')
        if args.repair:
            folder=root/'calibration/mextram_v12_repair'
            seed=power.parse_model(read_text(folder/'seed_model.txt'))
            reference=json.loads(read_text(folder/'seed_row.json'))
            measured=fit.evaluate(seed,'fresh_native_repair_seed')
            state['repair_seed']=measured['row']
            if measured['row']['status']!='EVALUATED':raise SuiteError('Native repair seed did not reproduce')
            lookup={(r['kind'],r['curve'],r['temperature_C']):r['p95_relative'] for r in measured['row']['curve_samples']}
            delta=max(abs(lookup[(r['kind'],r['curve'],r['temperature_C'])]-r['p95_relative']) for r in reference['curve_samples'])
            state['repair_seed_max_p95_delta']=delta
            if delta>5e-4:raise SuiteError('Repair seed P95 differs from saved native data by over 0.05 percentage point')
            from bjt_feasible_repair import optimize
            state['repair_optimizer']=optimize(fit,seed,old['protected_curves'])
        else:state['blocks']=temp.optimize(fit,BLOCKS)
        params,cstate=cap.calibrate(root,run/'transient_cap',fit.best['params'],cfg,args)
        state['transient_cap']=cstate
        final=fit.evaluate(params,'final_full_csv_after_cap',fine=True,force=True)
        state['best_fine']=final['row']
        if not final['row'].get('full_curve_guard_pass'):raise SuiteError('Final full-CSV candidate lost a required condition; held')
        (run/'candidate_model.txt').write_text(render(params),encoding='utf-8')
        path,validation=resume.stage_validation(root,run,cfg,params,args,model_renderer=render)
        shutil.copytree(path,run/'validation')
        assessment=compare.assess(validation,old['protected_curves'],cfg)
        state.update(evaluations=fit.evaluations,native_counts=fit.native_counts,
                     validation_summary=assessment['summary'],protected_regressions=assessment['protected_regressions'],
                     final_assessment=assessment)
        if any(sha256(root/name)!=digest for name,digest in old['original_hashes'].items()):raise SuiteError('Original inputs changed during extraction')
        state['result']='FULL_CSV_RESEARCH_CANDIDATE_SAVED' if assessment['eligible_dc'] else 'FINAL_REGRESSION_CANDIDATE_HELD'
        if args.repair and assessment['eligible_dc']:
            previous=start['row']['full_curve_assessment']['summary']['PASS']
            state['new_curve_passes']=assessment['summary']['PASS']-previous
            state['result']='REPAIR_NEW_PASS_RESEARCH_CANDIDATE_SAVED' if state['new_curve_passes']>0 else 'REPAIR_NO_NEW_PASS_RESEARCH_CANDIDATE_SAVED'
        save_report(run,state);print('Report:',run/'report.md');print('Diagnostics:',resume.diagnostics(root,run))
        return 0 if assessment['eligible_dc'] else 1
    except (Exception,KeyboardInterrupt) as exc:
        if state is not None:
            if 'fit' in locals():state.update(evaluations=fit.evaluations,native_counts=fit.native_counts)
            state.update(result='FULL_CSV_FIT_HELD',error=f'{type(exc).__name__}: {exc}')
            save_report(run,state);print('Diagnostics:',resume.diagnostics(root,run))
        print('ERROR:',exc,file=sys.stderr);return 2
