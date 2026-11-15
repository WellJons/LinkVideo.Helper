import unittest
from types import SimpleNamespace as NS
from desktop_v4.archive_report import project,comparison,make_reports,archive_filename
from linkvideo_vpn_helper.services.archive_service_core import ArchiveCamera,ArchiveDiscovery,ArchiveGap,ArchiveSlice,ReserveTransferEvent

def discovery(exact=False,hls=False):
    c=ArchiveCamera("100","linkvideo_100","vcore.example","stream","SECRET_SIGNATURE",7,{"archive_depth":30})
    d=ArchiveDiscovery(c,100000,100100,[ArchiveSlice("vcore.example","app","stream",100000,100040,"SECRET_SIGNATURE")] if exact else [],
        [ArchiveGap(100040 if exact else 100000,100100)],["vcore.example"],["https://example/?token=SECRET_TOKEN"],
        [ReserveTransferEvent("vcore.example","reserve.example",100050,100060)])
    if hls:d.hls_fallback_url="https://example/?token=SECRET_TOKEN";d.hls_fallback_duration=80;d.hls_fallback_segments=2
    return d

class ArchiveReportTests(unittest.TestCase):
    def test_hls_does_not_project_false_gap(self):
        p=project(discovery(hls=True));self.assertTrue(p["hls_only"]);self.assertFalse(p["exact_timeline"]);self.assertEqual(p["gaps"],[])
        self.assertIsNone(p["moves"][0]["overlap"]);self.assertNotIn("SECRET",str(p))
    def test_exact_gap_and_move(self):
        d=discovery(exact=True);p=project(d);self.assertEqual(p["gaps"][0]["duration"],60);self.assertTrue(p["moves"][0]["overlap"])
        self.assertEqual(comparison(d,[("101",d,None)])[0]["overlap_seconds"],60)
        self.assertIsNone(comparison(d,[("101",discovery(hls=True),None)])[0]["overlap_seconds"])
        self.assertTrue(archive_filename(d).startswith("linkvideo_100_"));self.assertTrue(archive_filename(d).endswith(".mp4"))
    def test_reports_no_raw_secrets(self):
        d=discovery(hls=True);dx=NS(title="Ограниченная диагностика",side_text="Причина не определена",confidence_text="Недостаточно данных",summary="Требуется дополнительная проверка.",evidence=[],cautions=[],gaps=[])
        report=make_reports(d,dx,[],[],{})
        self.assertEqual(set(report),{"summary","breaks","moves","compare","technical"})
        self.assertIn("UTC+7",report["moves"]);self.assertIn("нельзя",report["breaks"].lower())
        self.assertNotIn("SECRET",str(report))
