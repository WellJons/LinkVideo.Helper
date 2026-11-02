from __future__ import annotations

"""Safe migration and explicit sign-out of the single MikroTik work account.

Settings from the old VPNHelper product are migrated at most once. A user who
signs out must not be silently logged back in by an obsolete settings store.
Only login data are removed; theme, server registry, and other preferences stay.
"""


def migrate_legacy_work_login(settings, legacy_settings) -> bool:
    """Migrate old RouterOS credentials once, never after explicit logout."""
    if bool(settings.value("auth/legacy_work_login_checked", False, bool)):
        return False
    imported = False
    current_username = str(settings.value("username", "", str) or "").strip()
    current_password = str(settings.value("password", "", str) or "")
    if not current_username and not current_password:
        old_username = str(legacy_settings.value("username", "", str) or "").strip()
        old_password = str(legacy_settings.value("password", "", str) or "")
        if old_username and old_password:
            settings.setValue("username", old_username)
            settings.setValue("password", old_password)
            settings.setValue(
                "remember", bool(legacy_settings.value("remember", True, bool))
            )
            imported = True
    settings.setValue("auth/legacy_work_login_checked", True)
    settings.sync()
    return imported


def clear_work_login(settings, legacy_settings=None) -> None:
    """Make explicit logout durable across app restart and old migrations."""
    # Write migration tombstone first so a failed legacy cleanup still cannot
    # re-import the credentials on the next boot.
    settings.setValue("auth/legacy_work_login_checked", True)
    settings.setValue("remember", False)
    for key in ("username", "password"):
        settings.remove(key)
    settings.sync()

    if legacy_settings is not None:
        # Historical application stores credentials separately. Do not erase
        # unrelated legacy settings, only the previous work login.
        legacy_settings.setValue("remember", False)
        for key in ("username", "password"):
            legacy_settings.remove(key)
        legacy_settings.sync()


def force_fresh_login(settings) -> None:
    """For a process started by 'switch account', never auto-submit old data."""
    settings.setValue("auth/legacy_work_login_checked", True)
    settings.setValue("remember", False)
    for key in ("username", "password"):
        settings.remove(key)
    settings.sync()
