import copy,unittest,tempfile
from pathlib import Path
from eval.runner import run
from eval.report_contract import validate_report
class ReportContractTests(unittest.TestCase):
    def report(self):
        return run([('normal','blocking',lambda:'actual normal')])
    def test_good_actual_report_and_exit(self):
        r,c=self.report();validate_report(r,['normal'],c,'all')
    def test_one_blocker_and_observing_warning(self):
        def bad():raise AssertionError('expected=5 actual=8')
        r,c=run([('a','blocking',lambda:'ok'),('b','blocking',bad),('c','observing',bad)])
        self.assertEqual(c,1);self.assertEqual(r['summary']['blocking_failed'],1);self.assertEqual(r['summary']['observing_failed'],1)
    def test_unknown_and_zero_are_rejected(self):
        for args in [([],{}),([('a','blocking',lambda:None)],{'names':[]}),([('a','blocking',lambda:None)],{'names':['missing']})]:
            with self.assertRaises(ValueError):run(args[0],**args[1])
    def test_false_summary_and_false_exit_rejected(self):
        r,c=self.report();r['summary']['passed']=0
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],c,'all')
        r,c=self.report()
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],1,'all')
    def test_identity_and_level_mismatch_rejected(self):
        r,c=self.report();r['results'][0]['name']='other'
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],c,'all')
        r,c=self.report();r['results'][0]['level']='silently-downgraded'
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],c,'all')
    def test_bool_count_or_duration_rejected(self):
        r,c=self.report();r['summary']['total']=True
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],c,'all')
        r,c=self.report();r['results'][0]['duration_ms']=False
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],c,'all')
    def test_failed_check_without_reason_rejected(self):
        r,c=self.report();r['results'][0].update(passed=False,error=None)
        with self.assertRaises(RuntimeError):validate_report(r,['normal'],1,'all')
    def test_original_report_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'saved.json';p.write_text('original','utf8')
            with self.assertRaises(FileExistsError):run([('a','blocking',lambda:'ok')],report_path=p)
            self.assertEqual(p.read_text('utf8'),'original')
