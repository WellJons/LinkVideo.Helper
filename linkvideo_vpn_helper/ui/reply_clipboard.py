from __future__ import annotations

"""Copy a complete support reply (text + inline images) for rich-text editors.

The text/plain flavor remains available for terminals and plain text chats.
The text/html flavor embeds each screenshot as PNG data, so it does not depend
on local file paths or signed network URLs. Paste destinations determine
whether they accept HTML images; no clipboard format can force every app to.
"""

import base64
from html import escape
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, Qt
from PySide6.QtGui import QImageReader


MAX_INLINE_IMAGES = 12
MAX_INLINE_BYTES = 18 * 1024 * 1024
MAX_IMAGE_EDGE = 1600


def build_reply_mime(text: str, images: list[Path]) -> tuple[QMimeData, int, list[str]]:
    content = str(text or "").strip()
    mime = QMimeData()
    mime.setText(content)
    if not images:
        return mime, 0, []

    parts: list[str] = [
        '<html><body>',
        '<div style="font: 14px Segoe UI,Arial,sans-serif;white-space:pre-wrap">',
        escape(content),
        '</div>',
    ]
    count = 0
    total_bytes = 0
    failed: list[str] = []

    for path in images:
        source = Path(path)
        if count >= MAX_INLINE_IMAGES:
            failed.append("Можно скопировать не более 12 изображений за раз.")
            break
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        # Decode a downscaled image when supported, to avoid huge allocations.
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > MAX_IMAGE_EDGE:
            size.scale(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            failed.append(f"Изображение недоступно: {source.name}")
            continue
        if max(image.width(), image.height()) > MAX_IMAGE_EDGE:
            image = image.scaled(
                MAX_IMAGE_EDGE, MAX_IMAGE_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        output = QByteArray()
        buffer = QBuffer(output)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            failed.append(f"Не удалось подготовить: {source.name}")
            continue
        encoded = image.save(buffer, "PNG")
        buffer.close()
        if not encoded:
            failed.append(f"Не удалось преобразовать: {source.name}")
            continue
        raw = bytes(output)
        if total_bytes + len(raw) > MAX_INLINE_BYTES:
            failed.append("Объём изображений для буфера превышает 18 МБ.")
            break
        total_bytes += len(raw)
        data_url = base64.b64encode(raw).decode("ascii")
        parts.append(
            '<p style="margin:12px 0 0">'
            f'<img alt="{escape(source.stem, quote=True)}" '
            f'src="data:image/png;base64,{data_url}" '
            'style="max-width:100%;height:auto"></p>'
        )
        count += 1

    parts.append("</body></html>")
    if count:
        mime.setHtml("".join(parts))
    return mime, count, failed
