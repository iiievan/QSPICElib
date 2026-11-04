"""QSCH -> separate CIR per case -> QSPICE -> QPOST + CSV -> honest MD report.

Reference and simulation are independent inputs. No simulation data are invented.
The generic transport/parser helpers are reused from the working EL354NA suite.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, re, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from qspice_io import SuiteError, read_text, sha256, find_tools, command, parse_measurements, numeric_cells, real_value

COMMON=Path(__file__).resolve().parent
BENCH={
 'dc':'01_output_gummel_leakage_dc', 'cap':'02_junction_capacitance_ac',
 'resistive':'03_resistive_switching_tran', 'inductive':'04_clamped_inductive_tran',
 'saturation':'05_forced_beta_saturation_dc'}
PROBES={
 'output':['V(vce_out)','V(ic_out)'],
 'gain':['V(ic_gummel)','V(ib_gummel)','V(hfe_calc)','V(vbe)'],
 'saturation':['V(ic)','V(ib)','V(vce)','V(vbe)'],
 'cap':['imag(V(i_cobo))','imag(V(i_cibo))'],
 'resistive':['V(cmd)','V(vce)','V(vbe)','V(ic)','V(ib)','V(p_collector)','V(p_base)'],
 'inductive':['V(cmd)','V(vce)','V(vbe)','V(ic)','V(ib)','V(iload)','V(p_collector)','V(p_base)']}

def dump(path,data):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def plan(cfg,only=None):
    out=[]; only=set(only or PROBES)
    def add(kind,temp,params,tag=''):
        if kind not in only:return
        out.append({'id':f'{kind}_T{temp:+d}'.replace('+','p').replace('-','m')+tag,
          'kind':kind,'temperature_C':temp,'parameters':params,'status':'NOT_RUN',
          'analysis':'ac' if kind=='cap' else 'tran' if kind in ('resistive','inductive') else 'dc',
          'probes':PROBES[kind]})
    for ib in cfg['output_base_currents_A']:
        add('output',25,{'VCE_SWEEP':8,'R_COLLECTOR':.001,'IB_TEST':ib,'SCAN_STEP':.0005},f'_IB{round(ib*1000)}mA')
    for t in cfg['temperatures_C']:
        add('gain',t,{'VCE_GUMMEL':5,'VBE_START':.4,'VBE_STOP':1.6,'SCAN_STEP':.00025})
        add('saturation',t,{'IC_START':.1,'IC_STOP':3,'BETA_FORCED':5,'IC_TEST':1.5,'SCAN_STEP':.0005})
        for kind in ('resistive','inductive'):
            steps=cfg['transient_maxsteps_s'] if t==25 else [cfg['transient_maxsteps_s'][-1]]
            for j,step in enumerate(steps):
                if kind=='resistive':
                    p={'VBUS':400,'R_LOAD':200,'IB_ON':.4,'IB_OFF':-.8,'R_BASE':1,
                      'VBE_MAX':3,'VBE_MIN':-5,'IC_REFERENCE':2,'FSW':5000,'DUTY':.5,
                      'T_DELAY':5e-6,'T_EDGE':5e-9,'N_CYCLES':2,'T_MAX':step}
                else:
                    # Diagnostic CIL: NOT a reproduction of VCEX(sus)/RBSOA test.
                    p={'VCC':20,'VCLAMP':400,'L_LOAD':.002,'R_L':.1,'IB_ON':.3,
                      'R_B_ON':.1,'VBE_OFF':-2,'R_B_OFF':1,'R_CLAMP':.01,
                      'IC_REFERENCE':1.5,'T_DELAY':5e-6,'TON':150e-6,'T_EDGE':5e-9,
                      'T_TAIL':20e-6,'ENERGY_WINDOW':5e-6,'T_MAX':step}
                add(kind,t,p,f'_s{j}')
    add('cap',25,{'VCB_BIAS':10,'VEB_BIAS':1,'AC_AMPL':1,'F_TEST':1e6,'F_MIN':1e5,'F_MAX':1e7})
    if not out:raise SuiteError('No selected cases')
    return out

def validate_profile(root,cfg):
    model=root/cfg['model_file']
    if not model.is_file() or not model.stat().st_size:raise SuiteError('Empty/missing model file')
    if not re.search(r'(?im)^\s*\.model\s+'+re.escape(cfg['model_name'])+r'\s+NPN\b',read_text(model)):
        raise SuiteError('Expected NPN .model not found; update model_name or adapter')
    if any(t< -55 or t>150 for t in cfg['temperatures_C']):raise SuiteError('Temperature outside selected device range')
    if any(float(x)<=0 for x in cfg['transient_maxsteps_s']):raise SuiteError('Invalid timestep')
    provenance=json.loads(read_text(root/'reference/provenance.json'))
    if sha256(root/'reference/FJP5027_RevA_2003.pdf')!=provenance['pdf_sha256']:
        raise SuiteError('Reference PDF changed; re-extract and review curve mapping')

def measures(case):
    k=case['kind']; a=case['analysis'].upper()
    if k=='output':return ['.meas DC OUTPUT_ID_MAX MAX V(ic_out)']
    if k=='gain':return [
      '.meas DC HFE_0P2 FIND V(hfe_calc) WHEN V(ic_gummel)=0.2 CROSS=1',
      '.meas DC HFE_1 FIND V(hfe_calc) WHEN V(ic_gummel)=1 CROSS=1']
    if k=='saturation':return [
      '.meas DC VCESAT_1P5 FIND V(vce) WHEN V(ic)=1.5 CROSS=1',
      '.meas DC VBESAT_1P5 FIND V(vbe) WHEN V(ic)=1.5 CROSS=1',
      '.meas DC IB_AT_1P5 FIND V(ib) WHEN V(ic)=1.5 CROSS=1']
    if k=='cap':return [
      '.meas AC COBO_I_IM FIND imag(V(i_cobo)) AT={F_TEST}',
      '.meas AC CIBO_I_IM FIND imag(V(i_cibo)) AT={F_TEST}']
    common=[
      '.meas TRAN T_STORAGE_CMD TRIG V(cmd)=0.9 FALL=1 TARG V(ic_ratio)=0.9 FALL=1',
      '.meas TRAN T_STORAGE_IB TRIG V(ib)=0 FALL=1 TARG V(ic_ratio)=0.9 FALL=1',
      '.meas TRAN T_FALL_IC TRIG V(ic_ratio)=0.9 FALL=1 TARG V(ic_ratio)=0.1 FALL=1',
      '.meas TRAN VCE_PEAK MAX V(vce)',
      '.meas TRAN IB_REVERSE_PEAK MIN V(ib)']
    if k=='resistive':return common+[
      '.meas TRAN TON_IB TRIG V(ib)={0.1*IB_ON} RISE=1 TARG V(ic_ratio)=0.9 RISE=1',
      '.meas TRAN IC_ON FIND V(ic) AT={T_DELAY+T_EDGE+0.8*TON}',
      '.meas TRAN IB_ON_ACTUAL FIND V(ib) AT={T_DELAY+T_EDGE+0.8*TON}',
      '.meas TRAN P_COLLECTOR_AVG AVG V(p_collector) FROM={T_AVG0} TO={T_STOP}',
      '.meas TRAN P_BASE_AVG AVG V(p_base) FROM={T_AVG0} TO={T_STOP}',
      '.meas TRAN P_TOTAL_AVG AVG V(p_total) FROM={T_AVG0} TO={T_STOP}']
    return common+[
      '.meas TRAN IC_AT_COMMAND_OFF FIND V(ic) AT={T_OFF}',
      '.meas TRAN E_OFF_WINDOW INTEG V(p_collector) FROM={T_OFF} TO={T_OFF+ENERGY_WINDOW}']

def select_dc_branch(source,case):
    """Retain only the electrically independent branch being measured.

    Ground and the sweep command are shared controls, not electrical links
    between the three independent DUT fixtures on schematic 01.
    """
    if case['kind'] not in ('gain','output'):return source
    rows=source.splitlines(); components={}
    for i,line in enumerate(rows):
        s=line.strip()
        if not s or s[0] in '*;.+':continue
        t=s.split()
        if len(t)<3:raise SuiteError('Unrecognized component line: '+s)
        name=t[0].upper()
        nodes=t[1:-1] if name.startswith('X') else t[1:3]
        components[i]=(name,{n.lower() for n in nodes if n.lower() not in ('0','scan')},t)
    target='X_GUMMEL' if case['kind']=='gain' else 'X_OUT'
    match=[i for i,(name,_,_) in components.items() if name==target]
    if len(match)!=1:raise SuiteError('Independent branch DUT not found: '+target)
    selected=set(match);nodes=set(components[match[0]][1]);changed=True
    while changed:
        changed=False
        for i,(name,ports,tokens) in components.items():
            if i in selected or not nodes.intersection(ports):continue
            if name.startswith('X') and name!=target:
                raise SuiteError('DUT branches are electrically coupled; cannot isolate safely')
            selected.add(i);nodes.update(ports);changed=True
    monitors={'ic_gummel','ib_gummel','vbe'} if case['kind']=='gain' else {'vce_out','ic_out'}
    for i,(name,ports,tokens) in components.items():
        if name=='V_SCAN' or (name.startswith('B') and tokens[1].lower() in monitors):selected.add(i)
    case.setdefault('netlist_notes',[]).append('Only '+target+' branch retained; other independent DUT branches and unused hFE monitor omitted')
    return '\n'.join(line for i,line in enumerate(rows) if i not in components or i in selected)

def compile_deck(source,case,cfg,model):
    """Strict top-level parameter edits; never edit/patch the user QSCH."""
    source=source.replace('Âµ','u').replace('µ','u').replace('μ','u')
    source=select_dc_branch(source,case)
    params=dict(case['parameters']); found={k:0 for k in params}
    lines=[]; depth=0; temps=0; analyses=[]; adapters=0
    for line in source.splitlines():
        if re.match(r'^\s*\.subckt\b',line,re.I):depth+=1
        if depth==0:
            if re.match(r'^\s*\.step\b',line,re.I):raise SuiteError('No .step allowed: use separate Python cases')
            if re.match(r'^\s*\.temp\b',line,re.I):
                temps+=1;line=f'.temp {case["temperature_C"]}'
            if re.match(r'^\s*\.(?:lib|inc|include)\b',line,re.I):
                if re.search(r'00_device\.cir',line,re.I):adapters+=1;continue
                raise SuiteError('Unfrozen external include: '+line)
            if re.match(r'^\s*\.(?:libpath|meas(?:ure)?|plot|end)\b',line,re.I):continue
            if re.match(r'^\s*\.param\b',line,re.I):
                if re.search(r'\bPOL\s*=',line,re.I):raise SuiteError('POL must be declared only in injected adapter')
                for key,value in params.items():
                    pattern=r'(\b'+re.escape(key)+r'\s*=\s*)(\{[^}]*\}|[^\s]+)'
                    line,n=re.subn(pattern,lambda m:m[1]+format(float(value),'.15g'),line,flags=re.I)
                    found[key]+=n
            m=re.match(r'^\s*\.(dc|ac|tran)\b',line,re.I)
            if m:analyses.append(m[1].lower())
        if case['kind']=='saturation' and re.match(r'^\s*V_IB\s+',line,re.I):
            t=line.split()
            if len(t)!=4 or t[3]!='0':raise SuiteError('V_IB must be a zero-volt current probe')
            if [n.lower() for n in t[1:3]]==['base','b_feed']:
                line='V_IB b_feed base 0'
                case.setdefault('netlist_notes',[]).append('Reversed zero-volt V_IB probe normalized in generated CIR; correct its orientation in QSCH too')
            elif [n.lower() for n in t[1:3]]!=['b_feed','base']:
                raise SuiteError('Unexpected V_IB nodes in saturation fixture')
        lines.append(line)
        if re.match(r'^\s*\.ends\b',line,re.I):depth-=1
    if depth or temps!=1 or adapters!=1 or analyses!=[case['analysis']] or any(n!=1 for n in found.values()):
        raise SuiteError(f'Deck contract mismatch: parameters={found}, temps={temps}, adapters={adapters}, analyses={analyses}')
    lines += ['.param POL=1',model,'.subckt DUT_BJT C B E K',
      f'Q_CORE C B E {cfg["model_name"]}','R_K K E 1u','.ends DUT_BJT']
    if case['kind']=='gain':lines += ['B_HFE_CALC hfe_calc 0 V={if(V(ib_gummel)>1e-10,V(ic_gummel)/max(V(ib_gummel),1e-12),0)}']
    lines+=measures(case)+['.end']
    deck='* '+cfg['component']+' / '+case['id']+' / frozen model\n'+'\n'.join(lines)+'\n'
    expected=re.findall(r'(?im)^\s*\.meas\s+(?:DC|AC|TRAN)\s+(\w+)',deck)
    return deck,expected

def get_source(root,cfg,case,tools,run,cache,prepare,cir_only):
    k=case['kind']; name=BENCH['dc' if k in ('output','gain') else k]
    if name in cache:return cache[name]
    qsch=(root/cfg['schematic_dir']/(name+'.qsch')).resolve()
    cir=COMMON/'bjt'/(name+'.cir')
    if k=='saturation' and not qsch.is_file():
        source=read_text(cir); origin='common CIR (additional bench; QSCH can be drawn later)'; path=cir
    elif prepare or cir_only:
        source=read_text(cir); origin='CIR snapshot; QSCH was not netlisted'; path=cir
    else:
        if not qsch.is_file():raise SuiteError('Shared QSCH missing: '+str(qsch))
        path=qsch;origin='QUX netlist of user QSCH'
        folder=run/'netlists';folder.mkdir(exist_ok=True)
        target=folder/(name+'.cir')
        command([tools['QUX'],'-Netlist',qsch,'-stdout'],qsch.parent,target,folder/(name+'.stderr.log'),cfg['timeout_seconds'])
        source=read_text(target)
    cache[name]=(source,{'path':str(path),'sha256':sha256(path),'origin':origin})
    return cache[name]

def load_csv(path,case):
    data=[]; count=len(case['probes'])
    for line in read_text(path).splitlines():
        if re.search(r'\b(?:nan|inf)\b',line,re.I):raise SuiteError('Nonfinite waveform data')
        cells=numeric_cells(line)
        if not cells:
            if re.match(r'^\s*"?[+\-\d.(]',line):raise SuiteError('Damaged numeric CSV row')
            continue
        if case['analysis']=='ac' and len(cells)==1+2*count:
            cells=[cells[0]]+[complex(cells[1+2*i].real,cells[2+2*i].real) for i in range(count)]
        if len(cells)!=count+1:raise SuiteError(f'Expected {count+1} CSV columns, got {len(cells)}')
        data.append([real_value(v,str(path)) for v in cells])
    a=np.asarray(data,float)
    if len(a)<20 or np.any(np.diff(a[:,0])<=0):raise SuiteError('Missing or nonmonotonic waveform axis')
    if case['analysis']=='dc' and (abs(a[0,0])>1e-6 or abs(a[-1,0]-1)>1e-6):raise SuiteError('Incomplete DC sweep')
    if case['analysis']=='tran':
        stop=stop_time(case)
        if a[0,0]>1e-12 or abs(a[-1,0]-stop)>max(1e-12,stop*1e-6):raise SuiteError('Incomplete transient record')
    return a

def stop_time(case):
    p=case['parameters']
    if case['kind']=='resistive':return p['T_DELAY']+p['N_CYCLES']/p['FSW']
    return p['T_DELAY']+2*p['T_EDGE']+p['TON']+p['T_TAIL']

def operating_checks(case):
    m=case['measurements']; k=case['kind'];p=case['parameters']
    if k in ('resistive','inductive'):
        for key in ['T_STORAGE_CMD','T_STORAGE_IB','T_FALL_IC']:
            if m[key]<=0:raise SuiteError('Wrong switching crossings: '+key)
        if m['IB_REVERSE_PEAK']>=0:raise SuiteError('No reverse base current')
        if k=='resistive':
            if abs(m['IC_ON']/p['IC_REFERENCE']-1)>.05:raise SuiteError('Wrong collector current plateau')
            if abs(m['IB_ON_ACTUAL']/p['IB_ON']-1)>.05:raise SuiteError('Base compliance limited forward drive')
            if abs(m['IB_REVERSE_PEAK']/p['IB_OFF']-1)>.1:raise SuiteError('Reverse drive did not reach requested current')
        else:
            if abs(m['IC_AT_COMMAND_OFF']/p['IC_REFERENCE']-1)>.1:raise SuiteError('Inductive current target not reached')
            if not p['VCLAMP']*.99<=m['VCE_PEAK']<=p['VCLAMP']*1.02:raise SuiteError('Clamp regime invalid')
    if k=='cap' and m['COBO_I_IM']<=0:raise SuiteError('Capacitance current sign is invalid')
    if k=='saturation' and abs(m['IB_AT_1P5']/(1.5/p['BETA_FORCED'])-1)>.001:
        raise SuiteError('Forced base current sign or IC/IB ratio is invalid')

def postprocess(folder,case,tools,cfg,expected):
    raw=folder/'case.qraw'; deck=folder/'case.cir'
    if not raw.is_file() or raw.stat().st_size==0:raise SuiteError('Simulator did not create QRAW')
    out=folder/'measurements.txt'
    command([tools['QPOST'],deck,'-r',raw,'-o',out],folder,folder/'qpost.stdout.log',folder/'qpost.stderr.log',cfg['timeout_seconds'])
    case['measurements']=parse_measurements(read_text(out),expected)
    operating_checks(case)
    intervals=12000
    if case['analysis']=='tran':
        short=min(case['measurements'][x] for x in ['T_FALL_IC','T_STORAGE_IB'])
        requested=math.ceil(stop_time(case)/(short/30))
        intervals=max(20000,min(cfg['max_export_intervals'],requested))
        case['plot_resolution_limited']=requested>cfg['max_export_intervals']
        case['plot_sample_interval_s']=stop_time(case)/intervals
    csvpath=folder/'waveforms.csv'
    command([tools['QUX'],'-Export',raw,','.join(case['probes']),str(intervals),'CSV','-stdout'],folder,csvpath,folder/'export.stderr.log',cfg['timeout_seconds'])
    a=load_csv(csvpath,case);case['waveform_points']=len(a);case['export_intervals']=intervals
    case['status']='SIMULATED'
    return a

def reference(root):
    out={}
    with (root/'reference/curves.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            key=(r['kind'],int(r['temperature_C']),r['curve'])
            out.setdefault(key,[]).append((float(r['x']),float(r['y'])))
    return {k:np.array(v) for k,v in out.items()}

def metric_curve(rx,ry,mx,my,cfg,kind):
    ok=np.isfinite(mx)&np.isfinite(my)&(mx>0)
    mx,my=mx[ok],my[ok]
    if len(mx)<3:raise SuiteError('No valid curve points')
    # Repeated or folded x coordinates would make interpolation ambiguous.
    if np.any(np.diff(mx)<-1e-10):raise SuiteError('Nonmonotonic physical curve axis')
    xx,idx=np.unique(mx,return_index=True); yy=my[idx]
    inside=(rx>=xx[0])&(rx<=xx[-1]);coverage=float(np.mean(inside))
    if np.count_nonzero(inside)<3:raise SuiteError('Insufficient reference overlap')
    # Compare at reference coordinates, without extrapolation. Model curves are
    # sampled densely; interpolation is linear in the plotted x coordinate.
    if kind=='output':pred=np.interp(rx[inside],xx,yy)
    else:pred=np.interp(np.log10(rx[inside]),np.log10(xx),yy)
    err=np.abs(pred-ry[inside])/np.maximum(np.abs(ry[inside]),1e-12)
    p95=float(np.percentile(err,95));rms=float(np.sqrt(np.mean(err**2)))
    return {'status':'PASS' if coverage>=cfg['minimum_reference_coverage'] and p95<=cfg['curve_tolerance_p95_relative'][kind] else 'FAIL',
      'coverage':coverage,'rms_relative':rms,'p95_relative':p95,'max_relative':float(np.max(err)),'points':int(np.sum(inside))}

def curve_metrics_from_array(root,case,a,cfg):
    refs=reference(root);out=[]
    if case['kind']=='output':
        cid=f'IB{round(case["parameters"]["IB_TEST"]*1000)}mA';groups=[('output',cid,a[:,1],a[:,2])]
    elif case['kind']=='gain':groups=[('gain',f'T{case["temperature_C"]:+d}',a[:,1],a[:,3])]
    elif case['kind']=='saturation':groups=[('vcesat',f'T{case["temperature_C"]:+d}',a[:,1],a[:,3]),('vbesat',f'T{case["temperature_C"]:+d}',a[:,1],a[:,4])]
    else:return []
    for kind,cid,mx,my in groups:
        r=refs[(kind,case['temperature_C'],cid)]
        row=metric_curve(r[:,0],r[:,1],mx,my,cfg,kind)
        row.update({'kind':kind,'curve':cid,'case':case['id'],'temperature_C':case['temperature_C']});out.append(row)
    return out

def capture_case(root,folder,case,cfg,a):
    """Grade full CSV first, then retain bounded exact points for later plots."""
    case['curve_metrics']=curve_metrics_from_array(root,case,a,cfg)
    csvpath=folder/'waveforms.csv'
    if csvpath.is_file():case['grading_csv_sha256']=sha256(csvpath)
    edge=np.empty((0,a.shape[1]))
    if case['analysis']=='tran':
        p=case['parameters'];t_off=p['T_DELAY']+(p['DUTY']/p['FSW'] if case['kind']=='resistive' else p['T_EDGE']+p['TON'])
        window=max(5*(case['measurements']['T_STORAGE_CMD']+case['measurements']['T_FALL_IC']),50e-9)
        edge=a[(a[:,0]>=t_off-window*.15)&(a[:,0]<=t_off+window)]
        case['edge_export_points']=len(edge)
        if len(edge)>6000:
            edge=edge[overview_indices(edge,bins=256)];case['edge_display_reduced']=True
    overview=a[overview_indices(a,bins=256 if case['analysis']=='tran' else 512)]
    path=folder/'plot_data.npz';np.savez_compressed(path,overview=overview,edge=edge)
    case['plot_data_sha256']=sha256(path);case['plot_data_points']=len(overview)+len(edge)
    case['grading_basis']='Full CSV before cleanup; compact data are for rendering only'

def load_plot_data(folder,case,edge=False):
    compact=folder/'plot_data.npz'
    if compact.is_file():
        if case.get('plot_data_sha256') and sha256(compact)!=case['plot_data_sha256']:
            raise SuiteError('Compact plot data changed after simulation')
        with np.load(compact,allow_pickle=False) as d:a=d['edge' if edge else 'overview'].copy()
        if a.ndim!=2 or a.shape[1]!=len(case['probes'])+1 or not np.all(np.isfinite(a)):
            raise SuiteError('Invalid compact plot data')
        if len(a)>1 and np.any(np.diff(a[:,0])<=0):raise SuiteError('Compact time axis is not monotonic')
        return a
    return load_csv(folder/'waveforms.csv',case)

def cleanup_large_files(folder,keep_raw=False,keep_waveforms=False):
    """Delete only temporary waveform files in this generated case directory."""
    removed=[];retained=[];size=0
    paths=list(Path(folder).glob('*.qraw'))+[Path(folder)/'waveforms.csv']
    for path in paths:
        if not path.is_file():continue
        if (path.suffix.lower()=='.qraw' and keep_raw) or (path.name=='waveforms.csv' and keep_waveforms):
            retained.append(path.name);continue
        size+=path.stat().st_size;removed.append(path.name);path.unlink()
    return {'removed':removed,'removed_bytes':size,'retained':retained}

def evaluate_curves(root,run,data):
    cfg=data['config'];out=[];previous=data.get('curve_comparison',[])
    for case in data['cases']:
        if case['status']!='SIMULATED' or case['kind'] not in ('output','gain','saturation'):continue
        try:
            if case.get('curve_metrics'):
                out.extend(case['curve_metrics']);continue
            path=run/'cases'/case['id']/'waveforms.csv'
            if path.is_file():
                a=load_csv(path,case);case['curve_metrics']=curve_metrics_from_array(root,case,a,cfg)
                out.extend(case['curve_metrics']);continue
            old=[r for r in previous if r.get('case')==case['id']]
            if old:
                out.extend(old);case['curve_metrics']=old
                case['legacy_data_note']='Full CSV manually deleted in v1; original metrics and existing figures retained'
            else:raise SuiteError('No full CSV or saved curve metrics; cannot grade compact plot samples')
        except Exception as exc:
            out.append({'kind':case['kind'],'case':case['id'],'status':'INVALID','error':str(exc)})
    data['curve_comparison']=out

def evaluate_limits(data):
    rows=[]
    def check(c,name,value,lo=None,hi=None,basis='guaranteed table limit'):
        if not math.isfinite(value):raise SuiteError('Nonfinite limit result')
        rows.append({'case':c['id'],'name':name,'value':value,'min':lo,'max':hi,'basis':basis,
          'status':'PASS' if (lo is None or value>=lo) and (hi is None or value<=hi) else 'FAIL'})
    for c in data['cases']:
        if c['status']!='SIMULATED' or c['temperature_C']!=25:continue
        m=c['measurements'];k=c['kind']
        if k=='gain':
            check(c,'hFE at Ic=0.2 A, Vce=5 V',m['HFE_0P2'],10,40)
            check(c,'hFE at Ic=1 A, Vce=5 V',m['HFE_1'],8,None)
        elif k=='saturation':
            check(c,'VCEsat at Ic=1.5 A, Ib=0.3 A [V]',m['VCESAT_1P5'],0,2)
            check(c,'VBEsat at Ic=1.5 A, Ib=0.3 A [V]',m['VBESAT_1P5'],0,1.5)
        elif k=='cap':
            cap=m['COBO_I_IM']/(2*math.pi*c['parameters']['F_TEST']*c['parameters']['AC_AMPL'])
            c['derived']={'Cobo_pF':cap*1e12,'Cibo_pF':m['CIBO_I_IM']/(2*math.pi*1e6)*1e12}
            check(c,'Cobo [pF], 10 V, 1 MHz',cap*1e12,48,72,'project +/-20% around 60 pF typical; NOT guaranteed range')
        elif k=='resistive':
            check(c,'ton from Ib 10% to Ic 90% [s]',m['TON_IB'],0,.5e-6)
            check(c,'storage from Ib zero crossing [s]',m['T_STORAGE_IB'],0,3e-6,'table max; timing-start convention must be confirmed')
            check(c,'collector fall 90-10% [s]',m['T_FALL_IC'],0,.3e-6)
    data['limits']=rows
    conv=[]
    for kind in ('resistive','inductive'):
        cc=[c for c in data['cases'] if c['kind']==kind and c['temperature_C']==25 and c['status']=='SIMULATED']
        if len(cc)<2:continue
        cc.sort(key=lambda c:c['parameters']['T_MAX'],reverse=True)
        a,b=cc[-2:]
        metrics=['T_STORAGE_IB','T_FALL_IC']+(['E_OFF_WINDOW'] if kind=='inductive' else ['TON_IB','P_TOTAL_AVG'])
        for metric in metrics:
            x,y=a['measurements'][metric],b['measurements'][metric]
            delta=abs(x-y)/max(abs(y),1e-30)
            conv.append({'kind':kind,'metric':metric,'coarse':x,'fine':y,'relative_change':delta,
              'coarse_step_s':a['parameters']['T_MAX'],'fine_step_s':b['parameters']['T_MAX'],
              'status':'PASS' if delta<=data['config']['convergence_relative'] else 'FAIL'})
    data['convergence']=conv

def make_plots(root,run,data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':9,'axes.titlesize':11,'figure.dpi':150})
    refs=reference(root); folder=run/'figures';folder.mkdir(exist_ok=True)
    fig,axes=plt.subplots(2,2,figsize=(12,8.5)); kinds=['output','gain','vcesat','vbesat']
    temps=data['config']['temperatures_C']; colors={t:plt.cm.viridis(i/max(len(temps)-1,1)) for i,t in enumerate(temps)}
    labels={'output':('VCE [V]','IC [A]','Fig.1: output, fixed base currents'),
      'gain':('IC [A]','hFE','Fig.2: VCE = 5 V'),
      'vcesat':('IC [A]','VCE(sat) [V]','Fig.3: forced IC / IB = 5'),
      'vbesat':('IC [A]','VBE(sat) [V]','Fig.4: forced IC / IB = 5')}
    curves_loaded={}
    for c in data['cases']:
        if c['status']=='SIMULATED' and c['kind'] in ('output','gain','saturation'):
            try:curves_loaded[c['id']]=load_plot_data(run/'cases'/c['id'],c)
            except Exception:pass
    for ax,kind in zip(axes.flat,kinds):
        for (rk,t,cid),a in refs.items():
            if rk!=kind:continue
            lab=cid if kind=='output' else f'{t:+d} C'
            color=plt.cm.plasma(int(re.search(r'\d+',cid)[0])/400) if kind=='output' else colors[t]
            ax.plot(a[:,0],a[:,1],color=color,lw=1.2,label=lab+' reference')
        for c in data['cases']:
            if c['id'] not in curves_loaded:continue
            a=curves_loaded[c['id']];ck=c['kind'];t=c['temperature_C']
            if ck=='output' and kind=='output':
                ib=c['parameters']['IB_TEST'];ax.plot(a[:,1],a[:,2],'--',lw=1.4,color=plt.cm.plasma(ib*2.5),label=f'{ib*1000:g} mA QSPICE')
            elif ck=='gain' and kind=='gain':ax.plot(a[:,1],a[:,3],'--',color=colors[t],lw=1.4,label=f'{t:+d} C QSPICE')
            elif ck=='saturation' and kind in ('vcesat','vbesat'):
                ax.plot(a[:,1],a[:,3 if kind=='vcesat' else 4],'--',color=colors[t],lw=1.4,label=f'{t:+d} C QSPICE')
        ax.set(xlabel=labels[kind][0],ylabel=labels[kind][1],title=labels[kind][2])
        if kind=='output':ax.set(xlim=(0,8),ylim=(0,3))
        else:
            ax.set_xscale('log');ax.set_yscale('log')
            ax.set_xlim((.01,10) if kind=='gain' else (.1,10))
            ax.set_ylim((1,100) if kind=='gain' else (.01,100) if kind=='vcesat' else (.1,10))
        ax.grid(True,which='both',alpha=.27);ax.legend(fontsize=6,ncol=2)
    mode='QSPICE vs digitized typical reference' if curves_loaded else 'REFERENCE CURVES - no static QSPICE data'
    if data['mode'].startswith('prepare'):mode='REFERENCE ONLY - QSPICE NOT RUN'
    fig.suptitle(data['config']['component']+' | '+mode,fontsize=14)
    fig.tight_layout(rect=(0,0,1,.96))
    if curves_loaded or not (folder/'datasheet_comparison.png').is_file():fig.savefig(folder/'datasheet_comparison.png')
    plt.close(fig)
    # Separate axes keep volts, amperes and watts from hiding one another.
    for kind in ('resistive','inductive'):
        cc=[c for c in data['cases'] if c['kind']==kind and c['temperature_C']==25 and c['status']=='SIMULATED']
        if not cc:continue
        c=min(cc,key=lambda c:c['parameters']['T_MAX']);p=c['parameters']
        try:a=load_plot_data(run/'cases'/c['id'],c)
        except (OSError,SuiteError):continue  # retain v1 figures after user removed CSV
        t_off=p['T_DELAY']+(p['DUTY']/p['FSW'] if kind=='resistive' else p['T_EDGE']+p['TON'])
        for zoom in (False,True):
            fig,axs=plt.subplots(3,1,figsize=(10,7),sharex=True)
            window=max(5*(c['measurements']['T_STORAGE_CMD']+c['measurements']['T_FALL_IC']),50e-9)
            if zoom:
                edge=load_plot_data(run/'cases'/c['id'],c,edge=True)
                chosen=(edge[:,0]>=t_off-window*.15)&(edge[:,0]<=t_off+window)
                plotted=edge[chosen]
            else:plotted=a[overview_indices(a)]
            x=(plotted[:,0]-t_off)*1e6 if zoom else plotted[:,0]*1e6
            axs[0].plot(x,plotted[:,2],label='VCE [V]');axs[0].plot(x,plotted[:,3],label='VBE [V]')
            axs[1].plot(x,plotted[:,4],label='IC [A]');axs[1].plot(x,plotted[:,5],label='IB [A]')
            if kind=='inductive':axs[1].plot(x,plotted[:,6],label='IL [A]')
            pc=7 if kind=='inductive' else 6
            axs[2].plot(x,plotted[:,pc],label='collector port [W]');axs[2].plot(x,plotted[:,pc+1],label='base port [W]')
            for ax in axs:ax.legend(loc='best');ax.grid(alpha=.3)
            axs[0].set_ylabel('Voltage [V]');axs[1].set_ylabel('Current [A]');axs[2].set_ylabel('Power [W]')
            axs[2].set_xlabel('Time from turn-off command [us]' if zoom else 'Time [us]')
            if zoom:
                axs[2].set_xlim(-window*.15*1e6,window*1e6)
            fig.suptitle(kind+' | 25 C | QSPICE | '+c['id']);fig.tight_layout(rect=(0,0,1,.96))
            fig.savefig(folder/(kind+('_edge' if zoom else '')+'.png'));plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4));tc=np.linspace(-25,150,200)
    th=data['config']['thermal'];power=np.minimum(th['Pc_at_Tc25_W'],np.maximum(0,(th['Tj_max_C']-tc)/th['Rth_JC_K_per_W']))
    ax.plot(tc,power,label='Fig.8 derating / derived RthJC = 2.5 K/W')
    ax.set(xlabel='Case temperature TC [C]',ylabel='Power ceiling [W]',title='Steady thermal limit - no transient thermal simulation')
    ax.grid(alpha=.3);ax.legend();fig.tight_layout();fig.savefig(folder/'thermal_derating.png');plt.close(fig)

def overview_indices(a,bins=2000):
    """Subselect existing points, retaining min/max of every signal per bin."""
    if len(a)<=bins*2:return np.arange(len(a))
    keep={0,len(a)-1}
    for start in range(0,len(a),math.ceil(len(a)/bins)):
        block=a[start:start+math.ceil(len(a)/bins),1:]
        keep.update(start+np.argmin(block,axis=0));keep.update(start+np.argmax(block,axis=0))
    return np.array(sorted(keep),int)

def fmt(v):
    if v is None:return '-'
    if isinstance(v,(float,int)):return f'{v:.7g}'
    return str(v).replace('|','/').replace('\n',' ')

def write_report(root,run,data):
    cfg=data['config'];cases=data['cases'];sim=[c for c in cases if c['status']=='SIMULATED']
    invalid=any(c['status']=='INVALID' for c in cases)
    failures=any(r['status']!='PASS' for r in data.get('curve_comparison',[])+data.get('limits',[])+data.get('convergence',[]))
    data['result']='NOT_RUN' if not sim and not invalid else 'INVALID_OR_INCOMPLETE' if invalid or len(sim)!=len(cases) else 'NEEDS_CALIBRATION_OR_RESOLUTION' if failures else 'MATCHES_TESTED_CRITERIA'
    lines=[f'# {cfg["component"]}: сравнение модели с даташитом','',f'**Результат: {data["result"]}.**',
      '',f'Модель: `{cfg["model_name"]}`; статус: **{cfg["model_status"]}**.',
      'Это начальная модель-кандидат. Даже успешное сравнение типовых кривых не доказывает полное соответствие всех экземпляров, SOA и всех режимов.',
      '',f'Рассчитано QSPICE: **{len(sim)} / {len(cases)}** случаев. Режим: `{data["mode"]}`.',
      '', '![Кривые даташита и модели](figures/datasheet_comparison.png)',
      '', 'Сплошные линии: оцифрованный векторный PDF Fairchild Rev.A, December 2003. Пунктир: только реальные результаты QSPICE, если расчёт выполнен.',
      'Кривые не сглаживаются и не подменяются эталоном. На рисунках указаны физические величины и единицы, а не напряжения служебных B-мониторов.',
      'Метрики рассчитаны по полному CSV до очистки. Компактные NPZ служат только для рисунков: сохраняются исходные точки с min/max каждой величины и отдельное окно выключения; новые значения не интерполируются при уплотнении.',
      '', '## Сравнение типовых кривых','',
      'Критерий проекта: P95 относительной ошибки не выше допуска и покрытие не менее 95%. Это пользовательские критерии, не гарантия производителя. При калибровке на этих же кривых результат является качеством подгонки, не независимой проверкой.',
      '', '| График | Кривая | T, C | Покрытие | RMS | P95 | Max | Допуск P95 | Статус |',
      '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for r in data.get('curve_comparison',[]):
        if r['status']=='INVALID':lines.append(f'| {r["kind"]} | {fmt(r.get("error"))} | - | - | - | - | - | - | INVALID |');continue
        vals=[f'{100*r[k]:.3f}%' for k in ['coverage','rms_relative','p95_relative','max_relative']]
        lines.append('| '+' | '.join([r['kind'],r['curve'],str(r['temperature_C'])]+vals+[f'{100*cfg["curve_tolerance_p95_relative"][r["kind"]]:g}%',r['status']])+' |')
    if not data.get('curve_comparison'):lines+=['| Все | QSPICE ещё не выполнен | - | - | - | - | - | - | NOT_RUN |']
    lines+=['','## Табличные точки','', '| Проверка | Значение SI / указанные единицы | Min | Max | Статус | Основание |', '|---|---:|---:|---:|---|---|']
    for r in data.get('limits',[]):lines.append('| '+' | '.join(fmt(r.get(k)) for k in ['name','value','min','max','status','basis'])+' |')
    if not data.get('limits'):lines+=['| Измерений пока нет | - | - | - | NOT_RUN | - |']
    lines+=['','## Сходимость динамики','', 'Сравниваются два самых мелких успешно рассчитанных шага при 25 C. Измерения QPOST выполнены по полному исходному QRAW до очистки временных файлов.',
      '', '| Стенд | Метрика | Шаг грубее, с | Шаг мельче, с | Значение грубее | Значение мельче | Изменение | Статус |','|---|---|---:|---:|---:|---:|---:|---|']
    for r in data.get('convergence',[]):lines.append('| '+' | '.join(fmt(r[k]) for k in ['kind','metric','coarse_step_s','fine_step_s','coarse','fine'])+f' | {r["relative_change"]*100:.4g}% | {r["status"]} |')
    for kind in ('resistive','inductive'):
        if any(c['kind']==kind and c['status']=='SIMULATED' for c in cases):
            lines+=['',f'### {kind}','',f'![Общий вид](figures/{kind}.png)','',f'![Выключение](figures/{kind}_edge.png)']
    lines+=['','## Температура и тепловые пределы','',
      'Прогоны .temp являются изотермическими: температура модели задаётся явно. На типовых кривых обозначено TC; равенство Tj=TC является допущением сравнения без саморазогрева.',
      'Из Fig.8 выведено RthJC=(150-25)/50=2.5 K/W. Это стационарная оценка, не идентифицированная тепловая RC-цепь. В PDF нет Zth(t), поэтому Cth и переходный нагрев не выдуманы.',
      '', '![Тепловой предел](figures/thermal_derating.png)',
      '', 'Для посчитанной периодической мощности можно оценить Tj при фиксированной TC; эта оценка не замыкает электротепловую обратную связь:',
      '', '| Случай | Pcollector+Pbase, Вт | TC, C | Оценка Tj, C |', '|---|---:|---:|---:|']
    for c in sim:
        if c['kind']=='resistive':
            power=c['measurements']['P_TOTAL_AVG'];tj=c['temperature_C']+power*cfg['thermal']['Rth_JC_K_per_W']
            lines.append(f'| {c["id"]} | {power:.6g} | {c["temperature_C"]} | {tj:.6g} |')
    lines+=['','## Область применимости','',
      '- Рис.1: 7 значений IB, 25 C принято по условиям по умолчанию; температура внутри рисунка не подписана.',
      '- Рис.2: VCE=5 V; hFE строится по фактическому IC и IC/IB, не по V_SCAN.',
      '- Рис.3 и 4: принудительный IC/IB=5. Заголовки рисунков в PDF неоднозначны; используются подписи осей.',
      '- Табличное резистивное переключение: 400 V, 200 ohm, IB1=0.4 A, IB2=-0.8 A. Частота, фронт и отсчёт времени явно сохранены в случаях; определения времени нужно сверять при точной паспортной приёмке.',
      '- CIL является диагностикой коммутации при заданном ограничении, не доказательством VCEX(sus), лавины или RBSOA.',
      '- Fig.5 имеет оси IC/VCE и является границей переключаемого режима, несмотря на название Switching Time. Его нельзя сравнивать с временной осциллограммой.',
      '- Fig.7 содержит две временные кривые без однозначной подписи каждой: автоматическое присвоение ts/tf и PASS не выполняются.',
      '- fT, пробой, утечки при 800 V, вторичный пробой, ресурс и transient Zth не проверены этим набором.',
      '- Энергия E_OFF_WINDOW: знаковый интеграл коллекторного порта за окно, не энергия всего дросселя и не автоматически истинное тепло кристалла.',
      '', '## Случаи и диагностика','', '| Случай | Статус | Источник | Ошибка / замечание |','|---|---|---|---|']
    for c in cases:
        note='; '.join(filter(None,[c.get('error',''),c.get('legacy_data_note',''),c.get('reprocess_note','')]+c.get('netlist_notes',[])))
        if c.get('plot_resolution_limited'):note+=' CSV ограничен по числу точек; субнаносекундный фронт оценивайте по QPOST/QRAW.'
        lines.append(f'| {c["id"]} | {c["status"]} | {fmt(c.get("source",{}).get("origin","-"))} | {fmt(note)} |')
    lines+=['','## Все измерения QPOST','', '| Случай | Метрика | Значение SI |','|---|---|---:|']
    for c in sim:
        for key,value in c['measurements'].items():lines.append(f'| {c["id"]} | {key} | {value:.9g} |')
    lines+=['','## Воспроизводимость','',f'UTC: {data["created_utc"]}. Python: {data["python"]}.',
      '', 'Исходные QSCH не изменяются. На каждый случай создаётся отдельный CIR без .step; модель и адаптер заморожены в CIR. Сохранены параметры, хеши, журналы, QPOST, метрики и компактные данные рисунков. В версии 2 QRAW и полный waveforms.csv удаляются после обработки каждого случая по умолчанию; --keep-raw и --keep-waveforms включают их сохранение.',
      f'Освобождено этим запуском: {data.get("cleanup_removed_bytes",0)/2**20:.3f} MiB. Старые рисунки и метрики версии 1 сохраняются при пересборке, даже если пользователь уже удалил CSV/QRAW; утраченные исходные точки не восстанавливаются.',
      '', '| Файл | SHA-256 |','|---|---|']
    for key,value in data['hashes'].items():lines.append(f'| {fmt(key)} | `{value}` |')
    (run/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

def finish(root,run,data):
    if data.get('reference_sha256')!=sha256(root/'reference/curves.csv'):
        raise SuiteError('Reference changed since run; do not silently regrade old results')
    evaluate_curves(root,run,data);evaluate_limits(data);make_plots(root,run,data);write_report(root,run,data)
    dump(run/'run_state.json',data)

def main(root,argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prepare',action='store_true',help='Generate CIR snapshots and reference-only report; no simulator')
    ap.add_argument('--qspice-dir');ap.add_argument('--engine',choices=['64','80'],default='64')
    ap.add_argument('--only',nargs='+',choices=list(PROBES))
    ap.add_argument('--cir-only',action='store_true',help='Explicitly use CIR snapshots instead of QSCH')
    ap.add_argument('--keep-raw',action='store_true',help='Keep QRAW for debugging; default is delete after processing')
    ap.add_argument('--keep-waveforms',action='store_true',help='Keep full CSV; default is compact NPZ plus saved metrics')
    ap.add_argument('--compact-run',type=Path,help='Compact and clean a previous run without simulation')
    ap.add_argument('--report',type=Path,help='Rebuild an existing result from saved data without simulation')
    ap.add_argument('--reprocess',type=Path,help='Repeat QPOST/export on saved QRAW without resimulation')
    args=ap.parse_args(argv);root=Path(root).resolve()
    try:
        if sum(bool(x) for x in (args.report,args.reprocess,args.compact_run))>1:raise SuiteError('Choose one existing-run operation')
        if args.report or args.reprocess or args.compact_run:
            run=(args.report or args.reprocess or args.compact_run).resolve();data=json.loads(read_text(run/'run_state.json'))
            if args.reprocess or args.compact_run:
                tools=find_tools(args.qspice_dir,args.engine) if args.reprocess else None
                for c in data['cases']:
                    folder=run/'cases'/c['id']
                    try:
                        if args.reprocess:
                            if not (folder/'case.qraw').is_file():
                                c['reprocess_note']='QRAW not retained; saved results kept. Use --report or run again with --keep-raw.'
                                continue
                            expected=re.findall(r'(?im)^\s*\.meas\s+(?:DC|AC|TRAN)\s+(\w+)',read_text(folder/'case.cir'))
                            a=postprocess(folder,c,tools,data['config'],expected)
                            capture_case(root,folder,c,data['config'],a);c.pop('error',None)
                        elif c['status']=='SIMULATED' and (folder/'waveforms.csv').is_file():
                            a=load_csv(folder/'waveforms.csv',c);capture_case(root,folder,c,data['config'],a)
                    except Exception as exc:c.update(status='INVALID',error=str(exc))
                    finally:
                        c['cleanup']=cleanup_large_files(folder,args.keep_raw,args.keep_waveforms)
                data['cleanup_removed_bytes']=sum(c.get('cleanup',{}).get('removed_bytes',0) for c in data['cases'])
            finish(root,run,data);print(run/'report.md');return 0 if data['result'] in ('NOT_RUN','MATCHES_TESTED_CRITERIA') else 1
        cfg=json.loads(read_text(root/'profile.json'));validate_profile(root,cfg)
        cases=plan(cfg,args.only);tools=None if args.prepare else find_tools(args.qspice_dir,args.engine)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');run=root/'results'/stamp;run.mkdir(parents=True)
        data={'created_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version.split()[0],
          'mode':'prepare: NO QSPICE SIMULATION' if args.prepare else 'QSPICE',
          'config':cfg,'cases':cases,'hashes':{},'reference_sha256':sha256(root/'reference/curves.csv'),
          'format_version':2,'retention':{'qraw':args.keep_raw,'full_csv':args.keep_waveforms}}
        model=read_text(root/cfg['model_file']);model_digest=sha256(root/cfg['model_file'])
        for p in [root/'profile.json',root/cfg['model_file'],root/'reference/curves.csv',root/'reference/FJP5027_RevA_2003.pdf',Path(__file__),COMMON/'qspice_io.py']:
            data['hashes'][str(p)]=sha256(p)
        if tools:data['tool_hashes']={name:sha256(path) for name,path in tools.items()}
        dump(run/'run_state.json',data);cache={}
        for i,c in enumerate(cases,1):
            print(f'[{i}/{len(cases)}] {c["id"]}',flush=True)
            folder=run/'cases'/c['id'];folder.mkdir(parents=True)
            try:
                if sha256(root/cfg['model_file'])!=model_digest:raise SuiteError('Model changed during run')
                source,meta=get_source(root,cfg,c,tools,run,cache,args.prepare,args.cir_only);c['source']=meta
                data['hashes'][meta['path']]=meta['sha256']
                deck,expected=compile_deck(source,c,cfg,model)
                (folder/'case.cir').write_text(deck,encoding='utf-8');dump(folder/'parameters.json',c['parameters'])
                if not args.prepare:
                    command([tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',cfg['timeout_seconds'])
                    a=postprocess(folder,c,tools,cfg,expected)
                    capture_case(root,folder,c,cfg,a)
            except Exception as exc:c.update(status='INVALID',error=f'{type(exc).__name__}: {exc}');print('  INVALID:',exc,flush=True)
            finally:
                if not args.prepare:c['cleanup']=cleanup_large_files(folder,args.keep_raw,args.keep_waveforms)
            dump(run/'run_state.json',data)
        data['cleanup_removed_bytes']=sum(c.get('cleanup',{}).get('removed_bytes',0) for c in cases)
        finish(root,run,data);print('Report:',run/'report.md');print('Result:',data['result'])
        return 0 if data['result'] in ('NOT_RUN','MATCHES_TESTED_CRITERIA') else 1
    except Exception as exc:print('ERROR:',exc,file=sys.stderr);return 2
