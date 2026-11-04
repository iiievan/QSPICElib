"""Extract Fig.5/6/7 exact vector paths. Preserve repeated x and closure edges.
Source: user-supplied Fairchild FJP5027 Rev.A December 2003 only.
"""
from pathlib import Path
import csv,hashlib,json
import fitz
import numpy as np
from extract_reference import curves
ROOT=Path(__file__).resolve().parents[1]

def log_coordinates(p,rect,xr,yr):
 x0,y0,x1,y1=rect
 u=(p[:,0]-x0)/(x1-x0);v=(y1-p[:,1])/(y1-y0)
 return np.column_stack((10**(np.log10(xr[0])+u*np.log10(xr[1]/xr[0])),10**(np.log10(yr[0])+v*np.log10(yr[1]/yr[0]))))

def extract(root=ROOT):
 root=Path(root);pdf=root/'reference/FJP5027_RevA_2003.pdf'
 doc=fitz.open(pdf);data=[]
 def add(fig,label,p,rect,xr,yr):
  for i,(x,y) in enumerate(log_coordinates(p,rect,xr,yr)):data.append([fig,label,i,float(x),float(y)])
 # Fig.5: stitch left plateau + curved segment + vertical right closure.
 c=curves(doc[1],[128]);assert [len(x) for x in c]==[87,2,2]
 p=np.vstack((c[1],c[0][1:],c[2][1:]))
 add(5,'inductive_boundary',p,(132.6,538.08,279.84,658.08),(10,2000),(1,20))
 # Fig.6: drawing 136 also contains zero-length tick marks and the frame.
 c=[p for p in curves(doc[1],[136]) if len(p)>=3]
 assert [len(p) for p in c]==[4,4,4,5]
 # Path order reviewed against the leader lines in the supplied PDF.
 for label,p in zip(['10ms','100us','1ms','DC'],c):
  add(6,label,p,(346.44,537.72,496.08,660.72),(1,10000),(.001,100))
 # Fig.7 has no unambiguous individual ts/tf labels. Keep A/B, do not infer.
 c=curves(doc[2],[8]);assert [len(p) for p in c]==[88,89]
 for label,p in zip(['curve_A_unassigned','curve_B_unassigned'],c):
  add(7,label,p,(133.44,131.52,280.68,251.52),(.1,10),(.01,10))
 path=root/'reference/operating_curves.csv'
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['figure','curve','sequence','x','y']);w.writerows(data)
 meta={'source':'Supplied Fairchild FJP5027 Rev.A December 2003',
  'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
  'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
  'method':'Original vector line vertices, both log axes; sequence preserved, no smoothing or duplicate-x averaging',
  'fig5':{'pdf_page':2,'axis_rect_pt':[132.6,538.08,279.84,658.08],'x_range_V':[10,2000],'y_range_A':[1,20],
   'drawing':128,'IB1_A':3,'RB2_ohm':0,'L_H':.001,'VCC_V':20,
   'interpretation':'Voltage-current boundary; interpreted as inductive turn-off safe-operating envelope (RBSOA type), despite Switching Time caption. Not a temporal waveform.'},
  'fig6':{'pdf_page':2,'axis_rect_pt':[346.44,537.72,496.08,660.72],'x_range_V':[1,10000],'y_range_A':[.001,100],
   'drawing':136,'path_labels':['10ms','100us','1ms','DC'],'note':'Preserve original segments including right-hand 100us closure. Shared 10A horizontal segment is not separately duplicated for 1ms/10ms.'},
  'fig7':{'pdf_page':3,'axis_rect_pt':[133.44,131.52,280.68,251.52],'x_range_A':[.1,10],'y_range_us':[.01,10],
   'drawing':8,'IB1_A':.045,'IB2_A':-.5,'VCC_V':125,
   'temperature_C':None,'individual_curve_assignment':'Unknown: keep A/B; no automatic ts/tf matching or PASS',
   'missing_conditions':['pulse duration','drive edge','temperature','timing definitions']},
  'point_count':len(data),'curve_count':7,
  'limits':'SOA is manufacturer reference, not predicted device failure; no automatic SOA qualification from mismatched or unknown test conditions.'}
 (root/'reference/operating_provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return meta
if __name__=='__main__':print(json.dumps(extract(),indent=2))
