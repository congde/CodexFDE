"""Reference integration checks; synthetic L05 fixtures are not student evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

stock=module('stock_route',ROOT/'docs/courses/L06/examples/stock_practice.py')
bridge=module('stock_bridge',ROOT/'docs/courses/L07/examples/connect_l06_checks.py')


class StockRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='l06-reference-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        source=self.root/'synthetic-l05';source.mkdir()
        # Reference L05 checks exercise actual behavior; their source is not student work.
        templates=('test_receiving_starter.py','test_scope_starter.py','write_scope_eval.py')
        for name,template in zip(stock.L05,templates):
            p=source/name;p.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT/'docs/courses/L05/examples'/template,p)
        old=self.root/'old';old.mkdir()
        stock.save(old/'submission.json',{'isolation':{'path':str(source)},'task':{'id':'REFERENCE-ONLY'}})
        stock.save(old/'frozen-checks.json',{'files':{n:stock.sha(source/n) for n in stock.L05}})
        stock.save(old/'session.json',{'run':str(old)})
        stock.prepare(self.root/'run',old/'session.json')
        self.session=self.root/'run/session.json';self.s=stock.session(self.session)

    def repair_runner(self):
        p=self.s['candidate']/'eval/l06_runner.py'
        p.write_text(p.read_text('utf8').replace('blocking_failed = 0',"blocking_failed = sum(not r['passed'] and r['level']=='blocking' for r in results)"),'utf8')

    def repair_product(self):
        p=self.s['candidate']/'flowerp/service.py'
        t=p.read_text('utf8');a=t.index('    def export_inventory(');b=t.index('\n    def ',a+5)
        t=t[:a]+t[a:b].replace("{row['reserved']},{row['on_hand']}","{row['reserved']},{row['available']}")+t[b:]
        p.write_text(t,'utf8')

    def run_report(self,name,expected,opening=8,reserved=3):
        p=self.root/(name+'.json')
        self.assertEqual(stock.execute(self.s,p,opening,reserved),expected)
        return p

    def test_two_repairs_transfer_and_stale_evidence(self):
        fake=self.run_report('false-green',0)
        self.assertFalse(stock.read(fake)['results'][0]['passed'])
        with self.assertRaises(RuntimeError):stock.review(self.s,fake)
        self.repair_runner()
        red=self.run_report('credible-red',1);stock.review(self.s,red)
        contract=self.root/'contract.json'
        self.assertEqual(stock.execute(self.s,contract,contract=True),0)
        self.repair_product()
        green=self.run_report('green',0);stock.review(self.s,green)
        transfer=self.run_report('transfer',0,13,4);stock.review(self.s,transfer)
        with self.assertRaises(ValueError):stock.review(self.s,red)
        with self.assertRaises(FileExistsError):stock.execute(self.s,green)

    def test_bridge_runs_carried_checks_and_rejects_duplicate(self):
        self.repair_runner();self.repair_product()
        green=self.run_report('green',0)
        target=self.root/'l07';target.mkdir()
        stock.copy_experiment_sources(target)
        receipt=self.root/'bridge.json'
        bridge.connect(self.session,green,target,receipt)
        for name in bridge.FILES:
            self.assertEqual(stock.sha(target/name),stock.sha(self.s['candidate']/name))
        names=[n for n in bridge.NAMES if n!='help_image']
        command=[sys.executable,'-B','-X','utf8','-m','eval.harness','--suite','blocking','--report-path',str(self.root/'unified.json')]
        for name in names:command+=['--case',name]
        result=subprocess.run(command,cwd=target,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=stock.read(self.root/'unified.json')
        self.assertEqual({r['name'] for r in report['results']},set(names))
        with self.assertRaises(ValueError):bridge.connect(self.session,green,target,self.root/'again.json')
        # A carried runner regression must become a blocking failure in the unified entry.
        runner=target/'eval/l06_runner.py'
        runner.write_text(runner.read_text('utf8').replace("blocking_failed = sum(not r['passed'] and r['level']=='blocking' for r in results)",'blocking_failed = 0'),'utf8')
        command[command.index(str(self.root/'unified.json'))]=str(self.root/'broken-runner.json')
        result=subprocess.run(command,cwd=target,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertFalse(next(r for r in stock.read(self.root/'broken-runner.json')['results'] if r['name']=='l06_harness_contract')['passed'])

    def test_bridge_rejects_red_and_tampered_source(self):
        self.repair_runner();red=self.run_report('red',1)
        with self.assertRaises(ValueError):bridge.connect(self.session,red,self.root,self.root/'no.json')
        self.repair_product();green=self.run_report('green',0)
        checks=self.s['candidate']/'eval/l06_checks.py'
        checks.write_text(checks.read_text('utf8')+'\n# unauthorized change\n','utf8')
        with self.assertRaises(ValueError):stock.review(self.s,green)

if __name__=='__main__':unittest.main()
