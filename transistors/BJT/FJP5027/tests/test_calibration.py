from pathlib import Path
import copy,json,sys,tempfile,unittest,shutil,re
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_calibrate as c
from qspice_io import SuiteError

class CalibrationTests(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((ROOT/'profile.json').read_text());self.params=c.parse_card((ROOT/'FJP5027.txt').read_text())
 def measured_fixture(self,cases,factor=1):
  d={}
  for x in cases:
   values={t['name']:t['reference']*factor for t in x['fit_targets']}
   if x['kind']=='gain':values.update(CHECK_HFE_02=22,CHECK_HFE_1=14)
   if x['kind']=='saturation':values.update(CHECK_VCE_15=.5,CHECK_VBE_15=1.,CHECK_IB_15=.3)
   d[x['id']]=values
  return d
 def test_round_trip_card_and_capability_gate(self):
  self.assertNotRegex(c.render_card(self.params,'NK'),r'\bNK=')
  a=c.render_card(dict(self.params,NK=.7));self.assertNotRegex(a,r'\bNK=')
  a=c.render_card(dict(self.params,NK=.7),'NKF');self.assertIn('NKF=0.7',a)
  p=c.parse_card(a);self.assertEqual(p['NK'],.7);self.assertNotIn('NKF',p)
  self.assertEqual(p['CJC'],self.params['CJC'])
 def test_saturation_targets_stay_inside_actual_scan(self):
  cases=c.fitting_cases(ROOT,self.cfg);self.assertEqual(len(cases),6)
  for case in cases:
   if case['kind']=='saturation':self.assertTrue(all(.1<t['x']<3 for t in case['fit_targets']))
   text,names=c.attach_measures('.dc V_SCAN 0 1 .001\n.end\n',case)
   self.assertEqual(len(names),len(set(names)));self.assertEqual(len(names),len(case['fit_targets'])+len(case.get('guard_measures',[])))
 def test_family_weighting_does_not_change_acceptance_tolerances(self):
  original=copy.deepcopy(self.cfg);cases=c.fitting_cases(ROOT,self.cfg)
  r,detail,guards,ok=c.objective_values(cases,self.measured_fixture(cases,1.1))
  self.assertAlmostEqual(np.linalg.norm(r),2*np.log(1.1),places=12)
  self.assertTrue(ok);self.assertEqual(original,self.cfg)
  bad=self.measured_fixture(cases);bad[cases[-1]['id']]['CHECK_IB_15']=-.3
  self.assertFalse(c.objective_values(cases,bad)[3])
 def test_invalid_measurement_is_rejected(self):
  cases=c.fitting_cases(ROOT,self.cfg);data=self.measured_fixture(cases)
  name=cases[0]['fit_targets'][0]['name'];data[cases[0]['id']][name]=float('nan')
  with self.assertRaises(SuiteError):c.objective_values(cases,data)
 def test_unknown_parameter_rejected_but_shorted_adapter_allowed(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);log=p/'simulator.stderr.log';log.write_text('Ignoring shorted resistor: R_K\n')
   self.assertFalse(c.logs_have_unknown_parameter(p))
   log.write_text('Warning: Ignoring unknown model parameter NK\n')
   self.assertTrue(c.logs_have_unknown_parameter(p))
 def test_candidate_cache_budget_and_native_rejection(self):
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)
   with patch.object(c.core,'get_source',return_value=('fixture',{'origin':'test fixture'})):
    fit=c.NativeFit(ROOT,run,self.cfg,{},max_evals=1)
   def results(p,folder,cases=None,nk_keyword=None):
    (Path(folder)/'marker.txt').write_text('test fixture, not simulation')
    return self.measured_fixture(fit.cases,1.1)
   with patch.object(fit,'run_cases',side_effect=results) as call:
    a=fit.evaluate(self.params);b=fit.evaluate(self.params)
    self.assertEqual(call.call_count,1);self.assertEqual(fit.evaluations,1);self.assertEqual(a['cost'],b['cost'])
    with self.assertRaises(c.BudgetStop):fit.evaluate(dict(self.params,BF=31))
   self.assertIsNotNone(fit.best)
 def test_nk_parameter_requires_observed_response(self):
  with tempfile.TemporaryDirectory() as t:
   with patch.object(c.core,'get_source',return_value=('fixture',{'origin':'test fixture'})):
    fit=c.NativeFit(ROOT,Path(t),self.cfg,{})
   def results(p,folder,cases,nk_keyword):
    case=cases[0];value=1 if nk_keyword=='NK' else 1/(1+p['NK'])
    return {case['id']:{case['fit_targets'][0]['name']:value}}
   with patch.object(fit,'run_cases',side_effect=results):probe=fit.probe_nk(self.params)
   self.assertEqual(fit.nk_keyword,'NKF');self.assertFalse(probe['tests'][0]['accepted']);self.assertTrue(probe['tests'][1]['accepted'])
 def test_application_and_holdout_failure_restore_original_files(self):
  # Exercise the actual file-write path using labelled synthetic evaluator results.
  # No result produced here is included in the package or called native evidence.
  for fail in [False,True]:
   with self.subTest(fail=fail),tempfile.TemporaryDirectory() as t:
    root=Path(t)
    for name in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf']:
     dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dst)
    original=(root/'FJP5027.txt').read_bytes();profile=(root/'profile.json').read_bytes()
    folder=root/'calibration/seeds';folder.mkdir(parents=True)
    (folder/'test_seed.json').write_text(json.dumps({'params':{'IS':self.params['IS']*10}}))
    class FixtureFit:
     def __init__(inner,root,run,cfg,tools,prepare,max_evals):inner.cases=[];inner.best=None;inner.nk_keyword=None;inner.evaluations=0
     def probe_nk(inner,p):return {'status':'NOT_CONFIRMED_USE_STANDARD_GP'}
     def evaluate(inner,p,label='trial',fine=False,force=False):
      inner.evaluations+=1;cost=2 if p['IS']==self.params['IS'] else .5
      r={'params':dict(p),'cost':cost,'residual':np.ones(3),'row':{'status':'EVALUATED','cost':cost,'label':label,'table_guards_pass':True,'curve_samples':[]}}
      if inner.best is None or cost<inner.best['cost']:inner.best=r
      return r
    def validation(root,args):
     f=root/'results/validation_fixture';f.mkdir(parents=True)
     (f/'run_state.json').write_text(json.dumps({'result':'NEEDS_CALIBRATION_OR_RESOLUTION','cases':[{'status':'INVALID' if fail else 'SIMULATED'}],'limits':[{'status':'PASS'}]}));return 1
    with patch.object(c,'NativeFit',FixtureFit),patch.object(c,'find_tools',return_value={}),patch.object(c,'optimize',return_value={'reason':'test fixture'}),patch.object(c.core,'main',side_effect=validation):
     rc=c.main(root,['--max-evals','6'])
    if fail:
     self.assertEqual((root/'FJP5027.txt').read_bytes(),original);self.assertEqual((root/'profile.json').read_bytes(),profile);self.assertEqual(rc,1)
    else:
     self.assertNotEqual((root/'FJP5027.txt').read_bytes(),original);self.assertIn(c.NAME,(root/'FJP5027.txt').read_text());self.assertEqual(rc,0)

if __name__=='__main__':unittest.main()
