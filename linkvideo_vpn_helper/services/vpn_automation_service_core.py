from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService

from linkvideo_vpn_helper.services.vpn_lifecycle import (
    ARCHIVE_DAYS, DAY_NS, LV_AUTOMATION_VERSION, LV_MARKER, QUARANTINE_DAYS,
    SLEEP_DAYS, classify_state, compose_lv_comment, parse_lv_comment,
)

LV_ACTIVITY_SCRIPT = "LV-Activity"
LV_AGING_SCRIPT = "LV-Aging"
LV_RESTORE_SCRIPT = "LV-AutoRestore"
LV_ACTIVITY_SCHED = "LV-Activity"
LV_AGING_SCHED = "LV-Aging"
LV_RESTORE_SCHED = "LV-AutoRestore"
LV_LOG_ACTION = "LV-Auth"
LV_LOG_PREFIX_PPP = "LV-AUTH-PPP"
LV_LOG_PREFIX_L2TP = "LV-AUTH-L2TP"
LV_PAUSE_AGING_MARKER = "LV-Resume-Aging="


@dataclass(slots=True)
class AutomationStatus:
    server: str
    scripts_installed: int = 0
    scripts_expected: int = 3
    schedulers_installed: int = 0
    schedulers_expected: int = 3
    logging_ready: bool = False
    version: str = ""
    activity_enabled: bool = False
    aging_enabled: bool = False
    restore_enabled: bool = False
    logging_enabled: bool = False
    initialized: int = 0
    active: int = 0
    unknown: int = 0
    sleeping: int = 0
    quarantine: int = 0
    archive: int = 0
    manual: int = 0
    activity_run_count: int = 0
    restore_run_count: int = 0
    aging_run_count: int = 0

    @property
    def installed(self) -> bool:
        return (
            self.scripts_installed == self.scripts_expected
            and self.schedulers_installed == self.schedulers_expected
            and self.logging_ready
        )

    @property
    def scripts_ready(self) -> bool:
        return self.scripts_installed == self.scripts_expected

    @property
    def schedulers_ready(self) -> bool:
        return self.schedulers_installed == self.schedulers_expected

    @property
    def installation_detail(self) -> str:
        return (
            f"скрипты {self.scripts_installed}/{self.scripts_expected}, "
            f"планировщики {self.schedulers_installed}/{self.schedulers_expected}, "
            f"логирование {'готово' if self.logging_ready else 'не готово'}"
        )

    @property
    def paused(self) -> bool:
        return self.installed and not (self.activity_enabled or self.restore_enabled or self.aging_enabled)

    @property
    def runtime_enabled(self) -> bool:
        return self.installed and self.activity_enabled and self.restore_enabled

    @property
    def state_text(self) -> str:
        if not self.scripts_ready and self.scripts_installed == 0 and self.schedulers_installed == 0:
            return "Не установлено"
        if not self.installed:
            return "Установлено частично"
        if self.version and self.version != LV_AUTOMATION_VERSION:
            return f"Требуется обновление ({self.version})"
        if self.paused:
            return "Остановлено"
        if self.runtime_enabled:
            return "Работает · карантин включён" if self.aging_enabled else "Работает · карантин выключен"
        return "Частично включено"

@dataclass(slots=True)
class SeedResult:
    server: str
    total: int
    changed: int
    active: int
    sleeping: int
    quarantine: int
    archive: int
    manual: int
    unknown: int


def activity_script_source() -> str:
    refresh_ns = 6 * 60 * 60 * 1_000_000_000
    return f'''# LinkVideo.Helper LV Automation {LV_AUTOMATION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local refreshNs {refresh_ns}\n:foreach a in=[/ppp active find where service="l2tp"] do={{\n    :local u [/ppp active get $a name]\n    :foreach sid in=[/ppp secret find where name=$u] do={{\n        :local c [/ppp secret get $sid comment]\n        :local p [:find $c "{LV_MARKER}"]\n        :local base $c\n        :local last 0\n        :if ($p != nil) do={{\n            :set base [:pick $c 0 $p]\n            :local tail [:pick $c $p [:len $c]]\n            :local lp [:find $tail "|last="]\n            :if ($lp != nil) do={{\n                :local lr [:pick $tail ($lp + 6) [:len $tail]]\n                :local le [:find $lr "|"]\n                :if ($le != nil) do={{ :set last [:tonum [:pick $lr 0 $le]] }}\n            }}\n        }}\n        :if (($last = 0) || (($nowNs - $last) >= $refreshNs)) do={{\n            /ppp secret set $sid comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|ver={LV_AUTOMATION_VERSION}|")\n        }}\n    }}\n}}'''


