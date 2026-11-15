from __future__ import annotations
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import patch
from desktop_v4.operations import InputError
from desktop_v4.worker import Workspace

HOST="trusted.example"
class FakeService:
    def __init__(self):
        self.data={
            "secrets":[{".id":"*1","name":"one","profile":"one","remote-address":"172.16.0.1","password":"hidden","disabled":"yes","last-logged-out":"2025-01-01"}],
            "profiles":[{".id":"*A","name":"one","remote-address":"172.16.0.1"}],
            "nat_rules":[{".id":"*N","chain":"dstnat","action":"dst-nat","protocol":"tcp","dst-port":"10001","to-ports":"10001","to-addresses":"172.16.0.1","comment":"one","bytes":"5","packets":"7"}],
            "actives":[]}
        self.calls=[]
        self.partial=False
    @staticmethod
    def _normalize_ip(ip): return str(ip or "").strip().split("/")[0]
    @classmethod
    def _get_client_remote_address(cls,secret,profile): return cls._normalize_ip((secret or {}).get("remote-address") or (profile or {}).get("remote-address"))
    @staticmethod
    def _get_rule_remote(rule): return rule.get("to-addresses","")
    def fetch_config_snapshot(self,*args): return copy.deepcopy(self.data)
    def fetch_client_snapshot(self,*args): return copy.deepcopy(self.data)
    def get_client(self,host,creds,login,**kwargs):
        row=next((r for r in self.data["secrets"] if r["name"]==login),None)
        if not row:return None
        return NS(login=login,remote_address=row["remote-address"],secret_id=row[".id"],ports=[10001],disabled_ports=[],is_online=bool(self.data["actives"]),is_enabled=False,last_logged_out=row["last-logged-out"])
    def delete_client(self,host,creds,login):
        self.calls.append(login)
        self.data["secrets"]=[];self.data["profiles"]=[]
        if not self.partial:self.data["nat_rules"]=[]

class DeleteRecoveryTests(unittest.TestCase):
    def setUp(self):
        service=FakeService()
        registry=NS(defaults=lambda:[NS(host=HOST,country="Test")])
        self.w=Workspace(registry,lambda u,p,h:NS(username=u),service,None,lambda *a:a,lambda u:u=="AdminChats")
        self.w.dispatch("login",{"username":"admin","password":"test"})
        self.svc=service
        self.extra=self.w.ops.extra
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.flags=patch.dict("os.environ",{"LV4_ENABLE_WRITES":"1","LV4_ENABLE_DESTRUCTIVE":"1"});self.flags.start();self.addCleanup(self.flags.stop)
    def setup_backup(self,valid=True,change=False,tamper=False):
        root=Path(self.tmp.name);filename=root/"test_FULL.json"
        def backup(*a,**kw):
            sec={k:copy.deepcopy(self.svc.data[v]) for k,v in {"ppp_secrets":"secrets","ppp_profiles":"profiles","ppp_active_snapshot":"actives","firewall_nat":"nat_rules"}.items()}
            filename.write_text(json.dumps({"server":HOST,"format_version":1,"sections":sec}),encoding="utf-8")
            manifest={"servers_ok":[HOST],"files":[{"name":filename.name,"sha256":hashlib.sha256(filename.read_bytes()).hexdigest()}]}
            (root/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
            if tamper: filename.write_text("tampered",encoding="utf-8")
            if change:self.svc.data["nat_rules"][0]["to-ports"]="10002"
            return NS(ok=valid,json_path=filename)
        self.extra.backup_service=lambda:NS(backup_one=backup)
    def revision(self):return self.extra.delete_revision(HOST,"one")
    def test_counters_do_not_invalidate_revision(self):
        rev=self.revision()
        self.svc.data["nat_rules"][0]["bytes"]="999"
        self.assertEqual(self.revision(),rev)
        self.svc.data["nat_rules"][0]["to-ports"]="20000"
        self.assertNotEqual(self.revision(),rev)
    def test_active_shared_or_foreign_rule_fails_closed(self):
        baseline=copy.deepcopy(self.svc.data)
        for key,row in [("actives",{"name":"one","address":"172.16.0.1"}),("profiles",{".id":"*B","name":"other","remote-address":"172.16.0.1"}),("nat_rules",{".id":"*Z","comment":"other","to-addresses":"172.16.0.1","chain":"dstnat","action":"dst-nat"})]:
            self.svc.data=copy.deepcopy(baseline);self.svc.data[key].append(row)
            with self.assertRaises(InputError):self.revision()
        self.assertEqual(self.svc.calls,[])
    def test_backup_failure_or_changed_config_does_not_delete(self):
        for kwargs in [{"valid":False},{"tamper":True},{"change":True}]:
            original=copy.deepcopy(self.svc.data);rev=self.revision();self.setup_backup(**kwargs)
            with self.assertRaises(InputError):self.extra.confirmed_delete(HOST,"one",rev,Event(),lambda *_:None)
            self.svc.data=original
        self.assertEqual(self.svc.calls,[])
    def test_confirmed_and_partial_deletion(self):
        self.setup_backup();rev=self.revision();self.svc.partial=True
        with self.assertRaises(InputError):self.extra.confirmed_delete(HOST,"one",rev,Event(),lambda *_:None)
        self.assertEqual(self.svc.calls,["one"])
        self.svc.data=FakeService().data;self.svc.partial=False
        rev=self.revision()
        self.assertEqual(self.extra.confirmed_delete(HOST,"one",rev,Event(),lambda *_:None),{"deleted":True,"server":HOST,"login":"one"})
        self.assertEqual(self.svc.calls,["one","one"])
    def test_bulk_requires_phrase_and_known_activity(self):
        ops=self.w.ops
        ops.inactive_records[(HOST,"one")]=NS(last_logged_out_dt=datetime(2025,1,1),last_logged_out="2025-01-01",remote_address="172.16.0.1",is_enabled=False)
        req={"action":"delete","targets":[{"host":HOST,"login":"one"}],"confirm":"wrong"}
        with self.assertRaises(InputError):self.w.dispatch("inactive_batch_preview",req)
        req["confirm"]="УДАЛИТЬ 1"
        plan=self.w.dispatch("inactive_batch_preview",req)
        self.assertEqual(self.svc.calls,[])
        self.setup_backup()
        out=self.w.dispatch("commit",{"token":plan["token"]})
        self.assertEqual(out["items"],[{"server":HOST,"login":"one","status":"confirmed"}])
        self.assertEqual(self.svc.calls,["one"])
    def test_bulk_stops_after_uncertain_result(self):
        self.setup_backup();self.svc.partial=True
        out=self.w.ops.execute("inactive_batch",("delete",((HOST,"one",self.revision()),(HOST,"two","unverified"))),None,Event(),lambda *_:None)
        self.assertEqual([x["status"] for x in out["items"]],["unknown","not_attempted"])
        self.assertEqual(self.svc.calls,["one"])
