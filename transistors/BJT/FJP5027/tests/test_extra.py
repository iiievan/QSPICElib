from pathlib import Path
import copy,csv,json,re,sys,tempfile,unittest,zipfile
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_extra as e
from qspice_io import SuiteError

class ExtraTests(unittest.TestCase):
 def test_reference_preserves_boundary_vertical_edges(self):
  refs,meta=e.read_reference(ROOT);p=refs[5,'inductive_boundary']
  self.assertAlmostEqual(p[-1,0],p[-2,0]);self.assertGreater(p[-2,1],p[-1,1]*3)
  self.assertLess(abs(p[0,1]-10),.03);self.assertLess(abs(p[-1,0]-1100),30)
  self.assertEqual(meta['curve_count'],7);self.assertEqual(sum(len(x) for x in refs.values()),283)
  self.assertEqual(set(k[1] for k in refs if k[0]==7),{'curve_A_unassigned','curve_B_unassigned'})
 def test_soa_curves_have_expected_order_at_800V(self):
  refs,_=e.read_reference(ROOT)
  # Curves terminate at 800 V; 100us also has the vertical closure.
  currents={key:refs[6,key][-1,1] for key in ['DC','10ms','1ms']}
  self.assertLess(currents['DC'],currents['10ms']);self.assertLess(currents['10ms'],currents['1ms'])
  self.assertTrue(all(abs(refs[6,key][-1,0]-800)<5 for key in currents))
 def test_reference_changed_is_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'reference';p.mkdir()
   for name in ['operating_curves.csv','operating_provenance.json','FJP5027_RevA_2003.pdf']:
    (p/name).write_bytes((ROOT/'reference'/name).read_bytes())
   with (p/'operating_curves.csv').open('a') as f:f.write('\n')
   with self.assertRaisesRegex(SuiteError,'CSV changed'):e.read_reference(Path(t))
 def test_actual_collector_plateau_sets_thresholds(self):
  c=e.plan([2],[1e-9,.5e-9])[0]
  m={'IC_ON':.72,'VCE_ON':80,'IB_ON_ACTUAL':.045,'IB_REVERSE_PEAK':-.5,'VCE_PEAK':125}
  e.validate_pilot(c,m)
  self.assertEqual(c['regime_note'],'VCE_ON_HIGH');self.assertEqual(c['measured_current_reference_A'],.72)
  text='\n'.join(e.timing_measures(.72))
  levels=[float(x) for x in re.findall(r'V\(ic\)=([\d.e+-]+)',text)]
  self.assertTrue(any(abs(x-.648)<1e-14 for x in levels));self.assertTrue(any(abs(x-.072)<1e-14 for x in levels))
  self.assertNotIn('ic_ratio',text)
 def test_invalid_drive_does_not_produce_timing(self):
  c=e.plan([1],[1e-9,.5e-9])[0]
  with self.assertRaisesRegex(SuiteError,'Forward base drive'):
   e.validate_pilot(c,{'IC_ON':.7,'VCE_ON':30,'IB_ON_ACTUAL':.01,'IB_REVERSE_PEAK':-.5})
  for value in [0,-1,float('nan')]:
   with self.assertRaises(SuiteError):e.timing_measures(value)
 def test_two_pass_qpost_changes_only_measurement_directives(self):
  c=e.plan([2],[1e-9,.5e-9])[0];cfg=json.loads((ROOT/'profile.json').read_text())
  source=(e.core.COMMON/'bjt/03_resistive_switching_tran.cir').read_text()
  deck,_=e.core.compile_deck(source,c,cfg,(ROOT/'FJP5027.txt').read_text());deck=e.with_measures(deck,e.PILOT)
  vals={'IC_ON':.72,'VCE_ON':80,'IB_ON_ACTUAL':.045,'IB_REVERSE_PEAK':-.5,'VCE_PEAK':125,
        'T_STORAGE_CMD':2e-7,'T_STORAGE_IB':1.99e-7,'T_FALL_IC':1e-7}
  calls=[]
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'case.qraw').write_bytes(b'unit-test-placeholder-NOT-NATIVE-DATA');(p/'case.cir').write_text(deck)
   def mock_command(args,cwd,out,err,timeout):
    calls.append(str(args[0]));Path(err).write_text('');Path(out).write_text('')
    if args[0]=='QPOST':
     path=Path(args[args.index('-o')+1]);rows=[x for x in (p/'case.cir').read_text().splitlines() if x.startswith('.meas')]
     path.write_text('\n'.join(x+':\n '+str(vals[e.expected([x])[0]]) for x in rows))
    else:
     a=np.zeros((30,8));a[:,0]=np.linspace(0,e.core.stop_time(c),30);a[:,2]=125
     np.savetxt(out,a,delimiter=',')
   with patch.object(e,'command',mock_command):e.postprocess(ROOT,p,c,{'QPOST':'QPOST','QUX':'QUX'},cfg)
   self.assertEqual(calls,['QPOST','QPOST','QUX']);self.assertEqual(c['status'],'SIMULATED')
   self.assertEqual(c['measurements']['IC_ON'],.72)
   physical=lambda text:[x for x in text.splitlines() if not re.match(r'^\s*\.meas\b',x)]
   self.assertEqual(physical(deck),physical((p/'case.cir').read_text()))
   self.assertTrue((p/'plot_data.npz').is_file())
 def test_eighteen_decks_compile_from_existing_bench(self):
  cfg=json.loads((ROOT/'profile.json').read_text());cases=e.plan();self.assertEqual(len(cases),18)
  source=(e.core.COMMON/'bjt/03_resistive_switching_tran.cir').read_text();model=(ROOT/'FJP5027.txt').read_text()
  for c in cases:
   deck,_=e.core.compile_deck(source,c,cfg,model);deck=e.with_measures(deck,e.PILOT)
   self.assertIn('.param VBUS=125',deck);self.assertIn('.param IB_ON=0.045',deck);self.assertIn('.param IB_OFF=-0.5',deck)
   self.assertEqual(len(re.findall(r'(?m)^\.tran ',deck)),1);self.assertNotRegex(deck,r'(?im)^\.step ')
   self.assertEqual(len(re.findall(r'(?m)^\.meas ',deck)),5)
 def test_convergence_detects_unstable_fall_and_measured_current(self):
  cases=e.plan([1],[1e-9,.5e-9]);cfg=json.loads((ROOT/'profile.json').read_text())
  for c in cases:c.update(status='SIMULATED',measurements={'IC_ON':.75,'T_STORAGE_IB':1e-7,'T_FALL_IC':1e-8})
  d={'config':cfg,'cases':cases};e.convergence(d);self.assertEqual(d['convergence'][0]['status'],'PASS')
  cases[-1]['measurements']['T_FALL_IC']=1e-10;e.convergence(d);self.assertEqual(d['convergence'][0]['status'],'FAIL')
  self.assertEqual(d['result'],'TIMING_NOT_CONVERGED')
 def test_collector_excludes_large_waveforms(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);run=root/'extra_results/x';run.mkdir(parents=True)
   (run/'extra_state.json').write_text('{}');(run/'case.qraw').write_bytes(b'RAW');(run/'waveforms.csv').write_text('large')
   (run/'report.md').write_text('report');(root/'reference').mkdir()
   for name in ['profile.json','FJP5027.txt','reference/operating_curves.csv','reference/operating_provenance.json']:(root/name).write_text('fixture')
   z=e.collect(root,run)
   with zipfile.ZipFile(z) as f:self.assertFalse(any(x.endswith('.qraw') or x.endswith('waveforms.csv') for x in f.namelist()))

if __name__=='__main__':unittest.main()
