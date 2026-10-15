import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from workbench.cli import main


class CourseStatusCliTests(unittest.TestCase):
    def status(self, options, materials, *, baseline_ready=True):
        output = io.StringIO()
        with patch('sys.argv', ['workbench.cli', 'course-status', *options]), \
             patch('workbench.cli.validate_mainline', return_value={
                 'contract_valid': True, 'course_ready': baseline_ready, 'baseline_semantics': 'progression_gate'}), \
             patch('workbench.cli.session_versions_status', return_value=materials) as inspect, \
             redirect_stdout(output):
            code = main()
        return code, json.loads(output.getvalue()), inspect

    def test_default_reports_material_gaps_without_network_or_changing_baseline_semantics(self):
        code, result, inspect = self.status([], {'ready': False, 'errors': ['missing tag']})
        self.assertEqual(0, code)
        self.assertTrue(result['course_ready'])
        self.assertFalse(result['session_materials']['ready'])
        self.assertIn('配套课程材料尚未就绪', result['warnings'][0])
        self.assertEqual('progression_gate', result['baseline_semantics'])
        self.assertIsNone(inspect.call_args.kwargs['remote'])

    def test_require_materials_returns_failure_even_when_baselines_exist(self):
        code, _result, _inspect = self.status(['--require-materials'], {'ready': False})
        self.assertEqual(1, code)

    def test_require_materials_accepts_matching_versions(self):
        code, _result, _inspect = self.status(['--require-materials'], {'ready': True})
        self.assertEqual(0, code)

    def test_remote_gaps_return_failure_and_default_to_origin(self):
        code, result, inspect = self.status(['--check-remote'], {'ready': True, 'remote': {'ready': False}})
        self.assertEqual(1, code)
        self.assertTrue(result['session_materials']['ready'])
        self.assertEqual('origin', inspect.call_args.kwargs['remote'])

    def test_named_remote_can_pass_with_nonzero_local_baseline_gate(self):
        code, _result, inspect = self.status(['--check-remote', 'course-source', '--require-baselines'],
                                          {'ready': True, 'remote': {'ready': True}}, baseline_ready=False)
        self.assertEqual(1, code)
        self.assertEqual('course-source', inspect.call_args.kwargs['remote'])

    def test_matching_remote_returns_success(self):
        code, _result, _inspect = self.status(['--check-remote'], {'ready': True, 'remote': {'ready': True}})
        self.assertEqual(0, code)
