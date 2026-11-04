"""Native QSPICE DC calibration. All objective values come from native QPOST.

Offline estimates are seeds only. Acceptance tolerances and references stay fixed.
The optimizer never fits unassigned Fig.7 timing curves or SOA failure envelopes.
"""
from __future__ import annotations
import argparse,copy,csv,json,math,re,shutil,sys,time,zipfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import bjt_suite as core
from qspice_io import SuiteError,read_text,sha256,spice_number,command,find_tools,parse_measurements

NAME='FJP5027_DC25_CANDIDATE_V2'
BOUNDS={'IS':(1e-17,1e-7),'BF':(5,150),'IKF':(.005,50),'VAF':(5,1000),
 'BR':(.01,200),'IKR':(1e-5,100),'RB':(.001,10),'RC':(1e-5,.5),'NK':(.3,.95)}

class BudgetStop(Exception):pass

def parse_card(text):
    match=re.search(r'(?is)\.model\s+\S+\s+NPN\s*\((.*?)\)',text)
    if not match:raise SuiteError('Only one standard NPN .model card is supported')
    p={}
    for token in re.finditer(r'(\w+)\s*=\s*([^\s+)]+)',match[1].replace('\n+','\n')):
        p[token[1].upper()]=spice_number(token[2])
    if 'NKF' in p:p.setdefault('NK',p.pop('NKF'))
    required=['IS','BF','IKF','VAF','BR','IKR','RB','RBM','RC','RE','TNOM']
    if any(k not in p for k in required):raise SuiteError('Model card missing explicit calibration parameters')
    if p['TNOM']!=25:raise SuiteError('This calibration extraction expects TNOM=25 C')
    return p

def render_card(p,nk_keyword=None):
    data=dict(p);has_nk='NK' in data or 'NKF' in data;nk=data.pop('NK',data.get('NKF',.5));data.pop('NKF',None)
    if nk_keyword and has_nk:data[nk_keyword]=nk
    rows=['* DC calibration candidate; not a vendor model. See extraction provenance.',
      '* Fit domain: DC at 25 C. Full-temperature and dynamic acceptance remain separate.',
      '* TF/TR/CJE/CJC are retained unless explicitly changed in a later charge extraction.',
      '* No avalanche, secondary breakdown, SOA failure or self-heating is implemented.',f'.model {NAME} NPN(']
    tokens=[f'{k}={v:.15g}' for k,v in data.items()]
    rows+=['+ '+' '.join(tokens[i:i+5]) for i in range(0,len(tokens),5)]
    rows[-1]+=')';return '\n'.join(rows)+'\n'

