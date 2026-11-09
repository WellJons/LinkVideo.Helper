from __future__ import annotations

"""Desktop offline support reply library: fast search, editing and clipboard."""
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from linkvideo_vpn_helper.services.support_templates import TemplateStore
from linkvideo_vpn_helper.ui.components import Card, PageHeader, build_page_scaffold


class SupportTemplatesPage(QWidget):
    def __init__(self, parent=None, *, store: TemplateStore | None = None):
        super().__init__(parent)
        self.store = store or TemplateStore()
        self._selected_id: int | None = None
        self._build()
        self._refresh()

    def _build(self):
        self.page_scroll, self.page_canvas, root = build_page_scaffold(
            self, max_width=1580, min_width=760, margins=20, spacing=12)
        root.addWidget(PageHeader(
            "Шаблоны ответов",
            "Быстрый поиск, редактирование и копирование ответов. Библиотека хранится на этом ПК."
        ))

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.library_count = QLabel("Личная библиотека")
        self.library_count.setObjectName("Muted")
        toolbar.addWidget(self.library_count, 1)
        import_btn = QPushButton("↓ Импорт DOCX")
        import_btn.setToolTip("Загрузить отдельные шаблоны из заголовков Word-документа")
        import_btn.clicked.connect(self._import_docx)
        toolbar.addWidget(import_btn)
        new_btn = QPushButton("＋ Новый шаблон")
        new_btn.setProperty("role", "primary")
        new_btn.clicked.connect(self._new)
        toolbar.addWidget(new_btn)
        root.addLayout(toolbar)

        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(10)
        columns.setMinimumHeight(625)

        left = Card()
        left.setMinimumWidth(300)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(11)
        heading = QLabel("Все ответы")
        heading.setObjectName("SectionTitle")
        ll.addWidget(heading)

        self.search = QLineEdit()
        self.search.setObjectName("TemplateSearch")
        self.search.setMinimumHeight(42)
        self.search.setPlaceholderText("Поиск по названию и тексту")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        ll.addWidget(self.search)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(38)
        self.category_filter.currentIndexChanged.connect(self._refresh)
        filters.addWidget(self.category_filter, 1)
        self.favorites_only = QCheckBox("★ Избранные")
        self.favorites_only.toggled.connect(self._refresh)
        filters.addWidget(self.favorites_only)
        ll.addLayout(filters)

        self.templates_list = QListWidget()
        self.templates_list.setObjectName("TemplateResultsList")
        self.templates_list.setWordWrap(True)
        self.templates_list.setSpacing(6)
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
        right.setMinimumWidth(440)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(10)

        title_row = QHBoxLayout()
        section = QLabel("Редактор ответа")
        section.setObjectName("SectionTitle")
        title_row.addWidget(section)
        title_row.addStretch(1)
        self.star = QPushButton("☆ В избранное")
        self.star.setProperty("role", "soft")
        self.star.clicked.connect(self._toggle_favorite)
        title_row.addWidget(self.star)
        self.copy_btn = QPushButton("⧉ Копировать")
        self.copy_btn.setToolTip("Скопировать текст ответа в буфер обмена")
        self.copy_btn.clicked.connect(self._copy)
        title_row.addWidget(self.copy_btn)
        rl.addLayout(title_row)

        fields = QHBoxLayout()
        fields.setSpacing(12)
        name_column = QVBoxLayout()
        name_column.setSpacing(6)
        name_title = QLabel("НАЗВАНИЕ")
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
        name_category = QLabel("КАТЕГОРИЯ")
        name_category.setObjectName("TinyMuted")
        category_column.addWidget(name_category)
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.setMinimumHeight(42)
        self.category_input.setObjectName("TemplateCategoryInput")
        category_column.addWidget(self.category_input)
        fields.addLayout(category_column, 2)
        rl.addLayout(fields)

        body_row = QHBoxLayout()
        body_title = QLabel("Текст ответа")
        body_title.setObjectName("CardTitle")
        body_row.addWidget(body_title)
        body_row.addStretch(1)
        hint = QLabel("Обычный текст")
        hint.setObjectName("TinyMuted")
        body_row.addWidget(hint)
        rl.addLayout(body_row)
        self.body_input = QTextEdit()
        self.body_input.setObjectName("TemplateBodyInput")
        self.body_input.setPlaceholderText("Выберите шаблон слева или создайте новый ответ…")
        self.body_input.setAcceptRichText(False)
        self.body_input.setMinimumHeight(315)
        rl.addWidget(self.body_input, 1)

        attachment_header = QHBoxLayout()
        attachment_header.setSpacing(8)
        self.attachment_count = QLabel("Вложения · 0")
        self.attachment_count.setObjectName("CardTitle")
        attachment_header.addWidget(self.attachment_count)
        attachment_header.addStretch(1)
        self.add_image_btn = QPushButton("＋ Добавить изображение")
        self.add_image_btn.setProperty("role", "soft")
        self.add_image_btn.setEnabled(False)
        self.add_image_btn.clicked.connect(self._add_image)
        attachment_header.addWidget(self.add_image_btn)
        rl.addLayout(attachment_header)

        self.image_area = QWidget()
        image_layout = QVBoxLayout(self.image_area)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(6)
        image_row = QHBoxLayout()
        self.images_list = QListWidget()
        self.images_list.setMaximumHeight(100)
        self.images_list.setObjectName("TemplateImagesList")
        self.images_list.currentItemChanged.connect(self._preview_image)
        image_row.addWidget(self.images_list, 1)
        remove_image = QPushButton("Удалить вложение")
        remove_image.clicked.connect(self._remove_image)
        image_row.addWidget(remove_image)
        image_layout.addLayout(image_row)
        self.image_preview = QLabel()
        self.image_preview.setObjectName("TinyMuted")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMaximumHeight(125)
        self.image_preview.setMinimumHeight(0)
        image_layout.addWidget(self.image_preview)
        rl.addWidget(self.image_area)
        self.image_area.hide()

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить шаблон")
        self.save_btn.setObjectName("TemplateSaveButton")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(self._save)
        delete_btn = QPushButton("Удалить")
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

        columns.setStretchFactor(0, 4)
        columns.setStretchFactor(1, 6)
        root.addWidget(columns, 1)

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

    def _refresh(self, *_):
        selected = self._selected_id
        category = self.category_filter.currentData() if self.category_filter.count() else ""
        self._categories()
        records = self.store.list(self.search.text(), category or "",
                                  self.favorites_only.isChecked())
        self.templates_list.blockSignals(True)
        self.templates_list.clear()
        for template in records:
            preview = " ".join(template.body.split())
            if len(preview) > 74:
                preview = preview[:71].rstrip() + "…"
            item = QListWidgetItem(
                ("★  " if template.favorite else "") + template.title +
                "\n" + template.category + "  ·  " + preview)
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            item.setSizeHint(QSize(0, 76))
            item.setToolTip(template.title + "\n" + template.category)
            self.templates_list.addItem(item)
            if template.id == selected:
                self.templates_list.setCurrentItem(item)
        self.templates_list.blockSignals(False)
        self.library_count.setText(f"Всего шаблонов: {len(self.store.list())} · найдено: {len(records)}")
        self.empty_label.setVisible(not records)
        if selected is not None and self.store.get(selected) is None:
            self._new()

    def _select(self,item,*_):
        if item is None:
            return
        self._selected_id = int(item.data(Qt.ItemDataRole.UserRole))
        template = self.store.get(self._selected_id)
        if template is None:
            return
        self.title_input.setText(template.title)
        self.category_input.setCurrentText(template.category)
        self.body_input.setPlainText(template.body)
        self.star.setText("★ В избранном" if template.favorite else "☆ В избранное")
        self.add_image_btn.setEnabled(True)
        self._load_images()
        self.notice.setText("Шаблон загружен. Изменения сохраняются кнопкой «Сохранить».")

    def _new(self):
        self._selected_id = None
        self.templates_list.clearSelection()
        self.templates_list.setCurrentRow(-1)
        self.title_input.clear()
        self.body_input.clear()
        self.category_input.setCurrentText("Прочее")
        self.star.setText("☆ В избранное")
        self.images_list.clear()
        self.image_area.hide()
        self.attachment_count.setText("Вложения · 0")
        self.add_image_btn.setEnabled(False)
        self.image_preview.clear()
        self.notice.setText("Новый ответ. Заполните название и текст.")

    def _save(self):
        try:
            identifier = self.store.save(self.title_input.text(),self.body_input.toPlainText(),
                                         self.category_input.currentText(),self._selected_id)
        except (ValueError, OSError) as exc:
            self.notice.setText(str(exc))
            return
        self._selected_id = identifier
        self.add_image_btn.setEnabled(True)
        self._refresh()
        self.notice.setText("✓ Шаблон сохранён локально.")

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
        if not text:
            self.notice.setText("Нет текста для копирования.")
            return
        QApplication.clipboard().setText(text)
        self.notice.setText("✓ Ответ скопирован в буфер обмена.")

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

    def _load_images(self):
        self.images_list.clear()
        if self._selected_id is None:
            self.image_area.hide()
            self.attachment_count.setText("Вложения · 0")
            return
        images = self.store.images(self._selected_id)
        self.attachment_count.setText(f"Вложения · {len(images)}")
        self.image_area.setVisible(bool(images))
        for image_id,path in images:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole,(image_id,str(path)))
            self.images_list.addItem(item)
        self.image_preview.clear()

    def _preview_image(self,item,*_):
        if item is None:
            self.image_preview.clear()
            return
        image_id,path = item.data(Qt.ItemDataRole.UserRole)
        image = QPixmap(path)
        if image.isNull():
            self.image_preview.setText("Не удалось открыть изображение.")
        else:
            self.image_preview.setPixmap(image.scaled(
                500, 145, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _add_image(self):
        if self._selected_id is None:
            self.notice.setText("Сначала сохраните шаблон.")
            return
        path,_ = QFileDialog.getOpenFileName(
            self,"Выберите скриншот","",
            "Изображения (*.png *.jpg *.jpeg *.webp *.gif)")
        if not path:
            return
        try:
            self.store.attach_image(self._selected_id,Path(path))
            self._load_images()
            self.notice.setText("✓ Изображение добавлено.")
        except (ValueError,OSError) as exc:
            self.notice.setText(str(exc))

    def _remove_image(self):
        if self._selected_id is None or self.images_list.currentItem() is None:
            return
        image_id,_ = self.images_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.store.detach_image(image_id,self._selected_id)
        self._load_images()

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
