"""Explicit, local, read-only migration of 3.x support templates (no credentials)."""
from pathlib import Path
import sqlite3
import shutil
import uuid
from contextlib import closing

ALLOWED={".png",".jpg",".jpeg",".webp",".gif"}

def migrate_templates(source,store,cancel=None):
    source=Path(source).resolve(strict=True)
    if source.name!="templates.sqlite3" or source==store.path.resolve() or source.stat().st_size>100*1024*1024:
        raise ValueError("Выберите другую базу templates.sqlite3 размером до 100 МБ.")
    copied=[];inserted=skipped=attachments=missing=0
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            rows=db.execute("SELECT id,title,body,category,favorite,updated_at FROM templates ORDER BY id LIMIT 5001").fetchall()
            if len(rows)>5000: raise ValueError("Слишком много шаблонов для одной миграции.")
            imgs=db.execute("SELECT template_id,filename FROM template_images ORDER BY id LIMIT 10001").fetchall()
            if len(imgs)>10000: raise ValueError("Слишком много вложений для одной миграции.")
            images={}
            for img in imgs: images.setdefault(img["template_id"],[]).append(img["filename"])
            source_media=(source.parent/"images").resolve()
            if source_media.parent!=source.parent: raise ValueError("Папка вложений расположена за пределами библиотеки.")
            # One transaction; no overwrite of existing records or images.
            with store._db() as dest:
                dest.execute("BEGIN IMMEDIATE")
                known={(x[0],x[1],x[2]) for x in dest.execute("SELECT title,body,category FROM templates")}
                for row in rows:
                    if cancel and cancel.is_set(): raise ValueError("Миграция отменена.")
                    title,body,cat=row["title"],row["body"],row["category"]
                    if not all(isinstance(x,str) for x in (title,body,cat)) or not title.strip() or not body.strip() or len(title)>180 or len(body)>50000 or len(cat)>70: raise ValueError("База содержит некорректный шаблон.")
                    cat=cat or "Прочее";key=(title,body,cat)
                    if key in known: skipped+=1;continue
                    cur=dest.execute("INSERT INTO templates(title,body,category,favorite,updated_at) VALUES(?,?,?,?,?)",(title,body,cat,int(bool(row["favorite"])),int(row["updated_at"])))
                    new_id=cur.lastrowid;known.add(key);inserted+=1
                    for name in images.get(row["id"],[]):
                        if cancel and cancel.is_set(): raise ValueError("Миграция отменена.")
                        if not isinstance(name,str) or Path(name).name!=name or Path(name).suffix.lower() not in ALLOWED:
                            missing+=1;continue
                        original=source_media/name
                        if original.is_symlink() or not original.is_file() or original.resolve().parent!=source_media or original.stat().st_size>5*1024*1024:
                            missing+=1;continue
                        new_name=uuid.uuid4().hex+original.suffix.lower()
                        target=store.media/new_name
                        copied.append(target)
                        with original.open("rb") as src,target.open("xb") as out:
                            content=src.read(5*1024*1024+1)
                            if len(content)>5*1024*1024: raise ValueError("Вложение слишком большое.")
                            out.write(content)
                        dest.execute("INSERT INTO template_images(template_id,filename) VALUES(?,?)",(new_id,new_name));attachments+=1
        return {"imported":inserted,"skipped":skipped,"images":attachments,"missing":missing}
    except Exception:
        for path in copied: path.unlink(missing_ok=True)
        raise
