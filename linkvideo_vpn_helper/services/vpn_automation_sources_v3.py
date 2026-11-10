from __future__ import annotations

"""RouterOS v7 LV Automation: compact, backwards-compatible lifecycle markers.

RouterOS has no reliable persistent per-PPP-secret metadata field except comment.
The marker is deliberately short: " |LV2|s=A|l=20767|" (about 20 chars).
No automatic deletion of PPP Secret, NAT or profiles is performed on the
router; those are destructive recovery operations requiring a verified backup.

The old deployed LV 2.0.0 script incorrectly emitted
"|LV2|state=A|last=1760...|ver=2.0.0|".
The helper parser expected compact s/l. Both are read during migration.
"""

MARKER = "|LV2|"
LEGACY_MARKER = "|LV1|"
DAY_NS = 86_400_000_000_000

# Inserted inside the loop after :local c and :local dayNs have been defined.
# All variables are local to the current foreach iteration and are set
# before access. Explicitly ignore malformed/unparseable timestamps.
META_READ = r'''
    :local p2 [:find $c "|LV2|"]
    :local p1 [:find $c "|LV1|"]
    :local p $p2
    :if ($p = nil) do={ :set p $p1 } else={
        :if ($p1 != nil) do={
            :if ($p1 < $p) do={ :set p $p1 }
        }
    }
    :local base $c
    :if ($p != nil) do={ :set base [:pick $c 0 $p] }
    :if ([:len $base] > 0) do={
        :if ([:pick $base ([:len $base] - 1) [:len $base]] = " ") do={
            :set base [:pick $base 0 ([:len $base] - 1)]
        }
    }
    :local state "U"
    :local lastDay 0
    :local reading $p2
    :if ($reading = nil) do={ :set reading $p1 }
    :if ($reading != nil) do={
        :local tail [:pick $c $reading [:len $c]]
        :local stateKey "|s="
        :local sp [:find $tail $stateKey]
        :if ($sp = nil) do={
            :set stateKey "|state="
            :set sp [:find $tail $stateKey]
        }
        :if ($sp != nil) do={
            :local sr [:pick $tail ($sp + [:len $stateKey]) [:len $tail]]
            :local se [:find $sr "|"]
            :if ($se != nil) do={ :set state [:pick $sr 0 $se] }
        }
        :local lastKey "|l="
        :local lp [:find $tail $lastKey]
        :local legacyNs false
        :if ($lp = nil) do={
            :set lastKey "|last="
            :set lp [:find $tail $lastKey]
            :set legacyNs true
        }
        :if ($lp != nil) do={
            :local lr [:pick $tail ($lp + [:len $lastKey]) [:len $tail]]
            :local le [:find $lr "|"]
            :if ($le != nil) do={
                :local n [:tonum [:pick $lr 0 $le]]
                :if ([:typeof $n] = "num") do={
                    :if ($n > 0) do={
                        :if ($legacyNs) do={
                            :set lastDay [:tonum ($n / $dayNs)]
                        } else={
                            :set lastDay $n
                        }
                    }
                }
            }
        }
    }
'''

# RouterOS literals concatenate strings through the dot operator. Compose the
# marker once and use the original comment base only, so migration removes
# verbose metadata and never doubles it.
META_SAVE = r'''
    :local compact ("|LV2|s=" . $desired . "|l=" . $lastDay . "|")
    :local updated $compact
    :if ([:len $base] > 0) do={ :set updated ($base . " " . $compact) }
    :if ($updated != $c) do={ /ppp secret set $sid comment=$updated }
'''


def activity_script_source_v3(version: str) -> str:
    # At most one comment rewrite per active account per day, reducing router
    # writes versus old 10-minute/6-hour nanosecond precision.
    return (
        f"# LinkVideo.Helper LV Automation {version}\n"
        + r''':local dayNs 86400000000000
:local nowDay [:tonum ([:tonsec [:timestamp]] / $dayNs)]
:foreach activeId in=[/ppp active find where service="l2tp"] do={
    :local login [/ppp active get $activeId name]
    :foreach sid in=[/ppp secret find where name=$login] do={
        :local c [/ppp secret get $sid comment]
'''
        + META_READ
        + r'''
        :if (($state != "A") || ($lastDay != $nowDay) || ([:find $c "|LV2|s="] = nil)) do={
            :set lastDay $nowDay
            :local desired "A"
'''
        + META_SAVE
        + r'''
        }
    }
}
'''
    )


def aging_script_source_v3(version: str, sleep_days: int, quarantine_days: int, archive_days: int) -> str:
    return (
        f"# LinkVideo.Helper LV Automation {version}\n"
        + f":local sleepDays {sleep_days}\n"
        + f":local quarantineDays {quarantine_days}\n"
        + f":local archiveDays {archive_days}\n"
        + r''':local dayNs 86400000000000
:local nowDay [:tonum ([:tonsec [:timestamp]] / $dayNs)]
:foreach sid in=[/ppp secret find] do={
    :local login [/ppp secret get $sid name]
    :local c [/ppp secret get $sid comment]
'''
        + META_READ
        + r'''
    # No valid activity record -> do not guess, disable or delete anyone.
    :if (($state != "M") && ($lastDay > 0)) do={
        :local isActive ([:len [/ppp active find where name=$login]] > 0)
        :local desired "A"
        :if ($isActive) do={
            :set lastDay $nowDay
        } else={
            :local age ($nowDay - $lastDay)
            :if ($age >= $archiveDays) do={
                :set desired "R"
            } else={
                :if ($age >= $quarantineDays) do={
                    :set desired "Q"
                } else={
                    :if ($age >= $sleepDays) do={ :set desired "S" }
                }
            }
            :if (($desired = "Q") || ($desired = "R")) do={
                # Archive means disabled and recoverable, never deleted.
                :if ([/ppp secret get $sid disabled] = false) do={
                    /ppp secret set $sid disabled=yes
                }
            }
        }
'''
        + META_SAVE
        + r'''
    }
}
'''
    )


def restore_script_source_v3(version: str) -> str:
    # Restore is restricted to users marked Q/R by LV, never M (manual).
    # A failed authentication attempt alone cannot prove knowledge of the
    # correct PPP password. Treat this as a deliberate opt-in policy if
    # LV-Aging is enabled; it is NOT cryptographic authentication.
    return (
        f"# LinkVideo.Helper LV Automation {version}\n"
        + r''':local dayNs 86400000000000
:local nowDay [:tonum ([:tonsec [:timestamp]] / $dayNs)]
:local token "login failure for user "
:foreach logId in=[/log find where buffer="LVAuth"] do={
    :local message [/log get $logId message]
    :local pos [:find $message $token]
    :if ($pos != nil) do={
        :local rest [:pick $message ($pos + [:len $token]) [:len $message]]
        :local endOfLogin [:find $rest " from "]
        :if ($endOfLogin != nil) do={
            :local login [:pick $rest 0 $endOfLogin]
            :foreach sid in=[/ppp secret find where name=$login] do={
                :local c [/ppp secret get $sid comment]
'''
        + META_READ
        + r'''
                :if ((($state = "Q") || ($state = "R")) && ([/ppp secret get $sid disabled] = true)) do={
                    :local desired "A"
                    :set lastDay $nowDay
                    :local compact ("|LV2|s=A|l=" . $lastDay . "|")
                    :local updated $compact
                    :if ([:len $base] > 0) do={ :set updated ($base . " " . $compact) }
                    /ppp secret set $sid disabled=no comment=$updated
                    :log info ("LV AutoRestore: " . $login . " enabled after L2TP login attempt")
                }
            }
        }
    }
}
'''
    )
