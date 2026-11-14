import unittest
from types import SimpleNamespace as NS
from desktop_v4.worker import Workspace, Rejected

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
        self.w = Workspace(Registry,verify,None,NS(search_login_all=lambda *a:report, search_port_all=lambda *a:report),lambda *a:a,lambda user:user=="AdminChats")
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
if __name__=="__main__":
    unittest.main()
