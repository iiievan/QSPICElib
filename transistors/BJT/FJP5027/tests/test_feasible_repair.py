"""Constraint and coupled-solver tests; synthetic cases are not QSPICE results."""
from pathlib import Path
import copy,json,sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'_common'))
import bjt_feasible_repair as repair
import bjt_full_guard as full


class CoupledRepair(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]
        self.folder=self.root/'calibration/mextram_v12_repair'
        self.meta,self.params=full.checkpoint(self.root,repair=True)
        self.protected=self.meta['protected_curves']
        self.seed=json.loads((self.folder/'seed_row.json').read_text())
        self.baseline=json.loads((self.root.parents[2]/'review/native_full_csv_v12/full_curve_fit_state.json').read_text())['best_fine']

    def test_measured_seed_has_exactly_two_protected_constraint_violations(self):
        values=repair.constraints(self.seed,self.protected)
        self.assertEqual(len(values),15)
        bad=[(r['kind'],r['temperature_C']) for r,v in zip(self.protected,values) if v<0]
        self.assertEqual(set(bad),{('gain',-25),('vcesat',25)})
        self.assertTrue(np.all(values[-5:]>=0))

    def test_fallback_has_feasible_constraint_margin(self):
        self.assertTrue(np.all(repair.constraints(self.baseline,self.protected)>=0))

    def test_missing_or_nonfinite_measurements_never_look_feasible(self):
        row=copy.deepcopy(self.seed);row['guard_values']={}
        self.assertTrue(np.all(repair.constraints(row,self.protected)<0))
        row=copy.deepcopy(self.baseline);row['curve_samples'][0]['p95_relative']=float('nan')
        self.assertTrue(np.all(repair.constraints(row,self.protected)<0))

    def test_objective_and_constraints_share_the_native_evaluation(self):
        class Fit:
            def __init__(self,result):self.calls=0;self.result=result
            def evaluate(self,*args):self.calls+=1;return self.result
        fit=Fit({'cost':3.14,'row':self.seed})
        seed=full.power.parse_model((self.folder/'seed_model.txt').read_text())
        x,bounds,objective,inequalities=repair.problem(fit,seed,self.protected)
        self.assertAlmostEqual(objective(x),3.14)
        self.assertEqual(len(inequalities(x)),15)
        self.assertEqual(fit.calls,1)
        self.assertTrue(all(lo<=v<=hi for v,(lo,hi) in zip(x,bounds)))

    def test_saved_seed_coefficients_match_measured_native_row(self):
        card=full.power.parse_model((self.folder/'seed_model.txt').read_text())
        for key,value in self.seed['params'].items():self.assertAlmostEqual(card[key]/value,1.,places=12) if value else self.assertEqual(card[key],value)

    def test_slsqp_can_repair_coupled_synthetic_constraints(self):
        # An explicitly synthetic response tests optimizer plumbing and sign of inequalities.
        template=copy.deepcopy(self.baseline);protected=self.protected
        p=dict(self.params);center=dict(p)
        class Fit:
            def __init__(self):self.evaluations=0;self.feasible=[]
            def evaluate(self,params,label):
                self.evaluations+=1
                row=copy.deepcopy(template)
                for metric in row['curve_samples']:
                    metric['p95_relative']=.01;metric['coverage']=1.
                    if (metric['kind'],metric['temperature_C'])==('gain',-25):
                        metric['p95_relative']=.15*(1.10-40*(params['VGB']-center['VGB']))
                    if (metric['kind'],metric['temperature_C'])==('vcesat',25):
                        metric['p95_relative']=.2*(1.05-10*(params['RCV']-center['RCV']))
                cost=((params['VGB']-center['VGB']-.006)/.01)**2+((params['RCV']-center['RCV']-.015)/.02)**2
                if np.all(repair.constraints(row,protected)>=-1e-6):self.feasible.append((cost,params))
                return {'cost':cost,'row':row}
        fit=Fit();result=repair.optimize(fit,p,protected)
        self.assertTrue(result['scipy_success'],result)
        self.assertTrue(fit.feasible)
        cost,params=min(fit.feasible,key=lambda x:x[0])
        self.assertLess(cost,.01)
        self.assertGreater(params['VGB'],center['VGB']+.003)
        self.assertGreater(params['RCV'],center['RCV']+.008)


if __name__=='__main__':unittest.main()
