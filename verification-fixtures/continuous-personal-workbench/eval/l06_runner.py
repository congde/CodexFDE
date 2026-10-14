"""L06 teaching starter: repair aggregation without changing the frozen tests."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def run(entries, *, suite='all', names=None, report_path=None):
    if suite not in ('all', 'blocking', 'observing'):
        raise ValueError('unknown suite')
    registry = [name for name, level, fn in entries]
    if len(registry) != len(set(registry)):
        raise ValueError('duplicate names')
    if any(level not in ('blocking', 'observing') for _, level, _ in entries):
        raise ValueError('invalid level')
    requested = list(dict.fromkeys(registry if names is None else names))
    selected = [(n, level, fn) for n, level, fn in entries
                if n in requested and (suite == 'all' or level == suite)]
    if not selected or set(requested) != {n for n, _, _ in selected}:
        raise ValueError('empty, unknown or mismatched selection')
    results = []
    for name, level, fn in selected:
        start = time.perf_counter()
        try:
            evidence = str(fn()); passed = True; error = None
        except Exception as exc:
            evidence = str(exc); passed = False
            error = {'type': type(exc).__name__, 'message': str(exc)}
        results.append(dict(name=name, level=level, passed=passed,
                            duration_ms=round((time.perf_counter()-start)*1000),
                            evidence=evidence, error=error))
    # L06-GAP: this starter loses blocking failures. Implement the agreed rule.
    blocking_failed = sum(not r['passed'] and r['level']=='blocking' for r in results)
    summary = dict(total=len(results), passed=sum(r['passed'] for r in results),
                   blocking_failed=blocking_failed,
                   observing_failed=sum(not r['passed'] and r['level']=='observing' for r in results),
                   decision='block' if blocking_failed else 'pass')
    report = dict(schema_version='1.0', suite=suite, requested_cases=requested,
                  generated_at=datetime.now(timezone.utc).isoformat(), results=results, summary=summary)
    if report_path is not None:
        target = Path(report_path); target.parent.mkdir(parents=True, exist_ok=True)
        # A distinct report belongs to one invocation; never overwrite prior evidence.
        with target.open('x', encoding='utf8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report, (1 if blocking_failed else 0)
