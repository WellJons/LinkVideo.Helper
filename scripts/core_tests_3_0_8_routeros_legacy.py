from __future__ import annotations

from linkvideo_vpn_helper.services import vpn_automation_service as automation_module


class LegacyNoIdAPI:
    """RouterOS variant that creates objects but often exposes no add return."""

    def __init__(self):
        self.rows: dict[str, list[dict]] = {
            "/system/scheduler": [],
            "/system/script": [],
            "/system/logging/action": [],
            "/system/logging": [],
        }
        self.seq = 0

    def talk(self, command, params=None, raise_on_trap=True):
        if command.endswith("/find"):
            # Simulate a menu/API combination where find is unavailable. Helper
            # must not depend on it for successful installation.
            raise RuntimeError("no such command")
        raise AssertionError((command, params))

    def print(self, path, params=None):
        return [dict(row) for row in self.rows.setdefault(path, [])]

    def add(self, path, params):
        self.seq += 1
        row = dict(params)
        if path not in {"/system/logging"}:
            row[".id"] = f"*{self.seq}"
        self.rows.setdefault(path, []).append(row)
        # Logging rules intentionally never return ret/.id. Other menus also
        # return empty ret to exercise the print fallback.
        return ""

    def set(self, path, rid, params):
        for row in self.rows.setdefault(path, []):
            if row.get(".id") == rid:
                row.update(dict(params))
                return
        # Optional fields on a no-id row cannot be applied and should simply be
        # skipped by the compatibility facade rather than failing installation.
        raise RuntimeError("no such item")

    def enable(self, path, rid):
        self.set(path, rid, {"disabled": "no"})

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})


def main() -> None:
    api = LegacyNoIdAPI()
    service = automation_module.VPNAutomationService()
    service._ensure_components(api, preserve_pause=False)

    assert len(api.rows["/system/script"]) == 3
    assert len(api.rows["/system/scheduler"]) == 3
    assert len(api.rows["/system/logging/action"]) == 1

    logging_rows = api.rows["/system/logging"]
    assert len(logging_rows) == 2, logging_rows
    assert {row.get("topics") for row in logging_rows} == {"ppp", "l2tp"}
    assert all(row.get("action") == "LVAuth" for row in logging_rows)
    # Prefix is deliberately absent: action+topics is the functional identity.
    assert all("prefix" not in row for row in logging_rows)
    assert all(".id" not in row for row in logging_rows)

    # Re-running installation must recognize the existing action+topics rules
    # and must not create duplicates just because they have no prefix/.id.
    service._ensure_components(api, preserve_pause=False)
    assert len(api.rows["/system/logging"]) == 2, api.rows["/system/logging"]

    print("CORE TESTS 3.0.8 LEGACY ROUTEROS LV OK")


if __name__ == "__main__":
    main()
