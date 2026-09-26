"""Observe real Graph control with explicitly synthetic Eval reports.

No Codex execution, real human approval, or personal workspace mutation occurs.
Temporary reviewer strings are teaching inputs, never authenticated identities.
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent import graph
from agent.repair import build_repair_task

MODES = ("demo", "wait", "resume", "approve", "reject", "reject-at-limit",
         "blocking-failure", "eval-exception", "empty-report", "unknown-state",
         "malformed-state", "save-error", "stale-approval", "approval-without-wait",
         "terminal-rerun", "unchecked-move")

def report(passed=True):
    return {"suite": "blocking", "results": [{"name": "teaching_case", "level": "blocking",
            "passed": passed, "evidence": "SYNTHETIC report for Graph control"}],
            "summary": {"total": 1, "passed": int(passed), "blocking_failed": int(not passed),
                        "observing_failed": 0, "decision": "pass" if passed else "block"}}

def observe(mode):
    with tempfile.TemporaryDirectory(prefix="l12-graph-") as folder:
        base=Path(folder)
        state_file=base/'state.json'
        calls=[]
        def suite(*args,**kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            if mode=="eval-exception":
                raise RuntimeError("TEACHING Eval producer exception")
            if mode=="empty-report":
                return {"results": [], "summary": {"total": 0, "blocking_failed": 0, "decision": "pass"}}
            return report(mode!="blocking-failure")
        def repair(data,_requested_path):
            return build_repair_task(data,base/f'repair-{len(calls)}.json')
        observed={"mode":mode,"boundary":"Synthetic reports and reviewer input; real Graph; no developer execution or authenticated approval."}
        with patch.object(graph,"run_suite",suite), patch.object(graph,"build_repair_task",repair):
            if mode=="unchecked-move":
                s=graph.DeliveryState()
                s.move("completed","TEACHING direct move without transition validation")
                observed["result"]=graph._result(s)
                assert s.state=="completed" and not calls
            elif mode in {"malformed-state","save-error"}:
                if mode=="malformed-state":
                    state_file.write_text('{broken',encoding='utf-8')
                else:
                    block=base/'parent-is-a-file'
                    block.write_text('occupied',encoding='utf-8')
                    state_file=block/'state.json'
                try:
                    graph.run_graph(state_file=state_file)
                except (json.JSONDecodeError,FileExistsError) as exc:
                    observed["uncaught"]={"type":type(exc).__name__,"message":str(exc)}
                else:
                    raise AssertionError("expected state I/O exception to escape")
            elif mode=="unknown-state":
                state_file.write_text(json.dumps({"status":"unrecognized"}),encoding='utf-8')
                observed["result"]=graph.run_graph(state_file=state_file)
                assert observed["result"]["status"]=="failed" and not calls
            elif mode in {"resume","approve","reject","reject-at-limit","stale-approval","terminal-rerun"}:
                limit=1 if mode=="reject-at-limit" else 3
                candidate=base/'candidate.txt'
                candidate.write_text('candidate X',encoding='utf-8')
                observed["initial"]=graph.run_graph(max_rounds=limit,state_file=state_file)
                assert observed["initial"]["status"]=="awaiting_human_review"
                before_calls=len(calls)
                if mode=="stale-approval":
                    candidate.write_text('candidate Y: changed after test',encoding='utf-8')
                decision="reject" if mode in {"reject","reject-at-limit"} else None if mode=="resume" else "approve"
                result=graph.run_graph(max_rounds=limit,state_file=state_file,
                                       review_decision=decision,reviewer="TEACHING reviewer" if decision else "")
                expected={"resume":"awaiting_human_review","reject":"awaiting_human_review",
                          "reject-at-limit":"stopped"}.get(mode,"completed")
                assert result["status"]==expected
                observed["result"]=result
                observed["additional_eval_calls"]=len(calls)-before_calls
                if mode in {"resume","approve","stale-approval","reject-at-limit","terminal-rerun"}:
                    assert observed["additional_eval_calls"]==0
                if mode=="reject-at-limit":
                    assert result["rounds"]==2
                if mode=="stale-approval":
                    observed["candidate_observation"]=candidate.read_text(encoding='utf-8')
                    observed["judgment"]="No candidate identity binding or fresh Eval occurred; marker is a teaching file, not a product repair."
                if mode=="terminal-rerun":
                    again=graph.run_graph(state_file=state_file)
                    assert again==result and len(calls)==before_calls
                    observed["rerun_unchanged"]=True
            else:
                kwargs={} if mode=="demo" else {"state_file":state_file}
                if mode=="approval-without-wait":
                    kwargs.update(review_decision="approve",reviewer="TEACHING reviewer")
                result=graph.run_graph(max_rounds=3,**kwargs)
                expected={"demo":"completed","blocking-failure":"stopped","eval-exception":"failed"}.get(mode,"awaiting_human_review")
                assert result["status"]==expected
                if mode=="blocking-failure":
                    assert result["rounds"]==3 and len(calls)==3
                if mode=="approval-without-wait":
                    assert result["reviewer"] is None
                observed["result"]=result
        observed["eval_calls"]=len(calls)
        if state_file.is_file():
            observed["persisted_text"]=state_file.read_text(encoding='utf-8')
        return observed

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=MODES)
    args=parser.parse_args()
    print(json.dumps(observe(args.mode),ensure_ascii=False,indent=2))
