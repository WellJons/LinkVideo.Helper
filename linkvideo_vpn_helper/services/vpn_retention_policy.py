from __future__ import annotations

"""Autonomous 30/90/365-day lifecycle policy for LinkVideo VPN accounts.

RouterOS PPP secrets have ``last-logged-out`` but no reliable creation timestamp.
LinkVideo therefore keeps only the minimum missing state in a compact LV2 marker:

    |LV2|s=Q|l=20681|c=20590|r=q|

``l`` and ``c`` are whole days since Unix epoch, not nanoseconds. This keeps the
RouterOS comment short while preserving everything required for the policy:
30 days -> sleeping, 90 days -> quarantine/disable. After 365 days of no
activity (or 30 days after creation without a first connection), RouterOS
removes its PPP Secret, client-owned NAT and unused profile on RouterOS.
Google Sheets archives its deletion on the next successful sync.
"""

from dataclasses import dataclass
import re
import time

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.app_logging import event, error


RETENTION_VERSION = "2.1.2"
LV_MARKER = "|LV2|"
LEGACY_LV_MARKER = "|LV1|"
DAY_NS = 86_400_000_000_000
SLEEP_DAYS = 30
QUARANTINE_DAYS = 90
DELETE_DAYS = 365
NEVER_ACTIVE_DELETE_DAYS = 30

_REASON_TO_CODE = {
    "created": "b",
    "never_active_tracking": "c",
    "tracked": "a",
    "activity": "a",
    "inactive_30": "s",
    "inactive_90": "q",
    "inactive_365": "d",
    "never_active_30": "n",
    "manual_disabled": "m",
    "manual_or_external_disabled": "m",
    "manual_enabled": "e",
    "auto_restore": "r",
}
_CODE_TO_REASON = {
    "b": "created",
    "c": "never_active_tracking",
    "a": "activity",
    "s": "inactive_30",
    "q": "inactive_90",
    "d": "inactive_365",
    "n": "never_active_30",
    "m": "manual_or_external_disabled",
    "e": "manual_enabled",
    "r": "auto_restore",
}

_INSTALLED = False


@dataclass(slots=True)
class ExtendedLifecycle:
    base_comment: str = ""
    state: str = "U"
    last_ns: int = 0
    created_ns: int = 0
    reason: str = ""
    version: str = RETENTION_VERSION

    @property
    def reference_ns(self) -> int:
        return int(self.last_ns or self.created_ns or 0)


def _marker_base(raw: str) -> str:
    positions = [pos for pos in (raw.find(LV_MARKER), raw.find(LEGACY_LV_MARKER)) if pos >= 0]
    return raw[: min(positions)].rstrip() if positions else raw.rstrip()


def _to_day(value: int) -> int:
    number = max(0, int(value or 0))
    return number if 0 < number < 10_000_000 else number // DAY_NS


def _from_day(value: int) -> int:
    return max(0, int(value or 0)) * DAY_NS


def parse_extended_comment(comment: str) -> ExtendedLifecycle:
    """Parse LV2 and migrate-friendly LV1 metadata.

    If both markers are present because a migration was interrupted, LV2 owns the
    state/reason while the most recent activity and oldest known creation value are
    preserved from either marker.
    """
    raw = str(comment or "")
    base = _marker_base(raw)
    state = "U"
    last_ns = 0
    created_ns = 0
    reason = ""
    version = ""

    old_pos = raw.find(LEGACY_LV_MARKER)
    if old_pos >= 0:
        tail = raw[old_pos:]

        def old_text(name: str, default: str = "") -> str:
            match = re.search(rf"\|{re.escape(name)}=([^|]*)\|", tail)
            return str(match.group(1) if match else default)

        state = old_text("state", state) or state
        try:
            last_ns = max(last_ns, int(old_text("last", "0") or 0))
        except Exception:
            pass
        try:
            created_ns = int(old_text("created", "0") or 0)
        except Exception:
            created_ns = 0
        reason = old_text("reason", reason)
        version = old_text("ver", version)

    new_pos = raw.find(LV_MARKER)
    if new_pos >= 0:
        tail = raw[new_pos:]
        state_m = re.search(r"\|s=([A-Z]+)\|", tail)
        last_m = re.search(r"\|l=(\d+)\|", tail)
        created_m = re.search(r"\|c=(\d+)\|", tail)
        reason_m = re.search(r"\|r=([a-z])\|", tail, re.I)
        if state_m:
            state = state_m.group(1)
        if last_m:
            last_ns = max(last_ns, _from_day(int(last_m.group(1))))
        if created_m:
            current_created = _from_day(int(created_m.group(1)))
            if current_created > 0:
                created_ns = current_created
        if reason_m:
            reason = _CODE_TO_REASON.get(reason_m.group(1).lower(), reason)
        version = RETENTION_VERSION

    return ExtendedLifecycle(
        base_comment=base,
        state=state or "U",
        last_ns=max(0, int(last_ns or 0)),
        created_ns=max(0, int(created_ns or 0)),
        reason=reason,
        version=version,
    )


