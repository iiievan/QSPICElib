"""Regression checks for scientific validity and bench generation, no simulator mocks."""
from pathlib import Path
import copy,importlib.util,json,sys,tempfile,unittest,shutil,subprocess,zipfile
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
import bjt_suite as b
from qspice_io import parse_measurements,SuiteError

class Measurements(unittest.TestCase):
    def test_find_position_is_not_second_measurement(self):
        s='.meas dc vce find v(vce) when v(ic)=1.5:\n 0.42 0.5\n'
        self.assertEqual(parse_measurements(s,['VCE']),{'VCE':.42})
    def test_qpost_imag_still_prints_complex(self):
        s='.meas ac cob find imag(v(i_cobo)) at=1e6:\n (0.00037699, 0) 1e6\n'
        self.assertAlmostEqual(parse_measurements(s,['COB'])['COB'],.00037699)
    def test_missing_crossing_cannot_pass(self):
        with self.assertRaises(SuiteError):parse_measurements('.meas tran tf trig v(x)=.9 fall=1:\n(nan,nan)\n',['TF'])
    def test_missing_metric_cannot_pass(self):
        with self.assertRaises(SuiteError):parse_measurements('',['TF'])
    def test_duplicate_metric_cannot_pass(self):
        with self.assertRaises(SuiteError):parse_measurements('tf=1e-9\ntf=2e-9',['TF'])

class CurveValidity(unittest.TestCase):
    def setUp(self):self.cfg=json.loads((ROOT/'profile.json').read_text())
    def test_no_extrapolation_pass_for_partial_domain(self):
        x=np.geomspace(.1,3,50);r=b.metric_curve(x,x,np.linspace(.8,1.2,50),np.linspace(.8,1.2,50),self.cfg,'gain')
        self.assertEqual(r['status'],'FAIL');self.assertLess(r['coverage'],.95)
    def test_large_gain_error_detected(self):
        x=np.geomspace(.1,3,50);r=b.metric_curve(x,x,x,1.4*x,self.cfg,'gain')
        self.assertEqual(r['status'],'FAIL');self.assertAlmostEqual(r['p95_relative'],.4)
    def test_folded_physical_axis_rejected(self):
        with self.assertRaises(SuiteError):b.metric_curve(np.array([1,2,3]),np.ones(3),np.array([1,3,2]),np.ones(3),self.cfg,'gain')
    def test_correct_trace_passes(self):
        x=np.geomspace(.1,3,50);self.assertEqual(b.metric_curve(x,x,x,x,self.cfg,'gain')['status'],'PASS')
    def test_overview_preserves_narrow_current_and_power_peaks(self):
        a=np.zeros((10001,3));a[:,0]=np.arange(len(a));a[101,1]=10;a[8972,2]=-12
        picked=b.overview_indices(a,bins=50)
        self.assertIn(101,picked);self.assertIn(8972,picked)

class Decks(unittest.TestCase):
    def setUp(self):
        self.cfg=json.loads((ROOT/'profile.json').read_text());self.model=(ROOT/'FJP5027.txt').read_text()
    def test_all_cases_have_frozen_model_and_no_step_or_demo(self):
        cases=b.plan(self.cfg);self.assertEqual(len(cases),28)
        for c in cases:
            name=b.BENCH['dc' if c['kind'] in ('output','gain') else c['kind']]
            source=(b.COMMON/'bjt'/(name+'.cir')).read_text()
            deck,expected=b.compile_deck(source,c,self.cfg,self.model)
            self.assertIn('.model '+self.cfg['model_name'],deck)
            self.assertNotIn('BENCH_NPN_DEMO',deck)
            self.assertNotRegex(deck,r'(?im)^\s*\.step\b')
            self.assertNotRegex(deck,r'(?im)^\s*\.lib\b')
            self.assertIn('.temp '+str(c['temperature_C']),deck)
            self.assertTrue(expected)
    def test_unknown_parameter_typo_stops_run(self):
        c=b.plan(self.cfg,['gain'])[0];c=copy.deepcopy(c);c['parameters']['VCE_GUMMEL_TYPO']=5
        source=(b.COMMON/'bjt/01_output_gummel_leakage_dc.cir').read_text()
        with self.assertRaises(SuiteError):b.compile_deck(source,c,self.cfg,self.model)
    def test_nonpositive_switching_time_invalid(self):
        c=b.plan(self.cfg,['resistive'])[0];c['measurements']={'T_STORAGE_CMD':-1e-9,'T_STORAGE_IB':2e-9,'T_FALL_IC':1e-9}
        with self.assertRaises(SuiteError):b.operating_checks(c)
    def test_wrong_plateau_invalid_even_with_finite_timings(self):
        c=b.plan(self.cfg,['resistive'])[0]
        c['measurements']={'T_STORAGE_CMD':1e-9,'T_STORAGE_IB':2e-9,'T_FALL_IC':1e-9,'IB_REVERSE_PEAK':-.8,'IC_ON':.002}
        with self.assertRaises(SuiteError):b.operating_checks(c)
    def test_dc_axis_incomplete_rejected(self):
        c=b.plan(self.cfg,['gain'])[0]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'wave.csv';p.write_text('\n'.join(f'{i/100},1,1,1,1' for i in range(50)))
            with self.assertRaises(SuiteError):b.load_csv(p,c)
    def test_four_reference_families_and_nineteen_curves(self):
        ref=b.reference(ROOT);self.assertEqual(len(ref),19)
        self.assertEqual({k[0] for k in ref},{'output','gain','vcesat','vbesat'})
        for a in ref.values():self.assertTrue(np.all(np.diff(a[:,0])>0))
    def test_pdf_provenance_matches(self):b.validate_profile(ROOT,self.cfg)
    def test_reversed_zero_volt_base_probe_is_corrected_and_recorded(self):
        c=b.plan(self.cfg,['saturation'])[0]
        source=(b.COMMON/'bjt/05_forced_beta_saturation_dc.cir').read_text().replace('V_IB b_feed base 0','V_IB base b_feed 0')
        deck,_=b.compile_deck(source,c,self.cfg,self.model)
        self.assertIn('V_IB b_feed base 0',deck)
        self.assertTrue(any('Reversed' in n for n in c['netlist_notes']))
        self.assertIn('IB_AT_1P5',deck)
    def test_gain_branch_has_no_unused_dut_or_unmasked_hfe_monitor(self):
        c=b.plan(self.cfg,['gain'])[0]
        source=(b.COMMON/'bjt/01_output_gummel_leakage_dc.cir').read_text()
        deck,_=b.compile_deck(source,c,self.cfg,self.model)
        self.assertIn('X_GUMMEL',deck)
        self.assertNotIn('X_OUT ',deck);self.assertNotIn('X_ICEO ',deck)
        self.assertNotRegex(deck,r'(?im)^B_HFE\s')
        self.assertIn('B_HFE_CALC',deck)

