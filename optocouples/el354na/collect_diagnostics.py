#!/usr/bin/env python3
"""Collect small text diagnostics; leave QRAW and credentials on the local machine."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import zipfile
from run_suite import ROOT, SuiteError, latest_run, load_run, case_folder


def collect(run_dir, output=None):
    run_dir=Path(run_dir).resolve()
    data=load_run(run_dir)
    failures=[c for c in data['cases'] if c.get('status') not in ('PASS','WARN')]
    selected=(failures or data['cases'])[:2]
    files=[run_dir/'report.md',run_dir/'diagnostics'/'run_state.json']
    files+=list((run_dir/'diagnostics'/'netlists').glob('*.cir'))
    files+=list((run_dir/'diagnostics'/'netlists').glob('*.log'))
    for case in selected:
        folder=case_folder(run_dir,case['id'])
        files += [folder/name for name in ('case.cir','parameters.json','measurements.txt',
                  'simulator.stdout.log','simulator.stderr.log','qpost.stdout.log','qpost.stderr.log','export.stderr.log')]
    output=Path(output).resolve() if output else ROOT/('diagnostics_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.zip')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_file():
                relative=path.relative_to(run_dir)
                if path.stat().st_size<=5*1024*1024:
                    archive.write(path,relative)
                else:
                    archive.writestr(str(relative)+'.omitted.txt','Omitted: exceeds 5 MiB')
        archive.writestr('DIAGNOSTICS.txt','Includes report, saved run data, exported netlists and at most two failing cases.\nQRAW, waveform CSV, .venv and proxy credentials are not included.\n')
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir',nargs='?',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    path=collect(args.run_dir or latest_run(require_diagnostics=True),args.output)
    print(f'Diagnostics: {path} ({path.stat().st_size/1024:.1f} KiB)')
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (SuiteError,OSError,ValueError,KeyError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        raise SystemExit(2)
