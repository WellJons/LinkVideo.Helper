from __future__ import annotations

"""Desktop offline support reply library: fast search, editing and clipboard."""
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt
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
            self, max_width=1420, min_width=720, margins=22, spacing=13)
        root.addWidget(PageHeader(
            "Шаблоны ответов",
            "Личная библиотека для техподдержки: поиск, категории, изображения и копирование. "
            "Хранится только на этом компьютере."
        ))
        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)

        left = Card()
        left.setMinimumWidth(280)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.setSpacing(10)
        heading = QLabel("Библиотека")
        heading.setObjectName("SectionTitle")
        ll.addWidget(heading)

        self.search = QLineEdit()
        self.search.setObjectName("TemplateSearch")
        self.search.setPlaceholderText("Поиск по названию и тексту…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        ll.addWidget(self.search)

        filters = QHBoxLayout()
        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(38)
        self.category_filter.currentIndexChanged.connect(self._refresh)
        filters.addWidget(self.category_filter, 1)
        self.favorites_only = QCheckBox("★ Избранное")
        self.favorites_only.toggled.connect(self._refresh)
        filters.addWidget(self.favorites_only)
        ll.addLayout(filters)

        self.templates_list = QListWidget()
        self.templates_list.setObjectName("TemplateResultsList")
        self.templates_list.setSpacing(4)
        self.templates_list.setMinimumHeight(380)
        self.templates_list.currentItemChanged.connect(self._select)
        ll.addWidget(self.templates_list, 1)

        left_actions = QHBoxLayout()
        new_btn = QPushButton("＋ Создать шаблон")
        new_btn.setProperty("role", "primary")
        new_btn.clicked.connect(self._new)
        import_btn = QPushButton("Импорт DOCX")
        import_btn.clicked.connect(self._import_docx)
        left_actions.addWidget(new_btn, 1)
        left_actions.addWidget(import_btn)
        ll.addLayout(left_actions)
        columns.addWidget(left)

        right = Card()
        right.setMinimumWidth(360)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(18, 15, 18, 18)
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
        rl.addLayout(title_row)

        name_title = QLabel("Название")
        name_title.setObjectName("TinyMuted")
        rl.addWidget(name_title)
        self.title_input = QLineEdit()
        self.title_input.setObjectName("TemplateTitleInput")
        self.title_input.setPlaceholderText("Например: Потери Wi-Fi на камере")
        self.title_input.setMinimumHeight(40)
        rl.addWidget(self.title_input)

        name_category = QLabel("Категория")
        name_category.setObjectName("TinyMuted")
        rl.addWidget(name_category)
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.setMinimumHeight(40)
        self.category_input.setObjectName("TemplateCategoryInput")
        rl.addWidget(self.category_input)

        body_title = QLabel("Текст ответа")
        body_title.setObjectName("TinyMuted")
        rl.addWidget(body_title)
        self.body_input = QTextEdit()
        self.body_input.setObjectName("TemplateBodyInput")
        self.body_input.setPlaceholderText("Напишите готовый ответ клиенту…")
        self.body_input.setAcceptRichText(False)
        self.body_input.setMinimumHeight(250)
        rl.addWidget(self.body_input, 1)

        image_title = QLabel("Изображения и скриншоты")
        image_title.setObjectName("TinyMuted")
        rl.addWidget(image_title)
        image_row = QHBoxLayout()
        self.images_list = QListWidget()
        self.images_list.setMaximumHeight(95)
        self.images_list.setObjectName("TemplateImagesList")
        self.images_list.currentItemChanged.connect(self._preview_image)
        image_row.addWidget(self.images_list, 1)
        image_controls = QVBoxLayout()
        add_image = QPushButton("＋ Добавить")
        add_image.clicked.connect(self._add_image)
        remove_image = QPushButton("Убрать")
        remove_image.clicked.connect(self._remove_image)
        image_controls.addWidget(add_image)
        image_controls.addWidget(remove_image)
        image_controls.addStretch(1)
        image_row.addLayout(image_controls)
        rl.addLayout(image_row)
        self.image_preview = QLabel("Выберите изображение для просмотра")
        self.image_preview.setObjectName("TinyMuted")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumHeight(75)
        self.image_preview.setMaximumHeight(150)
        rl.addWidget(self.image_preview)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setObjectName("TemplateSaveButton")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(self._save)
        copy_btn = QPushButton("Копировать ответ")
        copy_btn.clicked.connect(self._copy)
        delete_btn = QPushButton("Удалить")
        delete_btn.setProperty("role", "danger")
        delete_btn.clicked.connect(self._delete)
        actions.addWidget(self.save_btn, 1)
        actions.addWidget(copy_btn, 1)
        actions.addWidget(delete_btn)
        rl.addLayout(actions)
        self.notice = QLabel("Все шаблоны хранятся локально. Данные не отправляются на сервер.")
        self.notice.setObjectName("TinyMuted")
        self.notice.setWordWrap(True)
        rl.addWidget(self.notice)
        columns.addWidget(right)
        columns.setStretchFactor(0, 3)
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

    def _refresh(self, *_):
        selected = self._selected_id
        category = self.category_filter.currentData() if self.category_filter.count() else ""
        self._categories()
        self.templates_list.blockSignals(True)
        self.templates_list.clear()
        for template in self.store.list(self.search.text(), category or "",
                                        self.favorites_only.isChecked()):
            item = QListWidgetItem(
                ("★ " if template.favorite else "") + template.title + "\n" + template.category)
            item.setData(Qt.ItemDataRole.UserRole,template.id)
            item.setSizeHint(item.sizeHint().expandedTo(item.sizeHint()))
            self.templates_list.addItem(item)
            if template.id == selected:
                self.templates_list.setCurrentItem(item)
        self.templates_list.blockSignals(False)
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
        self.image_preview.setText("Для добавления картинки сначала сохраните шаблон.")
        self.notice.setText("Новый ответ. Заполните название и текст.")

    def _save(self):
        try:
            identifier = self.store.save(self.title_input.text(),self.body_input.toPlainText(),
                                         self.category_input.currentText(),self._selected_id)
        except (ValueError, OSError) as exc:
            self.notice.setText(str(exc))
            return
        self._selected_id = identifier
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
            return
        for image_id,path in self.store.images(self._selected_id):
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
        path,_ = QFileDialog.getOpenFileName(self,"Импорт шаблона из Word","",
                                             "Word documents (*.docx)")
        if not path:
            return
        try:
            template_id = self.store.import_docx(Path(path))
            self._selected_id = template_id
            self._refresh()
            template = self.store.get(template_id)
            if template is not None:
                self.title_input.setText(template.title)
                self.body_input.setPlainText(template.body)
                self.category_input.setCurrentText(template.category)
            self.notice.setText("✓ DOCX импортирован как один шаблон.")
        except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
            self.notice.setText("Не удалось импортировать DOCX: " + str(exc))