def compose_extended_comment(
    base_comment: str,
    state: str,
    last_ns: int,
    created_ns: int,
    reason: str,
    version: str = RETENTION_VERSION,
) -> str:
    """Write compact LV2 metadata while preserving the operator comment."""
    base = _marker_base(str(base_comment or "")).strip()
    code = _REASON_TO_CODE.get(str(reason or "").strip(), "a")
    marker = (
        f"{LV_MARKER}s={str(state or 'U').upper()}|l={_to_day(last_ns)}|"
        f"c={_to_day(created_ns)}|r={code}|"
    )
    return f"{base} {marker}".strip()


def activity_script_source() -> str:
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}\n:local nowDay ([:tonsec [:timestamp]] / {DAY_NS})\n:foreach a in=[/ppp active find where service="l2tp"] do={{\n    :local u [/ppp active get $a name]\n    :foreach sid in=[/ppp secret find where name=$u] do={{\n        :local c [/ppp secret get $sid comment]\n        :local p [:find $c "{LV_MARKER}"]\n        :local base $c\n        :local state "U"\n        :local last 0\n        :local created $nowDay\n        :if ($p != nil) do={{\n            :set base [:pick $c 0 $p]\n            :local tail [:pick $c $p [:len $c]]\n            :local sp [:find $tail "|s="]\n            :if ($sp != nil) do={{ :local x [:pick $tail ($sp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set state [:pick $x 0 $e] }} }}\n            :local lp [:find $tail "|l="]\n            :if ($lp != nil) do={{ :local x [:pick $tail ($lp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set last [:tonum [:pick $x 0 $e]] }} }}\n            :local cp [:find $tail "|c="]\n            :if ($cp != nil) do={{ :local x [:pick $tail ($cp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set created [:tonum [:pick $x 0 $e]] }} }}\n        }}\n        :if ($created = 0) do={{ :set created $nowDay }}\n        :if (($last != $nowDay) || ($state != "A")) do={{\n            /ppp secret set $sid comment=($base . "{LV_MARKER}s=A|l=" . $nowDay . "|c=" . $created . "|r=a|")\n        }}\n    }}\n}}'''


def aging_script_source() -> str:
    """Daily RouterOS lifecycle: expire confirmed-inactive PPP accounts without Sheets dependency."""
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}
:local nowDay ([:tonsec [:timestamp]] / {DAY_NS})
:foreach sid in=[/ppp secret find] do={{
    :local u [/ppp secret get $sid name]
    :local c [/ppp secret get $sid comment]
    :local p [:find $c "{LV_MARKER}"]
    :local base $c
    :local state "U"
    :local last 0
    :local created $nowDay
    :local verifiedNever false
    :if ($p != nil) do={{
        :set base [:pick $c 0 $p]
        :local tail [:pick $c $p [:len $c]]
        :local sp [:find $tail "|s="]
        :if ($sp != nil) do={{ :local x [:pick $tail ($sp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set state [:pick $x 0 $e] }} }}
        :local lp [:find $tail "|l="]
        :if ($lp != nil) do={{ :local x [:pick $tail ($lp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set last [:tonum [:pick $x 0 $e]] }} }}
        :local cp [:find $tail "|c="]
        :if ($cp != nil) do={{ :local x [:pick $tail ($cp + 3) [:len $tail]]; :local e [:find $x "|"]; :if ($e != nil) do={{ :set created [:tonum [:pick $x 0 $e]] }} }}
        :local rp [:find $tail "|r="]
        :if ($rp != nil) do={{
            :local reason [:pick $tail ($rp + 3) ($rp + 4)]
            :if (($reason = "b") || ($reason = "n")) do={{ :set verifiedNever true }}
        }}
    }}
    :local isActive ([:len [/ppp active find where name=$u]] > 0)
    :local disabled [/ppp secret get $sid disabled]
    :if ($isActive) do={{
        /ppp secret set $sid comment=($base . "{LV_MARKER}s=A|l=" . $nowDay . "|c=" . $created . "|r=a|")
    }} else={{
        :local reference $last
        :if ($reference = 0) do={{ :set reference $created }}
        :local age ($nowDay - $reference)
        # Legacy "no last-login data" is NOT proof of never connecting.
        :if (($last = 0) && ($verifiedNever = false)) do={{ :set age 0 }}
        :local deleteAfter {DELETE_DAYS}
        :local deleteCode "d"
        :if ($last = 0) do={{
            :set deleteAfter {NEVER_ACTIVE_DELETE_DAYS}
            :set deleteCode "n"
        }}
        :if ($age >= $deleteAfter) do={{
            # Active sessions are excluded above. Do not depend on Google Sheets.
            :local profile [/ppp secret get $sid profile]
            :local remote [/ppp secret get $sid remote-address]
            :if (([:len $remote] = 0) && ([:len $profile] > 0)) do={{
                :foreach pid in=[/ppp profile find where name=$profile] do={{
                    :set remote [/ppp profile get $pid remote-address]
                }}
            }}
            :local profileUses 0
            :if ([:len $profile] > 0) do={{ :set profileUses [:len [/ppp secret find where profile=$profile]] }}
            # Only remove rules actually attributed to this client; no blind IP deletion.
            :foreach nid in=[/ip firewall nat find where comment=$u] do={{
                :local target [/ip firewall nat get $nid to-addresses]
                :if (([:len $target] = 0) || ($target = $remote)) do={{ /ip firewall nat remove $nid }}
            }}
            :log warning ("LV RETENTION DELETE user=" . $u . " age=" . $age)
            /ppp secret remove $sid
            :if (($profileUses = 1) && ([:len $profile] > 0) && ($profile != "default") && ($profile != "default-encryption")) do={{
                :foreach pid in=[/ppp profile find where name=$profile] do={{ /ppp profile remove $pid }}
            }}
        }} else={{
            :if ($disabled = true) do={{
                :if ($state = "Q") do={{
                    /ppp secret set $sid comment=($base . "{LV_MARKER}s=Q|l=" . $last . "|c=" . $created . "|r=q|")
                }} else={{
                    :local code "m"
                    :if (($last = 0) && ($verifiedNever)) do={{ :set code "b" }}
                    /ppp secret set $sid comment=($base . "{LV_MARKER}s=M|l=" . $last . "|c=" . $created . "|r=" . $code . "|")
                }}
            }} else={{
                :if ($age >= {QUARANTINE_DAYS}) do={{
                    /ppp secret disable $sid
                    /ppp secret set $sid comment=($base . "{LV_MARKER}s=Q|l=" . $last . "|c=" . $created . "|r=q|")
                    :log warning ("LV QUARANTINE user=" . $u . " age=" . $age)
                }} else={{
                    :if (($last > 0) && ($age >= {SLEEP_DAYS})) do={{
                        /ppp secret set $sid comment=($base . "{LV_MARKER}s=S|l=" . $last . "|c=" . $created . "|r=s|")
                    }} else={{
                        :if ($last = 0) do={{
                            :local code "c"
                            :if ($verifiedNever) do={{ :set code "b" }}
                            /ppp secret set $sid comment=($base . "{LV_MARKER}s=U|l=0|c=" . $created . "|r=" . $code . "|")
                        }} else={{
                            /ppp secret set $sid comment=($base . "{LV_MARKER}s=A|l=" . $last . "|c=" . $created . "|r=a|")
                        }}
                    }}
                }}
            }}
        }}
    }}
}}'''


