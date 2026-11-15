"""Headless projections and operator reports for 3.0.27 archive diagnostics."""
from datetime import datetime, timezone, timedelta
import re

def timestamp(t,offset):
    if t is None: return "—"
    return datetime.fromtimestamp(t,timezone(timedelta(hours=offset))).strftime("%d.%m.%Y %H:%M:%S")

def duration(t):
    h,rem=divmod(max(0,round(t)),3600);m,s=divmod(rem,60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def host(value):
    # Never project URLs, signatures, raw HTTP errors or tokens.
    raw=str(value or "")
    return raw if re.fullmatch(r"[A-Za-z0-9_.-]{1,253}",raw) else "—"

def project(d):
    depth=d.camera.raw.get("archive_depth") if isinstance(d.camera.raw,dict) else None
    try: depth=int(depth)
    except (ValueError,TypeError): depth=None
    if depth is not None and not 0<depth<=36500: depth=None
    exact=bool(d.slices)
    return {"camera":d.camera.label,"camera_id":d.camera.camera_id,"server":host(d.camera.server),"timezone":d.camera.timezone_offset,
            "requested_start":d.requested_start,"requested_end":d.requested_end,"requested_seconds":d.requested_duration,"covered_seconds":d.covered_duration,
            "coverage":d.coverage_percent,"downloadable":d.has_downloadable_archive,"exact_timeline":exact,
            "hls_only":bool(d.hls_fallback_url and not exact),"hls_segments":d.hls_fallback_segments,
            "hls_hosts":list(dict.fromkeys(host(h) for h in (d.hls_fallback_hosts or ([d.hls_fallback_host] if d.hls_fallback_host else [])))),
            "checked":len(d.checked_hosts),"checked_hosts":list(dict.fromkeys(host(h) for h in d.checked_hosts)),"error_count":len(d.errors),"archive_depth":depth,
            "gaps":[{"start":g.start,"end":g.end,"duration":g.duration} for g in d.gaps] if exact else [],
            "slices":[{"start":s.start,"end":s.end,"host":host(s.host),"source":s.source} for s in d.slices],
            "moves":[{"from":host(e.server_from),"to":host(e.server_to),"start":e.start,"end":e.end,
                      "overlap":any(e.overlaps(g.start,g.end) for g in d.gaps) if exact else None} for e in d.reserve_events]}

def comparison(main,rows):
    out=[]
    for cid,d,error in rows:
        if error or d is None:
            out.append({"id":cid,"ok":False});continue
        p=project(d)
        overlap=None
        if main.slices and d.slices:
            overlap=sum(max(0,min(g.end,x.end)-max(g.start,x.start)) for g in main.gaps for x in d.gaps)
        out.append({"id":cid,"ok":True,"camera":p["camera"],"server":p["server"],"timezone":p["timezone"],
                    "coverage":p["coverage"],"exact_timeline":p["exact_timeline"],"hls_only":p["hls_only"],
                    "gaps":p["gaps"],"slices":p["slices"],"overlap_seconds":overlap,"error_count":p["error_count"]})
    return sorted(out,key=lambda x:x["id"])

def make_reports(main,diagnosis,address,server,debug):
    p=project(main);tz=p["timezone"]
    fmt=lambda t:timestamp(t,tz)
    period=f"{fmt(p['requested_start'])} — {fmt(p['requested_end'])} (UTC{tz:+d})"
    status="HLS найден; точный DVR-таймлайн не подтверждён." if p["hls_only"] else (
        "Подтверждённые DVR-интервалы найдены." if p["exact_timeline"] else
        "Архив не подтверждён на проверенных источниках; отсутствие записи не доказано.")
    summary=["ИТОГ ДИАГНОСТИКИ",f"Камера: {p['camera']}",f"Текущий сервер: {p['server']}",f"Период камеры: {period}",
        f"Запрошено: {duration(p['requested_seconds'])}",f"{'Оценка HLS' if p['hls_only'] else 'Подтверждено DVR'}: {duration(p['covered_seconds'])} ({p['coverage']:.1f}%)",status,
        f"Пропуски: {len(p['gaps'])}" if p["exact_timeline"] else "Точные интервалы пропусков неизвестны.",
        "",diagnosis.title,diagnosis.side_text,f"Уверенность: {diagnosis.confidence_text}",diagnosis.summary]
    summary.extend(f"- {e.title}: {e.detail}" for e in diagnosis.evidence)
    summary.extend(f"Ограничение: {e}" for e in diagnosis.cautions)
    breaks=["РАЗРЫВЫ ПО ВРЕМЕНИ",period]
    if not p["exact_timeline"]: breaks.append("Точный DVR-таймлайн не подтверждён. Нельзя объявлять весь период подтверждённым разрывом.")
    elif not p["gaps"]: breaks.append("Подтверждённых пропусков нет.")
    for g in p["gaps"]:
        breaks.append(f"{fmt(g['start'])} — {fmt(g['end'])} ({duration(g['duration'])})")
        gd=next((x for x in diagnosis.gaps if x.start==g["start"] and x.end==g["end"]),None)
        if gd: breaks.extend([gd.title,gd.summary])
    breaks.extend(["","Подтверждённые участки:"])
    breaks.extend(f"{fmt(s['start'])} — {fmt(s['end'])} · {s['host']} · {s['source']}" for s in p["slices"])
    moves=["ПЕРЕЕЗДЫ / RESERVE-TRANSFERS",f"Период камеры: {period}",
           f"Период журнала B2O: {timestamp(p['requested_start'],7)} — {timestamp(p['requested_end'],7)} UTC+7",
           "Сопоставление по одному абсолютному epoch-периоду."]
    if not p["moves"]: moves.append("События переезда за данный период не подтверждены.")
    for e in p["moves"]:
        moves.append(f"{e['from']} → {e['to']} · {timestamp(e['start'],7)} — {timestamp(e['end'],7)} UTC+7"+(" · пересекает подтверждённый разрыв" if e["overlap"] is True else ""))
    compare=["СРАВНЕНИЕ КАМЕР",f"Основная: {p['camera']}"]
    for label,rows in (("Камеры адреса",address),("Камеры текущего vcore",server)):
        compare.extend(["",label])
        if not rows: compare.append("Нет проверенных кандидатов.")
        for r in rows:
            if not r["ok"]: compare.append(f"{r['id']}: проверка не подтверждена.");continue
            gapinfo=f"пропусков {len(r['gaps'])}" if r["exact_timeline"] else "точные интервалы неизвестны"
            overlap=duration(r["overlap_seconds"]) if r["overlap_seconds"] is not None else "не подтверждено"
            compare.append(f"{r['camera']} · {r['server']} · {r['coverage']:.1f}% · {gapinfo} · совпадение {overlap}")
    technical=["ТЕХНИЧЕСКИЕ ДЕТАЛИ",f"Camera ID: {p['camera_id']}",f"Период: {period}",
               "Проверенные DVR:",*p["checked_hosts"],f"Ошибок проверок: {p['error_count']}",
               f"Страниц B2O: {debug.get('pages',0)}",f"Кандидатов: {debug.get('found_total',0)}",
               f"Online на том же сервере: {debug.get('online_same_server',0)}",
               f"Ошибок подбора: {debug.get('error_count',0)}",
               f"Проверенных камер адреса: {sum(x['ok'] for x in address)}/{len(address)}",
               f"Проверенных камер vcore: {sum(x['ok'] for x in server)}/{len(server)}",
               "Секреты, URL потоков и необработанные ответы не включены."]
    return {k:"\n".join(v) for k,v in {"summary":summary,"breaks":breaks,"moves":moves,"compare":compare,"technical":technical}.items()}

def archive_filename(d):
    p=project(d)
    name=re.sub(r"[^A-Za-z0-9_.-]+","_",str(p["camera"]))[:80].strip("._") or "camera"
    fmt=lambda t:datetime.fromtimestamp(t,timezone(timedelta(hours=p["timezone"]))).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{name}_{fmt(p['requested_start'])}_{fmt(p['requested_end'])}.mp4"
