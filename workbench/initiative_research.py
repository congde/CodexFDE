"""Read-only Codex research for a business initiative, with inspectable invocation."""
import json
import time
from pathlib import Path

from .daily_delivery import manifest
from .execution import CodexExecutionRunner, normalize_write_scope
from .codex_options import headless_options
from .research_context import source_context


class InitiativeResearch:
    def __call__(self, repository, runtime, folder, context, progress):
        repository, folder = Path(repository), Path(folder)
        folder.mkdir(parents=True, exist_ok=False)
        runner = CodexExecutionRunner(repository, runtime)
        capability = runner.capabilities()
        if not capability['codex_available']:
            raise ValueError(capability['reason'])
        array = {'type': 'array', 'items': {'type': 'string'}}
        fields = {key: array for key in ('findings', 'questions', 'non_goals', 'acceptance', 'write_scope', 'steps', 'sources',
                                         'users', 'scope', 'test_plan')}
        fields['goal'] = {'type': 'string'}
        schema = {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}
        schema_path, output = folder / 'schema.json', folder / 'proposal.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        before = manifest(repository, runtime)
        (folder / 'source-manifest.json').write_text(json.dumps(before), encoding='utf-8')
        source = source_context(repository, before, context)
        (folder / 'source-context.json').write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
        prompt = (
            '你是工作台中的研发调研伙伴。只读检查当前项目源码，不修改文件、不执行业务操作。'
            '先阅读 AGENTS.md，再查明事项涉及的现有实现。向业务用户解释发现，区分已有能力和新增需求。'
            '根据原话及完整讨论记录，提出最多三个真正需要用户决定的问题；用户已经回答的不要重复问。'
            '信息足够时 questions 返回空列表，提出范围受控的本期目标、非目标、可验证的验收条件、实施步骤。'
            '人员安排与产品口径分开处理：独立复验者、最终人工验收人尚未指定，不阻止整理产品与技术方案。'
            '用户明确人员待定时，不要反复追问姓名；在实施步骤注明确认方案及作出人工接受决定前落实相应真实人员与职责。'
            '不得将待确认、操作人或 AI 自动视为已指定的独立复验者或最终验收人。'
            'users 写实际使用者及使用场景；scope 写本期产品行为范围，不要用文件路径替代业务范围。'
            'acceptance 写给定条件、操作、预期及失败后不变状态；test_plan 单独写如何构造数据、'
            '调用真实入口、读取结果和复验异常路径，关联对应验收条目，不能只重复验收文字。'
            '本轮调研只读是调研进程的权限，不是产品非目标；不要把“不修改代码、不执行测试”写入待开发需求。'
            '用户没有确认的产品口径应保留在 questions，不要替用户决定或编造效率提升数据。'
            'write_scope 是你经代码调研建议修改的明确相对路径（包含必要测试），不要让用户猜路径。'
            'sources 必须是你实际查看、当前存在的源文件相对路径；findings 引用这些文件说明依据。'
            '不要编造调研结果、测试结果或用户决定；当前没有报销功能时明确说明，不能冒称优化已存在流程。'
            '工作台已直接读取并附上真实源码索引与片段。优先分析这些内容，不要重复读取已提供文件；'
            '仅在证据确实不足时补充只读查询，明确区分片段中未见与全库确认不存在。'
            '返回指定 JSON。上下文：\n' + json.dumps(context, ensure_ascii=False)
            + '\n工作台采集的源码资料：\n' + json.dumps(source, ensure_ascii=False))
        command = [runner.executable, 'exec', *headless_options(), '--json', '--sandbox', 'read-only', '--ephemeral',
                   '--output-schema', str(schema_path), '--output-last-message', str(output),
                   '--cd', str(repository), '-']
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        (folder / 'invocation.json').write_text(json.dumps({'command': command, 'workspace': str(repository),
            'sandbox': 'read-only'}, ensure_ascii=False, indent=2), encoding='utf-8')
        progress(json.dumps({'type': 'research.invocation', 'invocation': {
            'command': command, 'workspace': str(repository), 'prompt': prompt, 'artifacts': str(folder)}}, ensure_ascii=False))
        def line(value):
            with (folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(value + '\n')
            progress(value)
        completed = runner._run_codex_streaming(command, prompt, 900, line, time.monotonic())
        (folder / 'process.json').write_text(json.dumps({'returncode': completed.returncode,
            'stderr': completed.stderr}, ensure_ascii=False), encoding='utf-8')
        after = manifest(repository, runtime)
        changed_sources = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        if completed.returncode != 0:
            raise RuntimeError(f'Codex 调研未完成（退出码 {completed.returncode}），过程与失败记录已保留')
        if not output.is_file():
            raise ValueError('Codex 未返回结构化方案，不能据此发起执行')
        proposal = json.loads(output.read_text(encoding='utf-8'))
        if not isinstance(proposal, dict) or set(proposal) != set(fields):
            raise ValueError('调研方案结构不完整')
        for key in fields:
            value = proposal[key]
            if key == 'goal':
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('方案缺少目标')
            elif not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError('方案字段无效：' + key)
        proposal['write_scope'] = normalize_write_scope(proposal['write_scope'])
        if len(proposal['questions']) > 3:
            raise ValueError('调研问题超过本轮上限，请重新整理')
        if not proposal['sources']:
            raise ValueError('方案没有可核对的源码依据：sources 为空，请重新调研')
        missing_sources = [p for p in proposal['sources'] if p not in before]
        if missing_sources:
            raise ValueError('方案没有可核对的源码依据：以下路径未纳入当前项目源码快照：'
                             + '、'.join(missing_sources)
                             + '。请核对文件是否存在或被 Git 忽略，再重新调研')
        if not proposal['questions'] and any(not proposal[key] for key in
                ('users', 'scope', 'write_scope', 'acceptance', 'steps', 'test_plan')):
            raise ValueError('可执行方案缺少使用者、产品范围、验收、实施步骤或测试计划')
        return {'proposal': proposal, 'source_manifest': before, 'changed_sources': changed_sources,
                'invocation': {'command': command,
                'workspace': str(repository), 'prompt': prompt, 'returncode': completed.returncode,
                'artifacts': str(folder)}}
