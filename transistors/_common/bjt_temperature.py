"""Native multi-temperature MEXTRAM extraction; installed GP is never replaced.

Fit 15 cases / 19 curves at -25, 25, 75 and 125 C, with fixed references
and explicit constraints for the union of previously passing curves.
"""
from __future__ import annotations
import argparse,copy,json,math,shutil,sys,time
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import bjt_power_resume as resume
power=resume.power;base=power.base;core=power.core
from qspice_io import SuiteError,read_text,sha256,find_tools

# Defaults and scaling names: NXP/Delft MEXTRAM definition 504.10.1,
# section 4.7. Native QSPICE acceptance is still checked separately.
DEFAULTS={'AQBO':.3,'AE':0.,'AB':1.,'AEPI':2.5,'AEX':.62,'AC':2.,
          'DAIS':0.,'DVGBF':.05,'DVGBR':.045}
TEMP_BOUNDS={'VGB':(1.05,1.30),'VGJ':(1.,1.30),'VGC':(1.,1.30),
 'DVGBF':(-.05,.15),'DVGBR':(-.1,.2),'AEPI':(0.,4.),'AB':(0.,3.),
 'AQBO':(-.5,1.5),'DAIS':(-3.,3.),'AEX':(0.,3.),'AC':(0.,3.)}
BLOCKS=[['VGB','DVGBF','AEPI','VGJ','DVGBR','AB'],
        ['IS','BF','VEF','RCV','SCRCV','IHC','BRI','RBC'],
        ['VGB','DVGBF','AEPI','AQBO','IS','BF','RCV','IHC','BRI']]
REFINE_BLOCKS=[['IK','BF','VEF','IBF','MLF','RCV','IHC','AXI'],
 ['VGB','VGJ','DVGBF','DVGBR','AEPI','AB','AQBO'],
 ['IK','BF','BRI','IBR','RBC','RBV','RCV','SCRCV','IHC','AXI']]

def render_temperature(params,nk_keyword=None):
    return power.render(params,nk_keyword).replace(
      '* Domain of this extraction: isothermal DC at 25 C. Read the native report.',
      '* Fit domain: isothermal DC at -25/25/75/125 C. Read the temperature report.')

def temp_cases(root,cfg,fine=False,dense=False):
    refs=core.reference(root);out=core.plan(cfg,['output','gain','saturation'])
    for c in out:
        temp=c['temperature_C'];kind=c['kind'];c['fit_targets']=[];c['guard_measures']=[]
        c['parameters']['SCAN_STEP']=(.0005 if kind=='saturation' else .00025) if fine else (.002 if kind=='saturation' else .001)
        families=[('output','ic_out','vce_out','O',22)] if kind=='output' else [('gain','hfe_calc','ic_gummel','G',30)] if kind=='gain' else [('vcesat','vce','ic','C',28),('vbesat','vbe','ic','B',28)]
        if kind=='gain':c['parameters']['VBE_STOP']=2.2
        curve=f'IB{round(c["parameters"]["IB_TEST"]*1000)}mA' if kind=='output' else f'T{temp:+d}'
        for family,monitor,axis,prefix,count in families:
            a=refs[family,temp,curve]
            if kind=='saturation':a=a[(a[:,0]>.100001)&(a[:,0]<2.999)]
            if len(a)<3:raise SuiteError('Reference target range is empty')
            ii=np.arange(len(a)) if dense else np.unique(np.round(np.linspace(0,len(a)-1,min(count,len(a)))).astype(int))
            for i,(x,y) in enumerate(a[ii]):
                c['fit_targets'].append({'name':f'{prefix}{i:02d}','x':float(x),'reference':float(y),
                  'kind':family,'curve':curve,'temperature_C':temp,
                  'measure':f'.meas DC {prefix}{i:02d} FIND V({monitor}) WHEN V({axis})={x:.15g} CROSS=1'})
        if kind=='gain':
            c['guard_measures']+=['.meas DC CHECK_GAIN_IC_MIN MIN V(ic_gummel)',
              '.meas DC CHECK_GAIN_IC_MAX MAX V(ic_gummel)',
              '.meas DC CHECK_GAIN_SCAN_END FIND V(scan) AT=1',
              '.meas DC CHECK_GAIN_VBE_END FIND V(vbe) AT=1']
            if temp==25:c['guard_measures']+=['.meas DC CHECK_HFE_02 FIND V(hfe_calc) WHEN V(ic_gummel)=0.2 CROSS=1',
              '.meas DC CHECK_HFE_1 FIND V(hfe_calc) WHEN V(ic_gummel)=1 CROSS=1']
        if kind=='saturation' and temp==25:c['guard_measures']+=['.meas DC CHECK_VCE_15 FIND V(vce) WHEN V(ic)=1.5 CROSS=1',
          '.meas DC CHECK_VBE_15 FIND V(vbe) WHEN V(ic)=1.5 CROSS=1','.meas DC CHECK_IB_15 FIND V(ib) WHEN V(ic)=1.5 CROSS=1']
    return out

