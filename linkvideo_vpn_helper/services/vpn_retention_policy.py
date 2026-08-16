from __future__ import annotations

"""Automatic RouterOS lifecycle/retention policy for LinkVideo VPN accounts.

RouterOS does not expose a reliable creation timestamp for PPP secrets. Helper
therefore stores its own ``created`` timestamp in the existing LV marker. New
accounts get it immediately; legacy accounts without a known activity date start
safe tracking from the day the policy is installed/seeded.
"""

from dataclasses import dataclass
import re
import time

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.app_logging import event, error


RETENTION_VERSION = "1.1.0"
LV_MARKER = "|LV1|"
DAY_NS = 86_400_000_000_000
SLEEP_DAYS = 30
QUARANTINE_DAYS = 90
DELETE_DAYS = 365

_INSTALLED = False


@dataclass(slots=True)
class ExtendedLifecycle:
    base_comment: str = ""
    state: str = "U"
    last_ns: int = 0
    created_ns: int = 0
    reason: str = ""
    version: str = RETENTION_VERSION


def parse_extended_comment(comment: str) -> ExtendedLifecycle:
    raw = str(comment or "")
    pos = raw.find(LV_MARKER)
    if pos < 0:
        return ExtendedLifecycle(base_comment=raw.rstrip())
    base = raw[:pos].rstrip()
    tail = raw[pos:]

    def text(name: str, default: str = "") -> str:
        match = re.search(rf"\|{re.escape(name)}=([^|]*)\|", tail)
        return str(match.group(1) if match else default)

    def number(name: str) -> int:
        raw_value = text(name, "0")
        try:
            return max(0, int(raw_value or 0))
        except Exception:
            return 0

    return ExtendedLifecycle(
        base_comment=base,
        state=text("state", "U") or "U",
        last_ns=number("last"),
        created_ns=number("created"),
        reason=text("reason", ""),
        version=text("ver", RETENTION_VERSION) or RETENTION_VERSION,
    )


def compose_extended_comment(
    base_comment: str,
    state: str,
    last_ns: int,
    created_ns: int,
    reason: str,
    version: str = RETENTION_VERSION,
) -> str:
    base = str(base_comment or "").strip()
    safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "").strip())[:48]
    marker = (
        f"{LV_MARKER}state={str(state or 'U').upper()}|last={max(0, int(last_ns or 0))}|"
        f"created={max(0, int(created_ns or 0))}|reason={safe_reason}|ver={version}|"
    )
    return f"{base} {marker}".strip()


