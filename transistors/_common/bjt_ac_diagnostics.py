"""Small native reproductions to localize the MEXTRAM AC crash.

Substrate variants are diagnostic only. Rejected five-terminal syntax is
not generated; no transient estimate is substituted for an AC acceptance.
"""
from pathlib import Path
import json,math,re
from qspice_io import SuiteError,read_text,sha256,find_tools,command,parse_measurements
import bjt_power_resume as resume
power=resume.power;base=resume.base;core=resume.core

def deck(model,name,analysis,topology='3pin'):
    if topology not in ['3pin','4pin']:
        raise SuiteError('Five-terminal syntax was rejected natively and is not generated')
    q=f'Q_TEST c 0 e {name}' if topology=='3pin' else f'Q_TEST c 0 e 0 {name}'
    rows=['* MEXTRAM crash diagnostic ONLY; no thermal parameter extraction',
      'V_TEST c 0 SIN(10 1m 1Meg)' if analysis=='tran' else 'V_TEST c 0 DC 10 AC 1',
      'R_EOPEN e 0 1T',q,model,'.temp 25','.options reltol=1e-5 abstol=1e-13 vntol=1e-8']
    if analysis=='op':rows+=['.op']
    elif analysis=='dc':rows+=['.dc V_TEST 9.9 10.1 .1']
    elif analysis=='ac':rows+=['.ac dec 40 100k 10Meg','.meas AC COBO_SOURCE_IM FIND imag(I(V_TEST)) AT=1Meg']
    elif analysis=='tran':rows+=['B_PROJECTION proj 0 V={I(V_TEST)*cos(2*pi*1Meg*time)}',
      '.tran 0 20u 0 2.5n','.meas TRAN COBO_COS INTEG V(proj) FROM=10u TO=20u']
    else:raise SuiteError('Unknown diagnostic analysis')
    return '\n'.join(rows+['.end',''])

def matrix(root,run,params,cfg,args,prepare=False):
    run.mkdir(parents=True,exist_ok=True)
    primary=args.engine;secondary='80' if primary=='64' else '64'
    tests=[('gp_ac','gp','ac','3pin',primary),('gp_tran','gp','tran','3pin',primary),
      ('mex_op','mex','op','3pin',primary),('mex_dc','mex','dc','3pin',primary),
      ('mex_ac3','mex','ac','3pin',primary),('mex_tran3','mex','tran','3pin',primary),
      ('mex_ac4','mex','ac','4pin',primary),
      ('mex_ac3_other','mex','ac','3pin',secondary),('mex_ac4_other','mex','ac','4pin',secondary),
      ]
    state={'purpose':'LOCALIZE_CRASH_ONLY_NO_MODEL_ACCEPTANCE','cases':[],
      'thermal_variant':'Not generated: the former five-terminal line was parsed incorrectly by native QSPICE'}
    gp_model=read_text(root/'FJP5027.txt');mex_model=power.render(params)
    for tag,family,analysis,topology,engine in tests:
        f=run/tag;f.mkdir();model=gp_model if family=='gp' else mex_model
        name=cfg['model_name'] if family=='gp' else power.NAME
        text=deck(model,name,analysis,topology);(f/'case.cir').write_text(text,encoding='utf-8')
        row={'id':tag,'family':family,'analysis':analysis,'topology':topology,'engine':engine,'status':'PREPARED_NOT_SIMULATED' if prepare else 'RUNNING','deck_sha256':sha256(f/'case.cir')}
        state['cases'].append(row)
        if prepare:continue
        try:
            tools=find_tools(args.qspice_dir,engine);row['engine_sha256']=sha256(tools['engine'])
            command([tools['engine'],f/'case.cir'],f,f/'simulator.stdout.log',f/'simulator.stderr.log',min(cfg['timeout_seconds'],60))
            if base.logs_have_unknown_parameter(f):raise SuiteError('Unknown/ignored diagnostic model parameter')
            if analysis in ['ac','tran']:
                raw=f/'case.qraw'
                if not raw.is_file() or not raw.stat().st_size:raise SuiteError('No native QRAW')
                key='COBO_SOURCE_IM' if analysis=='ac' else 'COBO_COS';m=f/'measurements.txt'
                command([tools['QPOST'],f/'case.cir','-r',raw,'-o',m],f,f/'qpost.stdout.log',f/'qpost.stderr.log',60)
                value=parse_measurements(read_text(m),[key])[key]
                cap=-value/(2*math.pi*1e6) if analysis=='ac' else -2*value/(10e-6*.001*2*math.pi*1e6)
                row['measured_capacitance_pF' if analysis=='ac' else 'apparent_transient_capacitance_pF']=cap*1e12
                if not math.isfinite(cap) or cap<=0:raise SuiteError('Invalid native capacitance sign/value')
            row['status']='COMPLETED_DIAGNOSTIC_ONLY'
        except Exception as exc:row.update(status='FAILED',error=f'{type(exc).__name__}: {exc}')
        finally:core.cleanup_large_files(f)
        print('AC diagnostic:',tag,row['status'],flush=True)
    core.dump(run/'ac_diagnostics_state.json',state)
    rows=['# Локализация сбоя MEXTRAM','',
      'Диагностические варианты не устанавливаются и не дают приёмки модели. Прежняя пятиузловая запись Q была неверно разобрана нативным движком; она исключена из генерации.',
      'Переходная оценка на одном шаге/амплитуде служит диагностике; проверки сходимости для принятия Cobo здесь нет.',
      '', '| Случай | Анализ | Топология | Движок | Статус | C, pF (диагностика) |','|---|---|---|---|---|---:|']
    for c in state['cases']:rows.append(f'| {c["id"]} | {c["analysis"]} | {c["topology"]} | {c["engine"]} | {c["status"]} | {c.get("measured_capacitance_pF",c.get("apparent_transient_capacitance_pF","—"))} |')
    (run/'report.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    return state
