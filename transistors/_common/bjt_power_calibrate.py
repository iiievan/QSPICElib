"""Compare MEXTRAM 504 candidates with the actual installed GP model.

Only native QSPICE/QPOST values can select a new candidate. Isothermal DC25
extraction; previous passing curves and tabular checks are protected.
"""
from __future__ import annotations
import argparse,copy,json,math,re,shutil,sys,time
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import bjt_calibrate as base
from qspice_io import SuiteError,read_text,sha256,spice_number,command,find_tools,parse_measurements
core=base.core
NAME='FJP5027_MEXTRAM_DC25_CANDIDATE_V3'
BOUNDS={'IS':(1e-17,1e-8),'BF':(5,150),'IK':(.005,50),'VEF':(5,1000),'VER':(.1,500),
 'BRI':(.05,200),'RBC':(.001,3),'RBV':(.001,10),'RCC':(.001,.5),
 'RCV':(.01,100),'SCRCV':(.01,10000),'IHC':(.01,30),'AXI':(.025,1),
 'IBF':(1e-18,1e-7),'MLF':(1,2.8),'IBR':(1e-18,1e-7)}
BLOCKS=[['IS','BF','IK','VEF','IBF','MLF','RBC'],
        ['RCV','SCRCV','IHC','AXI','RCC','BRI','VER','IBR'],
        ['IS','BF','IK','VEF','IBF','MLF','RCV','IHC','RCC','BRI']]

def parse_model(text):
    m=re.search(r'(?is)\.model\s+\S+\s+NPN\s*\((.*?)\)',text)
    if not m:raise SuiteError('Expected one NPN .model card')
    p={t[1].upper():spice_number(t[2]) for t in re.finditer(r'(\w+)\s*=\s*([^\s+)]+)',m[1].replace('\n+','\n'))}
    if int(p.get('LEVEL',1))!=504:return base.parse_card(text)
    if p.get('TREF')!=25 or p.get('RTH')!=0:raise SuiteError('MEXTRAM extraction requires explicit TREF=25 and RTH=0')
    return p

def render(p,nk_keyword=None):
    if int(p.get('LEVEL',1))!=504:
      # Preserve the actual installed GP card's NK rather than its default.
      return base.render_card(p,'NK' if 'NK' in p else None).replace(base.NAME,NAME)
    if p.get('RTH')!=0 or p.get('TREF')!=25:raise SuiteError('Unexpected thermal/reference setup')
    tokens=[f'{k}={v:.15g}' for k,v in p.items()]
    rows=['* Independent MEXTRAM 504 starting/fitted candidate. Not vendor SPICE.',
      '* Domain of this extraction: isothermal DC at 25 C. Read the native report.',
      '* Charge/transit/avalanche/thermal parameters are NOT extracted here.',
      '* Self-heating is disabled by RTH=0; no transient thermal impedance is claimed.',f'.model {NAME} NPN(']
    rows+=['+ '+' '.join(tokens[i:i+5]) for i in range(0,len(tokens),5)];rows[-1]+=')'
    return '\n'.join(rows)+'\n'

def seeds(current):
    # Mapping is an initial estimate, not an equivalence of model equations.
    p={'LEVEL':504.,'TREF':25.,'IS':current['IS'],'BF':25.,'VEF':80.,'VER':20.,'IK':2.,
       'IBF':1e-12,'MLF':2.,'BRI':8.,'IBR':1e-12,
       'RE':.001,'RBC':.10,'RBV':.03,'RCC':.02,'RCV':2.,'SCRCV':100.,'IHC':1.,'AXI':.1,
       'CJE':2e-9,'CJC':144.46e-12,'PE':.33,'PC':.33,'VDE':.75,'VDC':.75,
       'TAUE':5e-9,'TAUB':5e-9,
       'VGB':1.11,'VGC':1.11,'VGJ':1.11,'EXAVL':0.,'RTH':0.}
    if int(current.get('LEVEL',1))==504:p=dict(current)
    return [('mex_initial',p),('mex_low_epilayer',dict(p,RCV=.3,SCRCV=20.,IHC=2.,IK=1.)),
            ('mex_high_epilayer',dict(p,RCV=10.,SCRCV=300.,IHC=.4,IK=4.)),
            ('mex_strong_gain',dict(p,BF=40.,IBF=1e-11,RCV=1.,IHC=1.5,IK=.6))]

