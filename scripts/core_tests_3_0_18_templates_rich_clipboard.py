from __future__ import annotations

"""Windows/offscreen support-library regression: visible titles and rich replies."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.support_templates import TemplateStore
from linkvideo_vpn_helper.ui.pages.support_templates_page import SupportTemplatesPage


app = QApplication.instance() or QApplication([])

with TemporaryDirectory() as root:
    root = Path(root)
    image = QImage(320, 140, QImage.Format.Format_ARGB32)
    image.fill(QColor("#52A0DD"))
    path = root / "instruction.png"
    assert image.save(str(path), "PNG")

    store = TemplateStore(root / "library")
    page = SupportTemplatesPage(store=store)
    page.resize(1300, 800)
    page.show()
    app.processEvents()

    # Draft images must be allowed BEFORE the first DB save.
    page._new()
    assert page._selected_id is None
    assert page.add_image_btn.isEnabled()
    page.title_input.setText("Инструкция по настройке времени в приложении камеры")
    page.body_input.setPlainText("Здравствуйте!\nОткройте настройки времени.\nПроверьте часовой пояс.")
    page._stage_image(path)
    assert len(page._pending_images) == 1
    assert page.images_list.count() == 1
    assert not store.list()

    # Copy a draft answer and all images as HTML with embedded bytes.
    page._copy()
    clip = QApplication.clipboard().mimeData()
    assert clip is not None and clip.hasText() and clip.hasHtml()
    assert "Проверьте часовой пояс." in clip.text()
    assert 'data:image/png;base64,' in clip.html()
    assert clip.html().count("<img ") == 1

    page._save()
    saved = store.list()
    assert len(saved) == 1
    assert len(store.images(saved[0].id)) == 1
    assert page._selected_id == saved[0].id
    assert not page._pending_images
    assert not page._editing
    assert page.view_title.text().startswith("Инструкция по настройке")

    page._copy()
    clip = QApplication.clipboard().mimeData()
    assert clip.hasHtml()
    assert '<img ' in clip.html()
    assert "Здравствуйте!" in clip.text()

    # Changing a saved answer uses explicit read/edit mode; removal commits
    # only on Save (no silent image loss while previewing).
    page._set_editing(True)
    page.images_list.setCurrentRow(0)
    page._remove_image()
    assert len(store.images(saved[0].id)) == 1
    assert len(page._visible_image_paths()) == 0
    page._save()
    assert store.images(saved[0].id) == []

    # Ctrl+V / paste-image workflow works on an unsaved template.
    page._new()
    QApplication.clipboard().setImage(image)
    page._paste_image()
    assert page._selected_id is None
    assert len(page._pending_images) == 1
    assert page.images_list.count() == 1

    # Long names/excerpts must have measured (not hard-coded 103px) rows.
    lengthy = (
        "Перенос камеры между серверами и подробная инструкция "
        "по проверке подключения, записи, времени и архивов"
    )
    store.save(lengthy, "Первый длинный пункт " * 26, "Диагностика")
    page._new()
    page._refresh()
    app.processEvents()
    target = next(
        page.templates_list.item(i)
        for i in range(page.templates_list.count())
        if store.get(page.templates_list.item(i).data(
            Qt.ItemDataRole.UserRole)).title == lengthy
    )
    assert target.sizeHint().height() > 103, target.sizeHint().height()
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

print("CORE TESTS 3.0.18 RICH REPLIES / DRAFT ATTACHMENTS / ADAPTIVE TILES OK")
