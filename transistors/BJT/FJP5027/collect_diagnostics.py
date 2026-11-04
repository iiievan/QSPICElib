"""Small diagnostics by default; raw and full CSV require explicit opt-in."""
from pathlib import Path
import argparse,zipfile,json
root=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument('--raw',action='store_true');ap.add_argument('--waveforms',action='store_true');args=ap.parse_args()
runs=sorted(p for p in (root/'results').glob('*') if (p/'run_state.json').is_file()
            and not json.loads((p/'run_state.json').read_text(encoding='utf-8')).get('mode','').startswith('prepare'))
if not runs:raise SystemExit('No results. Run the suite first.')
run=runs[-1];target=root/'FJP5027_diagnostics.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(run.rglob('*')):
        if not p.is_file():continue
        if p.suffix.lower()=='.qraw' and not args.raw:continue
        if p.name=='waveforms.csv' and not args.waveforms:continue
        if p.suffix.lower() not in ('.qraw','.csv','.cir','.txt','.log','.json','.md','.png','.npz'):continue
        z.write(p,p.relative_to(root))
    for name in ['FJP5027.txt','profile.json','reference/provenance.json','reference/curves.csv']:
        z.write(root/name,name)
print(target)