class Retention(unittest.TestCase):
    def test_transient_edge_retained_as_original_samples_after_cleanup(self):
        cfg=json.loads((ROOT/'profile.json').read_text());c=b.plan(cfg,['resistive'])[0]
        p=c['parameters'];t_off=p['T_DELAY']+p['DUTY']/p['FSW']
        c['measurements']={'T_STORAGE_CMD':40e-9,'T_FALL_IC':24e-9}
        t=np.linspace(0,b.stop_time(c),200001);a=np.column_stack((t,*[np.sin(t*(i+1)) for i in range(len(c['probes']))]))
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);b.capture_case(ROOT,folder,c,cfg,a)
            edge=b.load_plot_data(folder,c,edge=True)
            self.assertGreater(len(edge),2)
            indices=np.searchsorted(t,edge[:,0]);np.testing.assert_array_equal(edge,a[indices])
    def test_cleanup_keeps_measurements_logs_and_compact_data(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for name in ['case.qraw','waveforms.csv','measurements.txt','case.cir','simulator.stderr.log','plot_data.npz']:(folder/name).write_bytes(b'data')
            removed=b.cleanup_large_files(folder)
            self.assertEqual(set(removed['removed']),{'case.qraw','waveforms.csv'})
            self.assertEqual(removed['removed_bytes'],8)
            for name in ['measurements.txt','case.cir','simulator.stderr.log','plot_data.npz']:self.assertTrue((folder/name).is_file())
    def test_keep_flags_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'case.qraw').write_bytes(b'x');(folder/'waveforms.csv').write_bytes(b'x')
            self.assertEqual(b.cleanup_large_files(folder,True,True)['removed_bytes'],0)
    def test_full_curve_metrics_survive_deletion_without_regrading_reduced_samples(self):
        cfg=json.loads((ROOT/'profile.json').read_text());c=b.plan(cfg,['output'])[0]
        x=np.linspace(0,8,12001);a=np.column_stack((x/8,x,.9*(1-np.exp(-x/.1))))
        c['status']='SIMULATED'
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);folder=run/'cases'/c['id'];folder.mkdir(parents=True)
            (folder/'case.qraw').write_bytes(b'dummy raw for cleanup test')
            (folder/'waveforms.csv').write_text('fixture',encoding='utf-8')
            b.capture_case(ROOT,folder,c,cfg,a);saved=copy.deepcopy(c['curve_metrics'])
            b.cleanup_large_files(folder)
            self.assertLess(len(b.load_plot_data(folder,c)),len(a))
            data={'config':cfg,'cases':[c]};b.evaluate_curves(ROOT,run,data)
            self.assertEqual(data['curve_comparison'],saved)
    def test_legacy_metrics_survive_missing_waveforms(self):
        cfg=json.loads((ROOT/'profile.json').read_text());c=b.plan(cfg,['gain'])[0];c['status']='SIMULATED'
        prior=[{'case':c['id'],'kind':'gain','status':'FAIL','p95_relative':.4}]
        with tempfile.TemporaryDirectory() as d:
            data={'config':cfg,'cases':[c],'curve_comparison':prior};b.evaluate_curves(ROOT,Path(d),data)
            self.assertEqual(data['curve_comparison'],prior)
    def test_collector_excludes_large_files_and_ignores_prepare_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);shutil.copy2(ROOT/'collect_diagnostics.py',root/'collect_diagnostics.py')
            run=root/'results/20260101/cases/c';run.mkdir(parents=True)
            (run.parents[1]/'run_state.json').write_text('{"mode":"QSPICE"}')
            prep=root/'results/20990101';prep.mkdir();(prep/'run_state.json').write_text('{"mode":"prepare: NO QSPICE SIMULATION"}')
            for name in ['case.qraw','waveforms.csv','measurements.txt','plot_data.npz']:(run/name).write_bytes(b'fixture')
            for name in ['FJP5027.txt','profile.json','reference/provenance.json','reference/curves.csv']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            subprocess.run([sys.executable,str(root/'collect_diagnostics.py')],check=True,capture_output=True)
            with zipfile.ZipFile(root/'FJP5027_diagnostics.zip') as z:
                self.assertTrue(any(n.endswith('plot_data.npz') for n in z.namelist()))
                self.assertFalse(any(n.endswith(('.qraw','waveforms.csv')) for n in z.namelist()))
                self.assertFalse(any('20990101' in n for n in z.namelist()))

if __name__=='__main__':unittest.main()
