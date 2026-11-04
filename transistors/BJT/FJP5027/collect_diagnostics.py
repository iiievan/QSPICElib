"""Bundle latest result + exact model/profile; raw files only with --raw."""
from pathlib import Path
import argparse,zipfile
root=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument('--raw',action='store_true');args=ap.parse_args()
runs=sorted(p for p in (root/'results').glob('*') if (p/'run_state.json').is_file())
if not runs:raise SystemExit('No results. Run the suite first.')
run=runs[-1];target=root/'FJP5027_diagnostics.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(run.rglob('*')):
        if p.is_file() and (args.raw or p.suffix.lower()!='.qraw'):z.write(p,p.relative_to(root))
    for name in ['FJP5027.txt','profile.json','reference/provenance.json','reference/curves.csv']:
        z.write(root/name,name)
print(target)
