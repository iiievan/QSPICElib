from pathlib import Path
import copy,json,math,sys,tempfile,unittest,shutil
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_temperature as t
import bjt_ac_diagnostics as ac
from qspice_io import SuiteError

class TemperatureTests(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((ROOT/'profile.json').read_text())
  self.cp,self.state,self.saved,self.params=t.resume.checkpoint(ROOT,None)
  self.mex=json.loads((ROOT/'calibration/mextram_v7_validation.json').read_text())
  self.gp=json.loads((self.cp/'baseline_validation.json').read_text())
  self.protected=t.protected_set(self.gp,self.mex,self.cfg)
 def measurements(self,cases,factor=1):
  d={c['id']:{x['name']:x['reference']*factor for x in c['fit_targets']} for c in cases}
  for c in cases:
   if c['kind']=='gain':d[c['id']].update(CHECK_GAIN_IC_MIN=1e-6,CHECK_GAIN_IC_MAX=20,CHECK_GAIN_SCAN_END=1,CHECK_GAIN_VBE_END=2.2)
   if c['temperature_C']==25:
    if c['kind']=='gain':d[c['id']].update(CHECK_HFE_02=22,CHECK_HFE_1=14)
    if c['kind']=='saturation':d[c['id']].update(CHECK_VCE_15=.27,CHECK_VBE_15=.84,CHECK_IB_15=.3)
  return d
 def test_fifteen_cases_nineteen_curves_498_targets_and_seven_protected(self):
  cases=t.temp_cases(ROOT,self.cfg)
  self.assertEqual(len(cases),15);self.assertEqual(sum(len(c['fit_targets']) for c in cases),498)
  self.assertEqual(len(self.protected),7)
  keys={(x['kind'],x['curve'],x['temperature_C']) for x in self.protected}
  self.assertIn(('vbesat','T+125',125),keys);self.assertIn(('gain','T+25',25),keys);self.assertIn(('output','IB100mA',25),keys)
  for c in cases:
   if c['temperature_C']!=25:self.assertFalse(any('CHECK_HFE_' in x or 'CHECK_VCE_15' in x for x in c['guard_measures']))
   if c['kind']=='saturation':self.assertTrue(all(.1<x['x']<3 for x in c['fit_targets']))
 def test_family_weights_and_protected_penalty_use_same_fixed_residual_length(self):
  cases=t.temp_cases(ROOT,self.cfg)
  r,details,guards,ok=t.measured_objective(cases,self.measurements(cases,1.01),self.protected)
  self.assertTrue(ok);self.assertEqual(len(details),19);self.assertEqual(len(r),505)
  self.assertAlmostEqual(np.linalg.norm(r),2*math.log(1.01),places=12)
  r2=t.measured_objective(cases,self.measurements(cases,1.3),self.protected)[0]
  self.assertEqual(len(r2),len(r));self.assertTrue(np.any(r2[-7:]>0))
 def test_cold_or_hot_measurements_cannot_override_25c_table_guards(self):
  cases=t.temp_cases(ROOT,self.cfg);data=self.measurements(cases)
  hot=next(c for c in cases if c['kind']=='gain' and c['temperature_C']==125)
  self.assertNotIn('CHECK_HFE_02',data[hot['id']])
  normal=next(c for c in cases if c['kind']=='gain' and c['temperature_C']==25)
  data[normal['id']]['CHECK_HFE_1']=1
  self.assertFalse(t.measured_objective(cases,data,self.protected)[3])
 def test_signed_temperature_and_log_dc_coordinates_round_trip(self):
  params=dict(t.DEFAULTS,**self.params);params['DVGBF']=-.02
  keys=['DVGBF','AQBO','VGB','IS','RCV'];x,decode=t.bounded_coordinates(params,keys)
  self.assertTrue(np.all((x>0)&(x<1)))
  out=decode(x)
  for k in keys:self.assertAlmostEqual(out[k]/params[k],1,places=10)
  for a in [np.zeros(len(keys)),np.ones(len(keys))]:
   values=decode(a)
   self.assertEqual(values['RTH'],0);self.assertEqual(values['CJC'],self.params['CJC'])
 def test_temperature_fit_uses_extended_residual_count_and_real_renderer(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(t.core,'get_source',return_value=('fixture',{'origin':'test fixture'})):
   fit=t.TemperatureFit(ROOT,Path(folder),self.cfg,{},protected=self.protected)
   self.assertEqual(fit.nres,505);self.assertEqual(len(fit.cases),15)
   self.assertIn('RTH=0',fit.renderer(self.params))
 def test_ac_diagnostic_variants_preserve_electrical_parameters_and_clamp_dt(self):
  original=t.power.render(self.params)
  for topology in ['3pin','4pin']:
   for analysis in ['op','dc','ac','tran']:
    text=ac.deck(original,t.power.NAME,analysis,topology)
    self.assertIn('R_EOPEN e 0 1T',text)
    self.assertIn('RTH=0',text);self.assertNotIn('V_DT',text)
  with self.assertRaises(SuiteError):ac.deck(original,t.power.NAME,'ac','5pin_clamped')
  self.assertEqual(original,t.power.render(self.params))
 def test_failed_ac_matrix_does_not_stop_following_cases_or_leave_qraw(self):
  class Args:engine='64';qspice_dir=None
  def command(args,folder,*other):
   (folder/'case.qraw').write_bytes(b'fixture');raise SuiteError('access violation fixture')
  with tempfile.TemporaryDirectory() as folder,patch.object(ac,'find_tools',return_value={'engine':str(ROOT/'FJP5027.txt')}),patch.object(ac,'command',side_effect=command):
   state=ac.matrix(ROOT,Path(folder),self.params,self.cfg,Args())
   self.assertEqual(len(state['cases']),9);self.assertTrue(all(x['status']=='FAILED' for x in state['cases']))
   self.assertFalse(list(Path(folder).rglob('*.qraw')))
 def test_full_temperature_workflow_never_replaces_installed_model(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)
   for name in ['FJP5027.txt','profile.json','reference/curves.csv','reference/provenance.json','reference/FJP5027_RevA_2003.pdf','calibration/mextram_v7_validation.json']:
    dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dst)
   shutil.copytree(self.cp,root/'calibration/mextram_v6_checkpoint')
   original=(root/'FJP5027.txt').read_bytes();cfg=(root/'profile.json').read_bytes()
   instances=[]
   class FixtureFit:
    def __init__(inner,*a,**kw):inner.cases=[];inner.evaluations=0;inner.native_counts={};inner.best=None;instances.append(inner)
    def evaluate(inner,params,label,**kwargs):
     inner.evaluations+=1
     r={'params':params,'row':{'status':'EVALUATED','table_guards_pass':True,'cost':1/inner.evaluations}}
     inner.best=r;return r
   def validation(root,run,cfg,params,args,**kwargs):
    f=run/'test_validation';f.mkdir();d=copy.deepcopy(self.mex)
    (f/'run_state.json').write_text(json.dumps(d));return f,d
   with patch.object(t,'find_tools',return_value={}),patch.object(t,'TemperatureFit',FixtureFit),patch.object(t,'optimize',return_value=[]),patch.object(t.resume,'stage_validation',side_effect=validation):
    code=t.main(root,['--skip-ac-diagnostics','--max-evals','15'])
   self.assertEqual(code,1);self.assertEqual((root/'FJP5027.txt').read_bytes(),original);self.assertEqual((root/'profile.json').read_bytes(),cfg)
   state=json.loads(next(root.glob('temperature_results/*/temperature_state.json')).read_text())
   self.assertFalse(state['applied']);self.assertEqual(instances[0].evaluations,4)

if __name__=='__main__':unittest.main()
