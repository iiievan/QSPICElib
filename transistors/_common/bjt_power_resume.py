"""Resume a saved native MEXTRAM fit; never repeat the 147-point search.

Fresh baseline/fine DC, independent one-DUT Cobo AC with GP controls,
then staged multi-temperature DC validation before any installation.
"""
from __future__ import annotations
import argparse,json,math,shutil,sys,zipfile
from datetime import datetime,timezone
from pathlib import Path
import bjt_power_calibrate as power
base=power.base
core=power.core
from qspice_io import SuiteError,read_text,sha256,find_tools,command,parse_measurements

def checkpoint(root,path):
    path=Path(path) if path else root/'calibration/mextram_v6_checkpoint'
    state=json.loads(read_text(path/'power_state.json'))
    for n,h in state['original_hashes'].items():
        if sha256(root/n)!=h:raise SuiteError('Checkpoint input mismatch: '+n+'; no model changed')
    best=json.loads(read_text(path/'best_power_before_cap.json'))
    if best.get('status')!='EVALUATED' or not best.get('table_guards_pass'):raise SuiteError('Checkpoint has no measured, guarded candidate')
    p=power.parse_model(read_text(path/'best_power_model.txt'))
    if int(p.get('LEVEL',1))!=504:raise SuiteError('Expected saved MEXTRAM candidate')
    for k,v in best['params'].items():
        if k not in p or not math.isclose(p[k],v,rel_tol=1e-12,abs_tol=1e-30):raise SuiteError('Checkpoint model/measurement mismatch: '+k)
    return path,state,best,dict(best['params'])

def cobo_deck(model,name):
    # IE approximately zero through 1 TOhm return; same bias as shared QSCH.
    # Remove the second DUT, adapter and behavioral current monitors to isolate
    # the native crash. Raw source current is negative for a positive capacitor.
    return '\n'.join(['* Single-DUT Cobo diagnostic; 10 V, emitter open, 1 MHz',
      'V_TEST c 0 DC 10 AC 1','R_EOPEN e 0 1T',f'Q_TEST c 0 e {name}',model,
      '.temp 25','.options reltol=1e-5 abstol=1e-13 vntol=1e-8',
      '.ac dec 40 100k 10Meg',
      '.meas AC COBO_SOURCE_IM FIND imag(I(V_TEST)) AT=1Meg','.end',''])

