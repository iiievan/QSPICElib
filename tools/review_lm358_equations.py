# Optional engineering review: Python 3 + numpy + scipy.
# Run from any directory. Reads selected actual B-source expressions.
# NOT a SPICE parser or QSPICE/QPOST regression. Does not validate netlists,
# syntax, .step, rail translation, or simulator-specific convergence.
# Default output is JSON on stdout; no files are overwritten.
from pathlib import Path
import re,math,json
import numpy as np
from scipy.optimize import root,brentq
from scipy.integrate import solve_ivp
s=(Path(__file__).resolve().parents[1]/'LM358/LM358.txt').read_text()
lines=[]
for l in s.splitlines():
 if l.startswith('*') or not l.strip():continue
 if l.startswith('+'):lines[-1]+=' '+l[1:]
 else:lines.append(l)
scales={'t':1e12,'g':1e9,'meg':1e6,'k':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'p':1e-12,'f':1e-15}
def spice(x):
 return re.sub(r'(?<![\w.])((?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(meg|[tgkmunpf])\b',lambda m:str(float(m[1])*scales[m[2].lower()]),x,flags=re.I)
P={k.upper():float(spice(v)) for k,v in re.findall(r'(\w+)=([^\s]+)',next(l for l in lines if l.startswith('.subckt LM358 ')).split('params:')[1])}
B={};R={}
for l in lines:
 if l.startswith('.ends'):break
 if l.startswith('B_'):name,a,b,expr=l.split(None,3);B[name]=(a.lower(),b.lower(),expr[2:].strip('{}'))
 if l.startswith('R_'):name,a,b,val=l.split(None,3);R[name]=val.strip('{}')
def calc(expr,n,temp=25,p=None):
 par=P.copy();par.update(p or {})
 e=re.sub(r'V\((\w+)(?:,(\w+))?\)',lambda m:f"({n.get(m[1].lower(),0)-n.get((m[2] or '0').lower(),0):.17g})",expr,flags=re.I)
 env={k: v for k,v in par.items()};env.update(temp=temp,limit=lambda x,a,b:min(max(x,a),b),max=max,min=min,abs=abs,log10=math.log10)
 return eval(spice(e),{'__builtins__':{}},env)
def nodes(inp,inm,vs,dom,hf,out,t=25,over=None):
 n={'inp':inp,'inm':inm,'vcc':vs,'vee':0,'dom':dom,'hf':hf,'out':out}
 for b in ['B_DT','B_HEAD','B_ERR','B_DRIVE','B_SINK','B_CMD']:
  a,z,e=B[b];n[a]=calc(e,n,t,over)+n.get(z,0)
 return n
def value(b,n,t=25,over=None):return calc(B[b][2],n,t,over)
def dc(vs,inp,t=25,rl=1e30,mode='follower',target=1.4,over=None):
 rd=calc(R['R_DOM'],{},t,over)
 def eq(x,ret=False):
  dom,out=x
  inm=out if mode=='follower' else inp+(out-target)*1000
  n=nodes(inp,inm,vs,dom,dom,out,t,over)
  residual=[(value('B_GM',n,t,over)-value('B_AW',n,t,over)-dom/rd)*1000,(n['cmd']-out/rl)*1000]
  return n if ret else residual
 guess=[inp+.002,inp+.002] if mode=='follower' else [target*(1+P['RO']/rl),target+2e-6]
 r=root(eq,guess,tol=1e-10)
 assert max(abs(np.array(eq(r.x))))<1e-7,(r.message,r.x,eq(r.x))
 return eq(r.x,True)
def forced(vs,inp,inm,out,t=25,over=None):
 rd=calc(R['R_DOM'],{},t,over)
 def eq(d):
  n=nodes(inp,inm,vs,d,d,out,t,over)
  return value('B_GM',n,t,over)-value('B_AW',n,t,over)-d/rd
 dom=brentq(eq,-1,vs+1,xtol=1e-13)
 return nodes(inp,inm,vs,dom,dom,out,t,over)
results={}
for label,vs,cm in [('OS5L',5,0),('OS5H',5,3.29),('OS30L',30,0),('OS30H',30,28.29)]:
 n=dc(vs,cm,mode='servo');v=n['inm']-n['inp'];assert abs(v)<=.007;results[label]=v
