"""Check real purchase approval/receipt using temporary SQLite data and Harness.

status-write-failure injects a database fault; collision uses a real reused key.
Reports reveal current behavior, including genuine failing invariants.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from workbench.course_experiments import ensure_product_process
ensure_product_process(__file__)
from flowerp.service import ERPService
from flowerp.store import ERPStore
from flowerp.models import ApprovalRequired,InvalidTransition,OrderLine
from eval import harness

MODES=("approved","unapproved","blank-reviewer","rejected","replay","different-key",
       "status-write-failure","recovery-same-key","key-collision")

def check(mode, states_path=None):
    with tempfile.TemporaryDirectory(prefix="l12-approval-") as folder:
        store=ERPStore(Path(folder)/'purchase.db')
        service=ERPService(store)
        service.add_product("A","商品 A",100)
        service.receive_stock("A",10,"opening")
        service.create_order("其他客户",[OrderLine("A",2,100)],"OTHER")
        service.reserve_order("OTHER")
        service.propose_purchase("A",3,"另一项需求","PR-OTHER")
        service.propose_purchase("A",7,"补货","PR-TARGET")
        tables=("purchase_requests","stock","inventory_events","sales_orders","sales_order_lines")
        def snapshot():
            return {t:store.rows(f"SELECT * FROM {t} ORDER BY 1") for t in tables}
        phases=[]
        def record(phase):
            phases.append({"phase":phase,"tables":snapshot()})
        try:
            before=snapshot()
            record("before_approval")
            if mode=="blank-reviewer":
                try: service.approve_purchase("PR-TARGET","  ")
                except ValueError: pass
                else: raise AssertionError("blank reviewer accepted")
                assert snapshot()==before
                return "blank-reviewer: rejected; five tables unchanged"
            if mode=="rejected":
                service.reject_purchase("PR-TARGET","TEACHING business reviewer")
            elif mode!="unapproved":
                approved=service.approve_purchase("PR-TARGET","TEACHING business reviewer")
                assert approved["approved_by"]=="TEACHING business reviewer" and approved["status"]=="approved"
                assert all(snapshot()[t]==before[t] for t in tables if t!="purchase_requests")
            before_receive=snapshot()
            record("before_receive")
            if mode in {"unapproved","rejected"}:
                try: service.receive_purchase("PR-TARGET","receipt:target")
                except ApprovalRequired: pass
                else: raise AssertionError("receipt without approval accepted")
                assert snapshot()==before_receive
                return f"{mode}: ApprovalRequired; five tables unchanged"
            if mode in {"status-write-failure","recovery-same-key"}:
                with store.connect() as conn:
                    conn.execute("CREATE TRIGGER fail_target_status BEFORE UPDATE OF status ON purchase_requests WHEN NEW.id='PR-TARGET' AND NEW.status='received' BEGIN SELECT RAISE(ABORT,'TEACHING receipt status failure'); END")
                try: service.receive_purchase("PR-TARGET","receipt:target")
                except sqlite3.IntegrityError: pass
                else: raise AssertionError("injected status failure did not occur")
                record("after_status_write_failure")
                current=service.product("A")
                if mode=="status-write-failure":
                    assert snapshot()==before_receive,(f"receipt partially committed: status={service.purchase('PR-TARGET')['status']}; on_hand={current['on_hand']}; expected unchanged 10")
                    return "status write failed atomically"
                assert current["on_hand"]==17 and service.purchase("PR-TARGET")["status"]=="approved"
                with store.connect() as conn:
                    conn.execute("DROP TRIGGER fail_target_status")
            key="opening" if mode=="key-collision" else "receipt:target"
            result=service.receive_purchase("PR-TARGET",key)
            record("after_receive")
            if mode=="replay":
                after_first=snapshot()
                replay=service.receive_purchase("PR-TARGET",key)
                assert snapshot()==after_first and replay["stock"]["idempotent_replay"]
            if mode=="different-key":
                after_first=snapshot()
                try: service.receive_purchase("PR-TARGET","receipt:another")
                except InvalidTransition: pass
                else: raise AssertionError("second receipt with another key accepted")
                assert snapshot()==after_first
            item=service.product("A")
            assert result["purchase"]["status"]=="received"
            assert (item["on_hand"],item["reserved"],item["available"])==(17,2,15),f"receipt state mismatch: {item['on_hand']}/{item['reserved']}/{item['available']}"
            events=store.rows("SELECT * FROM inventory_events WHERE reference='PR-TARGET' AND event_type='receive'")
            assert len(events)==1 and events[0]["quantity"]==7
            final=snapshot()
            assert final["sales_orders"]==before["sales_orders"]
            assert final["sales_order_lines"]==before["sales_order_lines"]
            assert service.purchase("PR-OTHER")==next(p for p in before["purchase_requests"] if p["id"]=="PR-OTHER")
            return f"{mode}: received; 17/2/15; one +7 receipt; OTHER and PR-OTHER preserved; teaching reviewer input is not authentication"
        finally:
            record("final")
            if states_path:
                states_path=Path(states_path)
                states_path.parent.mkdir(parents=True,exist_ok=True)
                payload={"mode":mode,"boundary":"Real service with temporary database; teaching reviewer and injected fault; no authenticated approval.","phases":phases}
                with states_path.open("x",encoding="utf-8") as stream:
                    json.dump(payload,stream,ensure_ascii=False,indent=2)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=MODES)
    parser.add_argument("--report-path",required=True)
    args=parser.parse_args()
    states_path=Path(args.report_path).with_suffix(".states.json")
    if Path(args.report_path).exists() or states_path.exists():
        parser.error("use a new report path; preserve previous evidence")
    with patch.object(harness,"EVALS",[("l12_teaching_approval","blocking",lambda:check(args.mode,states_path))]), \
         patch.object(sys,"argv",["harness","--suite","blocking","--case","l12_teaching_approval","--report-path",args.report_path]):
        raise SystemExit(harness.main())
