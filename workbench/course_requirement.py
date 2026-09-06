"""Freeze the L03-to-L04 handoff or a live L15/L16 requirement within course limits."""
import hashlib
import re
from .spec import parse_spec


def freeze_requirement_spec(lesson, text, cases, *, required=False):
    if text is None:
        if required:
            raise ValueError('L15/L16 代码执行必须提供本次六段式需求 Spec')
        return None
    if not lesson.dynamic_eval_required and lesson.number != 4:
        raise ValueError('自定义课程需求 Spec 仅用于 L04、L15/L16')
    if not isinstance(text, str) or not text.strip() or len(text) > 40000:
        raise ValueError('本次需求 Spec 必须是非空文本，最多 40000 字符')
    spec = parse_spec(text)
    for case in cases:
        if not re.search(r'(?<![A-Za-z0-9_])' + re.escape(case) + r'(?![A-Za-z0-9_])', spec.acceptance):
            raise ValueError('本次 Spec 的验收用例须明确关联：' + case)
    fields = [('来源',spec.source),('目标',spec.goal),('非目标',spec.non_goals),
              ('约束',spec.constraints + '\n\n课程执行写集：' + '、'.join(lesson.write_scope) +
               '\n本次需求不得扩大课程写集；先确认新增验收失败，范围内实现后独立复验。'),
              ('验收用例',spec.acceptance),('完成定义',spec.done +
               '\n\n课程通过标准：\n' + '\n'.join('- '+item for item in lesson.acceptance) +
               '\n检查通过仍须具名人审，不自动接受或合并。')]
    frozen = '# 本次课程需求\n\n' + '\n\n'.join('## '+name+'\n\n'+value for name,value in fields) + '\n'
    parsed = parse_spec(frozen)
    return {'text':frozen,'sha256':hashlib.sha256(frozen.encode('utf-8')).hexdigest(),
            'goal':parsed.goal,'acceptance':parsed.acceptance,'non_goals':parsed.non_goals}
