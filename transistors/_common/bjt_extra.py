"""FJP5027 Fig.5/6 external envelopes and Fig.7 native current sweep.

No SOA/failure behavior is inferred from a Gummel-Poon simulation.
QPOST timing thresholds are based on each case's measured collector plateau.
"""
from __future__ import annotations
import argparse,csv,json,math,re,shutil,sys,zipfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import bjt_suite as core
from qspice_io import SuiteError,read_text,sha256,command,find_tools,parse_measurements

PILOT=[
 '.meas TRAN IC_ON FIND V(ic) AT={T_DELAY+T_EDGE+0.8*TON}',
 '.meas TRAN VCE_ON FIND V(vce) AT={T_DELAY+T_EDGE+0.8*TON}',
 '.meas TRAN IB_ON_ACTUAL FIND V(ib) AT={T_DELAY+T_EDGE+0.8*TON}',
 '.meas TRAN IB_REVERSE_PEAK MIN V(ib)',
 '.meas TRAN VCE_PEAK MAX V(vce)']

def timing_measures(actual_ic):
    if not math.isfinite(actual_ic) or actual_ic<=0:raise SuiteError('No positive collector plateau')
    hi=f'{actual_ic*.9:.16g}';lo=f'{actual_ic*.1:.16g}'
    return PILOT+[
      f'.meas TRAN T_STORAGE_CMD TRIG V(cmd)=0.9 FALL=1 TARG V(ic)={hi} FALL=1',
      f'.meas TRAN T_STORAGE_IB TRIG V(ib)=0 FALL=1 TARG V(ic)={hi} FALL=1',
      f'.meas TRAN T_FALL_IC TRIG V(ic)={hi} FALL=1 TARG V(ic)={lo} FALL=1']

def with_measures(deck,rows):
    kept=[x for x in deck.splitlines() if not re.match(r'^\s*\.(?:meas(?:ure)?|end)\b',x,re.I)]
    return '\n'.join(kept+rows+['.end'])+'\n'

def expected(rows):return [re.match(r'\.meas\s+TRAN\s+(\w+)',x,re.I)[1] for x in rows]

def plan(currents=None,steps=None):
    currents=currents if currents is not None else np.geomspace(.35,2,9)
    steps=steps if steps is not None else [1e-9,.5e-9]
    if any(not math.isfinite(float(i)) or float(i)<=0 or float(i)>3 for i in currents):raise SuiteError('Current request must be in (0,3] A')
    if len(steps)<2 or any(float(t)<=0 or not math.isfinite(float(t)) for t in steps):raise SuiteError('At least two positive timesteps required')
    if len(set(steps))!=len(steps):raise SuiteError('Timesteps must differ')
    cases=[]
    for i,ic in enumerate(currents):
      for j,step in enumerate(steps):
        p={'VBUS':125,'R_LOAD':125/float(ic),'IB_ON':.045,'IB_OFF':-.5,'R_BASE':1,
           'VBE_MAX':3,'VBE_MIN':-5,'IC_REFERENCE':float(ic),'FSW':5000,'DUTY':.5,
           'T_DELAY':5e-6,'T_EDGE':5e-9,'N_CYCLES':2,'T_MAX':float(step)}
        cases.append({'id':f'fig7_load{i:02d}_s{j}','kind':'resistive','study':'figure7',
          'load_index':i,'nominal_current_A':float(ic),'temperature_C':25,
          'parameters':p,'analysis':'tran','probes':core.PROBES['resistive'],
          'status':'NOT_RUN'})
    return cases

