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
    def test_nonsecret_archive_settings(self):
        from desktop_v4.operations import MemorySettings
        saved={}
        disk=NS(value=lambda key,default,*args:saved.get(key,default),setValue=lambda key,val:saved.update({key:val}),sync=lambda:None)
        settings=MemorySettings(NS(registry=NS(settings=disk)))
        settings.setValue("archive/extra_cluster_enabled/provzor",True)
        settings.setValue("b2o/token","secret")
        self.assertTrue(settings.value("archive/extra_cluster_enabled/provzor",False,bool))
        self.assertNotIn("b2o/token",saved)
        settings.remove("b2o")
        self.assertFalse(settings.value("b2o/token",""))
    def test_rotation_wire_value_and_permission(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        self.w.ops.camera=NS(serial_number="cam")
        with patch.dict("os.environ",{"LV4_ENABLE_WRITES":"1"}):
            plan=self.w.dispatch("agent_preview",{"action":"rotation","value":3})
            self.assertIn("180",plan["label"])
            with self.assertRaises(InputError):
                self.w.dispatch("agent_preview",{"action":"rotation","value":90})
    def test_traffic_projection(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        self.w.service=NS(get_client=lambda *a,**kw:NS(is_online=True,rx_bytes=100,tx_bytes=20,uptime="1h",interface_name="l2tp",password="not-exported"))
        out=self.w.dispatch("traffic_sample",{"host":"trusted.example","login":"test"})
        self.assertEqual((out["rx"],out["tx"]),(100,20))
        self.assertNotIn("password",out)
        with self.assertRaises(InputError): self.w.dispatch("traffic_sample",{"host":"evil.example","login":"test"})
    def test_inactive_batch_employee_denied(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        with patch.dict("os.environ",{"LV4_ENABLE_WRITES":"1"}):
            with self.assertRaises(InputError): self.w.dispatch("inactive_batch_preview",{"action":"disable","targets":[]})
    def test_create_custom_pool_validation(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        with patch.dict("os.environ",{"LV4_ENABLE_WRITES":"1"}):
            plan=self.w.dispatch("create_preview",{"host":"trusted.example","login":"test","ports":2,"count":1,"start_ip":"172.18.1.1"})
            self.assertIn("172.18.1.1",plan["label"])
            with self.assertRaises(InputError): self.w.dispatch("create_preview",{"host":"trusted.example","login":"test","ports":2,"count":1,"start_ip":"127.0.0.1"})
    def test_camera_lookup_requires_independent_b2o(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        with self.assertRaises(InputError): self.w.dispatch("camera_lookup",{"query":"client"})
    def test_separate_admin_auth(self):
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        self.assertEqual(self.w.dispatch("admin_status",{}),{"username":"","b2o":False,"crm":False})
        with self.assertRaises(InputError): self.w.dispatch("billing_search",{"query":"client"})
        with self.assertRaises(InputError): self.w.dispatch("archive_discover",{"camera":"123"})
if __name__=="__main__":
    unittest.main()
