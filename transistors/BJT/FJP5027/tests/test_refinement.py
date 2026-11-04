from pathlib import Path
import copy,json,math,shutil,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_temperature as t
import bjt_transient_cap as cap
from qspice_io import SuiteError

class RefinementTests(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((ROOT/'profile.json').read_text())
  self.cp,self.state,self.saved,self.params=t.resume.checkpoint(ROOT,ROOT/'calibration/mextram_v8_checkpoint')
  self.validation=json.loads((ROOT/'calibration/mextram_v8_validation.json').read_text())
 def test_checkpoint_is_actual_native_evaluation_150_with_ten_passes(self):
  self.assertEqual(self.saved['evaluation'],150)
  self.assertAlmostEqual(self.saved['cost'],.19309204643169883)
  self.assertEqual(sum(r['status']=='PASS' for r in self.validation['curve_comparison']),10)
 def test_dense_cases_use_all_reference_points_within_physical_ranges(self):
  refs=t.core.reference(ROOT);cases=t.refine_cases(ROOT,self.cfg);total=0
  self.assertEqual(len(cases),15)
  for c in cases:
   for kind in {x['kind'] for x in c['fit_targets']}:
    targets=[x for x in c['fit_targets'] if x['kind']==kind]
    a=refs[kind,c['temperature_C'],targets[0]['curve']]
    if c['kind']=='saturation':a=a[(a[:,0]>.100001)&(a[:,0]<2.999)]
    self.assertEqual(len(a),len(targets));total+=len(targets)
  self.assertGreater(total,2800)
 def test_ik_one_has_visible_parameter_perturbation_in_normalized_coordinates(self):
  x,decode=t.bounded_coordinates(self.params,['IK'])
  perturbed=decode(x*1.001)['IK'];self.assertGreater(abs(perturbed/self.params['IK']-1),.001)
 def rows(self,value=60e-12):return [dict(s,status='MEASURED',Cobo_F=value) for s in cap.SPECS]
 def test_convergence_rejects_step_amplitude_settling_and_invalid_data(self):
  self.assertTrue(cap.convergence(self.rows(),control=True)['pass'])
  for key in ['step_5ns','half_amplitude','late_window']:
   rows=self.rows();next(r for r in rows if r['id']==key)['Cobo_F']*=1.02
   self.assertFalse(cap.convergence(rows)['pass'])
  self.assertFalse(cap.convergence(self.rows()[:-1])['pass'])
  self.assertFalse(cap.convergence(self.rows(90e-12),control=True)['pass'])
 def test_native_integral_scaling_sign_and_large_file_cleanup(self):
  spec=cap.SPECS[3]
  with tempfile.TemporaryDirectory() as folder:
   f=Path(folder)/'measure'
   def command(args,cwd,*other):
    if args[0]=='engine':(cwd/'case.qraw').write_bytes(b'fixture')
    else:
     integral=-.5*90e-12*(spec['stop']-spec['start'])*spec['amp']*2*math.pi*cap.FREQ
     (cwd/'measurements.txt').write_text(f'.meas tran COBO_COS integ V(proj) from=10u to=20u:\n {integral:.15g}\n')
   with patch.object(cap,'command',side_effect=command):
    measured=cap.measure(f,t.power.render(self.params),t.power.NAME,spec,{'engine':'engine','QPOST':'post'},60)
   self.assertAlmostEqual(measured/90e-12,1,places=12);self.assertFalse((f/'case.qraw').exists())
 def test_cjc_extraction_requires_new_converged_native_measurements(self):
  class Args:engine='64';qspice_dir=None
  original=dict(self.params)
  def measure_set(folder,card,name,tools,timeout):
   if name==self.cfg['model_name']:return self.rows(60e-12)
   p=t.power.parse_model(card);return self.rows(90e-12*p['CJC']/original['CJC'])
  def measure(folder,card,*args):return 90e-12*t.power.parse_model(card)['CJC']/original['CJC']
  with tempfile.TemporaryDirectory() as folder,patch.object(cap,'find_tools',return_value={}),patch.object(cap,'measure_set',side_effect=measure_set),patch.object(cap,'measure',side_effect=measure):
   p,state=cap.calibrate(ROOT,Path(folder),original,self.cfg,Args())
  self.assertEqual(state['status'],'TRAN_COBO_CALIBRATED_AND_CONVERGED')
  self.assertAlmostEqual(p['CJC']/original['CJC'],2/3)
  self.assertEqual(p['RTH'],0);self.assertEqual(state['native_ac_status'],'NOT_RUN_PREVIOUS_BUILD_CRASHED')
  self.assertEqual(original,self.params)
 def test_failed_transient_convergence_retains_original_candidate_parameters(self):
  class Args:engine='64';qspice_dir=None
  with tempfile.TemporaryDirectory() as folder,patch.object(cap,'find_tools',return_value={}),patch.object(cap,'measure_set',return_value=[]):
   p,state=cap.calibrate(ROOT,Path(folder),self.params,self.cfg,Args())
  self.assertEqual(p,self.params);self.assertEqual(state['status'],'TRAN_COBO_UNRESOLVED')
 def test_refinement_workflow_protects_ten_passes_and_never_installs_candidate(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)
   for name in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf','calibration/mextram_v8_validation.json']:
    dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
   shutil.copytree(self.cp,root/'calibration/mextram_v8_checkpoint')
   original=(root/'FJP5027.txt').read_bytes();cfg=(root/'profile.json').read_bytes();instances=[]
   class FixtureFit:
    def __init__(inner,*args,**kwargs):inner.cases=[];inner.evaluations=0;inner.native_counts={};inner.best=None;instances.append(inner)
    def evaluate(inner,p,label,**kw):
     inner.evaluations+=1;r={'params':p,'row':{'status':'EVALUATED','table_guards_pass':True,'cost':.1}}
     inner.best=r;return r
   def validation(root,run,*args,**kw):
    f=run/'test_validation';f.mkdir();d=copy.deepcopy(self.validation)
    (f/'run_state.json').write_text(json.dumps(d));return f,d
   with patch.object(t,'TemperatureFit',FixtureFit),patch.object(t,'find_tools',return_value={}),patch.object(t,'optimize',return_value=[]),patch.object(t.resume,'stage_validation',side_effect=validation),patch.object(cap,'calibrate',side_effect=lambda root,folder,p,*args:(p,{'status':'TRAN_COBO_CALIBRATED_AND_CONVERGED'})):
    self.assertEqual(t.main(root,['--refine','--max-evals','15']),1)
   state=json.loads(next(root.glob('temperature_results/*/temperature_state.json')).read_text())
   self.assertEqual(len(state['protected_curves']),10);self.assertEqual(len(state['objective_threshold_curves']),19)
   self.assertFalse(state['applied']);self.assertEqual(instances[0].evaluations,2)
   self.assertEqual((root/'FJP5027.txt').read_bytes(),original);self.assertEqual((root/'profile.json').read_bytes(),cfg)

if __name__=='__main__':unittest.main()