def fitting_cases(root,cfg,fine=False):
    refs=core.reference(root);out=[]
    def targets(kind,curve,count):
      a=refs[kind,25,curve]
      if kind in ('vcesat','vbesat'):a=a[(a[:,0]>.100001)&(a[:,0]<2.999)]
      if len(a)<3:raise SuiteError('Not enough reference points within physical sweep')
      ii=np.unique(np.round(np.linspace(0,len(a)-1,min(count,len(a)))).astype(int))
      return a[ii]
    for ib in [.05,.15,.25,.35]:
      c=next(c for c in core.plan(cfg,['output']) if abs(c['parameters']['IB_TEST']-ib)<1e-9)
      c['parameters']['SCAN_STEP']=.00025 if fine else .001
      c['fit_targets']=[{'name':f'O{i:02d}','x':float(x),'reference':float(y),'kind':'output',
        'measure':f'.meas DC O{i:02d} FIND V(ic_out) WHEN V(vce_out)={x:.15g} CROSS=1'} for i,(x,y) in enumerate(targets('output',f'IB{round(ib*1000)}mA',22))]
      out.append(c)
    c=next(c for c in core.plan(cfg,['gain']) if c['temperature_C']==25)
    c['parameters'].update(VBE_STOP=1.8,SCAN_STEP=.00025 if fine else .001)
    c['fit_targets']=[{'name':f'G{i:02d}','x':float(x),'reference':float(y),'kind':'gain',
      'measure':f'.meas DC G{i:02d} FIND V(hfe_calc) WHEN V(ic_gummel)={x:.15g} CROSS=1'} for i,(x,y) in enumerate(targets('gain','T+25',30))]
    c['guard_measures']=['.meas DC CHECK_HFE_02 FIND V(hfe_calc) WHEN V(ic_gummel)=0.2 CROSS=1',
      '.meas DC CHECK_HFE_1 FIND V(hfe_calc) WHEN V(ic_gummel)=1 CROSS=1'];out.append(c)
    c=next(c for c in core.plan(cfg,['saturation']) if c['temperature_C']==25)
    c['parameters']['SCAN_STEP']=.0005 if fine else .002
    c['fit_targets']=[]
    for kind,monitor,prefix in [('vcesat','vce','C'),('vbesat','vbe','B')]:
      for i,(x,y) in enumerate(targets(kind,'T+25',28)):
        c['fit_targets'].append({'name':f'{prefix}{i:02d}','x':float(x),'reference':float(y),'kind':kind,
          'measure':f'.meas DC {prefix}{i:02d} FIND V({monitor}) WHEN V(ic)={x:.15g} CROSS=1'})
    c['guard_measures']=['.meas DC CHECK_VCE_15 FIND V(vce) WHEN V(ic)=1.5 CROSS=1',
      '.meas DC CHECK_VBE_15 FIND V(vbe) WHEN V(ic)=1.5 CROSS=1',
      '.meas DC CHECK_IB_15 FIND V(ib) WHEN V(ic)=1.5 CROSS=1'];out.append(c)
    return out

def attach_measures(deck,case):
    lines=[s for s in deck.splitlines() if not re.match(r'^\s*\.(?:meas(?:ure)?|end)\b',s,re.I)]
    rows=[p['measure'] for p in case['fit_targets']]+case.get('guard_measures',[])
    lines+=rows+['.end'];expected=[re.match(r'\.meas\s+DC\s+(\w+)',r,re.I)[1] for r in rows]
    return '\n'.join(lines)+'\n',expected

def objective_values(cases,measurements):
    counts={k:sum(sum(t['kind']==k for t in c['fit_targets']) for c in cases) for k in ['output','gain','vcesat','vbesat']}
    residual=[];detail=[];guards={}
    # Equal total squared-log-error weight for each of the four figure families.
    for c in cases:
      values=measurements[c['id']];by_kind={}
      for t in c['fit_targets']:
        value=values[t['name']]
        if not math.isfinite(value) or value<=0:raise SuiteError('Nonpositive calibration measurement '+t['name'])
        residual.append(math.log(value/t['reference'])/math.sqrt(counts[t['kind']]))
        by_kind.setdefault(t['kind'],[]).append(abs(value/t['reference']-1))
      for k,errors in by_kind.items():detail.append({'case':c['id'],'kind':k,'p95_relative_sampled':float(np.percentile(errors,95))})
      guards.update({k:v for k,v in values.items() if k.startswith('CHECK_')})
    guard_ok=(10<=guards.get('CHECK_HFE_02',0)<=40 and guards.get('CHECK_HFE_1',0)>=8
      and 0<guards.get('CHECK_VCE_15',0)<=2 and 0<guards.get('CHECK_VBE_15',0)<=1.5
      and abs(guards.get('CHECK_IB_15',0)-.3)<.0003)
    return np.array(residual),detail,guards,guard_ok

def logs_have_unknown_parameter(folder):
    text='\n'.join(read_text(p) for p in folder.glob('*.log'))
    return bool(re.search(r'(?im)^.*(?:unknown|unrecognized|unsupported|ignored|ignoring).*\b(?:parameter|nkf?|model parameter)\b',text))

