from __future__ import annotations

"""Offline opt-in B2O operator and support-tile UI regression tests."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QLabel

from linkvideo_vpn_helper.services.archive_service import ArchiveService
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.support_templates import TemplateStore
from linkvideo_vpn_helper.ui.pages.archive_download_page import ArchiveDownloadPage
from linkvideo_vpn_helper.ui.pages.archive_diagnostics_page import ArchiveDiagnosticsPage
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage
from linkvideo_vpn_helper.ui.pages.support_templates_page import SupportTemplatesPage


app = QApplication.instance() or QApplication([])

with TemporaryDirectory() as tmp:
    settings = QSettings(str(Path(tmp) / "extra.ini"), QSettings.Format.IniFormat)
    b2o = B2OService(settings)
    assert [(key, operator) for key, _name, operator in b2o.EXTRA_CLUSTERS] == [
        ("zdravservice", 1442), ("goodline", 1442), ("provzor", 621)
    ]
    assert not b2o.active_extra_clusters()
    assert not b2o.is_operator_enabled(1442)
    assert not b2o.is_operator_enabled(621)
    assert all(b2o.is_operator_enabled(operator) for operator in (241, 1721, 1741))
    try:
        b2o.camera(1442, "10001")
    except ValueError as exc:
        assert "выключен" in str(exc)
    else:
        raise AssertionError("A disabled B2O cluster unexpectedly performed lookup")

    registry = SimpleNamespace(all=lambda include_disabled=True: [])
    settings_page = SettingsPage(settings, registry)
    assert len(settings_page.extra_cluster_switches) == 3
    assert not any(sw.isChecked() for sw in settings_page.extra_cluster_switches.values())
    settings_page.extra_cluster_switches["zdravservice"].setChecked(True)
    settings_page.extra_cluster_switches["goodline"].setChecked(True)
    settings_page.extra_cluster_switches["provzor"].setChecked(True)
    assert all(b2o.extra_cluster_enabled(key) for key, _, _ in b2o.EXTRA_CLUSTERS)
    settings_page.close()
    settings_page.deleteLater()

    assert b2o.valid_operator_id(1442) == 1442
    assert b2o.valid_operator_id(621) == 621
    assert b2o.detect_operator_id("12345", 1442) == 1442
    assert b2o.normalize_camera_id("12345", 1442) == ("12345", "12345")

    requests = []
    def fake_request(url, token, headers=None, timeout=25):
        requests.append(url)
        return {
            "data": {
                "server_name": "b2o-vcore01.video.goodline.info",
                "main_stream_name": "live/12345",
                "signature": "wmsAuthSign=mocked",
                "timezone": 7,
            }
        }
    b2o.request_json = fake_request
    b2o.token = lambda: "offline-test-token"
    found = b2o.camera(1442, "12345")
    assert "/operators/1442/cameras/12345" in requests[0]
    assert found.raw["_lv_operator_id"] == 1442

    service = ArchiveService(settings)
    download = ArchiveDownloadPage(service, settings)
    diagnosis = ArchiveDiagnosticsPage(service, settings)
    for page in (download, diagnosis):
        picker = page.extra_clusters
        assert picker.combo.count() == 4
        assert picker.selected_operator() is None
        picker.combo.setCurrentIndex(picker.combo.findData("goodline"))
        assert picker.selected_operator() == 1442
        assert page._operator_id() == 1442
        assert not page.operator.isEnabled()
        picker.combo.setCurrentIndex(picker.combo.findData("provzor"))
        assert page._operator_id() == 621
        picker.clear_selection()
        assert page._operator_id() == 241
        assert page.operator.isEnabled()
        page.close()
        page.deleteLater()

    # Disable one of the aliases: the second operator-1442 alias stays active.
    settings_page = SettingsPage(settings, registry)
    settings_page.extra_cluster_switches["zdravservice"].setChecked(False)
    assert b2o.is_operator_enabled(1442)
    settings_page.extra_cluster_switches["goodline"].setChecked(False)
    assert not b2o.is_operator_enabled(1442)
    assert b2o.is_operator_enabled(621)
    settings_page.close()
    settings_page.deleteLater()

    store = TemplateStore(Path(tmp) / "templates")
    tid = store.save("Обрыв архива", "Проверьте качество сети и соединение камеры.",
                     "Диагностика")
    page = SupportTemplatesPage(store=store)
    assert page.templates_list.count() == 1
    item = page.templates_list.item(0)
    tile = page.templates_list.itemWidget(item)
    assert tile is not None
    assert tile.findChild(QLabel, "TemplateTileTitle").text() == "Обрыв архива"
    assert tile.findChild(QLabel, "TemplateTileMeta").text() == "Диагностика"
    page.templates_list.setCurrentItem(item)
    page._copy()
    assert QApplication.clipboard().text().startswith("Проверьте качество")
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

print("CORE TESTS 3.0.18 OPT-IN B2O CLUSTERS AND TEMPLATE TILES OK")
