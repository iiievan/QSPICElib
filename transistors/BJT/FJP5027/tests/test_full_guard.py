"""Regression tests for native CSV grading and loss-free best selection."""
from pathlib import Path
import copy,json,sys,tempfile,unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'_common'))
import bjt_full_guard as full


class FullCurveRegression(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]
        self.cfg=json.loads((self.root/'profile.json').read_text())
        self.protected=json.loads((self.root/'calibration/mextram_v11_selected/checkpoint.json').read_text())['protected_curves']

    def test_first_saturation_reference_is_included(self):
        case=next(c for c in full.fitting_cases(self.root,self.cfg) if c['id']=='saturation_Tp75')
        values=[t['x'] for t in case['fit_targets'] if t['kind']=='vcesat']
        self.assertEqual(len(values),113)
        self.assertLess(min(values),.100001)
        self.assertGreaterEqual(min(values),.1)

    def test_saved_endpoint_demonstrates_old_pass_fail_flip(self):
        # Compact data are a diagnostic fixture, never used to regrade native acceptance.
        root=self.root.parents[2]/'review/native_comparison_v11/mex_guarded_eval21/validation'
        with np.load(root/'cases/saturation_Tp75/plot_data.npz') as data:a=data['overview']
        ref=full.core.reference(self.root)['vcesat',75,'T+75']
        complete=full.core.metric_curve(ref[:,0],ref[:,1],a[:,1],a[:,3],self.cfg,'vcesat')
        subset=ref[ref[:,0]>.100001]
        omitted=full.core.metric_curve(subset[:,0],subset[:,1],a[:,1],a[:,3],self.cfg,'vcesat')
        self.assertEqual(complete['status'],'FAIL');self.assertEqual(omitted['status'],'PASS')
        self.assertAlmostEqual(complete['p95_relative'],.207442,delta=.0001)

    def test_changed_reference_order_or_nonfinite_axis_is_rejected(self):
        with self.assertRaises(full.SuiteError):
            full.trace_residual(np.array([1.,2.]),np.array([1.,2.]),np.array([1.,3.,2.]),np.ones(3))

    def test_more_passes_do_not_override_a_protected_loss(self):
        fit=object.__new__(full.FullCurveFit)
        fit.best={'cost':3.,'row':{'full_curve_assessment':{'summary':{'PASS':10}}}}
        fit.assessment={'eligible_dc':False,'summary':{'PASS':18},'protected_regressions':[{'kind':'gain'}]}
        result={'cost':.01,'row':{'table_guards_pass':True}}
        self.assertFalse(fit.should_replace_best(result))
        self.assertFalse(result['row']['full_curve_guard_pass'])

    def test_selected_native_checkpoint_is_verified(self):
        state,p=full.checkpoint(self.root)
        self.assertEqual(state['selected_candidate'],'mex_v8')
        self.assertEqual(p['LEVEL'],504)
        self.assertAlmostEqual(p['CJC']*1e12,96.6455,places=3)

    def test_grade_full_array_before_large_file_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            fit=full.FullCurveFit(self.root,run,self.cfg,{},True,10,protected=self.protected)
            fit.prepare=False;fit.tools={'engine':'fixture_engine'}
            case=next(c for c in fit.cases if c['kind']=='output')
            scan=np.linspace(0,1,12001)
            array=np.column_stack((scan,8*scan,.01+scan))
            events=[]
            original_capture=full.core.capture_case;original_cleanup=full.core.cleanup_large_files
            def postprocess(folder,c,*args):
                c.update(status='SIMULATED',measurements={'OUTPUT_ID_MAX':1.01})
                (folder/'case.qraw').write_text('unit test fixture')
                (folder/'waveforms.csv').write_text('unit test fixture')
                return array
            def capture(root,folder,c,cfg,a):
                events.append(('grade',len(a)))
                return original_capture(root,folder,c,cfg,a)
            def cleanup(folder):
                events.append(('cleanup',0));return original_cleanup(folder)
            with patch.object(full,'command'),patch.object(full.base,'logs_have_unknown_parameter',return_value=False),\
                 patch.object(full.core,'postprocess',side_effect=postprocess),\
                 patch.object(full.core,'capture_case',side_effect=capture),\
                 patch.object(full.core,'cleanup_large_files',side_effect=cleanup):
                fit.run_cases({},run/'native_fixture',[case])
            self.assertEqual(events,[('grade',12001),('cleanup',0)])
            self.assertEqual(len(fit.curves),1)
            self.assertFalse(list(run.rglob('*.qraw')))
            self.assertFalse(list(run.rglob('waveforms.csv')))
            self.assertTrue(list(run.rglob('plot_data.npz')))


if __name__=='__main__':unittest.main()
