"""Exercise evidence preservation and rejection paths, not student acceptance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

EXAMPLES=Path(__file__).resolve().parents[1]/'docs/courses/L05/examples'
spec=importlib.util.spec_from_file_location('l05_delivery',EXAMPLES/'check_delivery.py')
delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)


class EvidenceTests(unittest.TestCase):
    def test_course_scope_reaches_task_and_cannot_expand_contract(self):
        from workbench.course_mainline import create_lesson_task, lesson_contract
        from workbench.task_store import TaskStore
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); store=TaskStore(root/'workbench.db')
            task=create_lesson_task(store,5,root,write_scope=('flowerp/service.py',))
            self.assertEqual(task['write_scope'],['flowerp/service.py'])
            default=create_lesson_task(store,5,root)
            self.assertEqual(default['write_scope'],[p.rstrip('/') for p in lesson_contract(5).write_scope])
            for scope in [(),('web/page.py',),('../flowerp/service.py',),('flowerp-other/service.py',)]:
                with self.subTest(scope=scope),self.assertRaises(ValueError):
                    create_lesson_task(store,5,root,write_scope=scope)

    def test_headless_options_preserve_required_code_mode_host(self):
        from workbench.codex_options import headless_options
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'CODEX_HOME':temp}):
            self.assertEqual(headless_options(),[])
            config=Path(temp)/'config.toml';config.write_text('[mcp_servers.node_repl]\nenabled=true\n','utf8')
            before=config.read_bytes()
            self.assertEqual(headless_options(),['-c','mcp_servers.node_repl.enabled=false'])
            self.assertEqual(config.read_bytes(),before)

    def test_isolated_cli_preserves_model_and_windows_sandbox_without_editing_config(self):
        from workbench.codex_options import headless_options
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'CODEX_HOME':temp,'WORKBENCH_CODEX_CONFIG_MODE':'isolated'}):
            config=Path(temp)/'config.toml'
            config.write_text('model="gpt-6-astra"\n[windows]\nsandbox="elevated"\n[mcp_servers.node_repl]\nenabled=true\n','utf8')
            before=config.read_bytes()
            self.assertEqual(headless_options(),['--ignore-user-config','-c','model="gpt-6-astra"','-c','windows.sandbox="elevated"'])
            self.assertEqual(config.read_bytes(),before)
            config.write_text('model_provider="custom"\n','utf8')
            with self.assertRaisesRegex(RuntimeError,'自定义提供方'):headless_options()

    def test_repeated_failed_commands_keep_both_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            command=[sys.executable,'-B','-X','utf8',str(EXAMPLES/'record_command.py'),'--cwd',temp,
                     '--evidence',temp,'--name','same-label','--expect','1','--',sys.executable,'-c','print("失败证据");raise SystemExit(1)']
            records=[]
            for _ in range(2):
                p=subprocess.run(command,capture_output=True,text=True,encoding='utf8');self.assertEqual(p.returncode,0,p.stderr)
                records.append(json.loads(p.stdout))
            self.assertNotEqual(records[0]['receipt'],records[1]['receipt'])
            for r in records:
                self.assertTrue(Path(r['receipt']).exists());self.assertIn('失败证据',r['stdout']);self.assertEqual(r['exit_code'],1)

    def test_unexpected_exit_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            p=subprocess.run([sys.executable,'-B',str(EXAMPLES/'record_command.py'),'--cwd',temp,'--evidence',temp,
                              '--name','mismatch','--expect','0','--',sys.executable,'-c','raise SystemExit(2)'],capture_output=True,text=True,encoding='utf8')
            self.assertEqual(p.returncode,1);self.assertEqual(json.loads(p.stdout)['exit_code'],2)

    def test_command_file_preserves_quotes_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            source = 'from pathlib import Path; print(Path("flowerp/service.py")); print("中文 空格")'
            command = [sys.executable, '-X', 'utf8', '-c', source]
            args = Path(temp) / 'command.json'
            args.write_text(json.dumps(command, ensure_ascii=False), encoding='utf-8-sig')
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', str(EXAMPLES/'record_command.py'),
                 '--cwd', temp, '--evidence', temp, '--name', 'quotes', '--expect', '0',
                 '--command-file', str(args)], capture_output=True, text=True, encoding='utf8')
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(record['command'], command)
            self.assertIn('中文 空格', record['stdout'])

    def test_delivery_detects_check_mutation_scope_and_later_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);candidate=root/'candidate';candidate.mkdir();runtime=root/'runtime';task='TASK-TEST'
            for name in delivery.CHECKS:
                p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# frozen\n','utf8')
            service=candidate/'flowerp/service.py';service.parent.mkdir();service.write_text('# repaired\n','utf8')
            frozen=root/'frozen.json';frozen.write_text(json.dumps({'files':{n:delivery.check_digest(candidate/n) for n in delivery.CHECKS}}),'utf8')
            allowed=root/'allowed.json';allowed.write_text('["flowerp/service.py"]','utf8')
            submission=root/'submission.json';submission.write_text(json.dumps({'task':{'id':task,'status':'review'},'isolation':{'path':str(candidate)},'implementation_evidence':True}),'utf8')
            evidence={'invocation':{'workspace':str(candidate)},'mode':'codex_exec','returncode':0,'success':True,
                      'changed_files':['flowerp/service.py'],'change_manifest':[{'path':'flowerp/service.py','after_sha256':delivery.digest(service)}]}
            ep=runtime/'delivery'/task/'evidence.json';ep.parent.mkdir(parents=True);ep.write_text(json.dumps(evidence),'utf8')
            self.assertEqual(delivery.verify(submission,runtime,allowed,frozen)['status'],'pass')
            value = json.loads(submission.read_text('utf8'))
            value['task']['events'] = [{'evidence': {'mode': 'codex_exec', 'attempt_id': 'run123'}}]
            submission.write_text(json.dumps(value), 'utf8')
            evidence['attempt_id'] = 'run123'
            attempt_path = ep.parent/'run123'/'evidence.json'
            attempt_path.parent.mkdir()
            attempt_path.write_text(json.dumps(evidence), 'utf8')
            self.assertEqual(delivery.verify(submission,runtime,allowed,frozen)['source'],str(attempt_path.resolve()))
            evidence['attempt_id'] = 'wrong'
            attempt_path.write_text(json.dumps(evidence), 'utf8')
            self.assertIn('执行记录与任务尝试编号不同',delivery.verify(submission,runtime,allowed,frozen)['issues'])
            evidence['attempt_id'] = 'run123'
            attempt_path.write_text(json.dumps(evidence), 'utf8')
            (candidate/delivery.CHECKS[0]).write_bytes(b'# frozen\r\n')
            self.assertEqual(delivery.verify(submission,runtime,allowed,frozen)['status'],'pass')
            (candidate/delivery.CHECKS[0]).write_text('# deleted assertion\n','utf8')
            self.assertIn('个人检查丢失或改变',str(delivery.verify(submission,runtime,allowed,frozen)['issues']))
            allowed.write_text('[]','utf8');self.assertEqual(delivery.verify(submission,runtime,allowed,frozen)['unexpected'],['flowerp/service.py'])
            service.write_text('# changed after execution\n','utf8');self.assertIn('执行后文件又发生变化',str(delivery.verify(submission,runtime,allowed,frozen)['issues']))

    def test_freeze_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in delivery.CHECKS:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# check','utf8')
            out=root/'frozen.json';cmd=[sys.executable,'-B',str(EXAMPLES/'check_delivery.py'),'freeze','--candidate',temp,'--output',str(out)]
            first=subprocess.run(cmd,capture_output=True);self.assertEqual(first.returncode,0)
            content=out.read_bytes();second=subprocess.run(cmd,capture_output=True);self.assertEqual(second.returncode,2);self.assertEqual(content,out.read_bytes())


if __name__=='__main__':unittest.main()