def refine_cases(root,cfg,fine=False):return temp_cases(root,cfg,fine,dense=True)

def protected_set(gp,mex,cfg):
    rows={}
    for source in [gp,mex]:
        for r in source['curve_comparison']:
            if r['status']=='PASS':
                key=(r['kind'],r['curve'],r['temperature_C'])
                rows[key]={'kind':key[0],'curve':key[1],'temperature_C':key[2],
                  'tolerance':cfg['curve_tolerance_p95_relative'][key[0]]}
    return [rows[k] for k in sorted(rows)]

def measured_objective(cases,measurements,protected,margin=.95):
    r,details,guards,ok=base.objective_values(cases,measurements)
    lookup={}
    for d in details:
        c=next(c for c in cases if c['id']==d['case'])
        target=next(t for t in c['fit_targets'] if t['kind']==d['kind'])
        d.update(curve=target['curve'],temperature_C=c['temperature_C'])
        lookup[d['kind'],d['curve'],d['temperature_C']]=d['p95_relative_sampled']
    # Fixed length penalties guide fitting. Full CSV, not sampled P95, decides PASS.
    penalty=[2*max(0.,lookup[(p['kind'],p['curve'],p['temperature_C'])]/p['tolerance']-margin) for p in protected]
    return np.r_[r,penalty],details,guards,ok

class TemperatureFit(base.NativeFit):
    def __init__(self,*args,protected,dense=False,margin=.95,**kwargs):
        self.protected=protected;self.margin=margin
        super().__init__(*args,**kwargs,renderer=render_temperature,model_name=power.NAME,case_factory=refine_cases if dense else temp_cases)
        self.nres+=len(protected)
    def read_case_measurements(self,text,names,case,folder):
        if case['kind']=='gain':return power.gain_measurements(text,names,case,folder)
        return super().read_case_measurements(text,names,case,folder)
    def measure_objective(self,cases,measurements):return measured_objective(cases,measurements,self.protected,self.margin)

def bounded_coordinates(p,keys):
    bounds={k:(TEMP_BOUNDS[k] if k in TEMP_BOUNDS else power.BOUNDS[k]) for k in keys}
    def encode(k,v):
        lo,hi=bounds[k]
        return (v-lo)/(hi-lo) if k in TEMP_BOUNDS else math.log(v/lo)/math.log(hi/lo)
    x=np.clip([encode(k,p[k]) for k in keys],1e-6,1-1e-6)
    def decode(a):
        out=dict(p)
        for k,u in zip(keys,a):
            lo,hi=bounds[k];out[k]=float(lo+(hi-lo)*u if k in TEMP_BOUNDS else lo*(hi/lo)**u)
        return out
    return x,decode

