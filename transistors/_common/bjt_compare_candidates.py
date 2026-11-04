"""Short native comparison of saved candidates; no optimizer or installation.

Previous PASS curves are mandatory eligibility conditions on full CSV metrics.
TRAN admittance is separate from the native AC path and from switching acceptance.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import bjt_temperature as temp
import bjt_transient_cap as cap
from qspice_io import SuiteError, read_text, sha256, find_tools

core = temp.core
power = temp.power
base = temp.base
resume = temp.resume


def identity(row):
    return row['kind'], row['curve'], row['temperature_C']


def assess(validation, protected, cfg):
    curves = validation.get('curve_comparison', [])
    cases = validation.get('cases', [])
    lookup = {identity(row): row for row in curves}
    expected = {(kind, f'T{t:+d}', t) for t in cfg['temperatures_C']
                for kind in ('gain', 'vcesat', 'vbesat')}
    expected.update(('output', f'IB{round(ib*1000)}mA', 25)
                    for ib in cfg['output_base_currents_A'])
    invalid = []
    for row in curves:
        for key in ('p95_relative', 'rms_relative', 'coverage'):
            value = row.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                invalid.append(str(identity(row)) + ':' + key)
    complete = (len(cases) == 15 and all(c.get('status') == 'SIMULATED' for c in cases)
                and len(curves) == len(expected) == 19 and set(lookup) == expected and not invalid)
    limits = validation.get('limits', [])
    tables = len(limits) == 4 and all(row.get('status') == 'PASS' for row in limits)
    base_current_ok = all(math.isclose(c.get('measurements', {}).get('IB_AT_1P5', float('nan')),
                                     0.3, rel_tol=1e-4, abs_tol=1e-9)
                          for c in cases if c.get('kind') == 'saturation')
    def passes(row):
        return (row.get('status') == 'PASS'
                and isinstance(row.get('p95_relative'), (int, float))
                and math.isfinite(row['p95_relative'])
                and row['p95_relative'] <= cfg['curve_tolerance_p95_relative'][row['kind']]
                and isinstance(row.get('coverage'), (int, float)) and row['coverage'] >= .95)
    losses = [p for p in protected if not passes(lookup.get(identity(p), {}))]
    summary = {s: sum(r.get('status') == s for r in curves) for s in ('PASS', 'FAIL', 'INVALID')}
    ratios = [r['p95_relative']/cfg['curve_tolerance_p95_relative'][r['kind']]
              for r in curves if isinstance(r.get('p95_relative'), (int, float))
              and math.isfinite(r['p95_relative'])]
    return {'complete_native_dc': complete, 'table_limits_pass': tables,
            'base_current_sign_and_value_pass': base_current_ok, 'summary': summary,
            'protected_regressions': losses, 'invalid_metrics': invalid,
            'eligible_dc': bool(complete and tables and base_current_ok and not losses),
            'worst_normalized_p95': max(ratios) if ratios else None}


def choose(rows):
    # A lower residual or a larger PASS count can never overrule a protected loss.
    eligible = [row for row in rows if row.get('assessment', {}).get('eligible_dc')]
    if not eligible:
        return None
    return min(eligible, key=lambda row: (-row['assessment']['summary']['PASS'],
                                         row['assessment']['worst_normalized_p95'], row['id']))['id']


def load_candidates(root):
    folder = root/'calibration/comparison_v11'
    manifest = json.loads(read_text(folder/'manifest.json'))
    for name, digest in manifest['original_hashes'].items():
        if sha256(root/name) != digest:
            raise SuiteError('Comparison input changed: ' + name)
    protected = json.loads(read_text(folder/'protected_curves.json'))
    candidates = manifest['candidates']
    for row in candidates:
        card = folder/row['file']
        if sha256(card) != row['sha256']:
            raise SuiteError('Frozen candidate changed: ' + row['id'])
        if not re.search(r'(?im)^\s*\.model\s+'+re.escape(row['model_name'])+r'\s+NPN\b', read_text(card)):
            raise SuiteError('Wrong model name/type: ' + row['id'])
    return folder, manifest, protected


def static_validation(root, folder, cfg, card, model_name, args):
    stage = folder/'candidate_workspace'
    stage.mkdir()
    shutil.copytree(root/'reference', stage/'reference')
    candidate_cfg = dict(cfg, model_name=model_name, model_status='COMPARISON_ONLY_NOT_INSTALLED',
                         schematic_dir=str((root/cfg['schematic_dir']).resolve()))
    core.dump(stage/'profile.json', candidate_cfg)
    (stage/'FJP5027.txt').write_text(card, encoding='utf-8')
    argv = ['--only', 'output', 'gain', 'saturation', '--engine', args.engine]
    if args.prepare:
        argv += ['--prepare', '--cir-only']
    if args.qspice_dir:
        argv += ['--qspice-dir', args.qspice_dir]
    code = core.main(stage, argv)
    runs = sorted((stage/'results').glob('*/run_state.json'))
    if len(runs) != 1:
        raise SuiteError(f'Static validation did not create one result (return {code})')
    source = runs[0].parent
    validation = json.loads(read_text(runs[0]))
    if not args.prepare:
        for case in validation['cases']:
            if base.logs_have_unknown_parameter(source/'cases'/case['id']):
                case.update(status='INVALID', error='Unknown/ignored model parameter')
                for metric in case.get('curve_metrics', []):
                    metric['status'] = 'INVALID'
        core.finish(stage, source, validation)
    shutil.copytree(source, folder/'validation')
    shutil.rmtree(stage)
    return validation


def save_report(run, state):
    rows = ['# FJP5027: короткое сравнение сохранённых кандидатов', '', f'**{state["result"]}**', '',
            'Оптимизация не запускается. Проверяются четыре замороженные карточки; исходная модель не заменяется.',
            'Прежние 10 PASS являются обязательным условием выбора по полным CSV, а не мягким штрафом.',
            'Cobo измеряется через TRAN-admittance при 10 В / 1 МГц. Нативный .ac и переключение этим не принимаются.', '',
            '| Кандидат | PASS / FAIL / INVALID | Потерь прежних PASS | Допущен по DC | Cobo, пФ | Cobo TRAN |',
            '|---|---|---:|---|---:|---|']
    for row in state['candidates']:
        assessment = row.get('assessment', {})
        counts = assessment.get('summary', {})
        count_text = ' / '.join(str(counts.get(s, '-')) for s in ('PASS', 'FAIL', 'INVALID'))
        pf = row.get('Cobo_F')
        pf_text = f'{pf*1e12:.6g}' if pf is not None else '-'
        label = f'[{row["id"]}]({row["id"]}/validation/report.md)' if (run/row['id']/'validation/report.md').is_file() else row['id']
        rows.append(f'| {label} | {count_text} | '
                    f'{len(assessment.get("protected_regressions", [])) if assessment else "-"} | '
                    f'{assessment.get("eligible_dc", False)} | {pf_text} | {row.get("cap_status", "NOT_RUN")} |')
        if row.get('error'):
            rows += ['', f'{row["id"]}: {row["error"]}']
    rows += ['', 'Кандидат для продолжения DC: **'+str(state.get('recommended_dc_candidate'))+'**.',
             'Рекомендация определяется количеством PASS среди вариантов без потери прежних PASS, затем худшим нормированным P95.',
             'Это выбор направления продолжения. Даже рекомендация по DC не является полной приёмкой модели и не устанавливает её.',
             '', 'В каждой папке сохранены итоговая карточка, отчёт, реальные метрики и компактные исходные точки рисунков.',
             'После извлечения данных удаляются QRAW и полный waveforms.csv. При ошибке кандидата остальные варианты проверяются.',
             '', 'Разбор предыдущего прогона сохранён в пакете: review/native_refine_v9/REVIEW.md.']
    if state.get('error'):
        rows += ['', 'Ошибка всего запуска: '+state['error']]
    (run/'report.md').write_text('\n'.join(rows)+'\n', encoding='utf-8')
    core.dump(run/'comparison_state.json', state)


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--qspice-dir')
    parser.add_argument('--engine', choices=['64', '80'], default='64')
    args = parser.parse_args(argv)
    root = Path(root).resolve()
    run = None
    state = None
    try:
        source, manifest, protected = load_candidates(root)
        cfg = json.loads(read_text(root/'profile.json'))
        core.validate_profile(root, cfg)
        tools = None if args.prepare else find_tools(args.qspice_dir, args.engine)
        run = root/'comparison_results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        run.mkdir(parents=True)
        state = {'result': 'PREPARE_ONLY' if args.prepare else 'RUNNING', 'applied': False,
                 'mode': 'prepare: NO QSPICE SIMULATION' if args.prepare else 'QSPICE',
                 'protected_curves': protected, 'candidates': [], 'original_hashes': manifest['original_hashes'],
                 'tool_hashes': {} if tools is None else {k: sha256(v) for k, v in tools.items()},
                 'script_sha256': sha256(Path(__file__))}
        for i, candidate in enumerate(manifest['candidates'], 1):
            print(f'[{i}/{len(manifest["candidates"])}] {candidate["id"]}', flush=True)
            folder = run/candidate['id']
            folder.mkdir()
            row = dict(candidate)
            state['candidates'].append(row)
            card = read_text(source/candidate['file'])
            (folder/'source_model.txt').write_text(card, encoding='utf-8')
            try:
                if candidate['family'] == 'MEXTRAM504':
                    params = power.parse_model(card)
                    if args.prepare:
                        cap.prepare(folder/'transient_cap', root, params, cfg)
                        row['cap_status'] = 'NOT_RUN_PREPARE_ONLY'
                    else:
                        params, cstate = cap.calibrate(root, folder/'transient_cap', params, cfg, args)
                        card = temp.render_temperature(params)
                        row['cap_status'] = cstate['status']
                        if cstate['status'] == 'TRAN_COBO_CALIBRATED_AND_CONVERGED':
                            row['Cobo_F'] = cstate['final']['checks']['Cobo_F']
                            card = card.replace('* Charge/transit/avalanche/thermal parameters are NOT extracted here.',
                                '* CJC calibrated separately via native TRAN admittance, 10 V / 1 MHz.\n'
                                '* Other transit, charge, avalanche and thermal parameters remain unextracted.')
                else:
                    if args.prepare:
                        for spec in cap.SPECS:
                            target = folder/'transient_cap'/spec['id']
                            target.mkdir(parents=True)
                            (target/'case.cir').write_text(cap.deck(card, candidate['model_name'], spec), encoding='utf-8')
                        row['cap_status'] = 'NOT_RUN_PREPARE_ONLY'
                    else:
                        values = cap.measure_set(folder/'transient_cap', card, candidate['model_name'], tools, cfg['timeout_seconds'])
                        check = cap.convergence(values)
                        cstate = {'method': 'NATIVE_TRAN_SMALL_SIGNAL_ADMITTANCE', 'rows': values, 'checks': check,
                                  'native_ac_status': 'NOT_RUN'}
                        core.dump(folder/'transient_cap/transient_cap_state.json', cstate)
                        if check['pass']:
                            row['Cobo_F'] = check['Cobo_F']
                        row['cap_status'] = ('TRAN_MEASURED_WITHIN_2PCT_OF_TYPICAL' if check['pass'] and
                                             abs(check['Cobo_F']/60e-12-1) <= .02 else 'TRAN_OUTSIDE_TARGET_OR_UNRESOLVED')
                (folder/'candidate_model.txt').write_text(card, encoding='utf-8')
                row['tested_model_sha256'] = sha256(folder/'candidate_model.txt')
                validation = static_validation(root, folder, cfg, card, candidate['model_name'], args)
                if not args.prepare:
                    row['assessment'] = assess(validation, protected, cfg)
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
                print('  INVALID:', row['error'], flush=True)
            save_report(run, state)
        if any(sha256(root/name) != value for name, value in manifest['original_hashes'].items()):
            raise SuiteError('Original inputs changed during comparison; recommendation blocked')
        if not args.prepare:
            state['recommended_dc_candidate'] = choose(state['candidates'])
            state['result'] = 'DC_CONTINUATION_CANDIDATE_IDENTIFIED' if state['recommended_dc_candidate'] else 'NO_REGRESSION_FREE_DC_CANDIDATE'
        save_report(run, state)
        print('Report:', run/'report.md')
        print('Diagnostics:', resume.diagnostics(root, run))
        return 0 if args.prepare or state.get('recommended_dc_candidate') else 1
    except (Exception, KeyboardInterrupt) as exc:
        if state is not None:
            state.update(result='COMPARISON_STOPPED', error=f'{type(exc).__name__}: {exc}', recommended_dc_candidate=None)
            save_report(run, state)
            print('Diagnostics:', resume.diagnostics(root, run))
        print('ERROR:', exc, file=sys.stderr)
        return 2
