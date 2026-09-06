"""Counterexamples for the baseline of a genuinely new requirement."""
import unittest
from workbench.course_workspace import dynamic_start_evidence

class DynamicStartTests(unittest.TestCase):
    def report(self, failed):
        names=['existing','new']
        results=[{'name':name,'level':'blocking','passed':name not in failed} for name in names]
        count=sum(not item['passed'] for item in results)
        return {'schema_version':'1.0','suite':'blocking','requested_cases':names,'results':results,
                'summary':{'total':2,'passed':2-count,'blocking_failed':count,'observing_failed':0,
                           'decision':'block' if count else 'pass'},
                'runner':{'validated':True,'process_returncode':1 if count else 0}}

    def test_only_new_requirement_red_is_acceptable(self):
        for failures, expected in ((set(),False),({'existing'},False),({'existing','new'},False),({'new'},True)):
            with self.subTest(failures=failures):
                result=dynamic_start_evidence(self.report(failures),('existing',),('new',))
                self.assertEqual(expected,result['accepted'])

    def test_missing_case_or_forged_summary_is_not_a_valid_red_start(self):
        missing=self.report({'new'}); missing['results'].pop()
        forged=self.report(set()); forged['summary']['decision']='block'
        wrong_exit=self.report({'new'}); wrong_exit['runner']['process_returncode']=0
        unvalidated=self.report({'new'}); unvalidated['runner']['validated']=False
        for report in (missing,forged,wrong_exit,unvalidated):
            with self.subTest(report=report):
                result=dynamic_start_evidence(report,('existing',),('new',))
                self.assertFalse(result['accepted'])
                self.assertIsNotNone(result['validation_error'])