def restore_script_source() -> str:
    token = "login failure for user "
    return f'''# LinkVideo.Helper LV Automation {RETENTION_VERSION}\n:local token "{token}"\n:global LVAuthLastEvent\n:local latestId ""\n:foreach lid in=[/log find where buffer="LVAuth"] do={{\n    :local msg [/log get $lid message]\n    :if ([:find $msg $token] != nil) do={{ :set latestId $lid }}\n}}\n:if ([:typeof $LVAuthLastEvent] = "nothing") do={{\n    :set LVAuthLastEvent ""\n    :if ([:len $latestId] > 0) do={{\n        :local oldMsg [/log get $latestId message]\n        :local oldTime [/log get $latestId time]\n        :set LVAuthLastEvent ($oldTime . "|" . $oldMsg)\n    }}\n}} else={{\n    :if ([:len $latestId] > 0) do={{\n        :local msg [/log get $latestId message]\n        :local stamp [/log get $latestId time]\n        :local sig ($stamp . "|" . $msg)\n        :if ($sig != $LVAuthLastEvent) do={{\n            :set LVAuthLastEvent $sig\n            :local p [:find $msg $token]\n            :if ($p != nil) do={{\n                :local rest [:pick $msg ($p + [:len $token]) [:len $msg]]\n                :local e [:find $rest " from "]\n                :if ($e != nil) do={{\n                    :local u [:pick $rest 0 $e]\n                    :foreach sid in=[/ppp secret find where name=$u] do={{\n                        :local c [/ppp secret get $sid comment]\n                        :local mp [:find $c "{LV_MARKER}"]\n                        :if ($mp != nil) do={{\n                            :local base [:pick $c 0 $mp]\n                            :local tail [:pick $c $mp [:len $c]]\n                            :local state "U"\n                            :local last 0\n                            :local created 0\n                            :local sp [:find $tail "|s="]\n                            :if ($sp != nil) do={{ :local x [:pick $tail ($sp + 3) [:len $tail]]; :local z [:find $x "|"]; :if ($z != nil) do={{ :set state [:pick $x 0 $z] }} }}\n                            :local lp [:find $tail "|l="]\n                            :if ($lp != nil) do={{ :local x [:pick $tail ($lp + 3) [:len $tail]]; :local z [:find $x "|"]; :if ($z != nil) do={{ :set last [:tonum [:pick $x 0 $z]] }} }}\n                            :local cp [:find $tail "|c="]\n                            :if ($cp != nil) do={{ :local x [:pick $tail ($cp + 3) [:len $tail]]; :local z [:find $x "|"]; :if ($z != nil) do={{ :set created [:tonum [:pick $x 0 $z]] }} }}\n                            :local disabled [/ppp secret get $sid disabled]\n                            :if (($state = "Q") && ($disabled = true)) do={{\n                                /ppp secret enable $sid\n                                /ppp secret set $sid comment=($base . "{LV_MARKER}s=A|l=" . $last . "|c=" . $created . "|r=r|")\n                                :log info ("LV RESTORE user=" . $u . " reason=quarantine_login")\n                            }}\n                        }}\n                    }}\n                }}\n            }}\n        }}\n    }}\n}}'''