def power_cases(root,cfg,fine=False):
    cases=base.fitting_cases(root,cfg,fine)
    for c in cases:
      if c['kind']=='gain':
        c['parameters']['VBE_STOP']=2.2
        c['guard_measures']+=['.meas DC CHECK_GAIN_IC_MIN MIN V(ic_gummel)',
          '.meas DC CHECK_GAIN_IC_MAX MAX V(ic_gummel)',
          '.meas DC CHECK_GAIN_SCAN_END FIND V(scan) AT=1',
          '.meas DC CHECK_GAIN_VBE_END FIND V(vbe) AT=1']
    return cases

class GainCoverageError(SuiteError):
    def __init__(self,details):
      self.details=details
      super().__init__(f'Gain sweep covers IC={details["ic_min_A"]:.6g}..{details["ic_max_A"]:.6g} A; '
        f'{len(details["missing_target_names"])} reference points not reached (required to {details["required_ic_max_A"]:.6g} A)')

def split_measurements(text,names,allow_nan=()):
    """Keep failed crossings explicit; never turn NaN into a measured number."""
    if re.search(r'\b\d+\s+of\s+\d+\s+steps\b',text,re.I):raise SuiteError('Stepped QPOST log is not supported')
    headers=list(re.finditer(r'(?im)^\s*\.meas(?:ure)?\s+(?:dc|ac|tran)\s+(\w+)\b',text))
    blocks={};allowed={n.upper() for n in allow_nan};values={};missing=[]
    for i,h in enumerate(headers):
      name=h[1].upper()
      if name in blocks:raise SuiteError('Duplicate measurement: '+name)
      blocks[name]=text[h.start():headers[i+1].start() if i+1<len(headers) else len(text)]
    for name in names:
      key=name.upper()
      if key not in blocks:raise SuiteError('Missing measurement header: '+key)
      block=blocks[key]
      if key in allowed and re.search(r'(?im)^\s*\(\s*nan\s*,\s*nan\s*\)\s*$',block):
        missing.append(key);continue
      values[key]=parse_measurements(block,[key])[key]
    return values,missing

def gain_measurements(text,names,case,folder):
    target_names={t['name'].upper():t for t in case['fit_targets']}
    allowed=set(target_names)|{'CHECK_HFE_02','CHECK_HFE_1'}
    values,missing=split_measurements(text,names,allowed)
    lo=values['CHECK_GAIN_IC_MIN'];hi=values['CHECK_GAIN_IC_MAX']
    if hi<=lo or abs(values['CHECK_GAIN_SCAN_END']-1)>1e-7:raise SuiteError('Incomplete/invalid gain sweep')
    missing_targets=[n for n in missing if n in target_names]
    guard_x={'CHECK_HFE_02':.2,'CHECK_HFE_1':1.}
    # Missing crossings inside the measured range are a different simulation error.
    for n in missing:
      x=target_names[n]['x'] if n in target_names else guard_x[n]
      if lo<=x<=hi:raise SuiteError('Gain crossing missing inside measured IC range: '+n)
    details={'type':'GAIN_COVERAGE','case':case['id'],'ic_min_A':lo,'ic_max_A':hi,
      'vbe_end_V':values['CHECK_GAIN_VBE_END'],
      'required_ic_max_A':max(t['x'] for t in case['fit_targets']),
      'measured_target_count':len(target_names)-len(missing_targets),'target_count':len(target_names),
      'missing_target_names':missing_targets,'missing_guard_names':[n for n in missing if n not in target_names],
      'coverage_complete':not missing}
    core.dump(Path(folder)/'gain_coverage.json',details)
    if missing:raise GainCoverageError(details)
    return values

def recovery_seeds(current):
    """Retry a bounded ladder, starting near the native seed that missed only G29.

    The uploaded v5 run rejected mex_low_epilayer only at IC=4.9753 A.
    These are new estimates to test, not accepted or vendor parameters.
    """
    p=dict(dict(seeds(current))['mex_low_epilayer'])
    return [('mex_range_ihc4',dict(p,IHC=4.)),
      ('mex_range_ihc8',dict(p,IHC=8.,RCV=.15,SCRCV=10.)),
      ('mex_range_low_epi',dict(p,IHC=16.,RCV=.03,SCRCV=2.)),
      ('mex_range_near_intrinsic',dict(p,IHC=30.,RCV=.01,SCRCV=.01))]