lo=dc(15,2,rl=2000,mode='servo',target=1);hi=dc(15,2,rl=2000,mode='servo',target=11)
aol=(hi['out']-lo['out'])/((hi['inp']-hi['inm'])-(lo['inp']-lo['inm']));assert 90e3<aol<110e3;results['AOL']=aol
n0=dc(5,0,mode='servo');n3=dc(5,3,mode='servo')
cmrr=20*math.log10(3/abs((n3['inm']-n3['inp'])-(n0['inm']-n0['inp'])));assert abs(cmrr-70)<1;results['CMRR']=cmrr
psrr=20*math.log10(25/abs(results['OS30L']-results['OS5L']));assert abs(psrr-100)<1;results['PSRR']=psrr
for name,vs,p,m,v,expected in [('source',15,2,1,7.5,.04),('sink',15,1,2,7.5,-.02),('sink_200mV',5,0,1,.2,-50e-6),('short',15,2,1,0,.04)]:
 n=forced(vs,p,m,v);assert abs(n['cmd']-expected)<1e-12;results[name]=n['cmd']
for name,vs,p,m,rl,lim in [('VOH5',5,2,1,2000,3.3),('VOH30_2k',30,2,1,2000,26),('VOH30_10k',30,2,1,10000,27),('VOL',5,0,1,10000,0)]:
 f=lambda out:forced(vs,p,m,out)['cmd']-out/rl
 out=brentq(f,0,vs);assert out>=lim
 if name=='VOL':assert out<.02
 results[name]=out
results['TEMP']=[]
for t in [-35,0,25,60,70]:
 n=dc(5,0,t,mode='servo');results['TEMP'].append({'T':t,'VOS':n['inm']-n['inp'],'IB':(value('B_IBP',n,t)+value('B_IBM',n,t))/2,'IOS':value('B_IBP',n,t)-value('B_IBM',n,t)})
 for vs in [3,5,15,30,32]:
  n=dc(vs,.5,t,rl=10000);assert abs(n['out']-.5)<.02
# AC transfer from actual R/C and output conductance; ideal buffer verified.
assert 'E_BUFFER buffered VEE dom VEE 1' in s
rd=calc(R['R_DOM'],{});rh=calc(R['R_HF'],{})
results['AC_DB']={}
for f,target in [(1,100),(100,80),(1000,60),(10000,40),(100000,20),(1000000,0)]:
 w=2j*math.pi*f;gm=2*math.pi*P['GBW']*1e-9
 h=gm/(1/rd+w*1e-9)/(1+w*rh*1e-9)/(1+P['RO']/2000+w*P['RO']*P['COUT'])
 db=20*math.log10(abs(h));results['AC_DB'][f]=db;assert abs(db-target)<3
# Integrate actual macro equations for two public fixtures as engineering check.
# Radau handles the very small output capacitance; not QSPICE/QPOST.
def transient(vs,rl,cl,knots,stop):
 inp=lambda tt:float(np.interp(tt,[k[0] for k in knots],[k[1] for k in knots]))
 n=dc(vs,inp(0),rl=rl);initial=[n['dom'],n['dom'],n['out']]
 def rhs(t,x):
  dom,hf,out=x;n=nodes(inp(t),out,vs,dom,hf,out)
  return [(value('B_GM',n)-value('B_AW',n)-dom/rd)/1e-9,(dom-hf)/rh/1e-9,(n['cmd']-out/rl)/(P['COUT']+cl)]
 times=np.linspace(0,stop,10001)
 r=solve_ivp(rhs,(0,stop),initial,method='Radau',rtol=1e-7,atol=1e-10,max_step=stop/2000,dense_output=True)
 assert r.success,r.message
 return times,r.sol(times)[2]
t,y=transient(15,2000,0,[(0,2),(10e-6,2),(10.01e-6,4),(50e-6,4),(50.01e-6,2),(100e-6,2)],100e-6)
for a,b,v in [(40e-6,45e-6,4),(90e-6,95e-6,2)]:assert max(abs(y[(t>=a)&(t<=b)]-v))<.01
# crossing interpolation, independent of QPOST syntax
cross=lambda level,a,b:np.interp(level,y[(t>=a)&(t<=b)],t[(t>=a)&(t<=b)])
rise=cross(3.5,10e-6,20e-6)-cross(2.5,10e-6,20e-6)
sel=(t>=50e-6)&(t<=60e-6)
fall=np.interp(2.5,y[sel][::-1],t[sel][::-1])-np.interp(3.5,y[sel][::-1],t[sel][::-1])
results['SLEW_INTERVALS']={'rise_1V':rise,'fall_1V':fall}
t,y=transient(30,1e30,50e-12,[(0,.380),(1e-6,.380),(1.001e-6,.280),(6e-6,.280),(6.001e-6,.380),(10e-6,.380)],10e-6)
results['SMALL_PULSE']={'min':float(min(y[(t>=1e-6)&(t<=3e-6)])),'max':float(max(y[(t>=6e-6)&(t<=8e-6)])),'settled_low':float(max(abs(y[(t>=4e-6)&(t<=5e-6)]-.28))),'settled_high':float(max(abs(y[(t>=9e-6)]-.38)))}
print(json.dumps(results,indent=2))

