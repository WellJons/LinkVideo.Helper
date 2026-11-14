import unittest
from types import SimpleNamespace as NS
from desktop_v4.worker import Workspace, Rejected
from desktop_v4.operations import InputError
from unittest.mock import patch
from dataclasses import dataclass

class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.calls = []
        class Registry:
            @staticmethod
            def defaults():
                return [NS(host="trusted.example",country="Test")]
        row = NS(server="trusted.example", login="client", remote_address="10.0.0.1", ports=[10001], active_ports=[], disabled_ports=[], is_online=False, is_enabled=True, uptime="", last_logged_out="", password="not-for-renderer")
        report = NS(matches=[row],checked=1,total=1,errors=[])
        def verify(user,password,hosts):
            self.calls.append(hosts)
            if password!="valid":
                raise Rejected("Invalid")
            return NS(username=user)
        self.w = Workspace(Registry,verify,None,NS(search_login_all=lambda *a,**kw:report, search_port_all=lambda *a,**kw:report),lambda *a:a,lambda user:user=="AdminChats")
    def test_no_access_before_login(self):
        for method,args in [("servers",{}),("search",{"query":"client","mode":"login"}),("analyze",{"host":"trusted.example"})]:
            with self.assertRaises(Rejected):
                self.w.dispatch(method,args)
    def test_login_roles_and_projection(self):
        self.assertEqual(self.w.dispatch("login",{"username":"AdminChats","password":"valid"})["role"],"employee")
        self.assertEqual(self.calls,[["trusted.example"]])
        self.assertNotIn("password",self.w.dispatch("search",{"query":"client","mode":"login"})["matches"][0])
        self.assertEqual(self.w.dispatch("search",{"query":"10001","mode":"port"})["checked"],1)
        with self.assertRaises(Rejected):
            self.w.dispatch("analyze",{"host":"untrusted.example"})
        with self.assertRaises(Rejected):
            self.w.dispatch("delete_client",{})
        with self.assertRaises(Rejected):
            self.w.dispatch("login",{"username":"AdminChats","password":"invalid"})
        self.assertFalse(self.w.dispatch("status",{})["authenticated"])
    def test_operational_permissions(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        with patch.dict("os.environ",{"LV4_ENABLE_WRITES":"1"}):
            with self.assertRaises(InputError): self.w.dispatch("lv_preview",{"host":"trusted.example","action":"install"})
            with self.assertRaises(InputError): self.w.dispatch("vpn_preview",{"host":"trusted.example","login":"client","action":"disable"})
            plan=self.w.dispatch("create_preview",{"host":"trusted.example","login":"test","ports":2,"count":1})
            self.assertIn("token",plan)
            with self.assertRaises(InputError): self.w.dispatch("commit",{"token":"wrong"})
            with self.assertRaises(InputError): self.w.dispatch("commit",{"token":plan["token"]})
        with patch.dict("os.environ",{"LV4_ENABLE_WRITES":"0"}):
            with self.assertRaises(InputError): self.w.dispatch("create_preview",{"host":"trusted.example","login":"test","ports":2,"count":1})
    def test_archive_fallback_and_secret_projection(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        calls=[]
        self.w.ops.extra.cloud=NS(request=lambda *a,**k:(calls.append(a) or [{"server":"trusted.example","login":"old","password":"NEVER","password_saved":True,"ports":[{"external_port":12000,"internal_port":80}]}]))
        self.w.dispatch("search",{"query":"client","mode":"login"})
        self.assertEqual(calls,[])
        self.w.search.search_login_all=lambda *a,**k:NS(matches=[],checked=1,total=1,errors=[])
        result=self.w.dispatch("search",{"query":"old","mode":"login"})
        self.assertEqual(calls,[("GET","/v1/deleted/search")])
        self.assertNotIn("password",result["deleted"][0])
        self.assertEqual(result["deleted"][0]["ports"],["12000 → 80"])
        with self.assertRaises(InputError): self.w.dispatch("cloud_restore_preview",{"host":"trusted.example","login":"old"})
    def test_separate_admin_auth(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        self.assertEqual(self.w.dispatch("admin_status",{}),{"username":"","b2o":False,"crm":False})
        with self.assertRaises(InputError): self.w.dispatch("billing_search",{"query":"client"})
        with self.assertRaises(InputError): self.w.dispatch("archive_discover",{"camera":"123"})
if __name__=="__main__":
    unittest.main()