def bootstrap(fit,current,state):
    for name,p in seeds(current):
      r=fit.evaluate(p,name);state['initial_results'].append(r['row'])
    if fit.best_power is not None:return
    failures=state['initial_results'][1:]
    if not failures or not all(r.get('failure_details',{}).get('type')=='GAIN_COVERAGE' for r in failures):
      raise SuiteError('MEXTRAM startup failed outside gain coverage; inspect first_rejected logs')
    print('All initial candidates missed gain targets; trying bounded collector-layer recovery...',flush=True)
    for name,p in recovery_seeds(current):
      if fit.evaluations>=fit.max_evals:break
      r=fit.evaluate(p,name);state['initial_results'].append(r['row'])
      if fit.best_power is not None:return
    raise SuiteError('No MEXTRAM candidate covers every required point after gain-range recovery; model unchanged')

class PowerFit(base.NativeFit):
    def __init__(self,*args,**kwargs):
      super().__init__(*args,**kwargs,renderer=render,model_name=NAME,case_factory=power_cases)
      self.best_power=None
    def read_case_measurements(self,text,names,case,folder):
      if case['kind']=='gain':return gain_measurements(text,names,case,folder)
      return super().read_case_measurements(text,names,case,folder)
    def evaluate(self,p,*args,**kwargs):
      result=super().evaluate(p,*args,**kwargs)
      if result['row']['status']=='REJECTED':
        dest=self.run/'last_rejected'
        if dest.exists():shutil.rmtree(dest)
        shutil.copytree(self.run/'work',dest)
      if int(p.get('LEVEL',1))==504 and result['row']['status']=='EVALUATED':
        if self.best_power is None or result['cost']<self.best_power['cost']:
          self.best_power=result
          dest=self.run/'best_power_native'
          if dest.exists():shutil.rmtree(dest)
          shutil.copytree(self.run/'work',dest)
          core.dump(self.run/'best_power_native.json',result['row'])
      return result
    def cap(self,p,tag):
      case=core.plan(self.cfg,['cap'])[0];cache={}
      source,meta=core.get_source(self.root,self.cfg,case,self.tools,self.run,cache,self.prepare,False)
      folder=self.run/'cap_checks'/tag;folder.mkdir(parents=True,exist_ok=True)
      try:
        deck,names=core.compile_deck(source,case,self.cfg,render(p));(folder/'case.cir').write_text(deck,encoding='utf-8')
        self.native_counts['engine_runs_started']+=1
        command([self.tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',self.cfg['timeout_seconds'])
        if base.logs_have_unknown_parameter(folder):raise SuiteError('MEXTRAM parameter rejected in cap test')
        raw=folder/'case.qraw'
        if not raw.is_file():raise SuiteError('No QRAW in capacitance check')
        out=folder/'measurements.txt'
        command([self.tools['QPOST'],folder/'case.cir','-r',raw,'-o',out],folder,folder/'qpost.stdout.log',folder/'qpost.stderr.log',self.cfg['timeout_seconds'])
        self.native_counts['qpost_runs_completed']+=1
        m=parse_measurements(read_text(out),names);value=m['COBO_I_IM']/(2*np.pi*1e6)
        if not math.isfinite(value) or value<=0:raise SuiteError('Invalid Cobo')
        return float(value)
      finally:core.cleanup_large_files(folder)


def optimize_blocks(fit,per_block):
    from scipy.optimize import least_squares
    reports=[]
    if fit.best_power is None:return [{'status':'NO_VALID_MEXTRAM_SEED'}]
    for block in BLOCKS:
      fixed=dict(fit.best_power['params']);keys=block
      x0=np.log([fixed[k] for k in keys]);lo=np.log([BOUNDS[k][0] for k in keys]);hi=np.log([BOUNDS[k][1] for k in keys]);x0=np.clip(x0,lo+1e-7,hi-1e-7)
      start_eval=fit.evaluations
      def fun(x):
        if fit.evaluations-start_eval>=per_block:raise base.BudgetStop()
        p=dict(fixed);p.update({k:float(np.exp(v)) for k,v in zip(keys,x)})
        return fit.evaluate(p,label='mex_'+keys[0].lower())['residual']
      try:
        r=least_squares(fun,x0,bounds=(lo,hi),max_nfev=100,ftol=1e-5,xtol=1e-5,gtol=1e-5,diff_step=.001)
        reports.append({'keys':keys,'reason':str(r.message),'evaluations':fit.evaluations-start_eval})
      except base.BudgetStop:reports.append({'keys':keys,'reason':'Evaluation budget reached; best native point retained','evaluations':fit.evaluations-start_eval})
      if fit.evaluations>=fit.max_evals:break
    return reports

def find_baseline(root,model_hash):
    paths=sorted((root/'results').glob('*/run_state.json'),reverse=True)+[root/'calibration/baseline_v2_validation.json']
    for path in paths:
      if not path.is_file():continue
      d=json.loads(read_text(path))
      matched=any(str(n).replace('\\','/').split('/')[-1]=='FJP5027.txt' and h==model_hash for n,h in d.get('hashes',{}).items())
      if matched and d.get('reference_sha256')==sha256(root/'reference/curves.csv') and d.get('cases') and all(c['status']=='SIMULATED' for c in d['cases']):return d
    return None

def protected_regressions(baseline,candidate):
    if not baseline:return []
    key=lambda r:(r['kind'],r['curve'],r.get('temperature_C'))
    new={key(r):r for r in candidate['curve_comparison']}
    return [list(key(r)) for r in baseline['curve_comparison'] if r['status']=='PASS' and new.get(key(r),{}).get('status')!='PASS']

def validate_static(root,args):
    before=set((root/'results').glob('*'));a=['--only','output','gain','saturation','cap','--engine',args.engine]
    if args.qspice_dir:a+=['--qspice-dir',args.qspice_dir]
    code=core.main(root,a);paths=sorted(p for p in set((root/'results').glob('*'))-before if (p/'run_state.json').is_file())
    if not paths:raise SuiteError('Static validation did not produce a result')
    path=paths[-1];return path,json.loads(read_text(path/'run_state.json')),code

def report(run,state):
    rows=['# FJP5027: сравнение с MEXTRAM 504','',f'**Результат: {state["result"]}.**','',
      'Исходная установленная GP-модель сравнивается с кандидатами MEXTRAM на одинаковых DC-стендах при 25 °C. Все значения выбора модели получены нативными QSPICE/QPOST. Начальные параметры MEXTRAM являются оценками, а не моделью производителя.',
      '', 'Сначала параметры усиления и токов базы, затем коллекторного слоя и квазинасыщения, затем совместный подбор. Лучший MEXTRAM-кандидат сохраняется даже если он хуже текущего GP; текущая модель при этом остаётся установленной.',
      '',f'Оценок модели выполнено: **{state.get("evaluations",0)}**; общий лимит: **{state.get("max_model_evaluations","—")}**. '
      f'Оптимизация запускалась: **{state.get("optimization_started",False)}**. Лимит не является обязательным числом итераций.',
      f'Для поиска выделено {state.get("max_search_evaluations","—")} оценок; одна зарезервирована на финальную тонкую сетку.',
      f'Нативных запусков движка в подборе/Cobo: **{state.get("native_counts",{}).get("engine_runs_started",0)}**. '
      'Отдельные полные статические проверки считаются в своих run_state.json.',
      '', '| Кандидат | Целевая функция | Табличные точки | Статус |', '|---|---:|---|---|']
    for r in state.get('initial_results',[]):rows.append(f'| {r["label"]} | {r.get("cost","—")} | {r.get("table_guards_pass",False)} | {r["status"]} |')
    coverage=[r for r in state.get('initial_results',[]) if r.get('failure_details',{}).get('type')=='GAIN_COVERAGE']
    if coverage:
      rows+=['','## Недостаточный диапазон стартовых моделей','',
        '| Кандидат | IC max, A | Получено точек усиления | Нужно до IC, A |','|---|---:|---:|---:|']
      for r in coverage:
        d=r['failure_details'];rows.append(f'| {r["label"]} | {d["ic_max_A"]:.6g} | {d["measured_target_count"]}/{d["target_count"]} | {d["required_ic_max_A"]:.6g} |')
      rows+=['','NaN не подменяются числами и не пропускаются при оценке. Если все четыре старта отклонены именно по диапазону усиления, проверяются до четырёх дополнительных вариантов коллекторного слоя; первый полностью измеренный кандидат допускается к оптимизации. Неподдерживаемые параметры и другие ошибки не обходятся этим восстановлением.']
    rows+=['',f'Кандидат установлен: **{state.get("applied",False)}**.',
      'Для установки требуется улучшение цели не менее 10%, проверка на более мелкой сетке, табличные ограничения и отдельный прогон 16 статических случаев. Все ранее проходящие кривые должны продолжить проходить те же допуски; иначе выполняется откат.',
      'Cobo отдельно измеряется и CJC при необходимости корректируется к типовым 60 pF, затем DC-критерий измеряется повторно. Это отдельная малосигнальная точка, не полная калибровка ёмкостей.',
      'Температура и динамика остаются отдельными целями извлечения. MEXTRAM описывает квазинасыщение, но сам переход на другой тип модели не доказывает совпадения с FJP5027. RTH=0 отключает саморазогрев, Cth не извлекается. Параметры заряда, лавины и предельных режимов не приняты.',
      '', 'Источники уравнений и параметров: [NXP MEXTRAM](https://www.nxp.com/design/design-center/development-boards-and-designs/models/mextram:MEXTRAM), [определение MEXTRAM](https://www.eng.auburn.edu/~niuguof/mextram/_downloads/f100656dc339d74fab1450e1cb6180ed/Mextramdefinition_504.10.1.pdf), [поддержка в QSPICE](https://forum.qorvo.com/t/models-of-transistors-with-thermal-parameters/15809).']
    if state.get('error'):rows+=['',f'Причина остановки: `{state["error"]}`.']
    if state.get('protected_regressions'):rows+=['',f'Регрессии ранее проходящих кривых: `{state["protected_regressions"]}`.']
    if state.get('termination_reason'):rows+=['',f'Остановка: `{state["termination_reason"]}`. Время: {state.get("elapsed_s",0):.1f} s.']
    (run/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8');core.dump(run/'power_state.json',state)

def progress(state,fit):
    if fit is None:return
    state['evaluations']=fit.evaluations
    state['native_counts']=dict(getattr(fit,'native_counts',{}))
    if hasattr(fit,'started'):state['elapsed_s']=time.monotonic()-fit.started

def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prepare',action='store_true');ap.add_argument('--qspice-dir');ap.add_argument('--engine',choices=['64','80'],default='64')
    ap.add_argument('--max-evals',type=int,default=150);ap.add_argument('--no-apply',action='store_true')
    args=ap.parse_args(argv);root=Path(root).resolve();run=None;state=None;fit=None
    try:
      if args.max_evals<10:raise SuiteError('At least 10 evaluations required')
      cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg)
      tools=None if args.prepare else find_tools(args.qspice_dir,args.engine)
      run=root/'power_calibration_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
      for n in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json']:
        dst=run/'inputs'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,dst)
      original_hashes={n:sha256(root/n) for n in ['FJP5027.txt','profile.json','reference/curves.csv']}
      current=parse_model(read_text(root/'FJP5027.txt'))
      state={'result':'PREPARED_NOT_SIMULATED' if args.prepare else 'RUNNING','applied':False,'original_hashes':original_hashes,'initial_results':[],'validation_runs':[],
        'max_model_evaluations':args.max_evals,'max_search_evaluations':args.max_evals-1,'optimization_started':False,
        'script_hashes':{'power':sha256(Path(__file__)),'base':sha256(Path(base.__file__))}}
      fit=PowerFit(root,run,cfg,tools,args.prepare,args.max_evals-1);core.dump(run/'targets.json',fit.cases)
      initial=seeds(current)
      if args.prepare:
        for name,p in initial+recovery_seeds(current):fit.run_cases(p,run/'prepared'/name)
        progress(state,fit)
        report(run,state);print(run/'report.md');return 0
      state['tool_hashes']={k:sha256(p) for k,p in tools.items()}
      baseline=find_baseline(root,original_hashes['FJP5027.txt'])
      if baseline is None:
        path,baseline,code=validate_static(root,args);state['validation_runs'].append(str(path))
        if any(c['status']!='SIMULATED' for c in baseline['cases']):raise SuiteError('Current model baseline incomplete; no model changed')
      core.dump(run/'baseline_validation.json',baseline)
      old=fit.evaluate(current,'installed_model');state['initial_results'].append(old['row'])
      if old['row']['status']!='EVALUATED':raise SuiteError('Installed model baseline failed')
      bootstrap(fit,current,state)
      state['optimization_started']=True
      state['blocks']=optimize_blocks(fit,max(10,(fit.max_evals-fit.evaluations)//3))
      state['search_evaluations']=fit.evaluations
      power=fit.best_power
      p=dict(power['params']);core.dump(run/'best_power_before_cap.json',power['row'])
      (run/'best_power_model.txt').write_text(render(p),encoding='utf-8')
      cap0=fit.cap(p,'initial');p['CJC']*=60e-12/cap0
      if not 1e-13<p['CJC']<1e-7:raise SuiteError('CJC correction outside selected range')
      cap1=fit.cap(p,'adjusted');state['Cobo_pF']={'initial':cap0*1e12,'adjusted':cap1*1e12}
      fine=fit.evaluate(p,'mex_best_fine',fine=True,force=True);state['best_fine']=fine['row'];state['evaluations']=fit.evaluations
      state['result']='MEXTRAM_CANDIDATE_NOT_BETTER_NO_REPLACEMENT'
      improved=(fine['row']['status']=='EVALUATED' and fine['row']['table_guards_pass'] and fine['cost']<old['cost']*.9 and 48e-12<=cap1<=72e-12)
      if improved:
        state['result']='MEXTRAM_DC25_IMPROVED_CANDIDATE';(run/'candidate_model.txt').write_text(render(p),encoding='utf-8')
      if improved and not args.no_apply:
        if any(sha256(root/n)!=h for n,h in original_hashes.items()):raise SuiteError('Inputs changed; model not replaced')
        newcfg=dict(cfg,model_name=NAME,model_status='MEXTRAM_PARTIAL_DC25_FIT_OTHER_REGIMES_NOT_ACCEPTED',
          model_provenance='Independent MEXTRAM 504 native DC25 calibration; see power_calibration_results')
        core.dump(run/'candidate_profile.json',newcfg)
        state['installation_started']=True
        try:
          shutil.copy2(run/'candidate_model.txt',root/'FJP5027.txt');shutil.copy2(run/'candidate_profile.json',root/'profile.json')
        except Exception:
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');raise
        state['applied']=True;progress(state,fit);report(run,state)
        path,new,code=validate_static(root,args);state['validation_runs'].append(str(path));state['validation_result']=new['result']
        bad=protected_regressions(baseline,new);state['protected_regressions']=bad
        if bad or any(c['status']!='SIMULATED' for c in new['cases']) or any(r['status']!='PASS' for r in new.get('limits',[])):
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json')
          state.update(applied=False,result='VALIDATION_REGRESSION_PREVIOUS_MODEL_RESTORED')
      state['termination_reason']='SEARCH_BUDGET_REACHED_BEST_RECHECKED' if state['search_evaluations']>=fit.max_evals else 'OPTIMIZER_BLOCKS_FINISHED_BEST_RECHECKED'
      progress(state,fit);report(run,state)
      print('Report:',run/'report.md');print('Diagnostics:',base.diagnostic_zip(root,run,state['validation_runs']))
      print('Result:',state['result']);return 0 if state['result']=='MEXTRAM_DC25_IMPROVED_CANDIDATE' else 1
    except (Exception,KeyboardInterrupt) as exc:
      if run is not None and state is not None:
        if state.get('applied') or state.get('installation_started'):
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');state['applied']=False
        progress(state,fit)
        state.update(result='POWER_CALIBRATION_STOPPED',error=f'{type(exc).__name__}: {exc}',termination_reason='ERROR_BEFORE_OPTIMIZATION' if not state.get('optimization_started') else 'ERROR_DURING_OR_AFTER_OPTIMIZATION');report(run,state)
        print('Diagnostics:',base.diagnostic_zip(root,run,state.get('validation_runs',[])))
      print('ERROR:',exc,file=sys.stderr);return 2
