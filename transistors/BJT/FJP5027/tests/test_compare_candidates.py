"""Acceptance-policy checks; none of these tests claims a native simulation."""
from pathlib import Path
import copy
import json
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'_common'))
import bjt_compare_candidates as compare


class ComparisonAcceptance(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]
        self.cfg=json.loads((self.root/'profile.json').read_text())
        self.protected=json.loads((self.root/'calibration/comparison_v11/protected_curves.json').read_text())
        self.validation={'curve_comparison':[], 'cases':[], 'limits':[{'status':'PASS'} for _ in range(4)]}
        for case in compare.core.plan(self.cfg,['output','gain','saturation']):
            self.validation['cases'].append(dict(case,status='SIMULATED',measurements={'IB_AT_1P5':.3}))
            curve=(f'IB{round(case["parameters"]["IB_TEST"]*1000)}mA' if case['kind']=='output'
                   else f'T{case["temperature_C"]:+d}')
            families=['vcesat','vbesat'] if case['kind']=='saturation' else [case['kind']]
            for kind in families:
                self.validation['curve_comparison'].append({'kind':kind,'curve':curve,
                    'temperature_C':case['temperature_C'],'status':'PASS','coverage':1.,
                    'p95_relative':.01,'rms_relative':.005})

    def test_complete_native_results_are_required(self):
        result=compare.assess(self.validation,self.protected,self.cfg)
        self.assertTrue(result['eligible_dc'])
        self.validation['cases'][0]['status']='NOT_RUN'
        self.assertFalse(compare.assess(self.validation,self.protected,self.cfg)['eligible_dc'])

    def test_missing_table_result_and_wrong_base_current_block_selection(self):
        self.validation['limits'].pop()
        self.assertFalse(compare.assess(self.validation,self.protected,self.cfg)['eligible_dc'])
        self.validation['limits'].append({'status':'PASS'})
        next(c for c in self.validation['cases'] if c['kind']=='saturation')['measurements']['IB_AT_1P5']=-.3
        self.assertFalse(compare.assess(self.validation,self.protected,self.cfg)['eligible_dc'])

    def test_protected_loss_cannot_be_bought_by_more_passes(self):
        guarded=compare.assess(self.validation,self.protected,self.cfg)
        guarded['summary']['PASS']=11
        target=self.protected[0]
        row=next(r for r in self.validation['curve_comparison'] if compare.identity(r)==compare.identity(target))
        row.update(status='FAIL',p95_relative=target['tolerance']*1.01)
        regressed=compare.assess(self.validation,self.protected,self.cfg)
        self.assertFalse(regressed['eligible_dc'])
        self.assertEqual(compare.choose([{'id':'guarded','assessment':guarded},
                                        {'id':'lower_cost_more_passes','assessment':regressed}]),'guarded')

    def test_nonfinite_and_insufficient_coverage_are_not_passes(self):
        target=self.protected[0]
        row=next(r for r in self.validation['curve_comparison'] if compare.identity(r)==compare.identity(target))
        row['coverage']=.94
        self.assertFalse(compare.assess(self.validation,self.protected,self.cfg)['eligible_dc'])
        row['coverage']=1.;row['p95_relative']=float('nan')
        self.assertFalse(compare.assess(self.validation,self.protected,self.cfg)['eligible_dc'])

    def test_original_inputs_and_candidate_cards_match_manifest(self):
        source,manifest,protected=compare.load_candidates(self.root)
        self.assertEqual(len(manifest['candidates']),4)
        self.assertEqual(len(protected),10)
        self.assertEqual(len(list(source.glob('mex_*.txt'))),3)

    def test_real_v9_final_is_blocked_by_full_curve_regressions(self):
        path=self.root.parents[2]/'review/native_refine_v9/validation/run_state.json'
        validation=json.loads(path.read_text())
        result=compare.assess(validation,self.protected,self.cfg)
        self.assertEqual(result['summary'],{'PASS':11,'FAIL':8,'INVALID':0})
        self.assertEqual({(r['kind'],r['temperature_C']) for r in result['protected_regressions']},
                         {('gain',-25),('vcesat',25)})
        self.assertFalse(result['eligible_dc'])


if __name__=='__main__':unittest.main()