def aging_script_source() -> str:
    sleep_ns = SLEEP_DAYS * DAY_NS
    quarantine_ns = QUARANTINE_DAYS * DAY_NS
    archive_ns = ARCHIVE_DAYS * DAY_NS
    return f'''# LinkVideo.Helper LV Automation {LV_AUTOMATION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local sleepNs {sleep_ns}\n:local quarantineNs {quarantine_ns}\n:local archiveNs {archive_ns}\n:foreach sid in=[/ppp secret find] do={{\n    :local u [/ppp secret get $sid name]\n    :local c [/ppp secret get $sid comment]\n    :local p [:find $c "{LV_MARKER}"]\n    :if ($p != nil) do={{\n        :local base [:pick $c 0 $p]\n        :local tail [:pick $c $p [:len $c]]\n        :local state "U"\n        :local last 0\n        :local sp [:find $tail "|state="]\n        :if ($sp != nil) do={{\n            :local sr [:pick $tail ($sp + 7) [:len $tail]]\n            :local se [:find $sr "|"]\n            :if ($se != nil) do={{ :set state [:pick $sr 0 $se] }}\n        }}\n        :local lp [:find $tail "|last="]\n        :if ($lp != nil) do={{\n            :local lr [:pick $tail ($lp + 6) [:len $tail]]\n            :local le [:find $lr "|"]\n            :if ($le != nil) do={{ :set last [:tonum [:pick $lr 0 $le]] }}\n        }}\n        :local isActive ([:len [/ppp active find where name=$u]] > 0)\n        :if ($isActive) do={{\n            /ppp secret set $sid comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|ver={LV_AUTOMATION_VERSION}|")\n        }} else={{\n            :if (($state != "M") && ($last > 0)) do={{\n                :local age ($nowNs - $last)\n                :local desired "A"\n                :if ($age >= $archiveNs) do={{ :set desired "R" }} else={{\n                    :if ($age >= $quarantineNs) do={{ :set desired "Q" }} else={{\n                        :if ($age >= $sleepNs) do={{ :set desired "S" }}\n                    }}\n                }}\n                :if (($desired = "Q") || ($desired = "R")) do={{\n                    /ppp secret set $sid disabled=yes\n                }}\n                /ppp secret set $sid comment=($base . "{LV_MARKER}state=" . $desired . "|last=" . $last . "|ver={LV_AUTOMATION_VERSION}|")\n            }}\n        }}\n    }}\n}}'''


def restore_script_source() -> str:
    token = "login failure for user "
    return f'''# LinkVideo.Helper LV Automation {LV_AUTOMATION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local token "{token}"\n:foreach lid in=[/log find where buffer="{LV_LOG_ACTION}"] do={{\n    :local msg [/log get $lid message]\n    :local p [:find $msg $token]\n    :if ($p != nil) do={{\n        :local rest [:pick $msg ($p + [:len $token]) [:len $msg]]\n        :local e [:find $rest " from "]\n        :if ($e != nil) do={{\n            :local u [:pick $rest 0 $e]\n            :foreach sid in=[/ppp secret find where name=$u] do={{\n                :local c [/ppp secret get $sid comment]\n                :local mp [:find $c "{LV_MARKER}"]\n                :if ($mp != nil) do={{\n                    :local base [:pick $c 0 $mp]\n                    :local tail [:pick $c $mp [:len $c]]\n                    :local state "U"\n                    :local sp [:find $tail "|state="]\n                    :if ($sp != nil) do={{\n                        :local sr [:pick $tail ($sp + 7) [:len $tail]]\n                        :local se [:find $sr "|"]\n                        :if ($se != nil) do={{ :set state [:pick $sr 0 $se] }}\n                    }}\n                    :local disabled [/ppp secret get $sid disabled]\n                    :if ((($state = "Q") || ($state = "R")) && ($disabled = true)) do={{\n                        /ppp secret set $sid disabled=no comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|ver={LV_AUTOMATION_VERSION}|")\n                        :log info ("LV AutoRestore: " . $u . " enabled after quarantine login attempt")\n                    }}\n                }}\n            }}\n        }}\n    }}\n}}'''


