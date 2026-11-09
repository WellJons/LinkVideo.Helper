from __future__ import annotations

"""Desktop offline support reply library: fast search, editing and clipboard."""
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import uuid
from weakref import WeakMethod
import zipfile

from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QIcon, QImageReader, QPixmap, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.support_templates import TemplateStore, ALLOWED_IMAGES, IMAGE_LIMIT
from linkvideo_vpn_helper.ui.reply_clipboard import build_reply_mime
from linkvideo_vpn_helper.ui.components import Card, PageHeader, build_page_scaffold


class SupportReplyBodyEditor(QTextEdit):
    """Plain text body; Ctrl+V with a screenshot attaches a draft image."""
    def __init__(self, image_paste, parent=None):
        super().__init__(parent)
        self._image_paste = WeakMethod(image_paste)

    def insertFromMimeData(self, source):
        if source.hasImage() and not source.hasText() and not self.isReadOnly():
            handler = self._image_paste()
            if handler is not None:
                handler()
            return
        super().insertFromMimeData(source)


class SupportTemplatesPage(QWidget):
    def __init__(self, parent=None, *, store: TemplateStore | None = None):
        super().__init__(parent)
        self.store = store or TemplateStore()
        self._selected_id: int | None = None
        self._pending_images: list[Path] = []
        self._removed_image_ids: set[int] = set()
        self._draft_dir = TemporaryDirectory(prefix="lv_reply_images_")
        self._editing = False
        self._build()
        self._refresh()

    def _build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1440, min_width=740, margins=22, spacing=12)
        root.addWidget(PageHeader(
            "Шаблоны ответов",
            "Готовые ответы поддержки · поиск по тексту и категориям · копирование одним нажатием."
        ))

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addStretch(1)
        import_btn = QPushButton("Импортировать DOCX")
        import_btn.setToolTip("Загрузить отдельные шаблоны из заголовков Word-документа")
        import_btn.clicked.connect(self._import_docx)
        toolbar.addWidget(import_btn)
        new_btn = QPushButton("＋ Создать шаблон")
        new_btn.setProperty("role", "primary")
        new_btn.clicked.connect(self._new)
        toolbar.addWidget(new_btn)
        root.addLayout(toolbar)

        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(14)
        columns.setMinimumHeight(600)
        columns.splitterMoved.connect(lambda *_: self._update_list_row_heights())

        left = Card()
        left.setMinimumWidth(350)
        left.setObjectName("TemplateLibraryPanel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(9)
        heading_line = QHBoxLayout()
        heading = QLabel("Библиотека")
        heading.setObjectName("SectionTitle")
        heading_line.addWidget(heading)
        heading_line.addStretch(1)
        self.library_count = QLabel("0 ответов")
        self.library_count.setObjectName("TinyMuted")
        heading_line.addWidget(self.library_count)
        ll.addLayout(heading_line)

        self.search = QLineEdit()
        self.search.setObjectName("TemplateSearch")
        self.search.setMinimumHeight(42)
        self.search.setPlaceholderText("Найти шаблон или фразу…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        ll.addWidget(self.search)

        filters = QVBoxLayout()
        filters.setSpacing(7)
        self.category_filter = QComboBox()
        self.category_filter.setObjectName("TemplateCategoryFilter")
        self.category_filter.setMinimumHeight(38)
        self.category_filter.currentIndexChanged.connect(self._refresh)
        filters.addWidget(self.category_filter)
        self.favorites_only = QCheckBox("★ Только избранное")
        self.favorites_only.toggled.connect(self._refresh)
        filters.addWidget(self.favorites_only)
        ll.addLayout(filters)

        self.templates_list = QListWidget()
        self.templates_list.setObjectName("TemplateResultsList")
        self.templates_list.setWordWrap(True)
        self.templates_list.setSpacing(8)
        self.templates_list.setUniformItemSizes(False)
        self.templates_list.setMinimumHeight(380)
        self.templates_list.currentItemChanged.connect(self._select)

        ll.addWidget(self.templates_list, 1)
        self.empty_label = QLabel("Пока нет ответов.\nСоздайте шаблон или импортируйте DOCX.")
        self.empty_label.setObjectName("TinyMuted")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        ll.addWidget(self.empty_label)

        local_note = QLabel("Сохранение локальное · без отправки на сервер")
        local_note.setObjectName("TinyMuted")
        ll.addWidget(local_note)
        columns.addWidget(left)

        right = Card()
        right.setMinimumWidth(400)
        right.setObjectName("TemplateEditorPanel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(22, 18, 22, 18)
        rl.setSpacing(10)

        title_row = QHBoxLayout()
        section = QLabel("Ответ клиенту")
        section.setObjectName("SectionTitle")
        title_row.addWidget(section)
        title_row.addStretch(1)
        self.star = QPushButton("☆ В избранное")
        self.star.setProperty("role", "soft")
        self.star.clicked.connect(self._toggle_favorite)
        title_row.addWidget(self.star)
        self.edit_btn = QPushButton("Редактировать")
        self.edit_btn.setProperty("role", "soft")
        self.edit_btn.clicked.connect(self._start_edit)
        title_row.addWidget(self.edit_btn)
        self.copy_btn = QPushButton("Копировать ответ")
        self.copy_btn.setProperty("role", "primary")
        self.copy_btn.setToolTip("Копировать текст и прикреплённые изображения для форматированного редактора")
        self.copy_btn.clicked.connect(self._copy)
        title_row.addWidget(self.copy_btn)
        rl.addLayout(title_row)

        fields = QHBoxLayout()
        fields.setSpacing(12)
        name_column = QVBoxLayout()
        name_column.setSpacing(6)
        name_title = QLabel("Название шаблона")
        name_title.setObjectName("TinyMuted")
        name_column.addWidget(name_title)
        self.title_input = QLineEdit()
        self.title_input.setObjectName("TemplateTitleInput")
        self.title_input.setPlaceholderText("Краткое название для поиска")
        self.title_input.setMinimumHeight(42)
        name_column.addWidget(self.title_input)
        fields.addLayout(name_column, 3)
        category_column = QVBoxLayout()
        category_column.setSpacing(6)
        name_category = QLabel("Категория")
        name_category.setObjectName("TinyMuted")
        category_column.addWidget(name_category)
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.setMinimumHeight(42)
        self.category_input.setObjectName("TemplateCategoryInput")
        category_column.addWidget(self.category_input)
        fields.addLayout(category_column, 2)
        self.editor_fields = QWidget()
        self.editor_fields.setObjectName("TemplateEditorFields")
        self.editor_fields.setLayout(fields)
        rl.addWidget(self.editor_fields)

        self.view_heading = QWidget()
        view_layout = QVBoxLayout(self.view_heading)
        view_layout.setContentsMargins(0, 8, 0, 3)
        view_layout.setSpacing(5)
        self.view_title = QLabel("Выберите ответ из библиотеки")
        self.view_title.setObjectName("TemplateViewHeading")
        self.view_title.setWordWrap(True)
        view_layout.addWidget(self.view_title)
        self.view_category = QLabel("")
        self.view_category.setObjectName("TemplateViewCategory")
        view_layout.addWidget(self.view_category)
        rl.addWidget(self.view_heading)

        body_row = QHBoxLayout()
        body_title = QLabel("Текст ответа")
        body_title.setObjectName("CardTitle")
        body_row.addWidget(body_title)
        body_row.addStretch(1)
        hint = QLabel("Текст и изображения копируются вместе")
        hint.setObjectName("TinyMuted")
        body_row.addWidget(hint)
        rl.addLayout(body_row)
        self.body_input = SupportReplyBodyEditor(self._paste_image)
        self.body_input.setObjectName("TemplateBodyInput")
        self.body_input.setPlaceholderText("Выберите шаблон слева или создайте новый ответ…")
        self.body_input.setAcceptRichText(False)
        self.body_input.setMinimumHeight(255)
        self.body_input.setMaximumHeight(440)
        rl.addWidget(self.body_input, 0)

        self.body_preview_area = QScrollArea()
        self.body_preview_area.setObjectName("TemplatePreviewScroll")
        self.body_preview_area.setWidgetResizable(True)
        self.body_preview_area.setFrameShape(QFrame.Shape.NoFrame)
        self.body_preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_preview = QLabel("Выберите ответ из библиотеки")
        self.body_preview.setObjectName("TemplateBodyPreview")
        self.body_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.body_preview.setWordWrap(True)
        self.body_preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_preview.setContentsMargins(15, 14, 15, 14)
        self.body_preview_area.setWidget(self.body_preview)
        rl.addWidget(self.body_preview_area, 0)

        attachment_header = QHBoxLayout()
        attachment_header.setSpacing(8)
        self.attachment_count = QLabel("Вложения · 0")
        self.attachment_count.setObjectName("CardTitle")
        attachment_header.addWidget(self.attachment_count)
        attachment_header.addStretch(1)
        self.add_image_btn = QPushButton("＋ Добавить фото")
        self.add_image_btn.setProperty("role", "soft")
        self.add_image_btn.clicked.connect(self._add_image)
        attachment_header.addWidget(self.add_image_btn)
        self.paste_image_btn = QPushButton("Вставить из буфера")
        self.paste_image_btn.setProperty("role", "soft")
        self.paste_image_btn.clicked.connect(self._paste_image)
        attachment_header.addWidget(self.paste_image_btn)
        rl.addLayout(attachment_header)

        self.image_area = QWidget()
        image_layout = QVBoxLayout(self.image_area)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(7)
        self.images_list = QListWidget()
        self.images_list.setObjectName("TemplateImagesList")
        self.images_list.setViewMode(QListView.ViewMode.IconMode)
        self.images_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.images_list.setMovement(QListView.Movement.Static)
        self.images_list.setWrapping(True)
        self.images_list.setIconSize(QSize(112, 78))
        self.images_list.setGridSize(QSize(140, 108))
        self.images_list.setSpacing(5)
        self.images_list.setMaximumHeight(122)
        self.images_list.setMinimumHeight(115)
        self.images_list.setWordWrap(False)
        self.images_list.setToolTip("Двойной щелчок открывает крупный просмотр")
        self.images_list.currentItemChanged.connect(self._preview_image)
        self.images_list.itemDoubleClicked.connect(self._open_image_preview)
        image_layout.addWidget(self.images_list)
        image_footer = QHBoxLayout()
        self.image_help = QLabel("Изображения добавятся к ответу при сохранении")
        self.image_help.setObjectName("TinyMuted")
        image_footer.addWidget(self.image_help, 1)
        self.remove_image_btn = QPushButton("Убрать выбранное")
        self.remove_image_btn.setProperty("role", "ghost")
        self.remove_image_btn.clicked.connect(self._remove_image)
        image_footer.addWidget(self.remove_image_btn)
        image_layout.addLayout(image_footer)
        rl.addWidget(self.image_area)
        self.image_area.hide()
        rl.addStretch(1)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить изменения")
        self.save_btn.setObjectName("TemplateSaveButton")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(self._save)
        delete_btn = QPushButton("Удалить шаблон")
        delete_btn.setProperty("role", "dangerGhost")
        delete_btn.clicked.connect(self._delete)
        actions.addWidget(self.save_btn, 1)
        actions.addWidget(delete_btn)
        rl.addLayout(actions)
        self.notice = QLabel("Выберите шаблон слева или нажмите «Новый шаблон».")
        self.notice.setObjectName("TinyMuted")
        self.notice.setWordWrap(True)
        rl.addWidget(self.notice)
        columns.addWidget(right)

        columns.setStretchFactor(0, 40)
        columns.setStretchFactor(1, 60)
        root.addWidget(columns, 1)
        self._set_editing(False)

    def _categories(self):
        existing = self.category_filter.currentData()
        chosen = self.category_input.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("Все категории", "")
        for category in self.store.categories():
            self.category_filter.addItem(category, category)
        idx = self.category_filter.findData(existing)
        self.category_filter.setCurrentIndex(max(0,idx))
        self.category_filter.blockSignals(False)
        self.category_input.clear()
        self.category_input.addItems(self.store.categories())
        idx = self.category_input.findText(chosen)
        if idx >= 0:
            self.category_input.setCurrentIndex(idx)
        elif chosen:
            self.category_input.setCurrentText(chosen)

    def _template_tile(self, template) -> QWidget:
        """Full-height reply item — no truncation halfway through a line."""
        tile = QFrame()
        tile.setObjectName("TemplateReplyTile")
        tile.setProperty("selected", "true" if template.id == self._selected_id else "false")
        tile.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(4)

        title_line = QHBoxLayout()
        title_line.setSpacing(6)
        title = QLabel(template.title)
        title.setObjectName("TemplateTileTitle")
        title.setWordWrap(True)
        title_line.addWidget(title, 1)
        if template.favorite:
            star = QLabel("★")
            star.setObjectName("TemplateTileFavorite")
            title_line.addWidget(star, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_line)

        meta = QLabel(template.category)
        meta.setObjectName("TemplateTileMeta")
        layout.addWidget(meta)
        preview = " ".join(template.body.split())
        if len(preview) > 130:
            preview = preview[:127].rstrip() + "…"
        excerpt = QLabel(preview)
        excerpt.setObjectName("TemplateTileExcerpt")
        excerpt.setWordWrap(True)
        layout.addWidget(excerpt)
        return tile

    @staticmethod
    def _wrapped_height(label: QLabel, width: int) -> int:
        label.ensurePolished()
        metrics = QFontMetrics(label.font())
        flags = int(Qt.TextFlag.TextWordWrap)
        bounding = metrics.boundingRect(QRect(0, 0, max(80, width), 5000), flags, label.text())
        return max(metrics.height(), bounding.height() + 4)

    def _update_list_row_heights(self):
        """Re-measure every card on resize to prevent cropped text."""
        if not hasattr(self, "templates_list"):
            return
        width = max(160, self.templates_list.viewport().width() - 96)
        for i in range(self.templates_list.count()):
            item = self.templates_list.item(i)
            tile = self.templates_list.itemWidget(item)
            if tile is None:
                continue
            title = tile.findChild(QLabel, "TemplateTileTitle")
            meta = tile.findChild(QLabel, "TemplateTileMeta")
            excerpt = tile.findChild(QLabel, "TemplateTileExcerpt")
            if not (title and meta and excerpt):
                continue
            height = (
                20 + self._wrapped_height(title, width)
                + self._wrapped_height(meta, width)
                + self._wrapped_height(excerpt, width) + 12
            )
            item.setSizeHint(QSize(0, max(95, height)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "templates_list"):
            self._update_list_row_heights()

    def _highlight_selected_tile(self):
        for i in range(self.templates_list.count()):
            item = self.templates_list.item(i)
            tile = self.templates_list.itemWidget(item)
            if tile is None:
                continue
            active = item is self.templates_list.currentItem()
            tile.setProperty("selected", "true" if active else "false")
            tile.style().unpolish(tile)
            tile.style().polish(tile)

    def _refresh(self, *_):
        selected = self._selected_id
        category = self.category_filter.currentData() if self.category_filter.count() else ""
        self._categories()
        records = self.store.list(
            self.search.text(), category or "", self.favorites_only.isChecked())
        self.templates_list.blockSignals(True)
        self.templates_list.clear()
        for template in records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            item.setSizeHint(QSize(0, 112))
            item.setToolTip(template.title + "\n" + template.body)
            self.templates_list.addItem(item)
            self.templates_list.setItemWidget(item, self._template_tile(template))
            if template.id == selected:
                self.templates_list.setCurrentItem(item)
        self.templates_list.blockSignals(False)
        self._update_list_row_heights()
        self._highlight_selected_tile()
        self.library_count.setText(f"Найдено: {len(records)} / {len(self.store.list())}")
        self.empty_label.setVisible(not records)
        if selected is not None and self.store.get(selected) is None:
            self._new()

    def _sync_preview_height(self):
        if not hasattr(self, "body_preview_area"):
            return
        text = self.body_preview.text() or " "
        width = max(180, self.body_preview_area.viewport().width() - 36)
        fm = QFontMetrics(self.body_preview.font())
        height = fm.boundingRect(
            QRect(0, 0, width, 30000), int(Qt.TextFlag.TextWordWrap), text
        ).height() + 42
        target = max(132, min(320, height))
        self.body_preview.setMinimumHeight(max(116, height))
        self.body_preview_area.setFixedHeight(target)

    def _sync_view(self):
        self.view_title.setText(self.title_input.text() or "Новый ответ")
        self.view_category.setText(self.category_input.currentText())
        self.body_preview.setText(self.body_input.toPlainText().strip())
        self._sync_preview_height()

    def _start_edit(self, *_):
        self._set_editing(True)

    def _set_editing(self, editing: bool):
        self._editing = bool(editing)
        self.title_input.setReadOnly(not self._editing)
        self.body_input.setReadOnly(not self._editing)
        self.category_input.setEnabled(self._editing)
        self.add_image_btn.setEnabled(self._editing)
        self.paste_image_btn.setEnabled(self._editing)
        self.remove_image_btn.setEnabled(self._editing and bool(self.images_list.count()))
        self.save_btn.setVisible(self._editing)
        self.edit_btn.setVisible(not self._editing and self._selected_id is not None)
        self.editor_fields.setVisible(self._editing)
        self.body_input.setVisible(self._editing)
        self.view_heading.setVisible(not self._editing)
        self.body_preview_area.setVisible(not self._editing)
        if not self._editing:
            self._sync_view()
        if self._editing:
            self.title_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self.image_help.setText(
            "Сохраните шаблон, чтобы закрепить добавленные изображения"
            if self._editing else "Двойной щелчок — просмотреть скриншот")

    def _select(self,item,*_):
        if item is None:
            return
        self._pending_images.clear()
        self._removed_image_ids.clear()
        self._selected_id = int(item.data(Qt.ItemDataRole.UserRole))
        template = self.store.get(self._selected_id)
        if template is None:
            return
        self.title_input.setText(template.title)
        self.category_input.setCurrentText(template.category)
        self.body_input.setPlainText(template.body)
        self.star.setText("★ В избранном" if template.favorite else "☆ В избранное")
        self._load_images()
        self._set_editing(False)
        self._highlight_selected_tile()
        self.notice.setText("Выберите «Редактировать», чтобы изменить ответ.")

    def _new(self):
        self._selected_id = None
        self._pending_images.clear()
        self._removed_image_ids.clear()
        self.templates_list.clearSelection()
        self.templates_list.setCurrentRow(-1)
        self.title_input.clear()
        self.body_input.clear()
        self.category_input.setCurrentText("Прочее")
        self.star.setText("☆ В избранное")
        self.images_list.clear()
        self.image_area.hide()
        self.attachment_count.setText("Вложения · 0")
        self._set_editing(True)
        self._highlight_selected_tile()
        self.notice.setText("Новый ответ: можно добавлять изображения до сохранения.")

    def _save(self):
        try:
            identifier = self.store.save(
                self.title_input.text(), self.body_input.toPlainText(),
                self.category_input.currentText(), self._selected_id)
        except (ValueError, OSError) as exc:
            self.notice.setText(str(exc))
            return

        remaining: list[Path] = []
        errors: list[str] = []
        for path in self._pending_images:
            try:
                self.store.attach_image(identifier, path)
            except (ValueError, OSError) as exc:
                remaining.append(path)
                errors.append(f"{path.name}: {exc}")
        self._pending_images = remaining
        for image_id in self._removed_image_ids:
            self.store.detach_image(image_id, identifier)
        self._removed_image_ids.clear()
        self._selected_id = identifier
        self._refresh()
        self._load_images()
        self._set_editing(bool(errors))
        if errors:
            self.notice.setText(
                "Текст сохранён, но не все вложения добавлены: " + "; ".join(errors[:2]))
        else:
            self.notice.setText("✓ Шаблон и изображения сохранены локально.")

    def _toggle_favorite(self):
        if self._selected_id is None:
            self.notice.setText("Сначала сохраните шаблон.")
            return
        template = self.store.get(self._selected_id)
        if template is not None:
            self.store.favorite(template.id,not template.favorite)
            self._refresh()
            self.star.setText("☆ В избранное" if template.favorite else "★ В избранном")

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        text = self.body_input.toPlainText().strip()
        paths = self._visible_image_paths()
        if not text and not paths:
            self.notice.setText("Нет текста или изображений для копирования.")
            return
        mime, count, failed = build_reply_mime(text, paths)
        QApplication.clipboard().setMimeData(mime)
        if failed:
            self.notice.setText(
                f"Текст и {count} изображений скопированы. "
                + "; ".join(failed[:2]))
        elif count:
            self.notice.setText(
                f"✓ Текст и {count} изображений скопированы. "
                "Вставляйте в редактор с поддержкой форматирования.")
        else:
            self.notice.setText("✓ Текст скопирован в буфер обмена.")

    def _delete(self):
        if self._selected_id is None:
            return
        confirmation = QMessageBox.question(
            self,"Удалить шаблон?","Удалить этот шаблон и его изображения без возможности восстановления?")
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(self._selected_id)
        self._new()
        self._refresh()
        self.notice.setText("Шаблон удалён.")

    def _visible_image_records(self) -> list[tuple[str, int | None, Path]]:
        records = []
        if self._selected_id is not None:
            records.extend(
                ("saved", image_id, path)
                for image_id, path in self.store.images(self._selected_id)
                if image_id not in self._removed_image_ids
            )
        records.extend(("pending", None, path) for path in self._pending_images)
        return records

    def _visible_image_paths(self) -> list[Path]:
        return [path for _, _, path in self._visible_image_records()]

    def _load_images(self):
        self.images_list.clear()
        records = self._visible_image_records()
        self.attachment_count.setText(f"Изображения · {len(records)}")
        self.image_area.setVisible(bool(records))
        for index, (kind, image_id, path) in enumerate(records, start=1):
            image = QPixmap(str(path))
            thumbnail = image.scaled(
                112, 78,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(thumbnail), f"Фото {index}")
            item.setData(Qt.ItemDataRole.UserRole, (kind, image_id, str(path)))
            item.setToolTip(
                f"{path.name}\n"
                + ("Будет сохранено вместе с шаблоном" if kind == "pending"
                   else "Двойной щелчок для просмотра"))
            self.images_list.addItem(item)
        self.remove_image_btn.setEnabled(self._editing and bool(records))

    def _preview_image(self, item, *_):
        # Thumbnails are already visible. The selected one can be removed.
        self.remove_image_btn.setEnabled(self._editing and item is not None)

    def _open_image_preview(self, item):
        if item is None:
            return
        _, _, source = item.data(Qt.ItemDataRole.UserRole)
        image = QPixmap(source)
        if image.isNull():
            self.notice.setText("Не удалось открыть изображение.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Просмотр изображения")
        dialog.resize(820, 620)
        panel = QVBoxLayout(dialog)
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setPixmap(image.scaled(
            780, 550, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        panel.addWidget(preview, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        panel.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _stage_image(self, source: Path):
        source = Path(source)
        if (not source.is_file() or source.suffix.casefold() not in ALLOWED_IMAGES
                or source.stat().st_size > IMAGE_LIMIT):
            raise ValueError("Допускаются PNG/JPEG/WEBP/GIF до 5 МБ.")
        reader = QImageReader(str(source))
        if not reader.canRead():
            raise ValueError("Не удалось распознать изображение.")
        dest = Path(self._draft_dir.name) / (uuid.uuid4().hex + source.suffix.lower())
        shutil.copyfile(source, dest)
        self._pending_images.append(dest)
        self._load_images()

    def _add_image(self):
        if not self._editing:
            self._set_editing(True)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Добавить изображения к ответу", "",
            "Изображения (*.png *.jpg *.jpeg *.webp *.gif)")
        if not paths:
            return
        errors: list[str] = []
        for path in paths:
            try:
                self._stage_image(Path(path))
            except (ValueError, OSError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if errors:
            self.notice.setText("Некоторые файлы не добавлены: " + "; ".join(errors[:2]))
        else:
            self.notice.setText(
                "✓ Изображения прикреплены к черновику. Можно сохранить шаблон.")

    def _paste_image(self):
        from PySide6.QtWidgets import QApplication
        image = QApplication.clipboard().image()
        if image.isNull():
            self.notice.setText("В буфере обмена нет изображения.")
            return
        if not self._editing:
            self._set_editing(True)
        destination = Path(self._draft_dir.name) / (uuid.uuid4().hex + ".png")
        if not image.save(str(destination), "PNG"):
            self.notice.setText("Не удалось сохранить изображение из буфера.")
            return
        try:
            self._stage_image(destination)
            self.notice.setText("✓ Изображение вставлено в черновик.")
        except (ValueError, OSError) as exc:
            self.notice.setText(str(exc))

    def _remove_image(self):
        item = self.images_list.currentItem()
        if item is None or not self._editing:
            return
        kind, image_id, filename = item.data(Qt.ItemDataRole.UserRole)
        if kind == "pending":
            self._pending_images = [
                path for path in self._pending_images if str(path) != filename
            ]
            Path(filename).unlink(missing_ok=True)
        elif image_id is not None:
            self._removed_image_ids.add(int(image_id))
        self._load_images()
        self.notice.setText("Вложение убрано из черновика. Сохраните изменения.")

    def _import_docx(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт шаблонов из Word", "", "Word documents (*.docx)")
        if not path:
            return
        try:
            category = str(self.category_filter.currentData() or "Прочее")
            identifiers = self.store.import_docx_many(Path(path), category)
            self._selected_id = identifiers[0]
            # Reset filters, otherwise the imported entries may be hidden.
            self.search.clear()
            self.favorites_only.setChecked(False)
            self.category_filter.setCurrentIndex(0)
            self._refresh()
            for index in range(self.templates_list.count()):
                item = self.templates_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == identifiers[0]:
                    self.templates_list.setCurrentItem(item)
                    break
            self.notice.setText(
                f"✓ Из DOCX импортировано отдельных шаблонов: {len(identifiers)}.")
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
            self.notice.setText("Не удалось импортировать DOCX: " + str(exc))