def native_cobo(folder,model,name,tools,timeout):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'case.cir').write_text(cobo_deck(model,name),encoding='utf-8')
    try:
        command([tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',timeout)
        if base.logs_have_unknown_parameter(folder):raise SuiteError('Unknown/ignored model parameter in Cobo check')
        raw=folder/'case.qraw'
        if not raw.is_file() or not raw.stat().st_size:raise SuiteError('Cobo check has no QRAW')
        out=folder/'measurements.txt'
        command([tools['QPOST'],folder/'case.cir','-r',raw,'-o',out],folder,folder/'qpost.stdout.log',folder/'qpost.stderr.log',timeout)
        value=-parse_measurements(read_text(out),['COBO_SOURCE_IM'])['COBO_SOURCE_IM']/(2*math.pi*1e6)
        if not math.isfinite(value) or value<=0:raise SuiteError('Wrong sign/nonfinite Cobo source current')
        return value
    finally:core.cleanup_large_files(folder)

def cap_trials(run,current,p,cfg,args,state):
    attempts=state.setdefault('cap_attempts',[])
    gp_name=json.loads(read_text(args.root/'profile.json'))['model_name']
    gp_card=read_text(args.root/'FJP5027.txt')
    # Changing arithmetic engine is explicit and recorded, never a silent PASS.
    engines=[args.engine]+[e for e in ['64','80'] if e!=args.engine]
    for engine in engines:
        row={'engine':engine,'status':'RUNNING'};attempts.append(row)
        try:
            tools=find_tools(args.qspice_dir,engine)
            row['tool_hashes']={k:sha256(v) for k,v in tools.items()}
            folder=run/'cap_checks'/('engine'+engine)
            control=native_cobo(folder/'gp_control',gp_card,gp_name,tools,cfg['timeout_seconds'])
            row['gp_control_pF']=control*1e12
            if not 48e-12<=control<=72e-12:raise SuiteError('Single-DUT GP control outside original Cobo limits')
            candidate=dict(p);row['candidate_checks']=[]
            for i in range(4):
                cap=native_cobo(folder/f'mex_{i}',power.render(candidate),power.NAME,tools,cfg['timeout_seconds'])
                row['candidate_checks'].append({'CJC_F':candidate['CJC'],'Cobo_pF':cap*1e12})
                # Require an actual repeat near 60 pF; scaling alone proves nothing.
                if i>=1 and abs(cap/60e-12-1)<=.02:
                    row.update(status='MEASURED_AND_RECHECKED',Cobo_pF=cap*1e12)
                    return candidate,True
                next_cjc=candidate['CJC']*60e-12/cap
                if not 1e-13<next_cjc<1e-7:raise SuiteError('CJC correction outside extraction bounds')
                candidate['CJC']=next_cjc
            raise SuiteError('Cobo correction did not settle within 2% in four native checks')
        except Exception as exc:
            row.update(status='FAILED',error=f'{type(exc).__name__}: {exc}')
            print(f'Cobo engine {engine}: {exc}',flush=True)
    return dict(p),False

def stage_validation(root,run,cfg,p,args,model_renderer=power.render):
    stage=run/'candidate_workspace';stage.mkdir()
    shutil.copytree(root/'reference',stage/'reference')
    newcfg=dict(cfg,model_name=power.NAME,model_status='MEXTRAM_CANDIDATE_VALIDATION_ONLY',
      schematic_dir=str((root/cfg['schematic_dir']).resolve()))
    core.dump(stage/'profile.json',newcfg)
    (stage/'FJP5027.txt').write_text(model_renderer(p),encoding='utf-8')
    before=set((stage/'results').glob('*'))
    a=['--only','output','gain','saturation','--engine',args.engine]
    if args.qspice_dir:a+=['--qspice-dir',args.qspice_dir]
    core.main(stage,a)
    paths=sorted(f for f in set((stage/'results').glob('*'))-before if (f/'run_state.json').is_file())
    if len(paths)!=1:raise SuiteError('Candidate validation did not produce one result')
    return paths[0],json.loads(read_text(paths[0]/'run_state.json'))

def acceptance(state,baseline,validation):
    bad=power.protected_regressions(baseline,validation)
    state['protected_regressions']=bad
    state['all_dc_cases_simulated']=len(validation['cases'])==15 and all(c['status']=='SIMULATED' for c in validation['cases'])
    state['dc_table_limits_pass']=len(validation.get('limits',[]))==4 and all(c['status']=='PASS' for c in validation['limits'])
    return (state.get('fine_dc_pass',False) and state.get('cap_pass',False) and
      state['all_dc_cases_simulated'] and state['dc_table_limits_pass'] and not bad)

def report(run,state):
    rows=['# FJP5027: продолжение сохранённого MEXTRAM-подбора','',f'**{state["result"]}**','',
      '147 прошлых оценок не повторяются. Выполняются новая GP-база, тонкая DC-проверка сохранённого кандидата, отдельная Cobo и 15 температурных DC-случаев в изолированной папке.',
      'Cobo: один транзистор, VCB=10 V, эмиттер через 1 TΩ, 1 MHz; ток берётся непосредственно из источника. Перед MEXTRAM выполняется GP-контроль. При ошибке 64 проверяется движок 80, каждый результат подписан.',
      '',f'Установка: **{state.get("applied",False)}**. Cobo принята: **{state.get("cap_pass",False)}**.',
      'Если Cobo не рассчиталась, температурные DC-кривые всё равно собираются для диагностики; установка блокируется. Отсутствующее измерение не объявляется PASS.',
      '', '| Проверка | Значение |','|---|---:|']
    for k in ['saved_cost','baseline_cost','fine_cost','fine_dc_pass','all_dc_cases_simulated','dc_table_limits_pass']:
        rows.append(f'| {k} | {state.get(k,"—")} |')
    for r in state.get('cap_attempts',[]):
        rows+=['',f'Движок {r["engine"]}: **{r["status"]}**; GP Cobo={r.get("gp_control_pF","—")} pF; MEXTRAM Cobo={r.get("Cobo_pF","—")} pF.']
        if r.get('error'):rows.append('Ошибка: `'+r['error']+'`.')
    if state.get('validation_summary'):
        s=state['validation_summary'];rows+=['',f'Полные кривые: PASS={s["PASS"]}, FAIL={s["FAIL"]}, INVALID={s["INVALID"]}. Эти результаты относятся ко всем точкам полного прогона, а не только к подбору.',
          '', '[Полный DC-отчёт](validation/report.md)', '', '![Сравнение с даташитом](validation/figures/datasheet_comparison.png)']
    if state.get('protected_regressions'):rows+=['',f'Потеря прежних PASS: `{state["protected_regressions"]}`.']
    if state.get('error'):rows+=['',f'Ошибка: `{state["error"]}`.']
    rows+=['','Температура проверяется изотермически; динамика, заряды и SOA этим продолжением не принимаются. Первичная причина аварии исходного AC-стенда пока не установлена.']
    (run/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8');core.dump(run/'power_resume_state.json',state)

def diagnostics(root,run):
    out=root/'FJP5027_calibration_diagnostics.zip'
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run.rglob('*')):
            if not f.is_file():continue
            rel=f.relative_to(run)
            if any(x in rel.parts for x in ['candidate_workspace','work']):continue
            if f.suffix.lower() in ['.qraw','.zip'] or f.name=='waveforms.csv':continue
            z.write(f,Path('resume')/rel)
    return out

def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--checkpoint');ap.add_argument('--qspice-dir')
    ap.add_argument('--engine',choices=['64','80'],default='64');ap.add_argument('--no-apply',action='store_true')
    args=ap.parse_args(argv);root=Path(root).resolve();args.root=root;run=None;state=None
    try:
        path,old,saved,p=checkpoint(root,args.checkpoint)
        cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg)
        tools=find_tools(args.qspice_dir,args.engine)
        run=root/'power_resume_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
        (run/'inputs').mkdir()
        for n in ['FJP5027.txt','profile.json']:shutil.copy2(root/n,run/'inputs'/n)
        shutil.copytree(path,run/'checkpoint')
        state={'result':'RUNNING','applied':False,'saved_cost':saved['cost'],'original_hashes':old['original_hashes'],
          'tool_hashes':{k:sha256(v) for k,v in tools.items()},'script_sha256':sha256(Path(__file__))}
        fit=power.PowerFit(root,run,cfg,tools,max_evals=3)
        baseline=fit.evaluate(power.parse_model(read_text(root/'FJP5027.txt')),'fresh_gp_baseline')
        if baseline['row']['status']!='EVALUATED':raise SuiteError('Fresh installed model baseline failed')
        state['baseline_cost']=baseline['cost']
        fine=fit.evaluate(p,'saved_mex_fine',fine=True)
        if fine['row']['status']!='EVALUATED':raise SuiteError('Saved MEXTRAM fine DC evaluation failed')
        state['fine_cost']=fine['cost']
        if abs(fine['cost']/saved['cost']-1)>.05:raise SuiteError('Fine objective differs from saved native checkpoint by more than 5%')
        p,cap_ok=cap_trials(run,baseline['params'],p,cfg,args,state);state['cap_pass']=cap_ok
        if cap_ok:
            fine=fit.evaluate(p,'cobo_adjusted_mex_fine',fine=True)
            state['fine_cost']=fine['cost']
        state['fine_dc_pass']=(fine['row']['status']=='EVALUATED' and fine['row']['table_guards_pass'] and fine['cost']<baseline['cost']*.9)
        (run/'candidate_model.txt').write_text(power.render(p),encoding='utf-8')
        validation_path,vd=stage_validation(root,run,cfg,p,args)
        shutil.copytree(validation_path,run/'validation')
        state['validation_summary']={s:sum(r['status']==s for r in vd['curve_comparison']) for s in ['PASS','FAIL','INVALID']}
        previous=json.loads(read_text(path/'baseline_validation.json'))
        accepted=acceptance(state,previous,vd)
        state['result']='MEXTRAM_RESUMED_CANDIDATE_READY' if accepted else 'MEXTRAM_HELD_PREVIOUS_MODEL_RETAINED'
        if accepted and not args.no_apply:
            if any(sha256(root/n)!=h for n,h in old['original_hashes'].items()):raise SuiteError('Inputs changed during resume; model not replaced')
            newcfg=dict(cfg,model_name=power.NAME,model_status='MEXTRAM_PARTIAL_DC25_FIT_WITH_NATIVE_STATIC_CHECKS',
              model_provenance='Independent native MEXTRAM extraction and resume; see power_resume_results')
            core.dump(run/'candidate_profile.json',newcfg)
            try:
                shutil.copy2(run/'candidate_model.txt',root/'FJP5027.txt');shutil.copy2(run/'candidate_profile.json',root/'profile.json')
            except BaseException:
                shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');raise
            state.update(applied=True,result='MEXTRAM_RESUMED_CANDIDATE_APPLIED')
        state['evaluations_this_resume']=fit.evaluations
        report(run,state);print('Report:',run/'report.md');print('Diagnostics:',diagnostics(root,run))
        return 0 if accepted else 1
    except (Exception,KeyboardInterrupt) as exc:
        if state is not None:
            if state.get('applied'):
                shutil.copy2(run/'inputs/FJP5027.txt',root/'FJP5027.txt');shutil.copy2(run/'inputs/profile.json',root/'profile.json');state['applied']=False
            state.update(result='POWER_RESUME_STOPPED',error=f'{type(exc).__name__}: {exc}')
            report(run,state);print('Diagnostics:',diagnostics(root,run))
        print('ERROR:',exc,file=sys.stderr);return 2