def activity_script_source() -> str:
    refresh_ns = 6 * 60 * 60 * 1_000_000_000
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local refreshNs {refresh_ns}\n:foreach a in=[/ppp active find where service="l2tp"] do={{\n    :local u [/ppp active get $a name]\n    :foreach sid in=[/ppp secret find where name=$u] do={{\n        :local c [/ppp secret get $sid comment]\n        :local p [:find $c "{LV_MARKER}"]\n        :local base $c\n        :local last 0\n        :local created $nowNs\n        :if ($p != nil) do={{\n            :set base [:pick $c 0 $p]\n            :local tail [:pick $c $p [:len $c]]\n            :local lp [:find $tail "|last="]\n            :if ($lp != nil) do={{\n                :local lr [:pick $tail ($lp + 6) [:len $tail]]\n                :local le [:find $lr "|"]\n                :if ($le != nil) do={{ :set last [:tonum [:pick $lr 0 $le]] }}\n            }}\n            :local cp [:find $tail "|created="]\n            :if ($cp != nil) do={{\n                :local cr [:pick $tail ($cp + 9) [:len $tail]]\n                :local ce [:find $cr "|"]\n                :if ($ce != nil) do={{ :set created [:tonum [:pick $cr 0 $ce]] }}\n            }}\n        }}\n        :if ($created = 0) do={{ :set created $nowNs }}\n        :if (($last = 0) || (($nowNs - $last) >= $refreshNs)) do={{\n            /ppp secret set $sid comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|created=" . $created . "|reason=activity|ver={RETENTION_VERSION}|")\n        }}\n    }}\n}}'''


def aging_script_source() -> str:
    sleep_ns = SLEEP_DAYS * DAY_NS
    quarantine_ns = QUARANTINE_DAYS * DAY_NS
    delete_ns = DELETE_DAYS * DAY_NS
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local sleepNs {sleep_ns}\n:local quarantineNs {quarantine_ns}\n:local deleteNs {delete_ns}\n:foreach sid in=[/ppp secret find] do={{\n    :local u [/ppp secret get $sid name]\n    :local c [/ppp secret get $sid comment]\n    :local p [:find $c "{LV_MARKER}"]\n    :local base $c\n    :local state "U"\n    :local last 0\n    :local created $nowNs\n    :if ($p != nil) do={{\n        :set base [:pick $c 0 $p]\n        :local tail [:pick $c $p [:len $c]]\n        :local sp [:find $tail "|state="]\n        :if ($sp != nil) do={{\n            :local sr [:pick $tail ($sp + 7) [:len $tail]]\n            :local se [:find $sr "|"]\n            :if ($se != nil) do={{ :set state [:pick $sr 0 $se] }}\n        }}\n        :local lp [:find $tail "|last="]\n        :if ($lp != nil) do={{\n            :local lr [:pick $tail ($lp + 6) [:len $tail]]\n            :local le [:find $lr "|"]\n            :if ($le != nil) do={{ :set last [:tonum [:pick $lr 0 $le]] }}\n        }}\n        :local cp [:find $tail "|created="]\n        :if ($cp != nil) do={{\n            :local cr [:pick $tail ($cp + 9) [:len $tail]]\n            :local ce [:find $cr "|"]\n            :if ($ce != nil) do={{ :set created [:tonum [:pick $cr 0 $ce]] }}\n        }}\n    }}\n    :if ($created = 0) do={{ :set created $nowNs }}\n    :local isActive ([:len [/ppp active find where name=$u]] > 0)\n    :local disabled [/ppp secret get $sid disabled]\n    :if ($isActive) do={{\n        /ppp secret set $sid disabled=no comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|created=" . $created . "|reason=activity|ver={RETENTION_VERSION}|")\n    }} else={{\n        :if (($disabled = true) && ($state != "Q")) do={{\n            /ppp secret set $sid comment=($base . "{LV_MARKER}state=M|last=" . $last . "|created=" . $created . "|reason=manual_or_external_disabled|ver={RETENTION_VERSION}|")\n        }} else={{\n            :local reference $last\n            :if ($reference = 0) do={{ :set reference $created }}\n            :local age ($nowNs - $reference)\n            :if ($age >= $deleteNs) do={{\n                :local deleteReason "inactive_365"\n                :if ($last = 0) do={{ :set deleteReason "never_active_365" }}\n                :local profile [/ppp secret get $sid profile]\n                :local remote [/ppp secret get $sid remote-address]\n                :if (([:len $remote] = 0) && ([:len $profile] > 0)) do={{\n                    :foreach pid in=[/ppp profile find where name=$profile] do={{\n                        :set remote [/ppp profile get $pid remote-address]\n                    }}\n                }}\n                :log warning ("LV RETENTION DELETE user=" . $u . " reason=" . $deleteReason)\n                :foreach nid in=[/ip firewall nat find where comment=$u] do={{ /ip firewall nat remove $nid }}\n                :if ([:len $remote] > 0) do={{\n                    :foreach nid in=[/ip firewall nat find where to-addresses=$remote] do={{ /ip firewall nat remove $nid }}\n                }}\n                :local profileUses 0\n                :if ([:len $profile] > 0) do={{ :set profileUses [:len [/ppp secret find where profile=$profile]] }}\n                /ppp secret remove $sid\n                :if (($profileUses <= 1) && ($profile != "") && ($profile != "default") && ($profile != "default-encryption")) do={{\n                    :foreach pid in=[/ppp profile find where name=$profile] do={{ /ppp profile remove $pid }}\n                }}\n            }} else={{\n                :if ($age >= $quarantineNs) do={{\n                    /ppp secret set $sid disabled=yes comment=($base . "{LV_MARKER}state=Q|last=" . $last . "|created=" . $created . "|reason=inactive_90|ver={RETENTION_VERSION}|")\n                }} else={{\n                    :if ($age >= $sleepNs) do={{\n                        /ppp secret set $sid comment=($base . "{LV_MARKER}state=S|last=" . $last . "|created=" . $created . "|reason=inactive_30|ver={RETENTION_VERSION}|")\n                    }} else={{\n                        :local keepState "A"\n                        :local keepReason "tracked"\n                        :if ($last = 0) do={{ :set keepState "U"; :set keepReason "never_active_tracking" }}\n                        /ppp secret set $sid comment=($base . "{LV_MARKER}state=" . $keepState . "|last=" . $last . "|created=" . $created . "|reason=" . $keepReason . "|ver={RETENTION_VERSION}|")\n                    }}\n                }}\n            }}\n        }}\n    }}\n}}'''


def restore_script_source() -> str:
    token = "login failure for user "
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}\n:local nowNs [:tonsec [:timestamp]]\n:local token "{token}"\n:foreach lid in=[/log find where buffer="LVAuth"] do={{\n    :local msg [/log get $lid message]\n    :local p [:find $msg $token]\n    :if ($p != nil) do={{\n        :local rest [:pick $msg ($p + [:len $token]) [:len $msg]]\n        :local e [:find $rest " from "]\n        :if ($e != nil) do={{\n            :local u [:pick $rest 0 $e]\n            :foreach sid in=[/ppp secret find where name=$u] do={{\n                :local c [/ppp secret get $sid comment]\n                :local mp [:find $c "{LV_MARKER}"]\n                :if ($mp != nil) do={{\n                    :local base [:pick $c 0 $mp]\n                    :local tail [:pick $c $mp [:len $c]]\n                    :local state "U"\n                    :local created $nowNs\n                    :local sp [:find $tail "|state="]\n                    :if ($sp != nil) do={{\n                        :local sr [:pick $tail ($sp + 7) [:len $tail]]\n                        :local se [:find $sr "|"]\n                        :if ($se != nil) do={{ :set state [:pick $sr 0 $se] }}\n                    }}\n                    :local cp [:find $tail "|created="]\n                    :if ($cp != nil) do={{\n                        :local cr [:pick $tail ($cp + 9) [:len $tail]]\n                        :local ce [:find $cr "|"]\n                        :if ($ce != nil) do={{ :set created [:tonum [:pick $cr 0 $ce]] }}\n                    }}\n                    :local disabled [/ppp secret get $sid disabled]\n                    :if (($state = "Q") && ($disabled = true)) do={{\n                        /ppp secret set $sid disabled=no comment=($base . "{LV_MARKER}state=A|last=" . $nowNs . "|created=" . $created . "|reason=auto_restore|ver={RETENTION_VERSION}|")\n                        :log info ("LV AutoRestore: " . $u . " enabled after quarantine login attempt")\n                    }}\n                }}\n            }}\n        }}\n    }}\n}}'''


def _ensure_tracking_for_server(server, creds) -> int:
    """Add created/reason metadata without backdating unknown legacy accounts."""
    from linkvideo_vpn_helper.services.vpn_service import VPNService

    now_ns = time.time_ns()
    changed = 0
    vpn = VPNService()
    with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
        try:
            rows = api.print("/ppp/secret", {".proplist": ".id,name,disabled,last-logged-out,comment"})
        except Exception:
            rows = api.print("/ppp/secret")
        active_names = {
            str(row.get("name", "") or "").strip()
            for row in api.print("/ppp/active")
            if str(row.get("name", "") or "").strip()
        }
        for row in rows:
            rid = str(row.get(".id", "") or "").strip()
            login = str(row.get("name", "") or "").strip()
            if not rid or not login:
                continue
            original = str(row.get("comment", "") or "")
            meta = parse_extended_comment(original)
            if meta.created_ns > 0 and meta.version == RETENTION_VERSION:
                continue

            last_ns = int(meta.last_ns or 0)
            raw_last = str(row.get("last-logged-out", "") or row.get("last_logged_out", "") or "")
            if last_ns <= 0 and raw_last:
                try:
                    last_dt = vpn.parse_router_datetime(raw_last)
                    if last_dt:
                        last_ns = int(last_dt.timestamp() * 1_000_000_000)
                except Exception:
                    pass

            created_ns = int(meta.created_ns or now_ns)
            disabled = str(row.get("disabled", "no") or "no").strip().lower() in {"yes", "true", "1", "on"}
            if login in active_names:
                state, last_ns, reason = "A", now_ns, "activity"
            elif disabled and meta.state not in {"Q"}:
                state, reason = "M", "manual_or_external_disabled"
            elif last_ns > 0:
                age_days = max(0, int((now_ns - last_ns) // DAY_NS))
                if age_days >= QUARANTINE_DAYS:
                    state, reason = "Q", "inactive_90"
                elif age_days >= SLEEP_DAYS:
                    state, reason = "S", "inactive_30"
                else:
                    state, reason = "A", "tracked"
            else:
                state, reason = "U", "never_active_tracking"

            new_comment = compose_extended_comment(meta.base_comment, state, last_ns, created_ns, reason)
            if new_comment != original:
                api.set("/ppp/secret", rid, {"comment": new_comment})
                changed += 1
    return changed


def _mark_created_accounts(server, creds, records) -> None:
    now_ns = time.time_ns()
    logins = [str(getattr(record, "login", "") or "").strip() for record in list(records or [])]
    logins = [login for login in logins if login]
    if not logins:
        return
    with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
        for login in logins:
            try:
                rows = api.print("/ppp/secret", {"?name=": login})
                if not rows:
                    rows = [row for row in api.print("/ppp/secret") if str(row.get("name", "") or "").strip() == login]
                if not rows:
                    continue
                row = rows[0]
                rid = str(row.get(".id", "") or "").strip()
                if not rid:
                    continue
                meta = parse_extended_comment(str(row.get("comment", "") or ""))
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, "U", 0, now_ns, "created")
                })
            except Exception as exc:
                error("LV", f"Не удалось записать дату создания {login}", exc)


def install_retention_policy() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services import vpn_lifecycle
    from linkvideo_vpn_helper.services import vpn_automation_service_core as core
    from linkvideo_vpn_helper.services import vpn_automation_service as public
    from linkvideo_vpn_helper.services.vpn_service import VPNService

    # Keep status/version checks consistent with the scripts being installed.
    vpn_lifecycle.LV_AUTOMATION_VERSION = RETENTION_VERSION
    core.LV_AUTOMATION_VERSION = RETENTION_VERSION
    public.LV_AUTOMATION_VERSION = RETENTION_VERSION

    sources = {
        core.LV_ACTIVITY_SCRIPT: activity_script_source,
        core.LV_AGING_SCRIPT: aging_script_source,
        core.LV_RESTORE_SCRIPT: restore_script_source,
    }
    core.VPNAutomationService.SCRIPT_SOURCES = dict(sources)
    public.VPNAutomationService.SCRIPT_SOURCES = dict(sources)

    original_install = public.VPNAutomationService.install_or_update
    original_seed = public.VPNAutomationService.seed_lifecycle

    def install_or_update(self, server, creds):
        status = original_install(self, server, creds)
        changed = _ensure_tracking_for_server(server, creds)
        if changed:
            event("LV", "Инициализирован retention tracking", f"{server} · учёток {changed}")
        return self.get_status(server, creds)

    def seed_lifecycle(self, server, creds):
        result = original_seed(self, server, creds)
        changed = _ensure_tracking_for_server(server, creds)
        if changed:
            event("LV", "Добавлена дата отсчёта retention", f"{server} · учёток {changed}")
        return result

    public.VPNAutomationService.install_or_update = install_or_update
    public.VPNAutomationService.seed_lifecycle = seed_lifecycle

    original_create = VPNService.create_clients_batch
    original_toggle = VPNService.set_secret_enabled

    def create_clients_batch(self, server, creds, *args, **kwargs):
        records = original_create(self, server, creds, *args, **kwargs)
        _mark_created_accounts(server, creds, records)
        return records

    def set_secret_enabled(self, server, creds, login, enabled):
        result = original_toggle(self, server, creds, login, enabled)
        now_ns = time.time_ns()
        try:
            with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
                rows = api.print("/ppp/secret", {"?name=": login})
                if not rows:
                    rows = [row for row in api.print("/ppp/secret") if str(row.get("name", "") or "").strip() == str(login)]
                if rows:
                    row = rows[0]
                    rid = str(row.get(".id", "") or "").strip()
                    meta = parse_extended_comment(str(row.get("comment", "") or ""))
                    created = int(meta.created_ns or now_ns)
                    reason = "manual_enabled" if enabled else "manual_disabled"
                    state = "A" if enabled else "M"
                    last_ns = now_ns if enabled else int(meta.last_ns or 0)
                    if rid:
                        api.set("/ppp/secret", rid, {
                            "comment": compose_extended_comment(meta.base_comment, state, last_ns, created, reason)
                        })
        except Exception as exc:
            error("LV", f"Не удалось записать причину состояния {login}", exc)
        return result

    VPNService.create_clients_batch = create_clients_batch
    VPNService.set_secret_enabled = set_secret_enabled

    event(
        "LV",
        "Retention policy готов",
        "30д сон · 90д карантин · 365д автоматическое удаление · never-active тоже 365д",
    )
    _INSTALLED = True
