"""Cobo from native sinusoidal TRAN admittance with convergence checks.

This does not repair or pass QSPICE's native .ac path. No thermal/model-family
substitution is made. The installed GP is a control, MEXTRAM is separate.
"""
from pathlib import Path
import math
import bjt_power_resume as resume
power=resume.power;base=resume.base;core=resume.core
from qspice_io import SuiteError,read_text,sha256,find_tools,command,parse_measurements

FREQ=1e6
SPECS=[{'id':'step_5ns','amp':.001,'step':5e-9,'start':10e-6,'stop':20e-6},
 {'id':'step_2p5ns','amp':.001,'step':2.5e-9,'start':10e-6,'stop':20e-6},
 {'id':'step_1p25ns','amp':.001,'step':1.25e-9,'start':10e-6,'stop':20e-6},
 {'id':'half_amplitude','amp':.0005,'step':1.25e-9,'start':10e-6,'stop':20e-6},
 {'id':'late_window','amp':.001,'step':1.25e-9,'start':30e-6,'stop':40e-6}]

def prepare(folder,root,params,cfg):
    for family,card,name in [('gp',read_text(root/'FJP5027.txt'),cfg['model_name']),
                              ('mex',power.render(params),power.NAME)]:
        for spec in SPECS:
            f=folder/family/spec['id'];f.mkdir(parents=True,exist_ok=True)
            (f/'case.cir').write_text(deck(card,name,spec),encoding='utf-8')

def deck(card,name,spec):
    return '\n'.join(['* Native TRAN Cobo: 10 V / 1 MHz; no .ac or thermal substitution',
      f'V_TEST c 0 SIN(10 {spec["amp"]:.15g} {FREQ:.15g})',
      'R_EOPEN e 0 1T',f'Q_TEST c 0 e {name}',card,'.temp 25',
      '.options reltol=1e-6 abstol=1e-14 vntol=1e-9',
      'B_PROJECTION proj 0 V={I(V_TEST)*cos(2*pi*1Meg*time)}',
      f'.tran 0 {spec["stop"]:.15g} 0 {spec["step"]:.15g}',
      f'.meas TRAN COBO_COS INTEG V(proj) FROM={spec["start"]:.15g} TO={spec["stop"]:.15g}',
      '.end',''])

