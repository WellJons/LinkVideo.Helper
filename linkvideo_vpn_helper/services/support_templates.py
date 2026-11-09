from __future__ import annotations

"""Offline support reply templates, stored in a private local SQLite database.

No server connection, B2O credentials or cloud account is required. The data
lives in the current Windows user's application data directory.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
import zipfile
from xml.etree import ElementTree as ET

from PySide6.QtCore import QStandardPaths


DEFAULT_CATEGORIES = (
    "Приветствие", "Диагностика", "Камеры и настройка",
    "VPN и роутеры", "Видеоархив", "Оплата и тарифы", "Прочее",
)
ALLOWED_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DOCX_LIMIT = 12 * 1024 * 1024
IMAGE_LIMIT = 5 * 1024 * 1024


@dataclass(frozen=True)
class Template:
    id: int
    title: str
    body: str
    category: str
    favorite: bool
    updated_at: int


class TemplateStore:
    def __init__(self, root: Path | None = None):
        if root is None:
            location = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation)
            root = Path(location) / "support_templates"
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.media = self.root / "images"
        self.media.mkdir(exist_ok=True)
        self.path = self.root / "templates.sqlite3"
        self._init()

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        try:
            with con:
                yield con
        finally:
            con.close()

    def _init(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS templates(
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    category TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS templates_category ON templates(category);
                CREATE TABLE IF NOT EXISTS template_images(
                    id INTEGER PRIMARY KEY,
                    template_id INTEGER NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL UNIQUE
                );
            """)

    def categories(self) -> tuple[str, ...]:
        with self._db() as db:
            existing = [r["category"] for r in db.execute(
                "SELECT DISTINCT category FROM templates ORDER BY category COLLATE NOCASE")]
        return tuple(dict.fromkeys((*DEFAULT_CATEGORIES, *existing)))

    @staticmethod
    def _row(row) -> Template:
        return Template(int(row["id"]), str(row["title"]), str(row["body"]),
                        str(row["category"]), bool(row["favorite"]), int(row["updated_at"]))

    def list(self, query: str = "", category: str = "", favorites: bool = False) -> list[Template]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM templates ORDER BY favorite DESC, updated_at DESC, id DESC").fetchall()
        needle = query.strip().casefold()
        return [self._row(r) for r in rows
                if (not needle or needle in str(r["title"]).casefold()
                    or needle in str(r["body"]).casefold())
                and (not category or r["category"] == category)
                and (not favorites or r["favorite"])]

    def get(self, template_id: int) -> Template | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM templates WHERE id=?", (int(template_id),)).fetchone()
        return self._row(row) if row else None

    def save(self, title: str, body: str, category: str, template_id: int | None = None) -> int:
        title = title.strip()
        body = body.strip()
        category = category.strip() or "Прочее"
        if not title or not body:
            raise ValueError("Заполните название и текст шаблона.")
        if len(title) > 180 or len(body) > 50000 or len(category) > 70:
            raise ValueError("Текст или название слишком длинные.")
        now = int(datetime.now(timezone.utc).timestamp())
        with self._db() as db:
            if template_id is None:
                cur = db.execute(
                    "INSERT INTO templates(title,body,category,updated_at) VALUES(?,?,?,?)",
                    (title,body,category,now))
                return int(cur.lastrowid)
            result = db.execute("UPDATE templates SET title=?,body=?,category=?,updated_at=? WHERE id=?",
                                (title,body,category,now,int(template_id)))
            if not result.rowcount:
                raise ValueError("Шаблон не найден.")
            return int(template_id)

    def favorite(self, template_id: int, selected: bool) -> None:
        with self._db() as db:
            db.execute("UPDATE templates SET favorite=? WHERE id=?", (int(selected),int(template_id)))

    def delete(self, template_id: int) -> None:
        # Only filenames originated from this store, never arbitrary paths.
        with self._db() as db:
            filenames = [str(r["filename"]) for r in db.execute(
                "SELECT filename FROM template_images WHERE template_id=?", (int(template_id),))]
            db.execute("DELETE FROM templates WHERE id=?", (int(template_id),))
        for name in filenames:
            if Path(name).name == name:
                (self.media / name).unlink(missing_ok=True)

    def images(self, template_id: int) -> list[tuple[int, Path]]:
        with self._db() as db:
            rows = db.execute("SELECT id,filename FROM template_images WHERE template_id=? ORDER BY id",
                              (int(template_id),)).fetchall()
        return [(int(row["id"]),self.media / row["filename"]) for row in rows
                if Path(row["filename"]).name==row["filename"]
                and (self.media / row["filename"]).is_file()]

    def attach_image(self, template_id: int, path: Path) -> None:
        if self.get(template_id) is None:
            raise ValueError("Сначала сохраните шаблон.")
        source = Path(path)
        if source.suffix.casefold() not in ALLOWED_IMAGES or source.stat().st_size > IMAGE_LIMIT:
            raise ValueError("Допускаются PNG/JPEG/WEBP/GIF не больше 5 МБ.")
        dest_name = uuid.uuid4().hex + source.suffix.lower()
        destination = self.media / dest_name
        shutil.copyfile(source,destination)
        try:
            with self._db() as db:
                db.execute("INSERT INTO template_images(template_id,filename) VALUES(?,?)",
                           (int(template_id),dest_name))
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def detach_image(self, image_id: int, template_id: int) -> None:
        with self._db() as db:
            row = db.execute("SELECT filename FROM template_images WHERE id=? AND template_id=?",
                             (int(image_id),int(template_id))).fetchone()
            if row is None:
                return
            db.execute("DELETE FROM template_images WHERE id=?",(int(image_id),))
        name = str(row["filename"])
        if Path(name).name==name:
            (self.media / name).unlink(missing_ok=True)

    @staticmethod
    def _docx_entries(path: Path) -> list[tuple[str, str]]:
        """Read separate Word replies from headings, without extracting files.

        Word Heading/Title styles, bold standalone headings, and explicitly
        labelled "Шаблон:" lines start a new answer. Unstructured documents
        remain a single template, so ordinary multi-paragraph replies are not
        accidentally split into dozens of records.
        """
        if path.suffix.lower() != ".docx" or not path.is_file() or path.stat().st_size > DOCX_LIMIT:
            raise ValueError("Выберите документ DOCX до 12 МБ.")
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 10 * 1024 * 1024:
                raise ValueError("Документ слишком большой.")
            try:
                root = ET.fromstring(archive.read(info))
            except ET.ParseError as exc:
                raise ValueError("Повреждённая структура DOCX.") from exc

        ns_uri = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        ns = {"w": ns_uri}
        attr = "{" + ns_uri + "}val"
        paragraphs: list[tuple[str, bool]] = []
        for paragraph in root.findall(".//w:body//w:p", ns):
            # Preserve text runs and paragraph boundaries, including table cells.
            value = "".join(part.text or "" for part in paragraph.findall(".//w:t", ns)).strip()
            if not value:
                paragraphs.append(("", False))
                continue
            style = paragraph.find("./w:pPr/w:pStyle", ns)
            style_name = str(style.get(attr, "") if style is not None else "").casefold()
            heading_style = (
                style_name.startswith(("heading", "заголовок"))
                or style_name in {"title", "subtitle", "название"}
            )
            runs = [run for run in paragraph.findall("./w:r", ns)
                    if "".join(x.text or "" for x in run.findall(".//w:t", ns)).strip()]
            bold_heading = bool(runs) and all(
                (bold := run.find("./w:rPr/w:b", ns)) is not None
                and str(bold.get(attr, "1")).casefold() not in {"0", "false", "off"}
                for run in runs
            )
            labelled = bool(re.match(r"^(?:шаблон|ответ|тема)\s*(?:№\s*\d+)?\s*[:—-]\s*\S",
                                     value, flags=re.IGNORECASE))
            paragraphs.append((
                value,
                len(value) <= 180 and (heading_style or bold_heading or labelled),
            ))

        full_text = [text for text, _ in paragraphs if text]
        if not full_text:
            raise ValueError("В документе не найден текст.")

        filename_title = re.sub(r"\s+", " ", path.stem).strip()[:180] or "Импортированный шаблон"

        # Some operator documents contain no Word heading styles at all:
        # each response is two or more paragraphs separated by empty lines.
        # Detect this repeated structure, but preserve ordinary one-block
        # letters as a single template.
        if not any(heading for _, heading in paragraphs):
            blocks: list[list[str]] = []
            block: list[str] = []
            empty = 0
            for value, _ in paragraphs:
                if not value:
                    empty += 1
                    continue
                if empty >= 2 and block:
                    blocks.append(block)
                    block = []
                block.append(value)
                empty = 0
            if block:
                blocks.append(block)
            if len(blocks) >= 2 and all(
                len(group) >= 2 and len(group[0]) <= 100 for group in blocks
            ):
                pairs = [(group[0].rstrip(":").strip(), "\n".join(group[1:]).strip())
                         for group in blocks]
                if len(pairs) <= 250 and all(
                    title and body and len(body) <= 50000 for title, body in pairs
                ):
                    return pairs

        entries: list[tuple[str, str]] = []
        title: str | None = None
        body: list[str] = []
        intro: list[str] = []
        blanks = 0
        for value, styled_title in paragraphs:
            if not value:
                blanks += 1
                continue
            numbered_title = bool(re.match(r"^\d{1,3}[.)]\s+.{3,120}$", value))
            heading = styled_title or (
                blanks and (numbered_title or (
                    len(value) <= 90 and (value.endswith(":") or blanks >= 2)

                    and not re.match(r"^(?:http|rtsp)s?:", value, flags=re.IGNORECASE)
                ))
            )
            if heading:
                if title is not None and body:
                    entries.append((title, "\n".join(body)))
                    body = []
                elif title is not None:
                    # An empty heading was likely part of the previous title.
                    intro.append(title)
                title = re.sub(r"^(?:шаблон|ответ|тема)\s*[:—-]\s*", "", value,
                               flags=re.IGNORECASE).rstrip(":").strip()[:180]
                if intro:
                    body = intro + body
                    intro = []
            elif title is None:
                intro.append(value)
            else:
                body.append(value)
            blanks = 0

        if title is not None and body:
            entries.append((title, "\n".join(body)))
        elif title is not None:
            intro.append(title)

        if intro:
            # Preamble or otherwise unclassified text must never be lost.
            entries.insert(0, (filename_title, "\n".join(intro)))
        if not entries:
            entries = [(filename_title, "\n".join(full_text))]

        cleaned = [(t.strip() or filename_title, b.strip()) for t, b in entries if b.strip()]
        if not cleaned or len(cleaned) > 250:
            raise ValueError("Не удалось выделить шаблоны или их слишком много (максимум 250).")
        if any(len(title) > 180 or len(body) > 50000 for title, body in cleaned):
            raise ValueError("Один из шаблонов слишком длинный.")
        return cleaned

    def import_docx_many(self, path: Path, category: str = "Прочее") -> list[int]:
        """Import all detected replies atomically; do not overwrite existing ones."""
        entries = self._docx_entries(Path(path))
        category = category.strip() or "Прочее"
        if len(category) > 70:
            raise ValueError("Слишком длинная категория.")
        now = int(datetime.now(timezone.utc).timestamp())
        identifiers: list[int] = []
        with self._db() as db:
            for title, body in entries:
                cursor = db.execute(
                    "INSERT INTO templates(title,body,category,updated_at) VALUES(?,?,?,?)",
                    (title, body, category, now))
                identifiers.append(int(cursor.lastrowid))
        return identifiers

    def import_docx(self, path: Path, category: str = "Прочее") -> int:
        """Backward-compatible entrypoint; imports all, returns the first ID."""
        return self.import_docx_many(path, category)[0]