class VPNAutomationService:
    SCRIPT_SOURCES = {
        LV_ACTIVITY_SCRIPT: activity_script_source,
        LV_AGING_SCRIPT: aging_script_source,
        LV_RESTORE_SCRIPT: restore_script_source,
    }

    @staticmethod
    def _bool(value) -> bool:
        return str(value or "no").strip().lower() in {"yes", "true", "1", "on", "enabled"}

    @staticmethod
    def _find(rows: Iterable[dict], field: str, value: str) -> dict | None:
        want = str(value).strip()
        return next((row for row in rows if str(row.get(field, "") or "").strip() == want), None)

    @staticmethod
    def _upsert_named(api: RouterOSAPIClient, path: str, name: str, params: dict) -> str:
        rows = api.print(path)
        row = VPNAutomationService._find(rows, "name", name)
        if row:
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                raise RuntimeError(f"RouterOS не вернул .id для {path} {name}")
            api.set(path, rid, params)
            return rid
        return api.add(path, {"name": name, **params})

    @staticmethod
    def _upsert_logging_rule(api: RouterOSAPIClient, prefix: str, topics: str, enabled: bool = True) -> None:
        rows = api.print("/system/logging")
        row = VPNAutomationService._find(rows, "prefix", prefix)
        params = {
            "topics": topics,
            "action": LV_LOG_ACTION,
            "regex": "login failure for user",
            "prefix": prefix,
            "disabled": "no" if enabled else "yes",
        }
        if row:
            rid = str(row.get(".id", "") or "").strip()
            if rid:
                api.set("/system/logging", rid, params)
                return
        api.add("/system/logging", params)

    @staticmethod
    def _scheduler_enabled(row: dict | None) -> bool:
        return bool(row) and not VPNAutomationService._bool(row.get("disabled", "no"))

    @staticmethod
    def _resume_aging_from_comment(row: dict | None) -> bool:
        if not row:
            return False
        comment = str(row.get("comment", "") or "")
        match = re.search(rf"{re.escape(LV_PAUSE_AGING_MARKER)}(yes|no)", comment, re.I)
        return bool(match and match.group(1).lower() == "yes")

    @staticmethod
    def _automation_comment(resume_aging: bool | None = None) -> str:
        base = f"LinkVideo.Helper LV Automation {LV_AUTOMATION_VERSION}"
        if resume_aging is None:
            return base
        return f"{base}; {LV_PAUSE_AGING_MARKER}{'yes' if resume_aging else 'no'}"

    def _set_logging_enabled(self, api: RouterOSAPIClient, enabled: bool) -> None:
        rows = api.print("/system/logging")
        for prefix in (LV_LOG_PREFIX_PPP, LV_LOG_PREFIX_L2TP):
            row = self._find(rows, "prefix", prefix)
            if row:
                rid = str(row.get(".id", "") or "").strip()
                if rid:
                    api.set("/system/logging", rid, {"disabled": "no" if enabled else "yes"})

    def _ensure_components(self, api: RouterOSAPIClient, *, preserve_pause: bool = True) -> None:
        """Create/repair the complete LV automation set in one RouterOS session.

        This is intentionally idempotent: missing scripts, schedulers, logging action
        or logging rules are recreated. Existing operator pause/quarantine state is
        preserved when possible.
        """
        existing_sched = api.print("/system/scheduler")
        activity_row = self._find(existing_sched, "name", LV_ACTIVITY_SCHED)
        restore_row = self._find(existing_sched, "name", LV_RESTORE_SCHED)
        aging_row = self._find(existing_sched, "name", LV_AGING_SCHED)
        had_runtime = bool(activity_row or restore_row or aging_row)
        activity_enabled = self._scheduler_enabled(activity_row)
        restore_enabled = self._scheduler_enabled(restore_row)
        aging_enabled = self._scheduler_enabled(aging_row)
        was_paused = preserve_pause and had_runtime and not (activity_enabled or restore_enabled or aging_enabled)
        resume_aging = self._resume_aging_from_comment(activity_row) if was_paused else aging_enabled

        for name, factory in self.SCRIPT_SOURCES.items():
            self._upsert_named(api, "/system/script", name, {
                "source": factory(),
                "comment": f"LinkVideo.Helper LV Automation {LV_AUTOMATION_VERSION}",
                "dont-require-permissions": "no",
                "policy": "read,write,policy,test,password,sensitive",
            })

        self._upsert_named(api, "/system/logging/action", LV_LOG_ACTION, {
            "target": "memory",
            "memory-lines": "300",
            "memory-stop-on-full": "no",
        })
        self._upsert_logging_rule(api, LV_LOG_PREFIX_PPP, "ppp", enabled=not was_paused)
        self._upsert_logging_rule(api, LV_LOG_PREFIX_L2TP, "l2tp", enabled=not was_paused)

        activity_comment = self._automation_comment(resume_aging if was_paused else None)
        self._upsert_named(api, "/system/scheduler", LV_ACTIVITY_SCHED, {
            "interval": "10m",
            "start-time": "00:00:00",
            "on-event": LV_ACTIVITY_SCRIPT,
            "disabled": "yes" if was_paused else "no",
            "comment": activity_comment,
            "policy": "read,write,policy,test,password,sensitive",
        })
        self._upsert_named(api, "/system/scheduler", LV_RESTORE_SCHED, {
            "interval": "1m",
            "start-time": "00:00:00",
            "on-event": LV_RESTORE_SCRIPT,
            "disabled": "yes" if was_paused else "no",
            "comment": self._automation_comment(),
            "policy": "read,write,policy,test,password,sensitive",
        })
        self._upsert_named(api, "/system/scheduler", LV_AGING_SCHED, {
            "interval": "1d",
            "start-time": "03:20:00",
            "on-event": LV_AGING_SCRIPT,
            "disabled": "yes" if was_paused or not aging_enabled else "no",
            "comment": self._automation_comment(),
            "policy": "read,write,policy,test,password,sensitive",
        })

    def install_or_update(self, server: str, creds: SessionCredentials) -> AutomationStatus:
        """Install or repair the complete LV automation set and verify it."""
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            self._ensure_components(api, preserve_pause=True)
        status = self.get_status(server, creds)
        if not status.installed:
            raise RuntimeError(
                "RouterOS не подтвердил полную установку LV-автоматики: " + status.installation_detail
            )
        return status

    @staticmethod
    def _set_menu_enabled(api, path: str, row: dict, enabled: bool) -> None:
        rid = str(row.get(".id", "") or "").strip()
        if not rid:
            raise RuntimeError(f"RouterOS не вернул ID для {path}")
        # Для RouterOS используем штатные enable/disable команды. Это надёжнее,
        # чем рассчитывать на преобразование disabled=yes/no разными версиями API.
        method = getattr(api, "enable" if enabled else "disable", None)
        if callable(method):
            method(path, rid)
        else:
            api.set(path, rid, {"disabled": "no" if enabled else "yes"})

    @staticmethod
    def _device_mode_hint(api) -> str:
        try:
            rows = api.print("/system/device-mode")
            row = rows[0] if rows else {}
            mode = str(row.get("mode", "") or "").strip()
            flagged = str(row.get("flagged", "") or "").strip().lower()
            scheduler = str(row.get("scheduler", "") or "").strip().lower()
            bits = []
            if mode:
                bits.append(f"device-mode={mode}")
            if flagged:
                bits.append(f"flagged={flagged}")
            if scheduler:
                bits.append(f"scheduler={scheduler}")
            return ", ".join(bits)
        except Exception:
            return ""

    def set_automation_enabled(self, server: str, creds: SessionCredentials, enabled: bool) -> AutomationStatus:
        """Полностью остановить/запустить автономную LV-автоматику и проверить результат.

        После команды Helper повторно читает Scheduler. Если RouterOS не подтвердил
        переключение, операция считается ошибкой вместо ложного "готово".
        """
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            sched = api.print("/system/scheduler")
            activity_row = self._find(sched, "name", LV_ACTIVITY_SCHED)
            restore_row = self._find(sched, "name", LV_RESTORE_SCHED)
            aging_row = self._find(sched, "name", LV_AGING_SCHED)
            if not (activity_row and restore_row and aging_row):
                # Старые/частично установленные версии могли оставить только /system script.
                # Пользователь явно нажал «Запустить LV», поэтому безопасно восстанавливаем
                # недостающие служебные компоненты и продолжаем запуск.
                self._ensure_components(api, preserve_pause=True)
                sched = api.print("/system/scheduler")
                activity_row = self._find(sched, "name", LV_ACTIVITY_SCHED)
                restore_row = self._find(sched, "name", LV_RESTORE_SCHED)
                aging_row = self._find(sched, "name", LV_AGING_SCHED)
                if not (activity_row and restore_row and aging_row):
                    missing = []
                    if not activity_row: missing.append(LV_ACTIVITY_SCHED)
                    if not restore_row: missing.append(LV_RESTORE_SCHED)
                    if not aging_row: missing.append(LV_AGING_SCHED)
                    raise RuntimeError("Не удалось восстановить Scheduler LV: " + ", ".join(missing))

            activity_is_on = self._scheduler_enabled(activity_row)
            restore_is_on = self._scheduler_enabled(restore_row)
            aging_is_on = self._scheduler_enabled(aging_row)
            currently_paused = not (activity_is_on or restore_is_on or aging_is_on)

            if enabled:
                desired_aging = self._resume_aging_from_comment(activity_row) if currently_paused else aging_is_on
                # Сохраняем желаемое состояние карантина в комментарии Activity.
                aid = str(activity_row.get(".id", "") or "").strip()
                if aid:
                    api.set("/system/scheduler", aid, {"comment": self._automation_comment()})
                self._set_menu_enabled(api, "/system/scheduler", activity_row, True)
                self._set_menu_enabled(api, "/system/scheduler", restore_row, True)
                self._set_menu_enabled(api, "/system/scheduler", aging_row, desired_aging)
                self._set_logging_enabled(api, True)
            else:
                resume_aging = self._resume_aging_from_comment(activity_row) if currently_paused else aging_is_on
                aid = str(activity_row.get(".id", "") or "").strip()
                if aid:
                    api.set("/system/scheduler", aid, {"comment": self._automation_comment(resume_aging)})
                self._set_menu_enabled(api, "/system/scheduler", activity_row, False)
                self._set_menu_enabled(api, "/system/scheduler", restore_row, False)
                self._set_menu_enabled(api, "/system/scheduler", aging_row, False)
                self._set_logging_enabled(api, False)

            # Проверяем RouterOS сразу в той же сессии. Это ловит случаи, когда
            # scheduler запрещён device-mode/flagged режимом или изменение не применилось.
            verify = api.print("/system/scheduler")
            va = self._find(verify, "name", LV_ACTIVITY_SCHED)
            vr = self._find(verify, "name", LV_RESTORE_SCHED)
            vg = self._find(verify, "name", LV_AGING_SCHED)
            runtime_ok = self._scheduler_enabled(va) and self._scheduler_enabled(vr)
            paused_ok = not (self._scheduler_enabled(va) or self._scheduler_enabled(vr) or self._scheduler_enabled(vg))
            if enabled and not runtime_ok:
                hint = self._device_mode_hint(api)
                extra = f" ({hint})" if hint else ""
                raise RuntimeError("RouterOS не подтвердил запуск LV Scheduler-задач" + extra)
            if (not enabled) and not paused_ok:
                raise RuntimeError("RouterOS не подтвердил остановку всех LV Scheduler-задач")

        status = self.get_status(server, creds)
        if enabled and not status.runtime_enabled:
            raise RuntimeError("После повторной проверки LV всё ещё отображается остановленной")
        if (not enabled) and not status.paused:
            raise RuntimeError("После повторной проверки LV всё ещё отображается запущенной")
        return status

    def set_quarantine_enabled(self, server: str, creds: SessionCredentials, enabled: bool) -> AutomationStatus:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            rows = api.print("/system/scheduler")
            row = self._find(rows, "name", LV_AGING_SCHED)
            if not row:
                raise RuntimeError("LV-Aging не установлен на сервере")
            rid = str(row.get(".id", "") or "").strip()
            if not rid:
                raise RuntimeError("RouterOS не вернул ID scheduler LV-Aging")
            method = getattr(api, "enable" if enabled else "disable", None)
            if callable(method):
                method("/system/scheduler", rid)
            else:
                api.set("/system/scheduler", rid, {"disabled": "no" if enabled else "yes"})
        status = self.get_status(server, creds)
        if bool(status.aging_enabled) != bool(enabled):
            raise RuntimeError("RouterOS не подтвердил изменение состояния LV-Aging")
        return status

    def seed_lifecycle(self, server: str, creds: SessionCredentials) -> SeedResult:
        """Initialize LV metadata without disabling or enabling any account."""
        now_ns = time.time_ns()
        vpn = VPNService()
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            try:
                rows = api.print("/ppp/secret", {".proplist": ".id,name,disabled,last-logged-out,comment"})
            except Exception:
                rows = api.print("/ppp/secret")
            try:
                active_rows = api.print("/ppp/active", {".proplist": ".id,name,service"})
            except Exception:
                active_rows = api.print("/ppp/active")
            active_names = {
                str(x.get("name", "") or "").strip()
                for x in active_rows
                if str(x.get("service", "") or "").strip().lower() in {"", "l2tp"}
            }

            counts = {"A": 0, "S": 0, "Q": 0, "R": 0, "M": 0, "U": 0}
            changed = 0
            for row in rows:
                rid = str(row.get(".id", "") or "").strip()
                login = str(row.get("name", "") or "").strip()
                if not rid or not login:
                    continue
                original = str(row.get("comment", "") or "")
                parsed = parse_lv_comment(original)
                if parsed.last_ns > 0 or parsed.state != "U":
                    state = parsed.state
                    last_ns = parsed.last_ns
                    if login in active_names:
                        state, last_ns = "A", now_ns
                else:
                    disabled = self._bool(row.get("disabled", "no"))
                    if login in active_names:
                        state, last_ns = "A", now_ns
                    else:
                        raw_last = str(row.get("last-logged-out", "") or row.get("last_logged_out", "") or "")
                        last_dt = vpn.parse_router_datetime(raw_last)
                        if disabled:
                            state, last_ns = "M", (int(last_dt.timestamp() * 1_000_000_000) if last_dt else 0)
                        elif last_dt:
                            last_ns = int(last_dt.timestamp() * 1_000_000_000)
                            state = classify_state(last_ns, False, False)
                        else:
                            state, last_ns = "U", 0
                counts[state if state in counts else "U"] += 1
                new_comment = compose_lv_comment(parsed.base_comment, state, last_ns)
                if new_comment != original:
                    api.set("/ppp/secret", rid, {"comment": new_comment})
                    changed += 1
        return SeedResult(
            server=server,
            total=sum(counts.values()),
            changed=changed,
            active=counts["A"],
            sleeping=counts["S"],
            quarantine=counts["Q"],
            archive=counts["R"],
            manual=counts["M"],
            unknown=counts["U"],
        )

    def mark_manual_state(self, server: str, creds: SessionCredentials, login: str, enabled: bool) -> None:
        """Keep manual Helper actions distinct from automatic quarantine."""
        now_ns = time.time_ns()
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            rows = api.print("/ppp/secret")
            row = self._find(rows, "name", login)
            if not row:
                raise ValueError("PPP Secret не найден")
            rid = str(row.get(".id", "") or "").strip()
            parsed = parse_lv_comment(str(row.get("comment", "") or ""))
            state = "A" if enabled else "M"
            last_ns = now_ns if enabled else parsed.last_ns
            api.set("/ppp/secret", rid, {
                "disabled": "no" if enabled else "yes",
                "comment": compose_lv_comment(parsed.base_comment, state, last_ns),
            })

    def get_status(self, server: str, creds: SessionCredentials) -> AutomationStatus:
        with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
            scripts = api.print("/system/script")
            sched = api.print("/system/scheduler")
            actions = api.print("/system/logging/action")
            rules = api.print("/system/logging")
            try:
                secrets = api.print("/ppp/secret", {".proplist": "name,comment,disabled"})
            except Exception:
                secrets = api.print("/ppp/secret")

        wanted = {LV_ACTIVITY_SCRIPT, LV_AGING_SCRIPT, LV_RESTORE_SCRIPT}
        found = [x for x in scripts if str(x.get("name", "") or "").strip() in wanted]
        versions = []
        for row in found:
            comment = str(row.get("comment", "") or "")
            m = re.search(r"LV Automation\s+([^\s]+)", comment)
            if m:
                versions.append(m.group(1))
        version = versions[0] if versions and all(v == versions[0] for v in versions) else ("mixed" if versions else "")

        def sched_enabled(name: str) -> bool:
            row = self._find(sched, "name", name)
            return bool(row) and not self._bool(row.get("disabled", "no"))

        def sched_run_count(name: str) -> int:
            row = self._find(sched, "name", name) or {}
            try:
                return int(str(row.get("run-count", "0") or "0"))
            except Exception:
                return 0

        action_ok = self._find(actions, "name", LV_LOG_ACTION) is not None
        prefixes = {str(x.get("prefix", "") or "").strip() for x in rules}
        logging_ready = action_ok and LV_LOG_PREFIX_PPP.strip() in prefixes and LV_LOG_PREFIX_L2TP.strip() in prefixes
        scheduler_names = {str(x.get("name", "") or "").strip() for x in sched}
        schedulers_installed = len({LV_ACTIVITY_SCHED, LV_AGING_SCHED, LV_RESTORE_SCHED} & scheduler_names)
        managed_rules = [x for x in rules if str(x.get("prefix", "") or "").strip() in {LV_LOG_PREFIX_PPP, LV_LOG_PREFIX_L2TP}]
        logging_enabled = bool(managed_rules) and all(not self._bool(x.get("disabled", "no")) for x in managed_rules)

        counts = {"A": 0, "S": 0, "Q": 0, "R": 0, "M": 0, "U": 0}
        initialized = 0
        for row in secrets:
            meta = parse_lv_comment(str(row.get("comment", "") or ""))
            if LV_MARKER in str(row.get("comment", "") or ""):
                initialized += 1
            if meta.state in counts:
                counts[meta.state] += 1

        return AutomationStatus(
            server=server,
            scripts_installed=len(found),
            schedulers_installed=schedulers_installed,
            logging_ready=logging_ready,
            version=version,
            activity_enabled=sched_enabled(LV_ACTIVITY_SCHED),
            aging_enabled=sched_enabled(LV_AGING_SCHED),
            restore_enabled=sched_enabled(LV_RESTORE_SCHED),
            logging_enabled=logging_enabled,
            initialized=initialized,
            active=counts["A"],
            unknown=counts["U"],
            sleeping=counts["S"],
            quarantine=counts["Q"],
            archive=counts["R"],
            manual=counts["M"],
            activity_run_count=sched_run_count(LV_ACTIVITY_SCHED),
            restore_run_count=sched_run_count(LV_RESTORE_SCHED),
            aging_run_count=sched_run_count(LV_AGING_SCHED),
        )