def _bool(value) -> bool:
    return str(value or "no").strip().lower() in {"yes", "true", "1", "on", "enabled", "disabled"}


def _native_last_ns(vpn, row: dict) -> int:
    raw = str(row.get("last-logged-out", "") or row.get("last_logged_out", "") or "").strip()
    if not raw:
        return 0
    try:
        parsed = vpn.parse_router_datetime(raw)
        return int(parsed.timestamp() * 1_000_000_000) if parsed else 0
    except Exception:
        return 0


def _desired_state(last_ns: int, created_ns: int, disabled: bool, was_quarantine: bool, now_ns: int, *, verified_never: bool = False) -> tuple[str, str]:
    reference = int(last_ns or created_ns or now_ns)
    age = max(0, int((now_ns - reference) // DAY_NS))
    if last_ns <= 0:
        if verified_never and age >= NEVER_ACTIVE_DELETE_DAYS:
            return "R", "never_active_30"
        if verified_never:
            return ("M", "created") if disabled else ("U", "created")
        return ("M", "manual_or_external_disabled") if disabled else ("U", "never_active_tracking")
    if last_ns > 0 and age >= DELETE_DAYS:
        return "R", "inactive_365"
    if disabled:
        return ("Q", "inactive_90") if was_quarantine else ("M", "manual_or_external_disabled")
    if age >= QUARANTINE_DAYS:
        return "Q", "inactive_90"
    if age >= SLEEP_DAYS:
        return "S", "inactive_30"
    if last_ns <= 0:
        return "U", "never_active_tracking"
    return "A", "tracked"


def _ensure_tracking_for_server(server, creds) -> int:
    """Migrate LV1/unknown accounts to compact LV2 without destructive actions."""
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
            native_last = _native_last_ns(vpn, row)
            last_ns = max(int(meta.last_ns or 0), native_last)
            created_ns = int(meta.created_ns or now_ns)
            disabled = _bool(row.get("disabled", "no"))

            if login in active_names:
                state, last_ns, reason = "A", now_ns, "activity"
            else:
                state, reason = _desired_state(
                    last_ns,
                    created_ns,
                    disabled,
                    meta.state == "Q",
                    now_ns,
                    verified_never=meta.reason in {"created", "never_active_30"},
                )

            new_comment = compose_extended_comment(meta.base_comment, state, last_ns, created_ns, reason)
            if new_comment != original:
                api.set("/ppp/secret", rid, {"comment": new_comment})
                changed += 1
    return changed


def _remove_client_objects(api, secret: dict, profiles: list[dict], nat_rules: list[dict]) -> tuple[int, bool]:
    """Delete only resources unambiguously belonging to one expired PPP account.

    IP-only matching is forbidden when another PPP Secret can use that remote IP.
    A shared PPP profile is never deleted; NAT with a foreign target is protected.
    """
    rid = str(secret.get(".id", "") or "").strip()
    login = str(secret.get("name", "") or "").strip()
    profile_name = str(secret.get("profile", "") or "").strip()
    profiles_by_name = {
        str(row.get("name", "") or "").strip(): row for row in profiles
    }

    def effective_remote(row: dict) -> str:
        remote = str(row.get("remote-address", "") or row.get("remote_address", "") or "").strip()
        if remote:
            return remote
        name = str(row.get("profile", "") or "").strip()
        p = profiles_by_name.get(name, {})
        return str(p.get("remote-address", "") or p.get("remote_address", "") or "").strip()

    remote = effective_remote(secret)
    all_secrets = api.print("/ppp/secret")
    others = [
        row for row in all_secrets
        if str(row.get(".id", "") or "").strip() != rid
    ]
    shared_remote = bool(remote and any(effective_remote(row) == remote for row in others))
    profile_uses = sum(
        1 for row in all_secrets
        if profile_name and str(row.get("profile", "") or "").strip() == profile_name
    )

    removed_nat = 0
    for rule in list(nat_rules):
        nrid = str(rule.get(".id", "") or "").strip()
        if not nrid:
            continue
        comment = str(rule.get("comment", "") or "").strip()
        target = str(rule.get("to-addresses", "") or rule.get("to_addresses", "") or "").strip()
        # Never remove a rule explicitly targeting a different client's address.
        if remote and target and target != remote:
            continue
        owned_by_comment = comment == login
        by_unique_remote = (
            bool(remote and target == remote)
            and not shared_remote
            and str(rule.get("chain", "dstnat") or "dstnat").strip().lower() == "dstnat"
            and str(rule.get("action", "dst-nat") or "dst-nat").strip().lower() == "dst-nat"
        )
        if not (owned_by_comment or by_unique_remote):
            continue
        api.remove("/ip/firewall/nat", nrid)
        removed_nat += 1
        nat_rules.remove(rule)

    if not rid:
        raise RuntimeError(f"Нет RouterOS ID PPP Secret {login}; удаление отменено")
    api.remove("/ppp/secret", rid)

    removed_profile = False
    if profile_name and profile_uses == 1 and profile_name not in {"default", "default-encryption"}:
        profile = profiles_by_name.get(profile_name, {})
        prid = str(profile.get(".id", "") or "").strip()
        if prid:
            api.remove("/ppp/profile", prid)
            removed_profile = True
    return removed_nat, removed_profile


def apply_policy_now(server, creds) -> dict[str, int]:
    """Apply and verify the same policy from Python immediately.

    RouterOS and Python enforce 30/90/365 lifecycle independently of Google Sheets.
    The existing Sheets sync stores archival tombstones when connectivity resumes.
    """
    from linkvideo_vpn_helper.services.vpn_service import VPNService

    now_ns = time.time_ns()
    vpn = VPNService()
    counts = {"sleeping": 0, "quarantined": 0, "deleted": 0, "nat_removed": 0, "profiles_removed": 0}

    with RouterOSAPIClient(server, creds.username, creds.password, port=creds.port, timeout=creds.timeout) as api:
        try:
            secrets = api.print("/ppp/secret", {".proplist": ".id,name,profile,remote-address,disabled,last-logged-out,comment"})
        except Exception:
            secrets = api.print("/ppp/secret")
        profiles = api.print("/ppp/profile")
        nat_rules = api.print("/ip/firewall/nat")
        active_names = {
            str(row.get("name", "") or "").strip()
            for row in api.print("/ppp/active")
            if str(row.get("name", "") or "").strip()
        }

        for secret in list(secrets):
            rid = str(secret.get(".id", "") or "").strip()
            login = str(secret.get("name", "") or "").strip()
            if not rid or not login:
                continue
            meta = parse_extended_comment(str(secret.get("comment", "") or ""))
            last_ns = max(int(meta.last_ns or 0), _native_last_ns(vpn, secret))
            created_ns = int(meta.created_ns or now_ns)

            if login in active_names:
                comment = compose_extended_comment(meta.base_comment, "A", now_ns, created_ns, "activity")
                api.set("/ppp/secret", rid, {"comment": comment})
                continue

            reference = int(last_ns or created_ns or now_ns)
            age = max(0, int((now_ns - reference) // DAY_NS))
            disabled = _bool(secret.get("disabled", "no"))

            if last_ns <= 0 and meta.reason not in {"created", "never_active_30"}:
                # Migrated legacy accounts may have no RouterOS login history.
                # Missing evidence is not proof of non-use.
                state = "M" if disabled else "U"
                reason = "manual_or_external_disabled" if disabled else "never_active_tracking"
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, state, last_ns, created_ns, reason)
                })
                continue

            delete_after = NEVER_ACTIVE_DELETE_DAYS if last_ns <= 0 else DELETE_DAYS
            if age >= delete_after:
                reason = "never_active_30" if last_ns <= 0 else "inactive_365"
                # Re-read just before deletion: a reconnect or replacement cancels it.
                current = next(
                    (r for r in api.print("/ppp/secret")
                     if str(r.get(".id", "") or "").strip() == rid
                     and str(r.get("name", "") or "").strip() == login), None
                )
                online = {
                    str(r.get("name", "") or "").strip()
                    for r in api.print("/ppp/active")
                }
                if current is None or login in online:
                    continue
                current_meta = parse_extended_comment(str(current.get("comment", "") or ""))
                current_last = max(int(current_meta.last_ns or 0), _native_last_ns(vpn, current))
                current_created = int(current_meta.created_ns or now_ns)
                if current_last <= 0 and current_meta.reason not in {"created", "never_active_30"}:
                    continue
                current_age = max(0, int((now_ns - (current_last or current_created)) // DAY_NS))
                if current_age < (NEVER_ACTIVE_DELETE_DAYS if current_last <= 0 else DELETE_DAYS):
                    continue
                nat_removed, profile_removed = _remove_client_objects(api, current, profiles, nat_rules)
                counts["deleted"] += 1
                counts["nat_removed"] += nat_removed
                counts["profiles_removed"] += int(profile_removed)
                event("LV", "VPN-учётка удалена автоматически", f"{server} · {login} · {reason} · {age} дн.")
                continue

            if disabled:
                state = "Q" if meta.state == "Q" else "M"
                reason = "inactive_90" if state == "Q" else ("created" if last_ns <= 0 and meta.reason == "created" else "manual_or_external_disabled")
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, state, last_ns, created_ns, reason)
                })
                counts["quarantined"] += int(state == "Q")
                continue

            if age >= QUARANTINE_DAYS:
                api.disable("/ppp/secret", rid)
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, "Q", last_ns, created_ns, "inactive_90")
                })
                counts["quarantined"] += 1
                event("LV", "VPN-учётка помещена в карантин", f"{server} · {login} · {age} дн. без активности")
            elif age >= SLEEP_DAYS:
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, "S", last_ns, created_ns, "inactive_30")
                })
                counts["sleeping"] += 1
            else:
                state = "U" if last_ns <= 0 else "A"
                reason = ("created" if meta.reason == "created" else "never_active_tracking") if state == "U" else "tracked"
                api.set("/ppp/secret", rid, {
                    "comment": compose_extended_comment(meta.base_comment, state, last_ns, created_ns, reason)
                })

        # Verify the hard Q => disabled invariant after all mutations.
        try:
            verify = api.print("/ppp/secret", {".proplist": ".id,name,disabled,comment"})
        except Exception:
            verify = api.print("/ppp/secret")
        bad = [
            str(row.get("name", "") or row.get(".id", ""))
            for row in verify
            if parse_extended_comment(str(row.get("comment", "") or "")).state == "Q"
            and not _bool(row.get("disabled", "no"))
        ]
        if bad:
            raise RuntimeError("RouterOS не отключил карантинные PPP-учётки: " + ", ".join(bad[:8]))

    event(
        "LV",
        "Retention policy применена",
        f"{server} · карантин {counts['quarantined']} · удалено {counts['deleted']} · NAT {counts['nat_removed']} · профили {counts['profiles_removed']}",
    )
    return counts


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
    original_quarantine = public.VPNAutomationService.set_quarantine_enabled
    original_runtime = public.VPNAutomationService.set_automation_enabled

    def install_or_update(self, server, creds):
        status = original_install(self, server, creds)
        changed = _ensure_tracking_for_server(server, creds)
        if changed:
            event("LV", "LV2 metadata инициализированы", f"{server} · учёток {changed}")
        status = self.get_status(server, creds)
        if status.aging_enabled:
            apply_policy_now(server, creds)
            status = self.get_status(server, creds)
        return status

    def seed_lifecycle(self, server, creds):
        result = original_seed(self, server, creds)
        changed = _ensure_tracking_for_server(server, creds)
        if changed:
            event("LV", "LV2 metadata инициализированы", f"{server} · учёток {changed}")
        return result

    def set_quarantine_enabled(self, server, creds, enabled):
        status = original_quarantine(self, server, creds, enabled)
        if enabled:
            _ensure_tracking_for_server(server, creds)
            apply_policy_now(server, creds)
            status = self.get_status(server, creds)
        return status

    def set_automation_enabled(self, server, creds, enabled):
        status = original_runtime(self, server, creds, enabled)
        if enabled and status.aging_enabled:
            _ensure_tracking_for_server(server, creds)
            apply_policy_now(server, creds)
            status = self.get_status(server, creds)
        return status

    public.VPNAutomationService.install_or_update = install_or_update
    public.VPNAutomationService.seed_lifecycle = seed_lifecycle
    public.VPNAutomationService.set_quarantine_enabled = set_quarantine_enabled
    public.VPNAutomationService.set_automation_enabled = set_automation_enabled

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
                    has_no_login = int(meta.last_ns or 0) <= 0 and meta.reason == "created"
                    reason = "created" if has_no_login else ("manual_enabled" if enabled else "manual_disabled")
                    state = ("U" if has_no_login else "A") if enabled else "M"
                    # Changing the admin toggle is not a successful VPN connection.
                    last_ns = int(meta.last_ns or 0)
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
        "LV2.1.2 · 30д сон · 90д карантин · 365д удаление · never-active 30д · независимо от Google Sheets",
    )
    _INSTALLED = True