def read_reference(root):
    root=Path(root);p=root/'reference/operating_curves.csv'
    meta=json.loads(read_text(root/'reference/operating_provenance.json'))
    if sha256(p)!=meta['csv_sha256']:raise SuiteError('Operating reference CSV changed')
    if sha256(root/'reference/FJP5027_RevA_2003.pdf')!=meta['pdf_sha256']:raise SuiteError('Reference PDF changed')
    out={}
    with p.open(encoding='utf-8') as f:
      for row in csv.DictReader(f):
        out.setdefault((int(row['figure']),row['curve']),[]).append((int(row['sequence']),float(row['x']),float(row['y'])))
    for k,rows in out.items():
      if [r[0] for r in rows]!=list(range(len(rows))):raise SuiteError('Reference sequence damaged')
      out[k]=np.array(rows,float)[:,1:]
    return out,meta

def choose_base(root,requested=None):
    if requested:
      p=Path(requested).resolve()
      if not (p/'run_state.json').is_file():raise SuiteError('Base run_state.json missing')
      return p
    for p in sorted((Path(root)/'results').glob('*/run_state.json'),reverse=True):
      d=json.loads(read_text(p))
      if d.get('mode')=='QSPICE' and all(any(c['kind']==k and c['status']=='SIMULATED' and c['temperature_C']==25 for c in d['cases']) for k in ('resistive','inductive')):
        return p.parent
    return None

def capture_base(base,run):
    if base is None:return None
    d=json.loads(read_text(base/'run_state.json'));chosen=[]
    for k in ('resistive','inductive'):
      candidates=[c for c in d['cases'] if c['kind']==k and c['status']=='SIMULATED' and c['temperature_C']==25]
      if not candidates:continue
      c=min(candidates,key=lambda x:x['parameters']['T_MAX'])
      # Check bytes before copying a frozen, self-contained overlay snapshot.
      core.load_plot_data(base/'cases'/c['id'],c)
      target=run/'base_overlay/cases'/c['id'];target.mkdir(parents=True,exist_ok=True)
      for name in ['plot_data.npz','case.cir','measurements.txt','parameters.json']:
        path=base/'cases'/c['id']/name
        if path.is_file():shutil.copy2(path,target/name)
      if not (target/'plot_data.npz').is_file():raise SuiteError('Base overlay needs compact NPZ; compact original run first')
      chosen.append(c)
    snap={'original_run':str(base),'original_state_sha256':sha256(base/'run_state.json'),
          'config':d['config'],'cases':chosen,'convergence':d.get('convergence',[])}
    core.dump(run/'base_overlay/run_state.json',snap)
    return {'path':'base_overlay','model':d['config']['model_name'],'cases':len(chosen),
            'original_run':base.name,'snapshot_sha256':sha256(run/'base_overlay/run_state.json')}

def validate_pilot(case,m):
    p=case['parameters']
    if m['IC_ON']<=0:raise SuiteError('No positive collector plateau')
    if not 0<=m['VCE_ON']<=p['VBUS']*1.01:raise SuiteError('Invalid on-state collector voltage')
    if abs(m['IB_ON_ACTUAL']/p['IB_ON']-1)>.05:raise SuiteError('Forward base drive compliance limited')
    if abs(m['IB_REVERSE_PEAK']/p['IB_OFF']-1)>.1:raise SuiteError('Reverse base drive not achieved')
    case['measured_current_reference_A']=m['IC_ON']
    case['current_fraction_of_nominal']=m['IC_ON']/case['nominal_current_A']
    case['regime_note']='VCE_ON_HIGH' if m['VCE_ON']>5 else 'VCE_ON_AT_MOST_5V'
    case['regime_basis']='5 V is a project diagnostic flag, not a specified Fig.7 acceptance limit'

