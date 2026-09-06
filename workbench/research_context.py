"""Bounded current-source excerpts for read-only initiative research."""
import hashlib
import json
import re
from pathlib import Path


def source_context(repository, fingerprints, context):
    query = json.dumps(context.get('initiative', {}), ensure_ascii=False).lower()
    discussion = context.get('discussion') or []
    query += '\n' + '\n'.join(m.get('text', '') for m in discussion if m.get('role') == 'user').lower()
    terms = set(re.findall(r'[a-z][a-z_]{2,}', query)) - {'flowerp', 'title', 'goal', 'raw_signal', 'acceptance', 'non_goals'}
    for words, aliases in [
        (('财务', '报销', '付款', '费用'), ('finance', 'accounting', 'expense', 'reimburse', 'payment', 'invoice')),
        (('库存', '仓库', '盘点'), ('inventory', 'warehouse', 'stock', 'count')),
        (('订单', '销售'), ('order', 'sales', 'customer')),
        (('采购', '供应商'), ('purchase', 'purchasing', 'supplier')),
        (('审批', '权限'), ('approval', 'approve', 'permission', 'auth')),
    ]:
        if any(word in query for word in words):
            terms.update(aliases)
    root = Path(repository)
    ranked = []
    for name in fingerprints:
        if name != 'AGENTS.md' and not name.startswith(('flowerp/', 'web/', 'workbench/', 'workbench_web/', 'tests/', 'eval/')):
            continue
        if Path(name).suffix not in {'.py', '.js', '.html', '.md'}:
            continue
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != fingerprints[name]:
            raise ValueError('整理调研资料期间源码变化，请重新调研')
        text = raw.decode('utf-8', errors='replace')
        score = sum(30 * (term in name.lower()) + min(10, text.lower().count(term)) for term in terms)
        if name == 'AGENTS.md':
            score = 100000
        if score:
            ranked.append((score, name, text))
    files = []
    for _, name, text in sorted(ranked, key=lambda row: (-row[0], row[1]))[:12]:
        lines = text.splitlines()
        if len(text) <= 9000 or name == 'AGENTS.md':
            excerpts = [{'start_line': 1, 'text': text}]
            truncated = False
        else:
            selected = set(range(min(20, len(lines))))
            for i, line in enumerate(lines):
                if any(term in line.lower() for term in terms) and len(selected) < 140:
                    selected.update(range(max(0, i - 3), min(len(lines), i + 12)))
            excerpts = [{'start_line': i + 1, 'text': lines[i]} for i in sorted(selected)]
            truncated = True
        files.append({'path': name, 'sha256': fingerprints[name], 'truncated': truncated, 'excerpts': excerpts})
    return {'notice': '工作台直接读取当前源码；仅列出的片段已提供，不能据截断片段断言整个项目不存在某能力。',
            'source_index': sorted(fingerprints), 'files': files}
