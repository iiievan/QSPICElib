#!/usr/bin/env python3
"""Run editable QSCH benches, check QPOST measurements, plot and write report.md."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
REAL = re.compile(rf'^{NUMBER}$')
CELL = re.compile(rf'\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)|({NUMBER})')
MARKER = 'EL354NA_RUN_DATA'


class SuiteError(RuntimeError):
    pass


def read_text(path):
    data = Path(path).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def spice_number(value):
    match = re.fullmatch(rf'\s*({NUMBER})(meg|[tgmkunpfµ]?)\s*', str(value), re.I)
    if not match:
        raise SuiteError(f'Invalid SPICE number: {value}')
    factor = {'': 1, 't': 1e12, 'g': 1e9, 'meg': 1e6, 'k': 1e3, 'm': 1e-3,
              'u': 1e-6, 'µ': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15}[match[2].lower()]
    return float(match[1]) * factor


def safe_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise SuiteError(f'Invalid identifier: {value}')
    return value


def load_config():
    cfg = json.loads(read_text(ROOT / 'tests.json'))
    ids = [safe_id(b['id']) for b in cfg['benches']]
    if len(ids) != len(set(ids)):
        raise SuiteError('Duplicate bench identifiers')
    for bench in cfg['benches']:
        if bench['analysis'] not in ('dc', 'ac', 'tran') or not bench['probes']:
            raise SuiteError(f'Invalid bench: {bench["id"]}')
    return cfg


def cases_from_config(cfg, selected=None):
    selected = set(selected or [])
    unknown = selected - {b['id'] for b in cfg['benches']}
    if unknown:
        raise SuiteError('Unknown bench: ' + ', '.join(sorted(unknown)))
    cases = []
    for bench in cfg['benches']:
        if selected and bench['id'] not in selected:
            continue
        sweep = bench.get('sweep')
        for index, value in enumerate(sweep['values'] if sweep else [None]):
            cases.append({'id': bench['id'] + f'__{index+1:02d}', 'bench': bench['id'],
                          'index': index, 'parameters': {sweep['parameter']: value} if sweep else {},
                          'status': 'NOT_RUN'})
    return cases


def inline_libraries(text, base, hashes, stack=()):
    """Freeze the actual referenced model inside generated CIR, without an inputs copy."""
    output = []
    for line in text.splitlines():
        match = re.match(r'^\s*\.(?:lib|include|inc)\s+(?:"([^"]+)"|\x27([^\x27]+)\x27|(\S+))\s*$', line, re.I)
        if match:
            path = (base / next(v for v in match.groups() if v is not None)).resolve()
            if path in stack:
                raise SuiteError(f'Include cycle: {path}')
            if not path.is_file():
                raise SuiteError(f'Model/include not found: {path}')
            try:
                key = path.relative_to(ROOT).as_posix()
            except ValueError:
                key = str(path)
            digest = sha256(path)
            if key in hashes and hashes[key] != digest:
                raise SuiteError('Referenced model changed during run: ' + key)
            hashes[key] = digest
            output += [f'* Included model: {path.name}', inline_libraries(read_text(path), path.parent, hashes, stack+(path,))]
        elif re.match(r'^\s*\.(lib|include|inc)\b', line, re.I):
            raise SuiteError('Library sections/extra include arguments are not supported: ' + line)
        elif not re.match(r'^\s*\.end\s*$', line, re.I):
            output.append(line)
    return '\n'.join(output)


def make_deck(source, bench, case, hashes):
    """Validate and split a top-level .step; keep subcircuit parameters untouched."""
    text = inline_libraries(source, (ROOT / bench['schematic']).parent, hashes)
    output, depth, steps, analyses = [], 0, [], []
    found = {key.upper(): 0 for key in case['parameters']}
    for line in text.splitlines():
        if re.match(r'^\s*\.subckt\b', line, re.I):
            depth += 1
        if depth == 0:
            step = re.match(r'^\s*\.step\s+param\s+(\w+)\s+list\s+(.+?)\s*$', line, re.I)
            if step:
                steps.append((step[1].upper(), step[2].split()))
                continue
            if re.match(r'^\s*\.step\b', line, re.I):
                raise SuiteError('Only one .step param ... list is supported')
            analysis = re.match(r'^\s*\.(dc|ac|tran)\b', line, re.I)
            if analysis:
                analyses.append(analysis[1].lower())
            if re.match(r'^\s*\.param\b', line, re.I):
                for name, value in case['parameters'].items():
                    pattern = rf'(\b{re.escape(name)}\s*=\s*)(\{{[^}}]*\}}|[^\s]+)'
                    line, count = re.subn(pattern, lambda m: m[1]+str(value), line, flags=re.I)
                    found[name.upper()] += count
        output.append(line)
        if re.match(r'^\s*\.ends\b', line, re.I):
            depth -= 1
            if depth < 0:
                raise SuiteError('Unbalanced .ends')
    sweep = bench.get('sweep')
    expected = [(sweep['parameter'].upper(), sweep['values'])] if sweep else []
    if steps and (len(steps) != 1 or not expected or steps[0][0] != expected[0][0] or
                  [spice_number(v) for v in steps[0][1]] != [spice_number(v) for v in expected[0][1]]):
        raise SuiteError('.step differs from tests.json; update both the schematic and configuration')
    if depth or analyses != [bench['analysis']] or any(count != 1 for count in found.values()):
        raise SuiteError(f'Invalid analysis/parameter declaration in {bench["id"]}: {analyses}, {found}')
    deck = '* EL354NA automatic bench: ' + case['id'] + '\n' + '\n'.join(output) + '\n.end\n'
    # QUX emits Windows-encoded micro signs; keep numeric SPICE suffixes ASCII.
    deck = re.sub(rf'\b({NUMBER})[µμ]', lambda m: m[1]+'u', deck)
    expected_names = re.findall(r'^\s*\.meas(?:ure)?\s+(?:dc|ac|tran)\s+(\w+)\b', deck, re.I | re.M)
    if not expected_names or len(expected_names) != len(set(n.upper() for n in expected_names)):
        raise SuiteError('Missing or duplicate .meas names')
    return deck, [n.upper() for n in expected_names]


def numeric_cells(line):
    """Accept CSV/TSV/whitespace and QSPICE complex pairs, with optional quotes."""
    cells, offset = [], 0
    for match in CELL.finditer(line):
        if line[offset:match.start()].strip(' \t,;"'):
            return None
        value = complex(float(match[1]), float(match[2])) if match[1] is not None else complex(float(match[3]), 0)
        cells.append(value)
        offset = match.end()
    if line[offset:].strip(' \t,;"'):
        return None
    return cells or None


def real_value(value, context):
    if not math.isfinite(value.real) or not math.isfinite(value.imag) or abs(value.imag) > max(1e-15, abs(value.real)*1e-9):
        raise SuiteError(f'Nonreal/nonfinite value: {context}')
    return value.real


def parse_measurements(text, expected):
    if re.search(r'\b\d+\s+of\s+\d+\s+steps\b', text, re.I):
        raise SuiteError('Unexpected stepped QPOST log: one unstepped result is required per case')
    results, current = {}, None
    names = {n.upper() for n in expected}
    for line in text.splitlines():
        header = re.match(r'^\s*\.meas(?:ure)?\s+(?:dc|ac|tran)\s+(\w+)\b', line, re.I)
        if header:
            current = header[1].upper()
            if current in results:
                raise SuiteError('Duplicate measurement: ' + current)
            line = line.rsplit(':', 1)[-1] if ':' in line else ''
        direct = re.match(r'^\s*(\w+)\s*[:=]\s*(.+)$', line)
        if direct and direct[1].upper() in names:
            current, line = direct[1].upper(), direct[2]
        if not current or not line.strip():
            continue
        if re.search(r'\b(?:failed|nan|inf|error)\b', line, re.I):
            raise SuiteError('Invalid measurement ' + current + ': ' + line.strip())
        line = re.sub(r'\s*\(at\s+[^)]+\)\s*$', '', line, flags=re.I)
        cells = numeric_cells(line)
        if cells:
            if current not in names or len(cells) > 2 or current in results:
                raise SuiteError('Ambiguous measurement row: ' + line)
            # FIND may append its independent-axis position. It is not another result.
            results[current] = real_value(cells[0], current)
            current = None
    for name in names:
        if name not in results:
            raise SuiteError('Missing/invalid measurement ' + name)
    return results


def read_waveforms(path, bench):
    rows = []
    for line in read_text(path).splitlines():
        if re.search(r'\b(?:nan|inf)\b', line, re.I):
            raise SuiteError(f'Nonfinite waveform value in {path}')
        cells = numeric_cells(line)
        if cells is None:
            # Header/blank rows are allowed; a numeric-looking damaged data row is not.
            if re.match(r'^\s*"?[+\-\d.(]', line) and line.strip():
                raise SuiteError(f'Malformed waveform row in {path}: {line[:120]}')
            continue
        # AC CSV may flatten each complex trace into separate Re/Im columns.
        # Group those columns before validating the logical signal count.
        probe_count = len(bench['probes'])
        if bench.get('analysis') == 'ac' and len(cells) == 1 + 2*probe_count:
            if any(v.imag != 0 for v in cells):
                raise SuiteError(f'Ambiguous mixed complex waveform format in {path}')
            cells = [cells[0]] + [complex(cells[1+2*i].real, cells[2+2*i].real)
                                 for i in range(probe_count)]
        if len(cells) != len(bench['probes']) + 1:
            raise SuiteError(f'Waveform column count: expected {len(bench["probes"])+1}, got {len(cells)}')
        rows.append([real_value(v, str(path)) for v in cells])
    if len(rows) < bench.get('minimum_points', 96) or any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
        raise SuiteError(f'Missing/nonmonotonic waveform data: {path}')
    start, stop = bench['axis']['range']
    tolerance = max(abs(stop-start)*1e-4, 1e-12)
    if abs(rows[0][0]-start) > tolerance or abs(rows[-1][0]-stop) > tolerance:
        raise SuiteError(f'Incomplete waveform range in {path}: {rows[0][0]} .. {rows[-1][0]}')
    return rows


def evaluate(case, bench):
    measurements, checks = case['measurements'], []
    for rule in bench['rules']:
        name = rule['metric']
        if name not in measurements:
            raise SuiteError('Configured metric is missing: ' + name)
        values = rule['expected']
        expected = spice_number(values[case['index']] if isinstance(values, list) and len(values)>1 else values[0] if isinstance(values, list) else values)
        tolerance, value = spice_number(rule['tolerance']), measurements[name]
        mode = rule['mode']
        if mode in ('abs', 'warn_abs'):
            ok, criterion = abs(value-expected) <= tolerance, f'{expected:g} ± {tolerance:g}'
        elif mode == 'max':
            ok, criterion = value <= expected+tolerance, f'≤ {expected:g}'
        elif mode == 'min':
            ok, criterion = value >= expected-tolerance, f'≥ {expected:g}'
        else:
            raise SuiteError('Unknown rule mode: ' + mode)
        checks.append({'metric': name, 'value': value, 'criterion': criterion, 'basis': rule['basis'],
                       'status': 'PASS' if ok else 'WARN' if mode.startswith('warn') else 'FAIL'})
    # Basic physical consistency is separate from an unverified thermal datasheet limit.
    if bench['id'] == '04_switching_tran':
        current = (2.2-measurements['VCE_ON'])/100
        checks.append({'metric': 'IC_ON_DERIVED', 'value': current, 'criterion': '0.002 ± 0.0001',
                       'basis': 'model', 'status': 'PASS' if abs(current-.002)<=.0001 else 'FAIL'})
        for name in ('T_RISE_IC','T_FALL_IC'):
            checks.append({'metric':name,'value':measurements[name],'criterion':'> 0',
                           'basis':'model','status':'PASS' if measurements[name]>0 else 'FAIL'})
    if bench['id'] == '06_temperature_dc':
        for name in ('CTR_AT_25','CTR_AT_COLD','CTR_AT_HOT','IC_NORM_AT_25','VF_AT_COLD','VF_AT_25','VF_AT_HOT'):
            checks.append({'metric': name, 'value': measurements[name], 'criterion': '> 0 (согласованность модели)',
                           'basis': 'model', 'status': 'PASS' if measurements[name] > 0 else 'FAIL'})
        for suffix in ('25','COLD','HOT'):
            error = abs(measurements['CTR_AT_'+suffix] - measurements['CTR_NEG_AT_'+suffix])
            checks.append({'metric': 'POLARITY_'+suffix, 'value': error, 'criterion': '≤ 0.001', 'basis': 'model',
                           'status': 'PASS' if error <= .001 else 'FAIL'})
    case['checks'] = checks
    case['status'] = 'FAIL' if any(c['status']=='FAIL' for c in checks) else 'WARN' if any(c['status']=='WARN' for c in checks) else 'PASS'


def find_tools(requested=None, engine='64'):
    candidates = [Path(p) for p in (requested, os.environ.get('QSPICE_DIR')) if p]
    candidates += [Path.home()/'QSPICE', Path('C:/Program Files/QSPICE'), Path('C:/Program Files (x86)/QSPICE')]
    located = shutil.which('QSPICE'+engine+'.exe')
    if located:
        candidates.append(Path(located).parent)
    for directory in candidates:
        paths = {'QUX': directory/'QUX.exe', 'QPOST': directory/'QPOST.exe', 'engine': directory/('QSPICE'+engine+'.exe')}
        if all(p.is_file() for p in paths.values()):
            return {key: str(path.resolve()) for key,path in paths.items()}
    raise SuiteError('QSPICE tools not found. Set QSPICE_DIR or --qspice-dir (QUX.exe, QPOST.exe, QSPICE'+engine+'.exe).')


def command(args, cwd, out, err, timeout):
    with Path(out).open('wb') as stdout, Path(err).open('wb') as stderr:
        try:
            done = subprocess.run([str(a) for a in args], cwd=str(cwd), stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SuiteError(f'Could not run {Path(args[0]).name}: {exc}') from exc
    if done.returncode:
        raise SuiteError(f'{Path(args[0]).name}: exit {done.returncode}; see {err}')
    for path in (out, err):
        if Path(path).suffix == '.log' and re.search(r'fatal error|timestep too small|unknown (?:device|subcircuit|voltage)', read_text(path), re.I):
            raise SuiteError(f'Simulator error; see {path}')


def case_folder(run_dir, case_id):
    return Path(run_dir)/'diagnostics'/'cases'/safe_id(case_id)


def postprocess_case(run_dir, case, bench, tools, cfg):
    folder = case_folder(run_dir, case['id'])
    cir, raw = folder/'case.cir', folder/'case.qraw'
    if not cir.is_file() or not raw.is_file() or not raw.stat().st_size:
        raise SuiteError(f'Missing case.cir/case.qraw: {folder}')
    expected = re.findall(r'^\s*\.meas(?:ure)?\s+(?:dc|ac|tran)\s+(\w+)\b', read_text(cir), re.I|re.M)
    meas = folder/'measurements.txt'
    command([tools['QPOST'], cir, '-r', raw, '-o', meas], folder, folder/'qpost.stdout.log', folder/'qpost.stderr.log', cfg['timeout_seconds'])
    if not meas.is_file():
        raise SuiteError('QPOST did not create measurements.txt')
    case['measurements'] = parse_measurements(read_text(meas), expected)
    wave = folder/'waveforms.csv'
    command([tools['QUX'], '-Export', raw, ','.join(bench['probes']), str(cfg['export_intervals']), 'CSV', '-stdout'], folder, wave, folder/'export.stderr.log', cfg['timeout_seconds'])
    case['waveform_points'] = len(read_waveforms(wave, bench))
    evaluate(case, bench)


def plot_label(case):
    return ', '.join(f'{key}={value}' for key,value in case['parameters'].items()) or 'EL354NA'


def _make_figures(run_dir, data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    out = Path(run_dir)/'figures'
    out.mkdir(exist_ok=True)
    pictures = {}
    with plt.rc_context({'font.size':11,'axes.grid':True,'grid.alpha':.25,'axes.formatter.useoffset':False}):
        for bench in data['config']['benches']:
            cases = [c for c in data['cases'] if c['bench']==bench['id'] and c.get('measurements') and (case_folder(run_dir,c['id'])/'waveforms.csv').is_file()]
            if not cases:
                continue
            number = bench['id'][:2]
            labels = {'01':['VF, V','CTR, %','CTR / CTR(IF=5 mA)','Polarity difference, percentage points'],
                      '02':['VCE(sat), V','IC load, mA','Dark current, nA','IC curve, mA'],
                      '03':['Input current, uA','Cin = |I| / (2πf), pF'],
                      '04':['VCE, V','IC, mA','IF, mA'],
                      '05':['Input, V (24 V peak)','Output, V','IF, mA'],
                      '06':['IC / IC(5 mA, 25°C)','CTR, %','VF, V (unfitted thermal law)','Dark current, nA (constant Rdark)']}[number]
            fig, axes = plt.subplots(len(labels),1,figsize=(12,3*len(labels)),sharex=True,layout='constrained')
            axes = np.atleast_1d(axes)
            colors = plt.cm.tab10.colors
            for index,case in enumerate(cases):
                values = np.asarray(read_waveforms(case_folder(run_dir,case['id'])/'waveforms.csv',bench))
                x = values[:,0]*bench['axis']['scale']
                y = values[:,1:]
                color, label = colors[index%len(colors)], plot_label(case)
                series = []
                if number=='01':
                    norm = np.interp(.005,values[:,0],y[:,2])
                    series = [(0,y[:,0],label+' +IF'),(0,y[:,1],label+' −IF', '--'),(1,100*y[:,2],label+' +IF'),(1,100*y[:,3],label+' −IF','--'),(2,y[:,2]/norm,label),(3,100*y[:,4],label)]
                elif number=='02':
                    series = [(0,y[:,0],label+' +IF'),(0,y[:,1],label+' −IF','--'),(1,1e3*y[:,2],label+' +IF'),(1,1e3*y[:,3],label+' −IF','--'),(2,1e9*y[:,4],label),(3,1e3*y[:,5],label+'; IF=5m')]
                elif number=='03':
                    series = [(0,1e6*y[:,0],label),(1,1e12*y[:,0]/(2*math.pi*values[:,0]),label)]
                elif number in ('04','05'):
                    series = [(0,y[:,0],label),(1,y[:,1]*(1000 if number=='04' else 1),label),(2,1000*y[:,2],label)]
                elif number=='06':
                    series = [(0,y[:,3],label),(1,100*y[:,0],label+' +IF'),(1,100*y[:,1],label+' −IF','--'),(2,y[:,4],label),(3,1e9*y[:,5],label+' @20V'),(3,1e9*y[:,6],label+' @3.3V','--')]
                for item in series:
                    axis,trace,legend,*style=item
                    axes[axis].plot(x,trace,style[0] if style else '-',color=color,label=legend,linewidth=1.5)
            references = {'01':[(2,1,'Normalized at IF=5mA')], '02':[(0,.2,'VCE(sat) max, 25°C')],
                          '03':[(1,50,'Cin typical @1kHz'),(1,250,'Cin max @1kHz')],
                          '04':[(0,2.18,'IC 10%'),(0,2.02,'IC 90%')],
                          '05':[(1,1,'LOW criterion'),(1,3.2,'HIGH criterion')], '06':[]}[number]
            for axis,value,label in references:
                axes[axis].axhline(value,color='#444444',linestyle=':',linewidth=1,label=label)
            if number=='03':
                for ax in axes:
                    ax.axvline(1000,color='#888888',linestyle=':',linewidth=1)
            if number=='06':
                axes[0].set_yscale('log')
                for ax in axes:
                    ax.axvline(25,color='#888888',linestyle=':',linewidth=1)
            for ax,label in zip(axes,labels):
                ax.set_ylabel(label)
                ax.legend(fontsize=8,ncol=2,loc='best')
                if bench['axis']['log']:
                    ax.set_xscale('log')
            axes[-1].set_xlabel(bench['axis']['label'])
            fig.suptitle(bench['id']+' | '+bench['datasheet'].split(' — ')[0],fontsize=12,wrap=True)
            path = out/(bench['id']+'.png')
            fig.savefig(path,dpi=150)
            plt.close(fig)
            pictures[bench['id']] = [path.relative_to(run_dir).as_posix()]
            if number=='04':
                fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
                for axis,(start,stop,title) in zip(axes,[(19,35,'IC rise'),(69,85,'IC fall')]):
                    axis.plot(x,1000*y[:,1],label='IC')
                    axis.axhline(.2,color='#777777',linestyle=':',label='10% = 0.2 mA')
                    axis.axhline(1.8,color='#333333',linestyle=':',label='90% = 1.8 mA')
                    axis.set(xlim=(start,stop),xlabel='Time, us',ylabel='IC, mA',title=title)
                    axis.legend(fontsize=9)
                path=out/(bench['id']+'_edges.png');fig.savefig(path,dpi=150);plt.close(fig)
                pictures[bench['id']].append(path.relative_to(run_dir).as_posix())
    return pictures


def make_figures(run_dir, data):
    """Keep plots for independent successful benches even if another plot fails."""
    pictures = {}
    for bench in data['config']['benches']:
        scoped = dict(data, config=dict(data['config'], benches=[bench]))
        try:
            pictures.update(_make_figures(run_dir, scoped))
        except Exception as exc:
            data.setdefault('errors', []).append(
                f'Plot generation failed for {bench["id"]}: {type(exc).__name__}: {exc}')
            # Release partially constructed figures before processing the next bench.
            import matplotlib.pyplot as plt
            plt.close('all')
    return pictures


def fmt(value):
    return '—' if value is None else f'{value:.7g}'


def md_cell(value):
    return str(value).replace('|','\\|').replace('\n',' ').replace('\r',' ')


def metric_unit(name):
    if name.startswith('CTR_') or name.startswith('F_T_') or name=='IC_NORM_AT_25' or name.startswith('POLARITY_'):
        return '1 (CTR ×100 = %)'
    if name.startswith('T_'):
        return 'с'
    if name.startswith('ACTUAL_TEMP'):
        return '°C'
    if name.endswith('_PF'):
        return 'пФ'
    if name.startswith(('ICEO','IC_LOAD','IC_CURVE','IC_ON','INPUT_I')):
        return 'А'
    return 'В'


def markdown_report(run_dir, data):
    cfg, cases = data['config'], data['cases']
    failures = [c for c in cases if c['status'] not in ('PASS','WARN')]
    headline = 'УСПЕШНО' if not failures and not data.get('errors') else 'НЕ УСПЕШНО'
    ds = Path(os.path.relpath(ROOT/cfg['datasheet'],run_dir)).as_posix()
    lines = ['# EL354NA — отчёт автоматической проверки модели','',
             f'**{headline}**. Получено измерений: {sum(len(c.get("measurements",{})) for c in cases)}. Расчётных случаев: {len(cases)}.',
             '',f'Дата расчёта UTC: {data["created_utc"]}. Методика: {cfg["suite_version"]}. Режим: {data["mode"]}.',
             '',f'Даташит: [EL354N-G Series, Rev.6, 01.09.2016]({ds}). Номера страниц ниже — печатные номера PDF.',
             '', 'PASS — заданный критерий выполнен. WARN — отклонение от типового значения при выполненных предельных критериях. FAIL — критерий не выполнен. ERROR — достоверное измерение или график не получены.',
             '', 'Пределы даташита применяются в указанных изготовителем условиях. Допуски сравнения с типовыми значениями и симметрии задаёт эта методика, они не являются допусками изготовителя. Протокол проверяет модель; применимость реальной оптопары в изделии подтверждается измерениями изделия.',
             '', '## Сводка','', '| Случай | Параметры | Результат | Замечание |','|---|---|---|---|']
    for case in cases:
        lines.append('| '+' | '.join(md_cell(v) for v in [case['id'],plot_label(case),case['status'],case.get('error','')])+' |')
    if data.get('errors'):
        lines += ['', '## Ошибки обработки',''] + ['- '+md_cell(e) for e in data['errors']]
    basis_names={'limit':'Предел даташита, 25 °C','typical':'Типовое значение; допуск методики','model':'Согласованность модели','circuit':'Критерий схемы'}
    for bench in cfg['benches']:
        group=[c for c in cases if c['bench']==bench['id']]
        if not group:
            continue
        lines += ['', '## '+bench['id']+' — '+bench['name'],'', '**С чем сверять:** '+bench['datasheet'],'']
        lines += ['Точек в графическом экспорте: '+', '.join(c['id']+' — '+str(c.get('waveform_points','—')) for c in group)+'.','']
        if bench['id']=='01_led_ctr_dc':
            lines += ['CTR выводится на графике в процентах; в измерениях — как IC/IF. При IF=±1 мА, VCE=5 В и 25 °C для ранга A диапазон 50…150%. Значения GAIN=0.5/1/1.5 — три заданных варианта модели. Проверка точности заданного GAIN не определяет распределение реальных деталей.',
                      '', 'Для Fig. 3 используется CTR(IF)/CTR(5 мА), поэтому сравнивается форма кривой с VCE=5 В. CTR_FIG3_* — дополнительные значения диагностического узла; сами по себе они не нормированы на 5 мА.','']
        if bench['id']=='02_output_dc':
            lines += ['Ветви насыщения работают при IF=±20 мА и IC≈1 мА. Ветвь утечки: IF=0, VCE=20 В. IC(VCE) снят при IF=5 мА; разные GAIN не заменяют разные IF на Fig. 5/6. Форма выходной кривой — визуальная диагностика модели.','']
        if bench['id']=='03_input_capacitance_ac':
            lines += ['Источник AC=1 В, DC=0 В. Cin=|I(Vac)|/(2πf). Критерий применяется в точке 1 кГц; INPUT_I_MAX также измерен именно при 1 кГц, а не как максимум по частотам. Это входная ёмкость, не ёмкость изоляции CIO.','']
        if bench['id']=='04_switching_tran':
            lines += ['Фронты IC определяются по уровням Vout=2.18/2.02 В: при VCC=2.2 В и RL=100 Ом это IC=0.2/1.8 мА. При IC(on)≈2 мА уровни соответствуют 10/90%. Измеренные интервалы сравниваются с tr/tf≤18 мкс. IF=2.7 мА настроен для этой модели; если установившийся IC существенно отличается от 2 мА, сравнение фиксированных уровней с даташитом теряет смысл.','']
        if bench['id']=='05_ac_polarities_tran':
            lines += ['На входе синус 24 В амплитудой, 50 Гц. V_POS (5 мс) и V_NEG (15 мс) проверяют включение на обеих полуволнах; V_ZERO (10 мс) — отпускание около перехода через ноль. Это функциональный тест с подтяжкой 10 кОм к 3.3 В; пороги 1/3.2 В выбраны для стенда. Проверка отключения фазы и температурная приёмка входа микроконтроллера в этот тест не входят.','']
        if bench['id']=='06_temperature_dc':
            lines += ['Реальная температура симулятора меняется от −35 до +60 °C. THERMAL=1, IF=0.5/1/5/10/25 мА, VCE=5 В. На Fig. 4 все кривые IC нормированы на одну опорную точку IC при IF=5 мА и 25 °C. График использует V(ic_norm), а F_T_COLD/HOT показывает изменение каждой кривой относительно её собственной точки при 25 °C.',
                      '', 'Поправка CTR(T,IF) — приближённое воспроизведение типовых кривых; гарантированные минимумы CTR во всём температурном диапазоне из неё не следуют. Температурная зависимость VF не подогнана к Fig. 1. Утечка задана постоянным Rdark=1 ГОм: 20 нА при 20 В и 3.3 нА при 3.3 В. Результаты ICEO при +60 °C показывают только эту модель, отдельного допуска даташита для них нет.','']
        lines += ['### Критерии','', '| Случай | Измерение | Значение | Единица | Критерий | Основание | Результат |','|---|---|---:|---|---|---|---|']
        for case in group:
            for check in case.get('checks',[]):
                lines.append('| '+' | '.join(md_cell(v) for v in [case['id'],check['metric'],fmt(check['value']),metric_unit(check['metric']),check['criterion'],basis_names[check['basis']],check['status']])+' |')
        lines += ['', '### Все измерения QPOST','', '| Измерение | Единица | '+' | '.join(md_cell(plot_label(c)) for c in group)+' |', '|---|---|'+'---:|'*len(group)]
        names=sorted({name for c in group for name in c.get('measurements',{})})
        for name in names:
            lines.append('| '+name+' | '+metric_unit(name)+' | '+' | '.join(fmt(c.get('measurements',{}).get(name)) for c in group)+' |')
        for picture in data.get('figures',{}).get(bench['id'],[]):
            lines += ['', f'![{bench["id"]}]({picture})']
    lines += ['', '## Воспроизводимость','', 'Измерения получены QPOST по полному QRAW. Для рисунков используется экспорт QUX в CSV: запрошено '+str(cfg['export_intervals'])+' интервалов на случай; фактическое число точек указано в разделе стенда. Сглаживание matplotlib не применяется. .step развёрнут в отдельные расчёты с теми же значениями параметров. Нетлист создаётся из QSCH; подключённая модель включается в расчётный CIR без изменения исходных файлов.',
              '', '| Исходный файл | SHA-256 |','|---|---|']
    for name, digest in sorted(data['hashes'].items()):
        lines.append(f'| {md_cell(name)} | `{digest}` |')
    lines += ['', 'Инструменты QSPICE:', '', '| Файл | SHA-256 |','|---|---|']
    for name,digest in sorted(data.get('tool_hashes',{}).items()):
        lines.append(f'| {md_cell(name)} | `{digest}` |')
    if data.get('postprocess_runner_sha256'):
        lines += ['', 'Повторная обработка QRAW: '+data['postprocess_utc']+'. SHA-256 скрипта обработки: `'+data['postprocess_runner_sha256']+'`.',
                  '', '| Инструмент повторной обработки | SHA-256 |','|---|---|']
        for name,digest in sorted(data.get('postprocess_tools',{}).items()):
            lines.append(f'| {md_cell(name)} | `{digest}` |')
    lines += ['',f'Python: {md_cell(data.get("python",""))}. matplotlib: {md_cell(data.get("matplotlib",""))}.',
              '', 'Модель не проверяет электрический пробой, изоляцию, саморазогрев, старение и температурную зависимость времени переключения. Эти характеристики требуют отдельных моделей и испытаний.',
              '', 'При успешной обработке всего выбранного набора расчётные файлы удаляются. При FAIL/ERROR сохраняются все полученные QRAW и диагностические файлы; collect_diagnostics.cmd собирает небольшой архив без QRAW и паролей.',
              '', '<!-- '+MARKER+'\n'+json.dumps(data,ensure_ascii=True,allow_nan=False)+'\n-->','']
    path=Path(run_dir)/'report.md'
    temp=path.with_suffix('.md.tmp');temp.write_text('\n'.join(lines),encoding='utf-8');temp.replace(path)
    return path


def load_run(run_dir):
    run_dir=Path(run_dir)
    state=run_dir/'diagnostics'/'run_state.json'
    if state.is_file():
        return json.loads(read_text(state))
    report=run_dir/'report.md'
    if not report.is_file():
        raise SuiteError('No run_state.json/report.md in '+str(run_dir))
    match=re.search(r'<!-- '+MARKER+r'\s+(.*?)\s+-->',read_text(report),re.S)
    if not match:
        raise SuiteError('Report has no embedded run data')
    return json.loads(match[1])


def latest_run(require_diagnostics=False):
    candidates=sorted((ROOT/'results').glob('*'),reverse=True)
    for path in candidates:
        if path.is_dir() and ((path/'diagnostics'/'run_state.json').is_file() if require_diagnostics else (path/'report.md').is_file() or (path/'diagnostics'/'run_state.json').is_file()):
            return path.resolve()
    raise SuiteError('No suitable results folder found')


def save_state(run_dir,data):
    folder=Path(run_dir)/'diagnostics';folder.mkdir(exist_ok=True)
    write_json(folder/'run_state.json',data)


def finish(run_dir,data,keep_raw=False,plot=True):
    data['errors']=[]
    if plot:
        try:
            data['figures']=make_figures(run_dir,data)
            import matplotlib
            data['matplotlib']=matplotlib.__version__
        except Exception as exc:
            data['errors'].append(f'Plot generation failed: {type(exc).__name__}: {exc}')
    for bench_id in {c['bench'] for c in data['cases'] if c['status'] in ('PASS','WARN')}:
        if not data.get('figures',{}).get(bench_id):
            data['errors'].append('Missing figures: '+bench_id)
    # Do not delete raw data if the sources changed while the simulator was running.
    if plot and data['mode']=='qspice':
        for name,digest in data['hashes'].items():
            path=ROOT/name
            if not path.is_file() or sha256(path)!=digest:
                data['errors'].append('Source changed during run: '+name)
    save_state(run_dir,data)
    report=markdown_report(run_dir,data)
    ok=bool(data['cases']) and all(c['status'] in ('PASS','WARN') for c in data['cases']) and not data['errors']
    if ok and not keep_raw:
        shutil.rmtree(Path(run_dir)/'diagnostics')
    print('Report:',report)
    print('PASS/WARN:',sum(c['status'] in ('PASS','WARN') for c in data['cases']), '/',len(data['cases']))
    if not ok:
        print('Diagnostic data retained. Run collect_diagnostics.cmd.')
    return report,0 if ok else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bench',action='append',help='Bench ID from tests.json; repeat for a subset')
    parser.add_argument('--qspice-dir',type=Path)
    parser.add_argument('--engine',choices=['64','80'],default='64')
    parser.add_argument('--keep-raw',action='store_true')
    parser.add_argument('--open-report',action='store_true')
    parser.add_argument('--reprocess',nargs='?',const='latest',metavar='RESULTS_FOLDER')
    parser.add_argument('--rebuild-report',nargs='?',const='latest',metavar='RESULTS_FOLDER')
    args=parser.parse_args(argv)
    if args.reprocess and args.rebuild_report:
        parser.error('Choose reprocess or rebuild-report')
    if args.reprocess or args.rebuild_report:
        requested=args.reprocess or args.rebuild_report
        run_dir=latest_run(require_diagnostics=bool(args.reprocess)) if requested=='latest' else Path(requested).resolve()
        data=load_run(run_dir)
        if args.reprocess:
            tools=find_tools(args.qspice_dir,args.engine)
            benches={b['id']:b for b in data['config']['benches']}
            for case in data['cases']:
                try:
                    postprocess_case(run_dir,case,benches[case['bench']],tools,data['config'])
                    case.pop('error',None)
                except Exception as exc:
                    case['status']='ERROR';case['error']=str(exc)
                save_state(run_dir,data)
            data['mode']='reprocess (без нового расчёта QSPICE)'
            data['postprocess_utc']=datetime.now(timezone.utc).isoformat()
            data['postprocess_runner_sha256']=sha256(ROOT/'run_suite.py')
            data['postprocess_tools']={Path(path).name:sha256(path) for key,path in tools.items() if key!='engine'}
            report,code=finish(run_dir,data,args.keep_raw)
        else:
            data['mode']='rebuild-report (из сохранённых измерений; без нового расчёта)'
            for pictures in data.get('figures',{}).values():
                for picture in pictures:
                    if not (run_dir/picture).is_file():
                        data.setdefault('errors',[]).append('Missing picture: '+picture)
            report=markdown_report(run_dir,data)
            code=0 if all(c['status'] in ('PASS','WARN') for c in data['cases']) and not data.get('errors') else 1
    else:
        cfg=load_config();cases=cases_from_config(cfg,args.bench)
        # Validate basic dependencies before creating any results folders.
        import matplotlib
        tools=find_tools(args.qspice_dir,args.engine)
        for case in cases:
            bench=next(b for b in cfg['benches'] if b['id']==case['bench'])
            if not (ROOT/bench['schematic']).is_file():
                raise SuiteError('Schematic not found: '+bench['schematic'])
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        run_dir=ROOT/'results'/stamp; (run_dir/'diagnostics').mkdir(parents=True)
        data={'created_utc':datetime.now(timezone.utc).isoformat(),'mode':'qspice','config':cfg,'cases':cases,'hashes':{},
              'tool_hashes':{Path(path).name:sha256(path) for path in tools.values()},'python':sys.version.split()[0],
              'matplotlib':matplotlib.__version__,'figures':{},'errors':[]}
        for name in ['run_suite.py','tests.json',cfg['datasheet']]:
            data['hashes'][name]=sha256(ROOT/name)
        sources={}
        save_state(run_dir,data)
        for index,case in enumerate(cases,1):
            bench=next(b for b in cfg['benches'] if b['id']==case['bench'])
            print(f'[{index}/{len(cases)}] {case["id"]} ({plot_label(case)})',flush=True)
            try:
                if bench['id'] not in sources:
                    schematic=ROOT/bench['schematic']
                    data['hashes'][bench['schematic']]=sha256(schematic)
                    folder=run_dir/'diagnostics'/'netlists';folder.mkdir(exist_ok=True)
                    cir=folder/(bench['id']+'.cir')
                    command([tools['QUX'],'-Netlist',schematic,'-stdout'],schematic.parent,cir,
                            folder/(bench['id']+'.stderr.log'),cfg['timeout_seconds'])
                    sources[bench['id']]=read_text(cir)
                deck,expected=make_deck(sources[bench['id']],bench,case,data['hashes'])
                folder=case_folder(run_dir,case['id']);folder.mkdir(parents=True,exist_ok=True)
                (folder/'case.cir').write_text(deck,encoding='utf-8')
                write_json(folder/'parameters.json',case['parameters'])
                command([tools['engine'],folder/'case.cir'],folder,folder/'simulator.stdout.log',folder/'simulator.stderr.log',cfg['timeout_seconds'])
                postprocess_case(run_dir,case,bench,tools,cfg)
                print('  '+case['status'])
            except Exception as exc:
                case['status']='ERROR';case['error']=f'{type(exc).__name__}: {exc}'
                print('  ERROR: '+str(exc),flush=True)
            save_state(run_dir,data)
        report,code=finish(run_dir,data,args.keep_raw)
    if args.open_report and os.name=='nt':
        try:
            os.startfile(str(report))
        except OSError:
            print('Report saved. Open report.md in an editor with Markdown preview.')
    return code


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nInterrupted. Existing diagnostic files were retained.',file=sys.stderr)
        raise SystemExit(130)
    except (SuiteError,OSError,ValueError,KeyError,ImportError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        raise SystemExit(2)