def postprocess(root,folder,case,tools,cfg):
    deck=folder/'case.cir';raw=folder/'case.qraw'
    if not raw.is_file() or not raw.stat().st_size:raise SuiteError('No QRAW produced')
    def qpost(rows,name):
      out=folder/(name+'.txt')
      command([tools['QPOST'],deck,'-r',raw,'-o',out],folder,folder/(name+'.stdout.log'),folder/(name+'.stderr.log'),cfg['timeout_seconds'])
      return parse_measurements(read_text(out),expected(rows))
    pilot=qpost(PILOT,'pilot_measurements');case['pilot_measurements']=pilot
    validate_pilot(case,pilot)
    # Measurement directives only. The native circuit and its QRAW remain identical.
    rows=timing_measures(pilot['IC_ON']);original=read_text(deck)
    (folder/'simulated_deck.cir').write_text(original,encoding='utf-8')
    deck.write_text(with_measures(original,rows),encoding='utf-8')
    m=qpost(rows,'measurements');case['measurements']=m
    if abs(m['IC_ON']/pilot['IC_ON']-1)>1e-6:raise SuiteError('Plateau changed between QPOST passes')
    if any(m[k]<=0 for k in ['T_STORAGE_CMD','T_STORAGE_IB','T_FALL_IC']):raise SuiteError('Missing/negative timing crossings')
    case['timing_basis']='90/10 percent of measured IC_ON; ts from actual IB=0 and separately cmd=0.9'
    intervals=max(20000,min(cfg['max_export_intervals'],math.ceil(core.stop_time(case)/(min(m['T_FALL_IC'],m['T_STORAGE_IB'])/30))))
    case['plot_resolution_limited']=core.stop_time(case)/intervals>min(m['T_FALL_IC'],m['T_STORAGE_IB'])/30
    case['plot_sample_interval_s']=core.stop_time(case)/intervals
    csvpath=folder/'waveforms.csv'
    command([tools['QUX'],'-Export',raw,','.join(case['probes']),str(intervals),'CSV','-stdout'],folder,csvpath,folder/'export.stderr.log',cfg['timeout_seconds'])
    a=core.load_csv(csvpath,case);case['waveform_points']=len(a);case['export_intervals']=intervals
    case['status']='SIMULATED';core.capture_case(root,folder,case,cfg,a)

def convergence(data):
    out=[]
    for index in sorted({c['load_index'] for c in data['cases']}):
      cases=[c for c in data['cases'] if c['load_index']==index]
      good=sorted([c for c in cases if c['status']=='SIMULATED'],key=lambda c:c['parameters']['T_MAX'],reverse=True)
      row={'load_index':index,'status':'INCOMPLETE'}
      if len(good)>=2:
        a,b=good[-2:];metrics={}
        for k in ['IC_ON','T_STORAGE_IB','T_FALL_IC']:
          x,y=a['measurements'][k],b['measurements'][k]
          metrics[k]=abs(x-y)/max(abs(y),1e-30)
        row.update(status='PASS' if max(metrics.values())<=data['config']['convergence_relative'] else 'FAIL',
                   changes=metrics,coarse_case=a['id'],fine_case=b['id'])
      out.append(row)
    data['convergence']=out
    if not data['cases'] or all(c['status']=='NOT_RUN' for c in data['cases']):data['result']='REFERENCE_AND_BASE_OVERLAY_ONLY'
    elif any(c['status']!='SIMULATED' for c in data['cases']):data['result']='INCOMPLETE_NATIVE_SWEEP'
    elif any(c['status']!='PASS' for c in out):data['result']='TIMING_NOT_CONVERGED'
    else:data['result']='SWEEP_COMPLETE_REFERENCE_ASSIGNMENT_UNRESOLVED'

def base_traces(run,data):
    if not data.get('base_overlay'):return []
    base=run/data['base_overlay']['path']
    if sha256(base/'run_state.json')!=data['base_overlay']['snapshot_sha256']:raise SuiteError('Base overlay snapshot changed')
    d=json.loads(read_text(base/'run_state.json'));out=[]
    for c in d['cases']:
      a=core.load_plot_data(base/'cases'/c['id'],c,edge=True)
      if len(a)<3:a=core.load_plot_data(base/'cases'/c['id'],c)
      p=c['parameters'];off=p['T_DELAY']+(p['DUTY']/p['FSW'] if c['kind']=='resistive' else p['T_EDGE']+p['TON'])
      duration=max(c['measurements']['T_STORAGE_CMD']+c['measurements']['T_FALL_IC'],50e-9)*5
      a=a[(a[:,0]>=off)&(a[:,0]<=off+duration)]
      if len(a)<3:continue
      x,y=a[:,2].copy(),a[:,4].copy()
      bad=(x<=0)|(y<=0);x[bad]=np.nan;y[bad]=np.nan
      out.append((c,x,y))
    return out

