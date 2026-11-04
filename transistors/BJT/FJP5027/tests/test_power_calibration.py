from pathlib import Path
import json,sys,tempfile,unittest,shutil
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_power_calibrate as p
from qspice_io import SuiteError

class PowerTests(unittest.TestCase):
 def setUp(self):self.current=p.parse_model((ROOT/'FJP5027.txt').read_text());self.cfg=json.loads((ROOT/'profile.json').read_text())
 def test_current_gp_nk_is_preserved_when_comparing_families(self):
  card=p.render(self.current);self.assertIn('NK=',card);self.assertIn(p.NAME,card)
  self.assertEqual(p.parse_model(card)['NK'],self.current['NK'])
 def test_mextram_starts_have_explicit_isothermal_setup(self):
  for name,params in p.seeds(self.current):
   self.assertEqual(params['LEVEL'],504);self.assertEqual(params['RTH'],0);self.assertEqual(params['TREF'],25)
   card=p.render(params);self.assertNotIn('NK=',card);self.assertNotIn('IKF=',card)
   self.assertEqual(p.parse_model(card)['RCV'],params['RCV'])
   with self.assertRaises(SuiteError):p.render(dict(params,RTH=300))
 def test_previous_passing_curves_are_protected(self):
  curve={'kind':'vbesat','curve':'T+25','temperature_C':25,'status':'PASS'}
  baseline={'curve_comparison':[curve]};same={'curve_comparison':[dict(curve)]}
  self.assertEqual(p.protected_regressions(baseline,same),[])
  self.assertEqual(len(p.protected_regressions(baseline,{'curve_comparison':[dict(curve,status='FAIL')]})),1)
  self.assertEqual(len(p.protected_regressions(baseline,{'curve_comparison':[]})),1)
 def test_packaged_native_baseline_matches_current_model_bytes(self):
  baseline=p.find_baseline(ROOT,p.sha256(ROOT/'FJP5027.txt'))
  self.assertIsNotNone(baseline);self.assertEqual(sum(c['status']=='PASS' for c in baseline['curve_comparison']),4)
  self.assertIsNone(p.find_baseline(ROOT,'0'*64))
 def test_power_bias_and_native_rendering_hook(self):
  cases=p.power_cases(ROOT,self.cfg);self.assertEqual(len(cases),6)
  gain=next(c for c in cases if c['kind']=='gain');self.assertEqual(gain['parameters']['VBE_STOP'],2.2)
  with tempfile.TemporaryDirectory() as t:
   with patch.object(p.core,'get_source',return_value=('fixture',{'origin':'test fixture'})):
    fit=p.PowerFit(ROOT,Path(t),self.cfg,{},True)
   self.assertEqual(fit.cfg['model_name'],p.NAME)
   self.assertIn('LEVEL=504',fit.renderer(p.seeds(self.current)[0][1]))
   self.assertEqual(next(c for c in fit.case_factory(ROOT,self.cfg,True) if c['kind']=='gain')['parameters']['VBE_STOP'],2.2)
 def test_application_rolls_back_if_a_previous_pass_is_lost(self):
  for lose_pass in [False,True]:
   with self.subTest(lose_pass=lose_pass),tempfile.TemporaryDirectory() as t:
    root=Path(t)
    for n in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf']:
     dest=root/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/n,dest)
    original=(root/'FJP5027.txt').read_bytes();profile=(root/'profile.json').read_bytes()
    curve={'kind':'vbesat','curve':'T+25','temperature_C':25,'status':'PASS'}
    baseline={'curve_comparison':[curve],'cases':[{'status':'SIMULATED'}]}
    class FixtureFit:
     def __init__(inner,*a,**k):inner.cases=[];inner.best_power=None;inner.evaluations=0;inner.max_evals=9
     def evaluate(inner,params,label='trial',**k):
      inner.evaluations+=1;cost=.2 if params.get('LEVEL')==504 else .4
      r={'params':dict(params),'cost':cost,'row':{'label':label,'status':'EVALUATED','table_guards_pass':True,'cost':cost}}
      if params.get('LEVEL')==504 and (inner.best_power is None or cost<inner.best_power['cost']):inner.best_power=r
      return r
     def cap(inner,*a):return 60e-12
    def validation(root,args):
     f=root/'validation_fixture';f.mkdir();d={'result':'NEEDS_CALIBRATION_OR_RESOLUTION','cases':[{'status':'SIMULATED'}],
       'limits':[{'status':'PASS'}],'curve_comparison':[dict(curve,status='FAIL' if lose_pass else 'PASS')]}
     (f/'run_state.json').write_text(json.dumps(d));return f,d,1
    with patch.object(p,'find_tools',return_value={}),patch.object(p,'PowerFit',FixtureFit),patch.object(p,'find_baseline',return_value=baseline),patch.object(p,'optimize_blocks',return_value=[]),patch.object(p,'validate_static',side_effect=validation):
     code=p.main(root,['--max-evals','10'])
    if lose_pass:
     self.assertEqual(code,1);self.assertEqual((root/'FJP5027.txt').read_bytes(),original);self.assertEqual((root/'profile.json').read_bytes(),profile)
    else:
     self.assertEqual(code,0);self.assertEqual(p.parse_model((root/'FJP5027.txt').read_text())['LEVEL'],504)
 def gain_fixture(self,max_ic=1.3):
  case=next(c for c in p.power_cases(ROOT,self.cfg) if c['kind']=='gain')
  values={t['name']:20. if t['x']<=max_ic else None for t in case['fit_targets']}
  values.update(CHECK_HFE_02=20.,CHECK_HFE_1=18. if max_ic>=1 else None,
    CHECK_GAIN_IC_MIN=.0001,CHECK_GAIN_IC_MAX=max_ic,CHECK_GAIN_SCAN_END=1.,CHECK_GAIN_VBE_END=2.2)
  text=''.join(f'.meas dc {name} find v(test) at=1:\n'+(' ( nan, nan)\n' if value is None else f' {value} 1\n') for name,value in values.items())
  return case,values,text
 def test_real_native_nan_log_keeps_19_valid_points_and_11_missing(self):
  case=next(c for c in p.power_cases(ROOT,self.cfg) if c['kind']=='gain')
  targets=[t['name'] for t in case['fit_targets']];names=targets+['CHECK_HFE_02','CHECK_HFE_1']
  text=(ROOT/'tests/fixtures/mex_initial_gain_v5.txt').read_text()
  values,missing=p.split_measurements(text,names,targets)
  self.assertEqual(missing,[f'G{i:02}' for i in range(19,30)])
  self.assertEqual(values['G18'],.143064);self.assertNotIn('G19',values)
  with self.assertRaises(SuiteError):p.split_measurements(text,names)
 def test_range_failure_reports_maximum_and_never_fills_missing_points(self):
  case,values,text=self.gain_fixture()
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(p.GainCoverageError) as cm:p.gain_measurements(text,list(values),case,t)
   d=cm.exception.details;self.assertEqual(d['ic_max_A'],1.3);self.assertFalse(d['coverage_complete'])
   self.assertEqual(d['missing_target_names'],[f'G{i:02}' for i in range(19,30)])
   self.assertTrue((Path(t)/'gain_coverage.json').is_file())
  with tempfile.TemporaryDirectory() as t:
   case,values,text=self.gain_fixture(max_ic=6)
   self.assertEqual(p.gain_measurements(text,list(values),case,t),values)
 def test_missing_inside_range_or_incomplete_scan_is_not_recovered_as_range(self):
  case,values,text=self.gain_fixture()
  for bad in [text.replace(' 1.3 1\n',' 6 1\n'),text.replace('CHECK_GAIN_SCAN_END find v(test) at=1:\n 1.0 1','CHECK_GAIN_SCAN_END find v(test) at=1:\n 0.9 1')]:
   with tempfile.TemporaryDirectory() as t:
    with self.assertRaises(SuiteError) as cm:p.gain_measurements(bad,list(values),case,t)
    self.assertNotIsInstance(cm.exception,p.GainCoverageError)
 def test_bootstrap_recovers_after_four_native_range_failures(self):
  state={'initial_results':[{'status':'EVALUATED','label':'baseline'}]}
  class FixtureFit:
   evaluations=1;max_evals=150;best_power=None
   def evaluate(inner,params,name):
    inner.evaluations+=1
    row={'label':name,'status':'REJECTED','failure_details':{'type':'GAIN_COVERAGE'}}
    if name=='mex_range_ihc4':inner.best_power=True;row={'label':name,'status':'EVALUATED'}
    return {'row':row}
  fit=FixtureFit();p.bootstrap(fit,self.current,state)
  self.assertEqual(fit.evaluations,6);self.assertEqual(state['initial_results'][-1]['status'],'EVALUATED')
 def test_bootstrap_does_not_hide_noncoverage_errors(self):
  state={'initial_results':[{'status':'EVALUATED','label':'baseline'}]}
  class FixtureFit:
   evaluations=1;max_evals=150;best_power=None
   def evaluate(inner,params,name):
    inner.evaluations+=1;return {'row':{'label':name,'status':'REJECTED','error':'Unknown model parameter'}}
  fit=FixtureFit()
  with self.assertRaisesRegex(SuiteError,'outside gain coverage'):p.bootstrap(fit,self.current,state)
  self.assertEqual(fit.evaluations,5)
 def test_failed_candidate_has_no_saturation_files_from_previous_model(self):
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)
   with patch.object(p.core,'get_source',return_value=('fixture',{'origin':'test fixture'})):
    fit=p.PowerFit(ROOT,run,self.cfg,{})
   stale=run/'work/saturation_Tp25';stale.mkdir(parents=True);(stale/'measurements.txt').write_text('previous candidate fixture')
   def failure(params,folder,*args):
    (Path(folder)/'gain_Tp25').mkdir();raise SuiteError('failed gain fixture')
   with patch.object(fit,'run_cases',side_effect=failure):r=fit.evaluate(self.current)
   self.assertEqual(r['row']['status'],'REJECTED')
   self.assertFalse((run/'first_rejected/saturation_Tp25').exists());self.assertFalse((run/'last_rejected/saturation_Tp25').exists())
 def test_failure_state_contains_used_budget_before_optimization(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for n in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf']:
    dest=root/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/n,dest)
   class FixtureFit:
    evaluations=1;native_counts={'engine_runs_started':6,'qpost_runs_completed':6};cases=[]
    def __init__(inner,*a,**k):pass
    def evaluate(inner,*a):return {'row':{'label':'installed_model','status':'EVALUATED'}}
   baseline={'cases':[{'status':'SIMULATED'}]}
   with patch.object(p,'find_tools',return_value={}),patch.object(p,'PowerFit',FixtureFit),patch.object(p,'find_baseline',return_value=baseline),patch.object(p,'bootstrap',side_effect=SuiteError('test failure')):
    self.assertEqual(p.main(root,[]),2)
   state=json.loads(next(root.glob('power_calibration_results/*/power_state.json')).read_text())
   self.assertEqual(state['evaluations'],1);self.assertEqual(state['max_model_evaluations'],150);self.assertEqual(state['max_search_evaluations'],149)
   self.assertFalse(state['optimization_started']);self.assertFalse(state['applied'])
   self.assertEqual(state['termination_reason'],'ERROR_BEFORE_OPTIMIZATION')

if __name__=='__main__':unittest.main()
