from __future__ import annotations
import re, math, os, shutil, subprocess, json, hashlib
from pathlib import Path

NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
REAL = re.compile(rf'^{NUMBER}$')
CELL = re.compile(rf'\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)|({NUMBER})')

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