def measure(folder,card,name,spec,tools,timeout):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'case.cir').write_text(deck(card,name,spec),encoding='utf-8')
    try:
        command([tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',min(timeout,120))
        if base.logs_have_unknown_parameter(folder):raise SuiteError('Unknown/ignored model parameter')
        raw=folder/'case.qraw'
        if not raw.is_file() or not raw.stat().st_size:raise SuiteError('No native TRAN QRAW')
        output=folder/'measurements.txt'
        command([tools['QPOST'],folder/'case.cir','-r',raw,'-o',output],folder,folder/'qpost.stdout.log',folder/'qpost.stderr.log',60)
        integral=parse_measurements(read_text(output),['COBO_COS'])['COBO_COS']
        duration=spec['stop']-spec['start']
        cap=-2*integral/(duration*spec['amp']*2*math.pi*FREQ)
        if not math.isfinite(cap) or cap<=0:raise SuiteError('Nonpositive/nonfinite transient Cobo')
        return cap
    finally:core.cleanup_large_files(folder)

def measure_set(folder,card,name,tools,timeout):
    rows=[]
    for spec in SPECS:
        row=dict(spec)
        try:
            row.update(Cobo_F=measure(folder/spec['id'],card,name,spec,tools,timeout),status='MEASURED')
        except Exception as exc:row.update(status='FAILED',error=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    return rows

def convergence(rows,control=False):
    d={r['id']:r for r in rows}
    if set(d)!={s['id'] for s in SPECS} or any(r['status']!='MEASURED' for r in rows):
        return {'pass':False,'reason':'Missing/invalid native measurements'}
    values={k:r['Cobo_F'] for k,r in d.items()}
    if any(not math.isfinite(x) or x<=0 for x in values.values()):return {'pass':False,'reason':'Invalid capacitance'}
    rel=lambda a,b:abs(values[a]/values[b]-1)
    checks={'step_5_to_2p5':rel('step_5ns','step_2p5ns'),
      'step_2p5_to_1p25':rel('step_2p5ns','step_1p25ns'),
      'amplitude':rel('half_amplitude','step_1p25ns'),
      'settling':rel('late_window','step_1p25ns')}
    good=all(v<=.005 for v in checks.values())
    if control:
        checks['GP_vs_previous_AC']=max(abs(x/60.0002994610429e-12-1) for x in values.values())
        good=good and checks['GP_vs_previous_AC']<=.02
    return {'pass':bool(good),'relative_checks':checks,'Cobo_F':values['half_amplitude']}

def write_report(folder,state):
    rows=['# Cobo через малосигнальный переходный анализ','',f'**{state["status"]}**','',
      'VCB=10 V, 1 MHz, эмиттер через 1 TΩ. Измеряется квадратурная составляющая тока при синусе: C = -2·integral(Iист·cos(wt))/(T·A·w). Используется реальный QPOST TRAN.',
      'Проверяются три шага, две амплитуды и два временных окна. Для каждой разницы требуется ≤0.5%; GP-контроль должен совпасть с прежним нативным AC в пределах 2%.',
      'Этот результат имеет метод TRAN-admittance. Нативный .ac не выполняется, его сбой этим не исправлен и не объявляется PASS. Тепловые параметры и тип модели не заменяются.']
    for key in ['gp','initial','final']:
        if key not in state:continue
        rows+=['',f'## {key}','', '| Случай | Cobo, pF | Статус |','|---|---:|---|']
        for r in state[key]['rows']:rows.append(f'| {r["id"]} | {r.get("Cobo_F",float("nan"))*1e12:.6g} | {r["status"]} |')
        rows+=['',f'Сходимость: `{state[key]["checks"]}`.']
    if state.get('error'):rows+=['',f'Ошибка: `{state["error"]}`.']
    (folder/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8');core.dump(folder/'transient_cap_state.json',state)

def calibrate(root,folder,params,cfg,args):
    folder.mkdir(parents=True,exist_ok=True);original=dict(params)
    state={'status':'RUNNING','method':'NATIVE_TRAN_SMALL_SIGNAL_ADMITTANCE',
      'native_ac_status':'NOT_RUN_PREVIOUS_BUILD_CRASHED','corrections':[]}
    try:
        tools=find_tools(args.qspice_dir,args.engine);state['tool_hashes']={k:sha256(v) for k,v in tools.items()}
        gp=measure_set(folder/'gp',read_text(root/'FJP5027.txt'),cfg['model_name'],tools,cfg['timeout_seconds'])
        state['gp']={'rows':gp,'checks':convergence(gp,control=True)}
        if not state['gp']['checks']['pass']:raise SuiteError('GP transient control failed convergence/reference check')
        initial=measure_set(folder/'initial',power.render(params),power.NAME,tools,cfg['timeout_seconds'])
        state['initial']={'rows':initial,'checks':convergence(initial)}
        if not state['initial']['checks']['pass']:raise SuiteError('Initial MEXTRAM transient Cobo not converged')
        p=dict(params);cap=state['initial']['checks']['Cobo_F']
        for i in range(4):
            if abs(cap/60e-12-1)<=.02:break
            p['CJC']*=60e-12/cap
            if not 1e-13<p['CJC']<1e-7:raise SuiteError('CJC correction outside extraction limits')
            cap=measure(folder/f'correction_{i}',power.render(p),power.NAME,SPECS[3],tools,cfg['timeout_seconds'])
            state['corrections'].append({'CJC_F':p['CJC'],'Cobo_F':cap})
        if abs(cap/60e-12-1)>.02:raise SuiteError('Cobo correction did not reach 60 pF +/-2%')
        final=measure_set(folder/'final',power.render(p),power.NAME,tools,cfg['timeout_seconds'])
        state['final']={'rows':final,'checks':convergence(final)}
        if not state['final']['checks']['pass'] or abs(state['final']['checks']['Cobo_F']/60e-12-1)>.02:
            raise SuiteError('Corrected Cobo failed repeated convergence/target checks')
        state.update(status='TRAN_COBO_CALIBRATED_AND_CONVERGED',CJC_F=p['CJC'])
        write_report(folder,state);return p,state
    except Exception as exc:
        state.update(status='TRAN_COBO_UNRESOLVED',error=f'{type(exc).__name__}: {exc}')
        write_report(folder,state);return original,state