def optimize(fit,blocks=None):
    from scipy.optimize import least_squares
    reports=[];blocks=blocks or BLOCKS
    for j,keys in enumerate(blocks):
        if fit.evaluations>=fit.max_evals:break
        p=dict(DEFAULTS,**fit.best['params']);x0,decode=bounded_coordinates(p,keys)
        start=fit.evaluations;budget=max(1,(fit.max_evals-start)//(len(blocks)-j))
        def fun(x):
            if fit.evaluations-start>=budget:raise base.BudgetStop()
            return fit.evaluate(decode(x),label=f'temp_block{j+1}')['residual']
        try:
            result=least_squares(fun,x0,bounds=(0,1),diff_step=.001,ftol=1e-5,xtol=1e-5,gtol=1e-5,max_nfev=100)
            reason=str(result.message)
        except base.BudgetStop:reason='Allocated evaluation budget reached; best measured point retained'
        reports.append({'keys':keys,'evaluations':fit.evaluations-start,'reason':reason})
    return reports

def report(run,state):
    rows=['# FJP5027: многотемпературная калибровка','',f'**{state["result"]}**','',
      'Подбор: 7 выходных кривых при 25 C и hFE/VCEsat/VBEsat при -25, 25, 75, 125 C. Все значения цели получены QSPICE/QPOST; четыре семейства имеют одинаковый суммарный вес квадрата логарифмической ошибки.',
      'Дополнительные штрафы защищают объединение ранее проходящих кривых GP и MEXTRAM. Штраф использует точки подбора и не заменяет проверку полного CSV. Допуски и эталонные точки не меняются.',
      '',f'Оценок: {state.get("evaluations",0)} / {state.get("max_evals","—")}. Установленная GP не изменяется этой стадией.',
      '', '[Диагностика ёмкости через TRAN](transient_cap/report.md)' if state.get('refinement') else '[Диагностика AC и структуры выводов](ac_diagnostics/report.md)',
      '', '| Стадия | Норма общей цели |','|---|---:|']
    for k in ['starting','explicit_defaults','temperature_seed','best_fine']:
        r=state.get(k,{})
        if r:rows.append(f'| {k} | {r.get("cost","REJECTED")} |')
    if state.get('validation_summary'):
        rows+=['',f'Полные кривые: {state["validation_summary"]}. Потери прежних PASS: {state.get("protected_regressions",[])}.',
          '', '[Полный статический отчёт](validation/report.md)','', '![Кривые](validation/figures/datasheet_comparison.png)']
    if state.get('error'):rows+=['',f'Ошибка: `{state["error"]}`.']
    if state.get('refinement'):
        rows+=['','Уточнение использует все доступные эталонные точки в пределах стендов и штрафы для всех 19 кривых. Запас в цели составляет 10% от прежнего допуска; приёмочные допуски не изменены.',
          '',f'Ёмкость через TRAN: **{state.get("transient_cap",{}).get("status","NOT_RUN")}**. Нативный AC этим не исправлен и не объявляется PASS.']
    rows+=['','Cobo, fT, заряд и переключение этим DC-подбором не приняты. RTH=0, саморазогрева нет. Новый кандидат сохраняется отдельно; до решения AC-сбоя автоматической установки нет.',
      '', '[Параметры и температурные зависимости MEXTRAM, §4.7](https://www.eng.auburn.edu/~niuguof/mextram/_downloads/f100656dc339d74fab1450e1cb6180ed/Mextramdefinition_504.10.1.pdf)']
    (run/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8');core.dump(run/'temperature_state.json',state)

def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--prepare',action='store_true');ap.add_argument('--qspice-dir')
    ap.add_argument('--engine',choices=['64','80'],default='64');ap.add_argument('--max-evals',type=int,default=150)
    ap.add_argument('--skip-ac-diagnostics',action='store_true')
    ap.add_argument('--refine',action='store_true')
    args=ap.parse_args(argv);root=Path(root).resolve();run=None;state=None;fit=None
    try:
        if args.max_evals<15:raise SuiteError('Use at least 15 evaluations')
        cp,old,saved,p=resume.checkpoint(root,root/'calibration/mextram_v8_checkpoint' if args.refine else None)
        cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg)
        previous=json.loads(read_text(root/('calibration/mextram_v8_validation.json' if args.refine else 'calibration/mextram_v7_validation.json')))
        protected=protected_set(json.loads(read_text(cp/'baseline_validation.json')),previous,cfg)
        penalties=copy.deepcopy(protected)
        if args.refine:
            keys={(x['kind'],x['curve'],x['temperature_C']) for x in penalties}
            for r in previous['curve_comparison']:
                if (r['kind'],r['curve'],r['temperature_C']) not in keys:
                    penalties.append({'kind':r['kind'],'curve':r['curve'],'temperature_C':r['temperature_C'],
                      'tolerance':cfg['curve_tolerance_p95_relative'][r['kind']]})
        tools=None if args.prepare else find_tools(args.qspice_dir,args.engine)
        run=root/'temperature_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
        (run/'inputs').mkdir()
        for n in ['FJP5027.txt','profile.json']:shutil.copy2(root/n,run/'inputs'/n)
        state={'result':'PREPARED_NOT_SIMULATED' if args.prepare else 'RUNNING','max_evals':args.max_evals,
          'original_hashes':old['original_hashes'],'protected_curves':protected,'objective_threshold_curves':penalties,'applied':False,'refinement':args.refine}
        fit=TemperatureFit(root,run,cfg,tools,args.prepare,args.max_evals-1,protected=penalties,dense=args.refine,margin=.9 if args.refine else .95)
        core.dump(run/'targets.json',fit.cases)
        if not args.skip_ac_diagnostics and not args.refine:
            import bjt_ac_diagnostics
            state['ac_diagnostics']=bjt_ac_diagnostics.matrix(root,run/'ac_diagnostics',p,cfg,args,args.prepare)
        explicit=dict(DEFAULTS,**p)
        if args.prepare:
            fit.run_cases(explicit,run/'prepared')
            if args.refine:
                import bjt_transient_cap
                bjt_transient_cap.prepare(run/'transient_cap',root,p,cfg)
            report(run,state);print(run/'report.md');return 0
        state['tool_hashes']={k:sha256(v) for k,v in tools.items()}
        state['starting']=fit.evaluate(p,'saved_mex_all_temperatures')['row']
        if state['starting']['status']!='EVALUATED':raise SuiteError('Saved MEXTRAM failed native multi-temperature baseline')
        if not args.refine:
            state['explicit_defaults']=fit.evaluate(explicit,'explicit_temperature_defaults')['row']
            if state['explicit_defaults']['status']!='EVALUATED':raise SuiteError('Explicit temperature parameters rejected; inspect logs')
            seed=dict(explicit,IS=explicit['IS']*4,VGB=1.24,VGJ=1.17,VGC=1.17,DVGBF=.08,AEPI=3.)
            state['temperature_seed']=fit.evaluate(seed,'estimated_temperature_seed')['row']
        state['blocks']=optimize(fit,REFINE_BLOCKS if args.refine else None)
        params=fit.best['params']
        if args.refine:
            import bjt_transient_cap
            params,state['transient_cap']=bjt_transient_cap.calibrate(root,run/'transient_cap',params,cfg,args)
        fine=fit.evaluate(params,'best_multitemperature_fine',fine=True,force=True)
        state['best_fine']=fine['row']
        if fine['row']['status']!='EVALUATED' or not fine['row']['table_guards_pass']:raise SuiteError('Fine candidate failed native measurements/table guards')
        params=fine['params'];(run/'candidate_model.txt').write_text(render_temperature(params),encoding='utf-8')
        validation_path,vd=resume.stage_validation(root,run,cfg,params,args,model_renderer=render_temperature)
        shutil.copytree(validation_path,run/'validation')
        keys=lambda d:{(r['kind'],r['curve'],r['temperature_C']):r for r in d['curve_comparison']}
        new=keys(vd)
        state['protected_regressions']=[p for p in protected if new.get((p['kind'],p['curve'],p['temperature_C']),{}).get('status')!='PASS']
        state['validation_summary']={s:sum(r['status']==s for r in vd['curve_comparison']) for s in ['PASS','FAIL','INVALID']}
        state['result']='MULTITEMPERATURE_RESEARCH_CANDIDATE_SAVED'
        if state['protected_regressions']:state['result']='MULTITEMPERATURE_CANDIDATE_HAS_PROTECTED_REGRESSIONS'
        if any(sha256(root/n)!=h for n,h in old['original_hashes'].items()):raise SuiteError('Installed model/config/reference changed during run')
        state['evaluations']=fit.evaluations;state['native_counts']=fit.native_counts
        report(run,state);print('Report:',run/'report.md');print('Diagnostics:',resume.diagnostics(root,run));return 0 if state['validation_summary']['PASS']==19 and not state['protected_regressions'] else 1
    except (Exception,KeyboardInterrupt) as exc:
        if state is not None:
            state.update(result='TEMPERATURE_CALIBRATION_STOPPED',error=f'{type(exc).__name__}: {exc}')
            if fit is not None:state.update(evaluations=fit.evaluations,native_counts=fit.native_counts)
            report(run,state);print('Diagnostics:',resume.diagnostics(root,run))
        print('ERROR:',exc,file=sys.stderr);return 2