class NativeFit:
    def __init__(self,root,run,cfg,tools,prepare=False,max_evals=100):
      self.root=Path(root);self.run=Path(run);self.cfg=dict(cfg,model_name=NAME);self.tools=tools;self.prepare=prepare
      self.cases=fitting_cases(root,cfg);self.sources={};cache={}
      for c in self.cases:
        source,meta=core.get_source(root,cfg,c,tools,run,cache,prepare,False)
        self.sources[c['id']]=source;c['source']=meta
      self.nk_keyword=None;self.max_evals=max_evals;self.evaluations=0;self.best=None;self.cache={}
      self.nres=sum(len(c['fit_targets']) for c in self.cases);self.started=time.monotonic()
    def run_cases(self,p,folder,cases=None,nk_keyword=None):
      folder=Path(folder);results={};cases=cases or self.cases
      for case in cases:
        f=folder/case['id']
        if f.exists():shutil.rmtree(f)
        f.mkdir(parents=True)
        try:
          deck,_=core.compile_deck(self.sources[case['id']],case,self.cfg,render_card(p,nk_keyword))
          deck,names=attach_measures(deck,case);(f/'case.cir').write_text(deck,encoding='utf-8')
          if self.prepare:continue
          command([self.tools['engine'],f/'case.cir'],f,f/'simulator.stdout.log',f/'simulator.stderr.log',self.cfg['timeout_seconds'])
          if logs_have_unknown_parameter(f):raise SuiteError('Unknown/ignored model parameter; see simulator logs')
          raw=f/'case.qraw'
          if not raw.is_file() or not raw.stat().st_size:raise SuiteError('No QRAW produced')
          result=f/'measurements.txt'
          command([self.tools['QPOST'],f/'case.cir','-r',raw,'-o',result],f,f/'qpost.stdout.log',f/'qpost.stderr.log',self.cfg['timeout_seconds'])
          results[case['id']]=parse_measurements(read_text(result),names)
        finally:
          core.cleanup_large_files(f)
      return results
    def probe_nk(self,p):
      if self.prepare:return {'status':'NOT_RUN'}
      c=copy.deepcopy(self.cases[0]);c['fit_targets']=[c['fit_targets'][-1]]
      findings=[]
      for key in ['NK','NKF']:
        try:
          a=self.run_cases(dict(p,NK=.5),self.run/'capability_probe'/key/'a',[c],key)
          b=self.run_cases(dict(p,NK=.75),self.run/'capability_probe'/key/'b',[c],key)
          name=c['fit_targets'][0]['name'];x=a[c['id']][name];y=b[c['id']][name]
          delta=abs(y-x)/max(abs(x),1e-20)
          findings.append({'keyword':key,'relative_response':delta,'accepted':delta>.01})
          if delta>.01:self.nk_keyword=key;break
        except Exception as exc:findings.append({'keyword':key,'accepted':False,'error':str(exc)})
      return {'status':'SUPPORTED_AND_RESPONDING' if self.nk_keyword else 'NOT_CONFIRMED_USE_STANDARD_GP','keyword':self.nk_keyword,'tests':findings}
    def evaluate(self,p,label='trial',fine=False,force=False):
      key=json.dumps({'params':p,'fine':fine},sort_keys=True)
      if key in self.cache and not force:return self.cache[key]
      if self.evaluations>=self.max_evals and not force:raise BudgetStop()
      self.evaluations+=1;number=self.evaluations
      work=self.run/'work';work.mkdir(exist_ok=True)
      # Each candidate has the same physical benches and reference targets.
      cases=fitting_cases(self.root,self.cfg,True) if fine else self.cases
      row={'evaluation':number,'label':label,'params':p,'fine_scan':fine,'elapsed_s':time.monotonic()-self.started}
      try:
        measured=self.run_cases(p,work,cases,self.nk_keyword)
        residual,detail,guards,guard_ok=objective_values(cases,measured)
        cost=float(np.linalg.norm(residual));row.update(cost=cost,curve_samples=detail,guard_values=guards,table_guards_pass=guard_ok,status='EVALUATED')
        result={'params':dict(p),'cost':cost,'residual':residual,'row':row}
        if guard_ok and (self.best is None or cost<self.best['cost']):
          self.best=result
          dest=self.run/'best_native'
          if dest.exists():shutil.rmtree(dest)
          shutil.copytree(work,dest)
          (self.run/'best_native_model.txt').write_text(render_card(p,self.nk_keyword),encoding='utf-8')
          core.dump(self.run/'best_native.json',row)
        print(f'[{number}/{self.max_evals}] {label}: cost={cost:.7g}; table guards={guard_ok}; best={self.best["cost"] if self.best else None}',flush=True)
      except Exception as exc:
        row.update(status='REJECTED',error=f'{type(exc).__name__}: {exc}');result={'params':dict(p),'cost':1e3,'residual':np.full(self.nres,50.),'row':row}
        # Preserve the first failure, rather than discarding every diagnostic.
        dest=self.run/'first_rejected'
        if not dest.exists():shutil.copytree(work,dest)
        print(f'[{number}/{self.max_evals}] REJECTED: {exc}',flush=True)
      with (self.run/'history.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
      self.cache[key]=result;return result


def optimize(fit,start):
    from scipy.optimize import least_squares
    keys=[k for k in BOUNDS if k!='NK' or fit.nk_keyword]
    fixed=dict(start);fixed['NK']=fixed.get('NK',.5)
    ratio=fixed['RBM']/fixed['RB']
    def unpack(x):
      p=dict(fixed);p.update({k:float(np.exp(v)) for k,v in zip(keys,x)})
      p['RBM']=min(p['RB'],p['RB']*ratio);return p
    x0=np.log([fixed[k] for k in keys]);lo=np.log([BOUNDS[k][0] for k in keys]);hi=np.log([BOUNDS[k][1] for k in keys]);x0=np.clip(x0,lo+1e-7,hi-1e-7)
    try:
      r=least_squares(lambda x:fit.evaluate(unpack(x))['residual'],x0,bounds=(lo,hi),
         max_nfev=100,ftol=1e-5,xtol=1e-5,gtol=1e-5,diff_step=.001)
      return {'reason':str(r.message),'scipy_success':bool(r.success),'scipy_nfev':int(r.nfev)}
    except BudgetStop:return {'reason':'Native evaluation budget reached; best evaluated candidate preserved','scipy_success':False}


def diagnostic_zip(root,run,validation_runs):
    out=Path(root)/'FJP5027_calibration_diagnostics.zip'
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
      for folder,prefix in [(Path(run),'calibration')]+[(Path(p),f'validation/{i}') for i,p in enumerate(validation_runs)]:
        for f in sorted(folder.rglob('*')):
          if f.is_file() and f.suffix.lower() not in ['.qraw','.zip'] and f.name!='waveforms.csv' and 'work' not in f.relative_to(folder).parts:
            z.write(f,Path(prefix)/f.relative_to(folder))
    return out


def write_report(run,state):
    rows=['# FJP5027: нативная калибровка DC при 25 °C','',f'**Результат: {state["result"]}.**',
      '', 'Все значения целевой функции получены QSPICE + QPOST. Предварительные математические оценки использованы только как начальные кандидаты.',
      '', 'Подбор охватывает четыре выходных кривых IB=50/150/250/350 mA, hFE(IC), VCEsat(IC), VBEsat(IC), при 25 °C. Каждая из четырёх групп имеет одинаковый суммарный вес квадрата логарифмической ошибки. Допуски приёмки и исходные эталоны не изменены.',
      'Остальные выходные кривые, температуры и Cobo проверяются отдельным полным статическим прогоном после установки улучшенного кандидата. График 7 не использован для подгонки: соответствие двух паспортных линий ts/tf и часть условий остаются неопределёнными.',
      '', 'Изменение NK/NKF проверяется отдельными нативными симуляциями. Параметр используется только при отсутствии предупреждений об игнорировании и наблюдаемом изменении результата. Это проверка данной установленной версии QSPICE, а не предположение о совместимости.',
      '', f'Поддержка NK: `{state.get("nk_probe",{}).get("status","NOT_RUN")}`.',
      '', '| Кандидат | Целевая функция (меньше лучше) | Табличные проверки |', '|---|---:|---|']
    for r in state.get('seed_results',[]):rows.append(f'| {r["label"]} | {r.get("cost","—")} | {r.get("table_guards_pass",False)} |')
    rows+=['',f'Оптимизация: {state.get("optimizer",{}).get("reason","не запускалась")}.',
      '', f'Установка кандидата: **{state.get("applied",False)}**. Бюджет включает действительные оценки модели, включая численные производные. Достижение бюджета не объявляется сходимостью оптимизатора.',
      'Даже после улучшения модель остаётся кандидатом. Сходимость схем и снижение целевой функции не подтверждают совпадение всех типовых кривых. Итоговые FAIL и непроверенные режимы сохраняются в отдельных отчётах.',
      '', 'Перед установкой сохранены исходные FJP5027.txt и profile.json в inputs/. Перед заменой проверяется, что исходные файлы не менялись во время подбора. Для отката вернуть эти два файла.',
      '', 'TF/TR/CJE/CJC автоматически не подгоняются. Температурная зависимость, заряд, fT, SOA, разрушение и саморазогрев этой стадией не откалиброваны.']
    if state.get('best_fine'):
      rows+=['','## Лучший кандидат, повторный расчёт на более мелкой сетке','', '| Характеристика / стенд | P95 на выбранных точках |','|---|---:|']
      for x in state['best_fine'].get('curve_samples',[]):rows.append(f'| {x["kind"]} / {x["case"]} | {x["p95_relative_sampled"]*100:.5g}% |')
      rows+=['', 'Эти P95 относятся только к выбранным точкам подбора, не заменяют метрики полного отчёта.']
    if state.get('error'):rows+=['',f'Причина остановки: `{state["error"]}`.']
    for p in state.get('validation_runs',[]):rows+=['',f'Отдельный проверочный прогон: `{p}`.']
    (Path(run)/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')


def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prepare',action='store_true');ap.add_argument('--qspice-dir');ap.add_argument('--engine',choices=['64','80'],default='64')
    ap.add_argument('--max-evals',type=int,default=100);ap.add_argument('--no-apply',action='store_true');ap.add_argument('--skip-validation',action='store_true')
    args=ap.parse_args(argv);root=Path(root).resolve();run=None;state=None
    try:
      if args.max_evals<6:raise SuiteError('Need at least 6 native evaluations')
      cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg)
      tools=None if args.prepare else find_tools(args.qspice_dir,args.engine)
      run=root/'calibration_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
      for n in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json']:
        p=run/'inputs'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p)
      hashes={n:sha256(root/n) for n in ['FJP5027.txt','profile.json','reference/curves.csv']}
      p=parse_card(read_text(root/'FJP5027.txt'))
      state={'format_version':1,'result':'PREPARED_NOT_SIMULATED' if args.prepare else 'RUNNING','config':cfg,'initial_hashes':hashes,
        'seed_results':[],'validation_runs':[],'applied':False,'native_simulation':not args.prepare,
        'script_hashes':{'bjt_calibrate.py':sha256(Path(__file__)),'bjt_suite.py':sha256(core.COMMON/'bjt_suite.py'),'qspice_io.py':sha256(core.COMMON/'qspice_io.py')}}
      fit=NativeFit(root,run,cfg,tools,args.prepare,args.max_evals)
      core.dump(run/'targets.json',fit.cases)
      if args.prepare:
        fit.run_cases(p,run/'prepared_cases');write_report(run,state);core.dump(run/'calibration_state.json',state)
        print('Prepared:',run/'report.md');return 0
      state['tool_hashes']={k:sha256(v) for k,v in tools.items()}
      print('Testing native high-current roll-off parameter support...',flush=True)
      state['nk_probe']=fit.probe_nk(p);core.dump(run/'calibration_state.json',state)
      baseline=fit.evaluate(p,'original_model');state['seed_results'].append(baseline['row'])
      if baseline['row']['status']!='EVALUATED':raise SuiteError('Original model failed native baseline; optimization stopped')
      seeds=[('original_model',p)]
      for path in sorted((root/'calibration/seeds').glob('*.json')):
        if path.stem.startswith('nk_') and not fit.nk_keyword:continue
        source=json.loads(read_text(path));seed=dict(p);seed.update(source['params'])
        if not fit.nk_keyword:seed.pop('NK',None)
        seed['RBM']=min(seed['RBM'],seed['RB'])
        r=fit.evaluate(seed,path.stem);state['seed_results'].append(r['row']);seeds.append((path.stem,seed))
      if fit.best is None:raise SuiteError('No candidate passed native table guards')
      state['optimizer']=optimize(fit,fit.best['params'])
      best=fit.best;fine=fit.evaluate(best['params'],'best_fine_scan',fine=True,force=True)
      state['best_fine']=fine['row'];state['evaluations']=fit.evaluations
      improved=(fine['row']['status']=='EVALUATED' and fine['row']['table_guards_pass'] and fine['cost']<baseline['cost']*.9)
      state['improvement_fraction']=1-fine['cost']/max(baseline['cost'],1e-30) if fine['row']['status']=='EVALUATED' else None
      state['result']='NATIVE_DC25_IMPROVED_CANDIDATE' if improved else 'NO_ACCEPTABLE_IMPROVEMENT_MODEL_NOT_REPLACED'
      if improved and not args.no_apply:
        if any(sha256(root/n)!=h for n,h in hashes.items()):raise SuiteError('Input changed during calibration; model not replaced')
        model=render_card(best['params'],fit.nk_keyword)
        newcfg=dict(cfg,model_name=NAME,model_status='PARTIAL_NATIVE_DC25_FIT_TEMPERATURE_AND_DYNAMICS_UNVALIDATED',
          model_provenance='Native QSPICE fitting to selected user-supplied Fairchild Rev.A curves; see calibration_results')
        (run/'candidate_model.txt').write_text(model,encoding='utf-8');core.dump(run/'candidate_profile.json',newcfg)
        try:
          shutil.copy2(run/'candidate_model.txt',root/'FJP5027.txt');shutil.copy2(run/'candidate_profile.json',root/'profile.json')
        except Exception:
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');raise
        state['applied']=True
      core.dump(run/'calibration_state.json',state);write_report(run,state)
      if state['applied'] and not args.skip_validation:
        before=set((root/'results').glob('*'))
        a=['--only','output','gain','saturation','cap','--engine',args.engine]
        if args.qspice_dir:a+=['--qspice-dir',args.qspice_dir]
        code=core.main(root,a);state['validation_exit_code']=code
        new=sorted(set((root/'results').glob('*'))-before)
        state['validation_runs']=[str(p) for p in new if (p/'run_state.json').is_file()]
        if not state['validation_runs']:raise SuiteError('Validation produced no result')
        vd=json.loads(read_text(Path(state['validation_runs'][-1])/'run_state.json'))
        state['validation_result']=vd['result']
        if any(c['status']!='SIMULATED' for c in vd['cases']) or any(r['status']!='PASS' for r in vd.get('limits',[])):
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json')
          state.update(applied=False,result='HOLDOUT_INVALID_OR_TABLE_FAIL_PREVIOUS_MODEL_RESTORED')
      core.dump(run/'calibration_state.json',state);write_report(run,state)
      print('Calibration report:',run/'report.md');print('Diagnostics:',diagnostic_zip(root,run,state['validation_runs']))
      print('Result:',state['result']);return 0 if improved and state['result']=='NATIVE_DC25_IMPROVED_CANDIDATE' else 1
    except (Exception,KeyboardInterrupt) as exc:
      if run is not None and state is not None:
        if state.get('applied') and not args.skip_validation:
          shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');state['applied']=False
        state.update(result='CALIBRATION_STOPPED',error=f'{type(exc).__name__}: {exc}');core.dump(run/'calibration_state.json',state);write_report(run,state)
        print('Diagnostics:',diagnostic_zip(root,run,state.get('validation_runs',[])))
      print('ERROR:',exc,file=sys.stderr);return 2
