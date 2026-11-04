"""Regression checks for scientific validity and bench generation, no simulator mocks."""
from pathlib import Path
import copy,importlib.util,json,sys,tempfile,unittest
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
            self.assertIn('.model FJP5027_CANDIDATE_V1',deck)
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

if __name__=='__main__':unittest.main()
