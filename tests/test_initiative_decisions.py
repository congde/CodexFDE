"""Actual candidate/Eval processes with controlled implementation output only."""
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

from tests import test_project_initiative_delivery as fixtures
from workbench.daily_delivery import submit_daily
from workbench.delivery_runtime import DeliveryRuntime
from workbench.execution import CodexExecutionRunner
from workbench.initiative_workflow import InitiativeWorkflow
from workbench.workbench_server import WorkbenchApp, make_handler


class InitiativeDecisionTests(unittest.TestCase):
    setUp = fixtures.ProjectInitiativeTests.setUp
    state = fixtures.ProjectInitiativeTests.state
    call = fixtures.ProjectInitiativeTests.call
    wait = fixtures.ProjectInitiativeTests.wait
    ready = fixtures.ProjectInitiativeTests.ready

    def researched(self):
        self.call('discuss', 'change value')
        self.assertEqual('ready', self.wait()['stage'])

    def expire(self):
        data = self.service._load(self.item['id'])
        data['plan']['expires_at'] = 1
        self.service._save(data)
        return copy.deepcopy(data)

    def failed_candidate(self):
        root = Path(self.registered[1]['root_path'])
        (root / 'fixture_eval.py').write_text("import json;from pathlib import Path\n"
            "ok=Path('value.txt').read_text() in ('1','2')\n"
            "print(json.dumps({'summary':{'total':1,'passed':int(ok),'blocking_failed':int(not ok),'decision':'pass' if ok else 'block'},"
            "'results':[{'name':'value','level':'blocking','passed':ok}]}))\nraise SystemExit(0 if ok else 1)\n")
        def factory(workspace, runtime):
            def process(command, **kwargs):
                (workspace / 'value.txt').write_text('3')
                if getattr(self, 'extra_write', False):
                    (workspace / 'outside.txt').write_text('unexpected write')
                return subprocess.CompletedProcess(command, 0, '', '')
            return CodexExecutionRunner(workspace, runtime, process_runner=process)
        original = self.service.submitter
        self.service.submitter = lambda source, runtime, tasks, plan, created: submit_daily(
            source, runtime, tasks, plan, created, runner_factory=factory)
        if getattr(self, 'course_mode', False):
            self.researched()
            self.call('configure_delivery', 'course', 'course first judgment and revision must remain traceable')
            self.call('confirm_prd')
            self.call('confirm', 'reviewer')
        else:
            self.ready()
        self.call('execute')
        state = self.wait()
        self.assertEqual('rework', state['stage'], state.get('error'))
        self.service.submitter = original
        return state

    def test_quick_delivery_records_all_confirmations_and_stops_at_human_review(self):
        self.researched()
        self.call('configure_delivery', 'quick', 'one local file; isolated and reversible')
        self.assertEqual([], self.tasks.list())
        with self.assertRaisesRegex(ValueError, '明确'):
            self.call('confirm_execute', 'reviewer', 'value is 2', False)
        self.call('confirm_execute', 'reviewer', 'value is 2', True)
        state = self.wait()
        self.assertEqual('review', state['stage'], state['error'])
        doc = state['documents'][-1]
        self.assertEqual('owner', doc['prd_confirmation']['actor'])
        self.assertEqual('owner', doc['technical_confirmation']['actor'])
        self.assertEqual('quick', state['route_decisions'][0]['mode'])
        self.assertEqual('1', (Path(self.registered[1]['root_path']) / 'value.txt').read_text())
        with self.assertRaisesRegex(ValueError, '验收负责人'):
            self.call('accept', 'cannot self-accept')
        self.assertEqual('review', self.tasks.get(state['active_task_id'])['status'])

    def test_course_selection_is_durable_and_cannot_use_combined_authorization(self):
        self.researched()
        self.call('configure_delivery', 'course', 'retain first judgment and peer acceptance')
        with self.assertRaisesRegex(ValueError, '课程事项'):
            self.call('configure_delivery', 'quick', 'cannot remove learning evidence')
        with self.assertRaisesRegex(ValueError, '仅已具名'):
            self.call('confirm_execute', 'reviewer', 'value is 2', True)
        self.call('confirm_prd')
        self.call('confirm', 'reviewer')
        restarted = InitiativeWorkflow(self.service.repository, self.runtime, self.items, self.tasks, projects=self.projects)
        state = restarted.get(self.item['id'])
        self.assertEqual('course', state['delivery_policy']['mode'])
        self.assertEqual(1, len(state['route_decisions']))
        self.assertEqual('owner', state['documents'][0]['prd_confirmation']['actor'])

    def test_bound_course_and_inventory_cannot_be_downgraded(self):
        for changes in ({'requirement_id': 'COURSE-L04'}, {'affected_areas': ['inventory']}):
            with self.subTest(changes=changes):
                item = self.items.get(self.item['id'])
                self.items.revise(self.item['id'], changes, 'owner', item['version'])
                self.researched()
                with self.assertRaises(ValueError):
                    self.call('configure_delivery', 'quick', 'ineligible')
        self.assertEqual([], self.tasks.list())

    def test_guardrail_and_governance_files_cannot_use_quick_delivery(self):
        self.researched()
        original = self.service._load(self.item['id'])
        for path in ('AGENTS.md', '.codex/hooks.json', 'eval/harness.py', 'agent/loop.py',
                     'workbench/control_surface.py', 'workbench/repair_loop.py',
                     'workbench\\delivery_runtime.py', '库存规则.md'):
            with self.subTest(path=path):
                data = copy.deepcopy(original)
                data['proposal']['write_scope'] = [path]
                self.service._save(data)
                self.assertFalse(self.state()['delivery_policy']['quick_eligible'])
                with self.assertRaisesRegex(ValueError, '快捷交付条件'):
                    self.call('configure_delivery', 'quick', 'must keep separate governance review')
        self.assertEqual([], self.tasks.list())

    def test_frozen_contract_command_and_budget_drift_block_execution_before_launch(self):
        self.ready()
        old = self.service._load(self.item['id'])
        with patch.object(self.service, '_launch') as launch:
            self.projects.configure(self.registered[1]['id'], ['different-program'])
            with self.assertRaisesRegex(ValueError, '质量检查命令'):
                self.call('execute')
            self.projects.configure(self.registered[1]['id'], old['plan']['project']['eval_command'])
            data = copy.deepcopy(old)
            data['repair_loop_config'] = {'max_rounds': 2}
            self.service._save(data)
            with self.assertRaisesRegex(ValueError, '预算发生变化'):
                self.call('execute')
            data = copy.deepcopy(old)
            data['plan']['write_scope'] = ['outside.txt']
            self.service._save(data)
            with self.assertRaisesRegex(ValueError, '已确认的目标'):
                self.call('execute')
            launch.assert_not_called()
        self.assertEqual([], self.tasks.list())

    def interrupted_runtime(self):
        runtime = DeliveryRuntime(self.service.repository, self.runtime, self.tasks, self.projects, self.items, self.service)
        self.addCleanup(runtime.close)
        self.service.delivery_runtime = runtime
        self.ready()
        old = self.service._load(self.item['id'])
        runtime._begin(self.item['id'], old['plan'])
        data = runtime._load(self.item['id'])
        data.update(state='interrupted')
        data['budget'].update(tokens_used=123, elapsed_seconds=10, max_workers=4)
        runtime._save(data)
        old['stage'] = 'interrupted'
        self.service._save(old)
        return runtime, old

    def test_environment_rework_retains_measured_runtime_budget_and_worker_limit(self):
        runtime, old = self.interrupted_runtime()
        result = self.call('rework', 'environment', 'verified unchanged code and measured prior usage')
        self.assertEqual(old['plan']['plan_id'], result['plan']['continuation_run_id'])
        self.assertEqual(123, result['rework_decisions'][-1]['retained_budget']['tokens_used'])
        self.assertEqual([], self.tasks.list())
        runtime._begin(self.item['id'], result['plan'])
        budget = runtime._load(self.item['id'])['budget']
        self.assertEqual(123, budget['tokens_used'])
        self.assertEqual(10, budget['elapsed_seconds'])
        self.assertEqual(4, budget['max_workers'])

    def test_rework_cannot_reset_unmeasured_exhausted_or_active_runtime(self):
        runtime, old = self.interrupted_runtime()
        baseline = runtime._load(self.item['id'])
        for changes, message in (({'usage_unavailable': True}, '可核验'),
                                 ({'tokens_used': baseline['budget']['token_budget']}, '预算已耗尽'),
                                 ({'elapsed_seconds': baseline['budget']['time_budget_seconds']}, '预算已耗尽'),
                                 ({'state': 'running'}, '尚未结束')):
            with self.subTest(changes=changes):
                data = copy.deepcopy(baseline)
                if 'state' in changes:
                    data['state'] = changes['state']
                else:
                    data['budget'].update(changes)
                runtime._save(data)
                with self.assertRaisesRegex(ValueError, message):
                    self.call('rework', 'environment', 'do not reset prior stop decision')
                self.assertEqual(old['plan']['plan_id'], self.state()['plan']['plan_id'])
        self.assertEqual([], self.tasks.list())

    def test_runtime_resume_keeps_frozen_contract_and_original_measured_budget(self):
        runtime, old = self.interrupted_runtime()
        data = runtime._load(self.item['id'])
        data['workspace'] = self.registered[1]['root_path']
        runtime._save(data)
        current = runtime.view(self.item['id'])
        with patch.object(self.service, 'execute'):
            runtime.control(self.item['id'], 'resume', 'owner', current['revision'], current['candidate_sha256'])
        work = self.service._load(self.item['id'])
        self.assertNotEqual(old['plan']['plan_id'], work['plan']['plan_id'])
        self.assertEqual(old['plan']['budget_policy'], work['plan']['budget_policy'])
        self.assertIn('confirmation_sha256', work['plan'])
        self.service.decisions.validate_frozen(self.item['id'], work)
        runtime._begin(self.item['id'], work['plan'])
        self.assertEqual(123, runtime._load(self.item['id'])['budget']['tokens_used'])

    def test_interruption_cannot_bypass_enabled_loop_usage_stop(self):
        self.call('configure_loop', {
            'enabled': True, 'max_rounds': 3, 'token_budget': 30000, 'time_budget_seconds': 900})
        failed = self.failed_candidate()
        data = self.service._load(self.item['id'])
        data['stage'] = 'interrupted'
        self.service._save(data)
        with self.assertRaisesRegex(ValueError, 'Loop 已停止.*用量'):
            self.call('rework', 'implementation', 'interruption does not make unknown usage measurable')
        self.assertEqual(failed['plan']['plan_id'], self.state()['plan']['plan_id'])
        self.assertEqual(1, len(self.tasks.list()))

    def test_authorization_renewal_keeps_contract_and_requires_separate_execution(self):
        self.ready()
        old = self.expire()
        with self.assertRaisesRegex(ValueError, '已过期'):
            self.call('execute')
        result = self.call('renew_authorization', 'verified same requirements and source', True)
        self.assertEqual('confirmed', result['stage'])
        self.assertEqual(old['plan']['plan_id'], result['plan']['plan_id'])
        self.assertEqual(old['documents'], result['documents'])
        self.assertEqual(old['plan']['spec_text'], result['plan']['spec_text'])
        self.assertEqual(1, len(self.sources))
        self.assertEqual([], self.tasks.list())
        self.assertFalse(result['authorization_renewals'][-1]['automatic_execution'])
        self.call('execute')
        self.assertEqual('review', self.wait()['stage'])

    def test_source_change_blocks_renewal_without_erasing_old_authorization(self):
        self.ready()
        old = self.expire()
        (Path(self.registered[1]['root_path']) / 'value.txt').write_text('9')
        with self.assertRaisesRegex(ValueError, '源码发生变化'):
            self.call('renew_authorization', 'try stale authorization', True)
        self.assertEqual(old['plan']['expires_at'], self.state()['plan']['expires_at'])
        self.assertEqual([], self.state().get('authorization_renewals', []))

    def test_historical_confirmed_contract_can_renew_without_inventing_new_confirmation(self):
        self.ready()
        old = self.expire()
        old['plan'].pop('confirmation_sha256')
        self.service._save(old)
        result = self.call('renew_authorization', 'verified historical frozen contract', True)
        self.assertEqual(old['documents'], result['documents'])
        self.assertEqual(old['plan']['plan_id'], result['plan']['plan_id'])
        self.assertEqual([], self.tasks.list())

    def test_changed_requirement_command_and_budget_each_block_renewal(self):
        self.ready()
        old = self.expire()
        with self.subTest('command'):
            self.projects.configure(self.registered[1]['id'], ['different-program'])
            with self.assertRaisesRegex(ValueError, '质量检查命令'):
                self.call('renew_authorization', 'stale command', True)
            self.projects.configure(self.registered[1]['id'], old['plan']['project']['eval_command'])
        with self.subTest('budget'):
            data = self.service._load(self.item['id'])
            data['repair_loop_config'] = {'max_rounds': 2}
            self.service._save(data)
            with self.assertRaisesRegex(ValueError, '预算发生变化'):
                self.call('renew_authorization', 'stale budget', True)
            data['repair_loop_config'] = {}
            self.service._save(data)
        with self.subTest('requirement'):
            item = self.items.get(self.item['id'])
            self.items.set_home_hidden(self.item['id'], True, 'owner', item['version'])
            with self.assertRaisesRegex(ValueError, '需求发生变化'):
                self.call('renew_authorization', 'stale requirement', True)
        self.assertEqual([], self.tasks.list())

    def test_changed_runtime_profile_cannot_be_renewed(self):
        runtime = DeliveryRuntime(self.service.repository, self.runtime, self.tasks, self.projects, self.items, self.service)
        self.addCleanup(runtime.close)
        self.service.delivery_runtime = runtime
        self.ready()
        self.expire()
        data = runtime._load(self.item['id'])
        plugin = runtime.sessions.composition(data['profile_id'])['plugins'][0]
        runtime.sessions.set_plugin_enabled(plugin['id'], False)
        with self.assertRaisesRegex(ValueError, '运行配置'):
            self.call('renew_authorization', 'changed profile', True)

    def test_renewal_rejects_old_revision_and_another_actor(self):
        self.ready()
        self.expire()
        with self.assertRaisesRegex(ValueError, '进展已变化'):
            self.service.renew_authorization(self.item['id'], 'owner', self.state()['revision'] - 1, 'old page', True)
        with self.assertRaisesRegex(ValueError, '原授权人'):
            self.call('renew_authorization', 'another signer', True, actor='reviewer')
        self.assertEqual([], self.state().get('authorization_renewals', []))

    def test_implementation_rework_keeps_failed_candidate_and_original_requirements(self):
        failed = self.failed_candidate()
        old_task, old_workspace = failed['active_task_id'], Path(failed['workspace'])
        result = self.call('rework', 'implementation', 'value is 3; repair to confirmed value 2')
        self.assertEqual('confirmed', result['stage'])
        self.assertEqual(1, len(self.sources))
        self.assertEqual(failed['documents'], result['documents'])
        self.assertEqual(failed['plan']['write_scope'], result['plan']['write_scope'])
        self.assertNotEqual(failed['plan']['plan_id'], result['plan']['plan_id'])
        self.assertEqual('3', (old_workspace / 'value.txt').read_text())
        self.call('execute')
        result = self.wait()
        self.assertEqual('review', result['stage'], result['error'])
        self.assertEqual('rework', self.tasks.get(old_task)['status'])
        self.assertEqual('2', (Path(result['workspace']) / 'value.txt').read_text())
        self.assertEqual('3', (old_workspace / 'value.txt').read_text())
        self.assertEqual(2, len(result['iterations']))
        self.assertEqual('implementation', result['rework_decisions'][0]['reason'])

    def test_failed_out_of_scope_write_cannot_become_a_repair_baseline(self):
        self.extra_write = True
        failed = self.failed_candidate()
        with self.assertRaisesRegex(ValueError, '范围外变化'):
            self.call('rework', 'implementation', 'do not inherit the unexpected file')
        self.assertEqual(1, len(self.tasks.list()))
        self.assertEqual(failed['plan']['plan_id'], self.state()['plan']['plan_id'])

    def test_course_repair_keeps_first_judgment_failed_task_and_revision_evidence(self):
        self.course_mode = True
        failed = self.failed_candidate()
        result = self.call('rework', 'implementation', 'student revision: use confirmed value 2 instead of failed value 3')
        self.assertEqual('course', result['delivery_policy']['mode'])
        self.assertEqual(failed['documents'], result['documents'])
        self.assertEqual(failed['active_task_id'], result['rework_decisions'][-1]['task_id'])
        self.assertEqual('rework', result['rework_decisions'][-1]['failure_evidence']['task_status'])
        self.assertIn('student revision', result['plan']['spec_text'])
        with self.assertRaisesRegex(ValueError, '仅已具名|当前阶段'):
            self.call('confirm_execute', 'reviewer', 'value is 2', True)
        self.call('execute')
        repaired = self.wait()
        self.assertEqual('review', repaired['stage'], repaired['error'])
        self.assertEqual('rework', self.tasks.get(failed['active_task_id'])['status'])
        self.assertEqual('review', self.tasks.get(repaired['active_task_id'])['status'])
        self.assertEqual(2, len(repaired['iterations']))

    def test_changed_business_goal_in_technical_rework_requires_new_prd_confirmation(self):
        self.failed_candidate()
        original = self.service.researcher
        def research(*args):
            result = original(*args)
            result['proposal']['goal'] = 'a different business goal'
            return result
        self.service.researcher = research
        self.call('rework', 'technical', 'technical change also changes business goal')
        result = self.wait()
        self.assertFalse(result['prd_confirmed'])
        with self.assertRaisesRegex(ValueError, '先分别确认 PRD'):
            self.call('confirm', 'reviewer')

    def test_technical_rework_carries_unchanged_prd_but_requires_new_technical_confirmation(self):
        failed = self.failed_candidate()
        self.call('rework', 'technical', 'change implementation approach; goal remains value 2')
        result = self.wait()
        self.assertEqual('ready', result['stage'])
        self.assertTrue(result['prd_confirmed'])
        self.assertEqual(1, result['documents'][-1]['prd_confirmation']['carried_from_version'])
        self.assertNotIn('technical_confirmation', result['documents'][-1])
        self.assertEqual(failed['documents'][0], result['documents'][0])
        self.call('confirm', 'reviewer')
        self.assertEqual('confirmed', self.state()['stage'])

    def test_technical_clarification_preserves_original_prd_across_more_than_one_round(self):
        failed = self.failed_candidate()
        original = self.service.researcher
        def research(*args):
            result = original(*args)
            result['proposal']['questions'] = ['Which implementation approach?']
            return result
        self.service.researcher = research
        self.call('rework', 'technical', 'refine implementation approach')
        self.assertEqual('clarifying', self.wait()['stage'])
        self.service.researcher = original
        self.call('discuss', 'use the existing local approach')
        result = self.wait()
        self.assertTrue(result['prd_confirmed'])
        self.assertEqual(1, result['documents'][-1]['prd_confirmation']['carried_from_version'])
        self.assertEqual(failed['documents'][0], result['documents'][0])

    def test_requirement_rework_requires_new_prd_confirmation(self):
        failed = self.failed_candidate()
        self.call('rework', 'requirement', 'reconsider intended value')
        result = self.wait()
        self.assertFalse(result['prd_confirmed'])
        self.assertEqual(failed['documents'][0], result['documents'][0])
        with self.assertRaisesRegex(ValueError, '先分别确认 PRD'):
            self.call('confirm', 'reviewer')

    def test_environment_recheck_does_not_call_implementation_or_create_another_task(self):
        failed = self.failed_candidate()
        with patch.object(self.service, 'submitter', side_effect=AssertionError('must not write code')):
            self.call('rework', 'environment', 'verify the original candidate command again')
            result = self.wait()
        self.assertEqual('rework', result['stage'])
        self.assertEqual(failed['active_task_id'], result['active_task_id'])
        self.assertEqual(1, len(self.tasks.list()))
        self.assertEqual(1, len(result['eval_runs']))
        self.assertEqual('3', (Path(result['workspace']) / 'value.txt').read_text())

    def test_environment_recovery_from_interruption_prepares_only_a_new_authorization(self):
        self.ready()
        old = self.service._load(self.item['id'])
        old['stage'] = 'interrupted'
        self.service._save(old)
        result = self.call('rework', 'environment', 'environment is restored; checked no candidate write')
        self.assertEqual('confirmed', result['stage'])
        self.assertNotEqual(old['plan']['plan_id'], result['plan']['plan_id'])
        self.assertEqual([], self.tasks.list())
        self.assertEqual(1, len(self.sources))

    def test_route_http_request_replay_keeps_one_judgment(self):
        self.researched()
        app = WorkbenchApp(self.runtime)
        self.addCleanup(app.delivery_runtime.close)
        app.initiative_workflow = self.service
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        payload = {'actor': 'owner', 'revision': self.state()['revision'], 'mode': 'quick',
                   'note': 'reviewed exact local file', 'submission_key': 'route-once'}
        def post(action='delivery-route'):
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                client.request('POST', '/api/v1/initiatives/' + self.item['id'] + '/workflow/' + action,
                               json.dumps(payload), {'Content-Type': 'application/json'})
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()
        first, repeated = post(), post()
        self.assertEqual(200, first[0], first)
        self.assertEqual(first, repeated)
        self.assertEqual(1, len(self.state()['route_decisions']))
        payload = {'actor': 'owner', 'revision': self.state()['revision'], 'reviewer': 'reviewer',
                   'success_metric': 'value is 2', 'confirmed': True, 'submission_key': 'execute-once'}
        first, repeated = post('confirm-execute'), post('confirm-execute')
        self.assertEqual(202, first[0], first)
        self.assertEqual(first, repeated)
        self.assertEqual('review', self.wait()['stage'])
        self.assertEqual(1, len(self.tasks.list()))


if __name__ == '__main__':
    unittest.main()