def plot_report(root,run,data,refs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':9,'axes.titlesize':11,'figure.dpi':160})
    folder=run/'figures';folder.mkdir(exist_ok=True)
    fig,aa=plt.subplots(1,3,figsize=(17,5));traces=base_traces(run,data)
    for figure,ax in zip([5,6,7],aa):
      for (f,label),p in refs.items():
        if f!=figure:continue
        ax.plot(p[:,0],p[:,1],lw=1.5,label='PDF '+label.replace('_unassigned',''))
      ax.set_xscale('log');ax.set_yscale('log');ax.grid(True,which='both',alpha=.25)
    for c,x,y in traces:
      label=c['kind']+' turn-off (different test)'
      if c['kind']=='inductive':aa[0].plot(x,y,'--',lw=1.1,label=label)
      aa[1].plot(x,y,'--',lw=1.1,label=label)
    aa[0].set(xlim=(10,2000),ylim=(1,20),xlabel='VCE [V]',ylabel='IC [A]',title='Fig.5: voltage-current boundary')
    aa[0].text(.03,.04,'PDF: IB1=3 A; RB2=0; L=1 mH; VCC=20 V\nOverlay: diagnostic only; conditions differ',transform=aa[0].transAxes,fontsize=8,bbox=dict(facecolor='white',alpha=.85,edgecolor='none'))
    aa[1].set(xlim=(1,1e4),ylim=(1e-3,100),xlabel='VCE [V]',ylabel='IC [A]',title='Fig.6: manufacturer SOA limits')
    aa[1].axhline(3,color='.5',ls=':',lw=.8,label='IC continuous = 3 A')
    aa[1].text(.03,.04,'No SOA pass/fail: pulse and temperature\nconditions are not established for this overlay',transform=aa[1].transAxes,fontsize=8,bbox=dict(facecolor='white',alpha=.85,edgecolor='none'))
    aa[2].set(xlim=(.1,10),ylim=(.01,10),xlabel='Measured IC_ON [A]',ylabel='Time [us]',title='Fig.7: resistive switching sweep')
    fine=[]
    for index in sorted({c['load_index'] for c in data['cases']}):
      cs=[c for c in data['cases'] if c['load_index']==index and c['status']=='SIMULATED']
      if cs:fine.append(min(cs,key=lambda c:c['parameters']['T_MAX']))
    for key,color,label in [('T_STORAGE_IB','tab:red','QSPICE ts (IB=0 to IC90%)'),('T_FALL_IC','tab:blue','QSPICE tf (IC90% to IC10%)')]:
      ordered=sorted(fine,key=lambda c:c['measurements']['IC_ON'])
      if ordered:
        x=[c['measurements']['IC_ON'] for c in ordered];y=[c['measurements'][key]*1e6 for c in ordered]
        aa[2].plot(x,y,'o--',color=color,ms=4,label=label)
        flagged=[c for c in ordered if c['regime_note']=='VCE_ON_HIGH']
        if flagged:aa[2].scatter([c['measurements']['IC_ON'] for c in flagged],[c['measurements'][key]*1e6 for c in flagged],marker='x',color='black',s=35,zorder=5)
    aa[2].text(.03,.04,'IB1=45 mA; IB2=-0.5 A; VCC=125 V\nT=25 C, pulse ~100 us assumed\nPDF curves A/B are not assigned to ts/tf',transform=aa[2].transAxes,fontsize=8,bbox=dict(facecolor='white',alpha=.85,edgecolor='none'))
    if not fine:aa[2].text(.5,.5,'Fig.7 sweep: NOT RUN',transform=aa[2].transAxes,ha='center',weight='bold')
    for ax in aa:ax.legend(loc='upper right',fontsize=7)
    fig.suptitle('FJP5027 | supplied PDF limits + actual simulation data only',fontsize=13)
    fig.tight_layout(rect=(0,.035,1,.94));fig.text(.5,.01,'SOA envelopes are external manufacturer data. A working SPICE trace does not establish survival or safe operating margin.',ha='center',fontsize=9)
    fig.savefig(folder/'figures_5_6_7.png');plt.close(fig)


def finish(root,run,data):
    refs,meta=read_reference(root)
    if data['reference_sha256']!=sha256(root/'reference/operating_curves.csv'):raise SuiteError('Reference changed since this run')
    convergence(data);plot_report(root,run,data,refs)
    text=['# FJP5027: дополнительные графики 5, 6, 7','',f'**Результат: {data["result"]}.**',
      '',f'Модель: `{data["config"]["model_name"]}`; статус: **{data["config"]["model_status"]}**.',
      '',f'Нативный Fig.7: {sum(c["status"]=="SIMULATED" for c in data["cases"])} / {len(data["cases"])} случаев. Режим: {data["mode"]}.',
      '', '![Паспортные кривые и расчёты](figures/figures_5_6_7.png)',
      '', '## Что означают графики',
      '', '- Fig.5 имеет оси IC/VCE. По осям и условиям это интерпретируется как граница индуктивного выключения типа RBSOA; название Switching Time не превращает её во временную характеристику.',
      '- Fig.6 — паспортная SOA для DC, 10 ms, 1 ms, 100 us. Кривые взяты из PDF; модель не вычисляет вторичный пробой или разрушение. Наложение траектории носит диагностический характер, автоматического допуска по SOA нет.',
      '- Fig.7 — две типовые зависимости времени от тока. В PDF нет однозначного соответствия линий A/B величинам ts/tf. Поэтому они не переименованы, автоматического совпадения/PASS нет.',
      '', '## Условия Fig.7',
      '', 'Используется существующий общий QSCH 03. Новая схема не требуется. VBUS=125 V, IB_ON=45 mA, IB_OFF=-0.5 A. Число 45 mA сохранено ровно как в PDF. Меняется R_LOAD=125/Iном; по оси X показан фактический IC_ON.',
      'Допущения: .temp=25 C; 5 kHz; DUTY=0.5; фронт команды 5 ns; два периода. TON=99.995 us. Температура, длительность импульса, фронт и точное определение ts в Fig.7 не указаны.',
      'QPOST сначала измеряет IC_ON, затем по тому же QRAW вычисляет ts от IB=0 до IC=90% фактического IC_ON, tf между 90% и 10%. Второго запуска физической симуляции для нормировки нет.',
      'Высокое VCE_ON (>5 V) отмечено крестиком и в таблице: это проектный диагностический признак. Такая точка не доказывает переключение из насыщения, даже если время математически измерено.',
      'Кривые самого даташита построены из 283 исходных векторных точек. Вертикальные границы и порядок вершин сохранены. Сглаживания и усреднения повторных X нет.',
      '', '## Траектории из предыдущего прогона']
    if data.get('base_overlay'):
      b=data['base_overlay'];text += ['',f'Источник: `{b["original_run"]}`; модель `{b["model"]}`. Сохранён отдельный снимок компактных данных с проверкой SHA-256.']
      for c,_,_ in base_traces(run,data):
        p=c['parameters']
        details=(f'VCC={p["VCC"]} V, L={p["L_LOAD"]*1000:g} mH, VCLAMP={p["VCLAMP"]} V, IB_ON={p["IB_ON"]} A, R_B_OFF={p["R_B_OFF"]} ohm' if c['kind']=='inductive' else f'VBUS={p["VBUS"]} V, R_LOAD={p["R_LOAD"]} ohm, IB_ON={p["IB_ON"]} A, IB_OFF={p["IB_OFF"]} A')
        text += [f'- `{c["id"]}`: {details}; шаг {p["T_MAX"]:g} s. Условия отличаются от границ PDF.']
      text+=['', 'Точки траекторий взяты из компактного экспорта и не предназначены для оценки субнаносекундных пиков или запаса до SOA. Сходимость исходного индуктивного tf не подтверждена.']
    else:text+=['', 'Предыдущего полного прогона не найдено: показаны только паспортные границы. Для наложения задать --base-run results/ИМЯ.']
    text+=['','## Измерения Fig.7','', '| Случай | Статус | Iном, A | IC_ON, A | VCE_ON, V | ts, us | tf, us | Режим / ошибка |', '|---|---|---:|---:|---:|---:|---:|---|']
    for c in data['cases']:
      m=c.get('measurements',{});fmt=lambda key,scale=1:f'{m[key]*scale:.7g}' if key in m else '—'
      text.append(f'| {c["id"]} | {c["status"]} | {c["nominal_current_A"]:.6g} | {fmt("IC_ON")} | {fmt("VCE_ON")} | {fmt("T_STORAGE_IB",1e6)} | {fmt("T_FALL_IC",1e6)} | {c.get("error",c.get("regime_note",""))} |')
    text+=['','## Сходимость Fig.7','', '| Нагрузка | Статус | ΔIC | Δts | Δtf |', '|---:|---|---:|---:|---:|']
    for c in data['convergence']:
      m=c.get('changes',{});fmt=lambda k:f'{m[k]*100:.5g}%' if k in m else '—'
      text.append(f'| {c["load_index"]} | {c["status"]} | {fmt("IC_ON")} | {fmt("T_STORAGE_IB")} | {fmt("T_FALL_IC")} |')
    text+=['', 'PASS в этой таблице относится только к проверке изменения при уменьшении шага. Совпадение с паспортом, отсутствие разрушения и SOA им не подтверждаются.',
      '', 'QRAW и полный CSV удаляются после обработки по умолчанию; QPOST, CIR, SHA-256 и компактные NPZ остаются. --keep-raw/--keep-waveforms включают сохранение явно.',
      '', '## Ограничения модели и дальнейшая калибровка',
      '', 'Исходная CANDIDATE_V1 остаётся стартовой оценкой. Это дополнение добавляет независимые цели проверки, но не выполняет подгонку или сертификацию модели. Сначала надо согласовать DC и температурные зависимости; затем динамику. Для проверки fT нужен отдельный AC-стенд.',
      'QSPICE поддерживает MEXTRAM 504 с саморазогревом, но переход требует извлечения дополнительных параметров. Наличие более сложных уравнений не восстанавливает отсутствующие данные; переходная тепловая цепь по этому PDF не определена.',
      '', 'Источники: приложенный Fairchild Rev.A December 2003; [Qorvo о генераторе моделей](https://www.qorvo.com/about/news-events/news-releases/2025/qorvo-adds-modeling-capabilities-to-award-winning-qspice-circuit-simulation-software); [Qorvo о MEXTRAM](https://www.qorvo.com/design-hub/videos/2024-qspice-accelerate-your-development-with-new-features); [Toshiba о SOA](https://toshiba.semicon-storage.com/eu/semiconductor/knowledge/faq/mosfet_bipoler-transistors/technical-documents-for-individual-power-transistors-contain.html).']
    (run/'report.md').write_text('\n'.join(text)+'\n',encoding='utf-8');core.dump(run/'extra_state.json',data)
    return run/'report.md'


def collect(root,run):
    root=Path(root);run=Path(run);out=root/'FJP5027_extra_diagnostics.zip'
    if not (run/'extra_state.json').is_file():raise SuiteError('Not an extra run')
    files=sorted([p for p in run.rglob('*') if p.is_file() and p.suffix.lower()!='.qraw' and p.name!='waveforms.csv'])
    files += [root/x for x in ['profile.json','FJP5027.txt','reference/operating_curves.csv','reference/operating_provenance.json']]
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
      for p in files:z.write(p,Path('extra_run')/p.relative_to(run) if p.is_relative_to(run) else p.relative_to(root))
    return out


def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--overlay-only',action='store_true')
    mode.add_argument('--report',type=Path);mode.add_argument('--collect',type=Path)
    ap.add_argument('--base-run',type=Path);ap.add_argument('--qspice-dir');ap.add_argument('--engine',choices=['64','80'],default='64')
    ap.add_argument('--cir-only',action='store_true');ap.add_argument('--keep-raw',action='store_true');ap.add_argument('--keep-waveforms',action='store_true')
    ap.add_argument('--currents',nargs='+',type=float);ap.add_argument('--maxsteps',nargs='+',type=float)
    args=ap.parse_args(argv);root=Path(root).resolve()
    try:
      if args.collect:print(collect(root,args.collect.resolve()));return 0
      if args.report:
        run=args.report.resolve();d=json.loads(read_text(run/'extra_state.json'));print(finish(root,run,d));return 0
      cfg=json.loads(read_text(root/'profile.json'));core.validate_profile(root,cfg);read_reference(root)
      native=not(args.prepare or args.overlay_only);tools=find_tools(args.qspice_dir,args.engine) if native else None
      cases=[] if args.overlay_only else plan(args.currents,args.maxsteps)
      run=root/'extra_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run.mkdir(parents=True)
      data={'format_version':1,'mode':'QSPICE' if native else 'prepare: NO QSPICE SIMULATION' if args.prepare else 'REFERENCE_AND_PREVIOUS_NATIVE_OVERLAY',
            'config':cfg,'cases':cases,'reference_sha256':sha256(root/'reference/operating_curves.csv'),
            'model_sha256':sha256(root/cfg['model_file']),'base_overlay':capture_base(choose_base(root,args.base_run),run),
            'hashes':{'bjt_extra.py':sha256(Path(__file__)),'bjt_suite.py':sha256(core.COMMON/'bjt_suite.py'),'qspice_io.py':sha256(core.COMMON/'qspice_io.py')}}
      for name in ['profile.json',cfg['model_file'],'reference/operating_curves.csv','reference/operating_provenance.json']:
        dest=run/'inputs'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dest)
      if tools:data['tool_hashes']={k:sha256(p) for k,p in tools.items()}
      cache={};model=read_text(root/cfg['model_file'])
      for i,c in enumerate(cases,1):
        print(f'[{i}/{len(cases)}] {c["id"]}',flush=True);folder=run/'cases'/c['id'];folder.mkdir(parents=True)
        try:
          if sha256(root/cfg['model_file'])!=data['model_sha256']:raise SuiteError('Model changed during sweep')
          source,c['source']=core.get_source(root,cfg,c,tools,run,cache,args.prepare,args.cir_only)
          deck,_=core.compile_deck(source,c,cfg,model);deck=with_measures(deck,PILOT)
          (folder/'case.cir').write_text(deck,encoding='utf-8');core.dump(folder/'parameters.json',c['parameters'])
          if native:
            command([tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',cfg['timeout_seconds'])
            postprocess(root,folder,c,tools,cfg)
        except Exception as exc:c.update(status='INVALID',error=f'{type(exc).__name__}: {exc}');print(' INVALID:',exc,flush=True)
        finally:
          if native:c['cleanup']=core.cleanup_large_files(folder,args.keep_raw,args.keep_waveforms)
        core.dump(run/'extra_state.json',data)
      data['cleanup_removed_bytes']=sum(c.get('cleanup',{}).get('removed_bytes',0) for c in cases)
      print('Report:',finish(root,run,data));print('Diagnostics:',collect(root,run));print('Result:',data['result'])
      if not native:return 0
      return 0 if data['result']=='SWEEP_COMPLETE_REFERENCE_ASSIGNMENT_UNRESOLVED' else 1
    except Exception as exc:print('ERROR:',exc,file=sys.stderr);return 2
