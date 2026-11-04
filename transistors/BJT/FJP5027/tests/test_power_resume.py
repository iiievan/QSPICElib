from pathlib import Path
import copy,json,math,shutil,sys,tempfile,unittest,zipfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_power_resume as r
from qspice_io import SuiteError

class ResumeTests(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((ROOT/'profile.json').read_text())
  self.cp,self.state,self.best,self.params=r.checkpoint(ROOT,None)
 def copy_root(self,root):
  for name in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf']:
   dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dst)
  shutil.copytree(self.cp,root/'calibration/mextram_v6_checkpoint')
 def test_native_checkpoint_is_matched_to_installed_gp_and_card(self):
  self.assertEqual(self.best['evaluation'],146);self.assertAlmostEqual(self.best['cost'],.1654871724218501)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);self.copy_root(root)
   (root/'FJP5027.txt').write_text('changed model')
   with self.assertRaisesRegex(SuiteError,'input mismatch'):r.checkpoint(root,None)
 def test_native_source_current_sign_and_cleanup(self):
  for sign in [-1,1]:
   with self.subTest(sign=sign),tempfile.TemporaryDirectory() as t:
    folder=Path(t)/'cap'
    def command(args,cwd,*other):
     if args[0]=='engine':(cwd/'case.qraw').write_bytes(b'test fixture')
     else:(cwd/'measurements.txt').write_text(f'.meas ac COBO_SOURCE_IM find imag(I(V_TEST)) at=1Meg:\n {sign*2*math.pi*1e6*60e-12} 1e6\n')
    with patch.object(r,'command',side_effect=command):
     if sign<0:self.assertAlmostEqual(r.native_cobo(folder,r.power.render(self.params),r.power.NAME,{'engine':'engine','QPOST':'post'},10),60e-12)
     else:
      with self.assertRaises(SuiteError):r.native_cobo(folder,r.power.render(self.params),r.power.NAME,{'engine':'engine','QPOST':'post'},10)
    self.assertFalse((folder/'case.qraw').exists())
 def test_cap_crash_is_recorded_and_fallback_requires_gp_control_and_repeat(self):
  class Args:root=ROOT;engine='64';qspice_dir=None
  state={};calls=[]
  def measure(folder,model,name,tools,timeout):
   calls.append((tools['engine'],folder.name,name))
   if name==self.cfg['model_name']:return 60e-12
   if tools['engine']=='64':raise SuiteError('access violation fixture')
   return 100e-12 if folder.name=='mex_0' else 60e-12
  with tempfile.TemporaryDirectory() as t,patch.object(r,'find_tools',side_effect=lambda d,e:{'engine':e}),patch.object(r,'sha256',return_value='fixture_hash'),patch.object(r,'native_cobo',side_effect=measure):
   params,ok=r.cap_trials(Path(t),{},self.params,self.cfg,Args(),state)
  self.assertTrue(ok);self.assertEqual([x['status'] for x in state['cap_attempts']],['FAILED','MEASURED_AND_RECHECKED'])
  self.assertEqual([x[1] for x in calls],['gp_control','mex_0','gp_control','mex_0','mex_1'])
  self.assertAlmostEqual(params['CJC']/self.params['CJC'],.6)
 def test_failed_caps_never_modify_parameters_or_create_pass(self):
  class Args:root=ROOT;engine='64';qspice_dir=None
  state={};original=dict(self.params)
  with tempfile.TemporaryDirectory() as t,patch.object(r,'find_tools',return_value={'engine':'fixture'}),patch.object(r,'sha256',return_value='fixture_hash'),patch.object(r,'native_cobo',side_effect=SuiteError('crash fixture')):
   params,ok=r.cap_trials(Path(t),{},self.params,self.cfg,Args(),state)
  self.assertFalse(ok);self.assertEqual(params,original);self.assertEqual(self.params,original)
 def test_acceptance_requires_cobo_and_all_previously_passing_curves(self):
  old=json.loads((self.cp/'baseline_validation.json').read_text())
  vd={'cases':[{'status':'SIMULATED'}]*15,'limits':[{'status':'PASS'}]*4,'curve_comparison':copy.deepcopy(old['curve_comparison'])}
  state={'cap_pass':True,'fine_dc_pass':True}
  self.assertTrue(r.acceptance(state,old,vd))
  state['cap_pass']=False;self.assertFalse(r.acceptance(state,old,vd))
  state['cap_pass']=True
  next(c for c in vd['curve_comparison'] if c['status']=='PASS')['status']='FAIL'
  self.assertFalse(r.acceptance(state,old,vd));self.assertEqual(len(state['protected_regressions']),1)
 def test_diagnostics_keep_validation_and_exclude_staging_and_waveforms(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);run=root/'resume';run.mkdir()
   for name in ['report.md','validation/run_state.json','cap_checks/case.cir','cap_checks/case.qraw','cap_checks/waveforms.csv','candidate_workspace/reference/big.pdf','work/case.cir']:
    f=run/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('fixture')
   with zipfile.ZipFile(r.diagnostics(root,run)) as z:
    self.assertEqual(sorted(z.namelist()),['resume/cap_checks/case.cir','resume/report.md','resume/validation/run_state.json'])
 def test_resume_uses_two_or_three_dc_evaluations_and_applies_only_after_checks(self):
  for cap_ok,lose_pass in [(True,False),(False,False),(True,True)]:
   with self.subTest(cap_ok=cap_ok,lose_pass=lose_pass),tempfile.TemporaryDirectory() as t:
    root=Path(t);self.copy_root(root);original=(root/'FJP5027.txt').read_bytes();profile=(root/'profile.json').read_bytes()
    instances=[]
    class FixtureFit:
     def __init__(inner,*a,**k):inner.evaluations=0;instances.append(inner)
     def evaluate(inner,params,label,**kwargs):
      inner.evaluations+=1;cost=.3029924 if inner.evaluations==1 else self.best['cost']
      return {'params':params,'cost':cost,'row':{'status':'EVALUATED','table_guards_pass':True}}
    def validation(root,run,cfg,params,args):
     folder=run/'fixture_validation';folder.mkdir()
     old=json.loads((self.cp/'baseline_validation.json').read_text())
     curves=copy.deepcopy(old['curve_comparison'])
     if lose_pass:next(c for c in curves if c['status']=='PASS')['status']='FAIL'
     vd={'cases':[{'status':'SIMULATED'}]*15,'limits':[{'status':'PASS'}]*4,'curve_comparison':curves}
     (folder/'run_state.json').write_text(json.dumps(vd));return folder,vd
    with patch.object(r,'find_tools',return_value={}),patch.object(r.power,'PowerFit',FixtureFit),patch.object(r,'cap_trials',side_effect=lambda run,current,p,*rest:(p,cap_ok)),patch.object(r,'stage_validation',side_effect=validation):
     code=r.main(root,[])
    self.assertEqual(instances[0].evaluations,3 if cap_ok else 2)
    if cap_ok and not lose_pass:
     self.assertEqual(code,0);self.assertEqual(r.power.parse_model((root/'FJP5027.txt').read_text())['LEVEL'],504)
    else:
     self.assertEqual(code,1);self.assertEqual((root/'FJP5027.txt').read_bytes(),original);self.assertEqual((root/'profile.json').read_bytes(),profile)

if __name__=='__main__':unittest.main()
