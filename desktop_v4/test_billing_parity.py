import unittest
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import patch
from desktop_v4.test_worker import TestWorkspace
from desktop_v4.operations import InputError
from linkvideo_vpn_helper.services.billing_catalog import TariffCatalogEntry,BillingCountry,AllTariffsSnapshot

class BillingParityTests(unittest.TestCase):
    def setUp(self):
        fixture=TestWorkspace();fixture.setUp();self.w=fixture.w
        self.w.dispatch("login",{"username":"AdminChats","password":"valid"})
        self.ops=self.w.ops
    def test_catalog_filters_match_release(self):
        rows=(TariffCatalogEntry("1","30 дней 4 Мбит/с","Казахстан","ФЛ",Decimal("1200"),test_days=7),
              TariffCatalogEntry("2","7 дней 2 Мбит/с","Казахстан","ЮЛ",Decimal("400")),
              TariffCatalogEntry("3","Monitor","Казахстан","ЮЛ",Decimal("500"),test_days=30),
              TariffCatalogEntry("4","30 дней 4 Мбит/с BYN","Казахстан","ФЛ",Decimal("100")),
              TariffCatalogEntry("5","30 дней 4 Мбит/с","Беларусь","ФЛ",Decimal("10")))
        country=BillingCountry("1","KZ","Казахстан","KZT","")
        catalog=AllTariffsSnapshot(rows,(country,))
        self.ops.billing=NS(all_tariffs=lambda:catalog)
        initial=self.w.dispatch("billing_catalog",{"scope":"all"})
        self.assertEqual(initial["total"],5)
        result=self.w.dispatch("billing_filter",{"country":"Казахстан","days":30,"bitrate":4})
        self.assertEqual([x["id"] for x in result["tariffs"]],["1"])
        self.assertEqual(result["tariffs"][0]["currency"],"KZT")
        self.assertEqual(result["tariffs"][0]["archive_days"],30)
    def test_retry_only_requested_session(self):
        ops=self.ops;old=NS(token=lambda:"b2o-token")
        ops.archive=NS(b2o=old);ops.b2o_ready=True;ops.admin_name="corp"
        ops.admin_credentials=ops.work_admin_credentials=NS(username="corp",password="valid")
        transport=NS(close=lambda:None)
        with patch("linkvideo_vpn_helper.services.billing_login_probe.check_billing_login",return_value=NS(token="secret")),patch("linkvideo_vpn_helper.services.billing_token_transport.BillingTokenTransport",return_value=transport),patch("linkvideo_vpn_helper.services.billing_editable_gateway.BillingEditableGateway",return_value=NS(all_tariffs=lambda:None)):
            out=self.w.dispatch("admin_retry",{"target":"crm"})
        self.assertTrue(out["b2o"] and out["crm"]);self.assertIs(ops.archive.b2o,old)
        self.assertNotIn("password",str(out));self.assertNotIn("secret",str(out))
        self.w.dispatch("admin_logout",{})
        self.assertIsNone(ops.work_admin_credentials);self.assertIsNone(ops.billing)
        with self.assertRaises(InputError): self.w.dispatch("admin_retry",{"target":"crm"})
    def test_failed_retry_keeps_other_service(self):
        ops=self.ops;ops.archive=NS(b2o=NS(token=lambda:"old"));ops.b2o_ready=True
        ops.admin_name="corp";ops.work_admin_credentials=NS(username="corp",password="valid")
        with patch("linkvideo_vpn_helper.services.billing_login_probe.check_billing_login",side_effect=ValueError("login failed")):
            out=self.w.dispatch("admin_retry",{"target":"crm"})
        self.assertTrue(out["b2o"]);self.assertFalse(out["crm"])
        self.assertTrue(out["errors"])
        self.w.dispatch("admin_logout",{})
        with self.assertRaises(InputError):self.w.dispatch("billing_filter",{})
