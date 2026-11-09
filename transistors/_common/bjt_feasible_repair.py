"""Coupled temperature/collector repair of a measured 12-PASS candidate.

SLSQP sees the protected full-CSV P95 inequalities explicitly. Every value still
comes from FullCurveFit; the optimizer's success flag never substitutes for it.
"""
from __future__ import annotations
import math
import numpy as np
import bjt_temperature as temp

KEYS=['VGB','DVGBF','AEPI','RCV','SCRCV']
TABLE_KEYS=('hFE at Ic=0.2 A, Vce=5 V','hFE at Ic=1 A, Vce=5 V',
            'VCEsat at Ic=1.5 A, Ib=0.3 A [V]','VBEsat at Ic=1.5 A, Ib=0.3 A [V]')
MARGIN=.97


def constraints(row,protected,margin=MARGIN):
    size=len(protected)+5
    if row.get('status')!='EVALUATED':return np.full(size,-50.)
    curves={(r['kind'],r['curve'],r['temperature_C']):r for r in row.get('curve_samples',[])}
    if len(curves)!=19:return np.full(size,-50.)
    for metric in curves.values():
        for key in ('p95_relative','coverage'):
            value=metric.get(key)
            if not isinstance(value,(int,float)) or not math.isfinite(value):return np.full(size,-50.)
        if metric['coverage']<.95:return np.full(size,-50.)
    output=[]
    for target in protected:
        metric=curves.get((target['kind'],target['curve'],target['temperature_C']))
        if not metric or not math.isfinite(metric.get('p95_relative',float('nan'))):return np.full(size,-50.)
        if metric.get('coverage',0)<.95:return np.full(size,-50.)
        output.append(margin-metric['p95_relative']/target['tolerance'])
    guard=row.get('guard_values',{})
    values=[guard.get(k,float('nan')) for k in TABLE_KEYS]
    if not all(math.isfinite(v) for v in values):return np.full(size,-50.)
    h02,h1,vce,vbe=values
    output.extend([(h02-10)/30,(40-h02)/30,(h1-8)/8,(2-vce)/2,(1.5-vbe)/1.5])
    return np.asarray(output,dtype=float)


def problem(fit,seed,protected,radius=.08):
    if not 0<radius<=.25:raise ValueError('Repair radius must be in (0, 0.25] normalized units')
    x0,decode=temp.bounded_coordinates(seed,KEYS)
    bounds=list(zip(np.maximum(1e-6,x0-radius),np.minimum(1-1e-6,x0+radius)))
    cache={}
    def evaluate(x):
        key=tuple(float(v) for v in x)
        if key not in cache:cache[key]=fit.evaluate(decode(x),'repair_joint')
        return cache[key]
    def objective(x):return float(evaluate(x)['cost'])
    def inequalities(x):return constraints(evaluate(x)['row'],protected)
    return x0,bounds,objective,inequalities


def optimize(fit,seed,protected,radius=.08):
    import scipy
    from scipy.optimize import minimize
    x0,bounds,objective,inequalities=problem(fit,seed,protected,radius)
    start=fit.evaluations
    result={'method':'SLSQP','scipy_version':scipy.__version__,'keys':KEYS,
            'constraint_margin':MARGIN,'local_radius_normalized':radius,
            'constraint_source':'Native full CSV; protected P95 and table inequalities',
            'documentation':'https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html'}
    try:
        optimum=minimize(objective,x0,method='SLSQP',jac='2-point',bounds=bounds,
            constraints=[{'type':'ineq','fun':inequalities}],
            options={'maxiter':40,'ftol':1e-7,'finite_diff_rel_step':.002,'disp':False})
        result.update(reason=str(optimum.message),scipy_success=bool(optimum.success),
                      scipy_iterations=int(optimum.nit),scipy_nfev=int(optimum.nfev))
    except temp.base.BudgetStop:
        result.update(reason='Native evaluation budget reached; best feasible measured candidate retained',scipy_success=False)
    result['native_evaluations']=fit.evaluations-start
    return result
