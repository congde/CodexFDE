"""Local contract fixtures, never live project or student acceptance evidence.

Only the Codex implementation output is controlled. Source snapshots, project
Eval subprocesses, task reports and named review gates use the real workbench.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


class LocalDeliveryFixture:
    def __init__(self):
        from workbench.daily_delivery import manifest, submit_daily
        from workbench.execution import CodexExecutionRunner
        from workbench.initiative import InitiativeStore
        from workbench.initiative_workflow import InitiativeWorkflow
        from workbench.project_store import ProjectStore
        from workbench.task_store import TaskStore

        self.temporary = tempfile.TemporaryDirectory(prefix='workbench-contract-')
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        self.tasks = TaskStore(self.runtime / 'workbench.db')
        self.items = InitiativeStore(self.tasks.path)
        self.projects = ProjectStore(self.tasks.path)
        self.project = self.register('project')
        self.source = Path(self.project['root_path'])
        self.value = 2

        def research(source, runtime, folder, context, progress):
            return {'proposal': {'goal': 'Update value in the local contract fixture',
                'questions': [], 'findings': ['value.txt contains a positive integer'],
                'acceptance': ['value is positive and reflects the requested change'],
                'non_goals': ['No other project files'], 'write_scope': ['value.txt'],
                'steps': ['Update value.txt', 'Run the registered project Eval'],
                'sources': ['value.txt', 'policy.txt']},
                'source_manifest': manifest(source, runtime), 'invocation': {'fixture': True}}

        def factory(workspace, runtime):
            def process(command, **kwargs):
                if self.value is not None:
                    (workspace / 'value.txt').write_text(str(self.value), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            return CodexExecutionRunner(workspace, runtime, process_runner=process)

        def submit(source, runtime, tasks, plan, created):
            return submit_daily(source, runtime, tasks, plan, created, runner_factory=factory)

        self.service = InitiativeWorkflow(self.source, self.runtime, self.items, self.tasks,
            projects=self.projects, enabled=True, researcher=research, submitter=submit)
        self.learning = self.service.learning

    def register(self, name):
        source = self.root / name
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
        (source / 'value.txt').write_text('1', encoding='utf-8')
        (source / 'policy.txt').write_text('positive value contract fixture', encoding='utf-8')
        (source / 'AGENTS.md').write_text('Only change value.txt. Local automated fixture.', encoding='utf-8')
        # Use a descriptive fixture filename; the check and real gates stay intact.
        (source / 'contract_eval.py').write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "passed = int(Path('value.txt').read_text()) > 0\n"
            "print(json.dumps({'summary': {'total': 1, 'passed': int(passed), "
            "'blocking_failed': int(not passed), 'observing_failed': 0, "
            "'decision': 'pass' if passed else 'block'}, 'results': "
            "[{'name': 'positive_value', 'level': 'blocking', 'passed': passed}]}))\n"
            "sys.exit(0 if passed else 1)\n", encoding='utf-8')
        return self.projects.create('Automated fixture ' + name, source, [sys.executable, '-B', 'contract_eval.py'])

    def item(self, title='value improvement', project=None):
        return self.items.create({'title': title, 'raw_signal': title, 'source': 'automated contract fixture',
            'project_id': (project or self.project)['id'], 'success_metric': 'value reflects the requested change'},
            'fixture-requester')

    def state(self, item):
        return self.service.get(item['id'])

    def call(self, item, action, *args, actor='fixture-requester'):
        return getattr(self.service, action)(item['id'], actor, self.state(item)['revision'], *args)

    def wait(self, item, *, idle_timeout=30, total_timeout=180):
        """Bound the whole fixture and stop on thirty seconds without progress."""
        worker = self.service.workers[item['id']]
        deadline = time.monotonic() + total_timeout
        idle_deadline = time.monotonic() + idle_timeout
        previous = None
        while worker.is_alive():
            work = self.service._load(item['id'])
            task = self.tasks.get(work['active_task_id']) if work.get('active_task_id') else None
            progress = (work['revision'], task['version'] if task else None,
                        len(task.get('events', [])) if task else 0)
            if progress != previous:
                previous = progress
                idle_deadline = time.monotonic() + idle_timeout
            remaining = min(deadline, idle_deadline) - time.monotonic()
            if remaining <= 0:
                break
            worker.join(min(.25, remaining))
        assert not worker.is_alive(), 'Local contract delivery did not finish within its bounded progress deadline'
        return self.state(item)

    def prepare(self, item, *, trials=False, choices=None):
        self.call(item, 'discuss', 'Update value under the positive value contract')
        state = self.wait(item)
        assert state['stage'] == 'ready', state.get('error')
        if trials:
            self.call(item, 'learning_action', {'action': 'recall', 'trials': True})
            state = self.state(item)
        if choices is not None:
            self.call(item, 'learning_action', {'action': 'decide', 'recall_id': state['learning_recall']['id'],
                                               'choices': choices})
        self.call(item, 'confirm_prd')
        return self.call(item, 'confirm', 'fixture-reviewer')

    def execute(self, item):
        self.call(item, 'execute')
        return self.wait(item)

    def accept(self, item):
        return self.call(item, 'accept', 'Checked actual local candidate and project Eval fixture', actor='fixture-reviewer')

    def integrate(self, item):
        self.call(item, 'integrate', actor='fixture-reviewer')
        state = self.wait(item)
        assert state['stage'] == 'integrated', state.get('error')
        return state

    def accepted_source(self):
        item = self.item('value source A')
        self.prepare(item)
        state = self.execute(item)
        assert state['stage'] == 'review', state.get('error')
        self.accept(item)
        return item, self.state(item)['active_task_id']

    @staticmethod
    def candidate(task_id, kind='workflow', **changes):
        fields = {'kind': kind, 'task_id': task_id, 'title': 'Positive value delivery',
            'content': 'Use the project Eval and named acceptance before integrating a positive value change.',
            'applies': ['value'], 'excludes': ['negative-only'],
            'boundary': 'Local value fixture only; no business or student acceptance claim.',
            'conflict_key': 'positive-value'}
        if kind == 'workflow':
            phases = ['precheck', 'implement', 'eval', 'review']
            fields['recipe'] = {'parameters': [],
                'preconditions': [{'kind': 'file_contains', 'path': 'policy.txt', 'text': 'positive value'}],
                'steps': [{'phase': phase, 'role': ['harness', 'codex', 'harness', 'human'][i],
                    'depends_on': [] if i == 0 else [phases[i-1]], 'instruction': 'Perform ' + phase}
                    for i, phase in enumerate(phases)], 'authorization': 'confirmed_plan',
                'eval_entry': 'project_blocking', 'outputs': ['Diff', 'blocking report', 'named review'],
                'stop': 'Stop on failed preconditions, execution or Eval',
                'rollback': 'Keep the candidate and failure evidence; do not integrate'}
        return fields | changes

    @staticmethod
    def choice(asset, *, adopt=True):
        return {'id': asset['id'], 'adopt': adopt, 'reason': 'Checked applicability in the local fixture', 'parameters': {}}

    def workflow_asset(self):
        source, task = self.accepted_source()
        asset = self.learning.create(source['id'], 'fixture-author', self.candidate(task))
        asset = self.learning.govern(source['id'], asset['id'], 'fixture-peer', 'approve', 'Independent local fixture review')
        return source, asset

    def close(self):
        deadline = time.monotonic() + 30
        for event in list(self.service.cancel_events.values()):
            event.set()
        workers = set(self.service.workers.values())
        try:
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join(max(0, deadline - time.monotonic()))
            pending = [worker.name for worker in workers if worker.is_alive()]
            if pending:
                raise RuntimeError('本地契约夹具关闭未完成，线程仍在运行，目录保留：'
                                   + str(self.root) + '；' + ', '.join(pending))
            self.temporary.cleanup()
        except BaseException:
            # A later GC/exit cleanup must not delete a retained, incomplete tree.
            # TemporaryDirectory has no public API for cancelling that finalizer.
            self.temporary._finalizer.detach()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if exc is None:
                raise
            if hasattr(exc, 'add_note'):
                exc.add_note('本地契约夹具关闭未完成：' + str(cleanup))
            else:
                print('本地契约夹具关闭未完成：' + str(cleanup), file=sys.stderr)
        return False


def initiative_delivery_is_controlled():
    with LocalDeliveryFixture() as f:
        item = f.item()
        f.prepare(item)
        state = f.execute(item)
        assert state['stage'] == 'review', state.get('error')
        task = f.tasks.get(state['active_task_id'])
        assert task['result']['runner']['validated'] and task['result']['runner']['process_returncode'] == 0
        assert Path(task['result']['runner']['process_path']).is_file()
        assert (f.source / 'value.txt').read_text() == '1', 'Source changed before named review'
        try:
            f.call(item, 'accept', 'Unauthorized fixture acceptance', actor='fixture-requester')
        except ValueError:
            pass
        else:
            raise AssertionError('Wrong reviewer accepted the candidate')
        f.accept(item)
        f.integrate(item)
        assert (f.source / 'value.txt').read_text() == '2'
        failed = f.item('value failure fixture')
        f.value = -1
        f.prepare(failed)
        state = f.execute(failed)
        assert state['stage'] == 'rework', state.get('error')
        assert f.tasks.get(state['active_task_id'])['result']['runner']['process_returncode'] == 1
        assert (f.source / 'value.txt').read_text() == '2'
    return '本地契约夹具：真实候选 Eval 子进程、具名人审与隔离合入，失败保留返工；不代表真实客户或学生验收'


def learning_reuse_is_evidence_bound():
    with LocalDeliveryFixture() as f:
        source, asset = f.workflow_asset()
        assert not f.learning.recall(source['id'], 'value', trials=True)['matches']
        trial = f.item('value trial B')
        assert not f.learning.recall(trial['id'], 'value')['matches']
        f.prepare(trial, trials=True, choices=[f.choice(asset)])
        state = f.execute(trial)
        assert state['stage'] == 'review', state.get('error')
        assert not f.learning.view(trial['id'])['metrics']['reuse_passed']
        f.accept(trial)
        assert f.learning.view(trial['id'])['metrics']['reuse_passed'] == 1
        asset = f.learning.govern(source['id'], asset['id'], 'fixture-peer', 'publish', 'Independent trial passed')
        reuse = f.item('value reuse C')
        assert [m['id'] for m in f.learning.recall(reuse['id'], 'value')['matches']] == [asset['id']]
        f.value = 3
        f.prepare(reuse, choices=[f.choice(asset)])
        state = f.execute(reuse)
        assert state['stage'] == 'review', state.get('error')
        f.accept(reuse)
        assert f.learning.view(reuse['id'])['metrics']['reuse_passed'] == 1
        failed = f.item('value failed reuse D')
        f.value = -1
        f.prepare(failed, choices=[f.choice(asset)])
        state = f.execute(failed)
        assert state['stage'] == 'rework', state.get('error')
        assert f.learning.get(asset['id'])['state'] == 'revoked'
        assert f.learning.view(failed['id'])['metrics']['reuse_failed'] == 1
        assert not f.learning.recall(f.item('value after failure')['id'], 'value')['matches']
    return '本地跨事项契约夹具：A 来源、B 显式试用/Eval/具名验收后发布、C 默认复用通过、D 失败停用；保留来源和采用证据，不代表真实学习闭环'
