"""Extract plotted polylines from the supplied Fairchild Rev.A PDF (no OCR).

The mapping is revision-specific and guarded by a SHA-256 recorded in provenance.
Disconnected drawing paths are stitched only when their endpoints coincide.
"""
from pathlib import Path
import csv, hashlib, json
import fitz
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

def curves(page, indices):
    out, points = [], []
    drawings = page.get_drawings()
    for index in indices:
        for item in drawings[index]['items']:
            if item[0] != 'l':
                raise ValueError('Unexpected path type; review PDF revision')
            a, b = tuple(item[1]), tuple(item[2])
            if points and np.linalg.norm(np.array(points[-1])-a) > .02:
                out.append(np.array(points)); points = []
            if not points: points.append(a)
            points.append(b)
    if points: out.append(np.array(points))
    return out

def convert(points, rect, xr, yr, logx=False, logy=False):
    x0,y0,x1,y1 = rect
    xx=(points[:,0]-x0)/(x1-x0); yy=(y1-points[:,1])/(y1-y0)
    x=10**(np.log10(xr[0])+xx*np.log10(xr[1]/xr[0])) if logx else xr[0]+xx*(xr[1]-xr[0])
    y=10**(np.log10(yr[0])+yy*np.log10(yr[1]/yr[0])) if logy else yr[0]+yy*(yr[1]-yr[0])
    # Average repeated x coordinates; do not extrapolate or smooth the curves.
    ux=np.unique(x); uy=np.array([np.mean(y[x==v]) for v in ux])
    return np.column_stack((ux,uy))

def main():
    pdf=ROOT/'reference/FJP5027_RevA_2003.pdf'
    doc=fitz.open(pdf); page=doc[1]
    plans=[
      ('output',range(8,22),(132.48,131.16,279.60,251.16),(0,8),(0,3),False,False,7),
      ('gain',range(38,41),(347.52,131.52,494.76,251.52),(.01,10),(1,100),True,True,4),
      ('vcesat',range(70,72),(132.72,330.60,279.96,450.60),(.1,10),(.01,100),True,True,4),
      ('vbesat',range(97,99),(347.52,330.60,494.76,450.60),(.1,10),(.1,10),True,True,4)]
    rows=[]
    for kind, indices, rect, xr, yr, lx, ly, expected in plans:
        cc=[convert(p,rect,xr,yr,lx,ly) for p in curves(page,list(indices))]
        if len(cc)!=expected: raise ValueError(f'{kind}: {len(cc)} paths, expected {expected}')
        # Assignment uses the low-current ordering, not the high-current end:
        # the gain curves cross at high Ic. Temperature labels reviewed visually.
        for i,p in enumerate(cc):
            temp=25 if kind=='output' else [-25,25,75,125][i]
            ib=.05*(i+1) if kind=='output' else ''
            cid=f'IB{round(ib*1000)}mA' if kind=='output' else f'T{temp:+d}'
            for x,y in p:
                if kind=='output' and x<.05: continue  # line clipping at the axis
                rows.append([kind,cid,temp,ib,float(x),float(y)])
    with (ROOT/'reference/curves.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['kind','curve','temperature_C','base_current_A','x','y']);w.writerows(rows)
    meta={'source':'User-supplied Fairchild FJP5027 Rev.A, December 2003',
      'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'pdf_page_1based':2,
      'method':'Vector polyline coordinates; linear/log axis calibration; no OCR, smoothing or model fitting',
      'figure_mapping':{'1':'output','2':'gain','3':'vcesat (axis, despite caption typo)','4':'vbesat (axis, despite caption typo)'},
      'temperatures_C':[-25,25,75,125],'figure1_temperature':'25 C assumed from default conditions; not printed inside figure',
      'uncertainty_note':'Line width, axis rounding and typical-device spread remain. Not measured unit data.',
      'calibration_note':'Figure 2 temperature assignment is made at low Ic; curves cross at high Ic.',
      'holdout_note':'These same curves are potential calibration targets, not independent validation data.',
      'curve_counts':{'output':7,'gain':4,'vcesat':4,'vbesat':4}}
    (ROOT/'reference/provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Extracted',len(rows),'points in 19 curves')

if __name__=='__main__':main()
