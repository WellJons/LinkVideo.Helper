from __future__ import annotations

"""Public archive service facade for 3.0.8.

The mature archive implementation lives in ``archive_service_core``.  This
facade keeps its API stable while tightening the final fallback so a successful
player/reserve discovery cannot still be held open by dozens of stale workers.
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from linkvideo_vpn_helper.services.archive_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.archive_service_core import ArchiveService as _CoreArchiveService


class ArchiveService(_CoreArchiveService):
    DEEP_FALLBACK_MAX_HOSTS = 48
    DEEP_FALLBACK_MAX_WORKERS = 12
    DEEP_FALLBACK_DEADLINE_SECONDS = 14.0

    def _deep_candidate_hosts(self, primary_server: str, operator_id: int = 241, camera_id: str = "") -> list[str]:
        """Return a bounded, evidence-first fallback list.

        Order matters: camera-specific history is strongest local evidence, then
        the current B2O server inventory, and only then stale global history.
        The old implementation's comment described this order but global history
        was actually checked before the live B2O inventory.
        """
        primary = self.b2o.normalize_vcore_host(primary_server)
        result: list[str] = []
        seen: set[str] = {primary} if primary else set()

        def add(host: str):
            clean = self.b2o.normalize_vcore_host(host)
            if clean and clean.endswith(".video.goodline.info") and clean not in seen:
                seen.add(clean)
                result.append(clean)

        operator_id = self.b2o.valid_operator_id(operator_id)
        for host in self._history_hosts(camera_id):
            add(host)

        try:
            live = self.b2o.archive_servers(operator_id, online=True)
        except Exception:
            live = self.b2o.archive_servers(operator_id, online=False)
        for host in live:
            add(host)

        for host in self._global_history_hosts(operator_id):
            add(host)

        return result[: self.DEEP_FALLBACK_MAX_HOSTS]

    def _deep_search_missing(
        self,
        camera: ArchiveCamera,
        start_ts: float,
        end_ts: float,
        known_slices: list[ArchiveSlice],
        already_checked: set[str],
        progress: Callable[[str, str], None] | None = None,
        cancel_event=None,
    ) -> tuple[list[ArchiveSlice], list[str], int]:
        """Bound the last-resort B2O fallback by coverage and wall-clock time.

        ThreadPoolExecutor's context manager waits for all running workers on
        exit.  That made an early ``break`` ineffective: the UI could still wait
        for every slow HTTPS probe.  We deliberately shut the pool down with
        ``wait=False`` once coverage is complete, cancelled, or the deadline is
        reached. Socket workers unwind on their own timeout in the background.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")

        current_plan = self._build_plan(list(known_slices), start_ts, end_ts)
        missing = self._gaps(current_plan, start_ts, end_ts)
        if not missing:
            return [], [], 0

        # Probe only the envelope that is still missing, not blindly the entire
        # requested interval again. Disjoint gaps remain covered by this envelope.
        probe_start = min(gap.start for gap in missing)
        probe_end = max(gap.end for gap in missing)

        candidates: list[str] = []
        seen = {self.b2o.normalize_vcore_host(x) for x in already_checked}

        def add(host: str):
            clean = self.b2o.normalize_vcore_host(host)
            if clean and clean not in seen:
                seen.add(clean)
                candidates.append(clean)

        for host in list(camera.candidate_hosts or []):
            add(host)
        resolved_operator = self.b2o.resolve_operator_id(camera.label, 241)
        for host in self._deep_candidate_hosts(camera.server, resolved_operator, camera.label):
            add(host)

        if not candidates:
            return [], [], 0

        found: list[ArchiveSlice] = []
        checked_hosts: list[str] = []
        total = len(candidates)
        checked_count = 0
        workers = min(self.DEEP_FALLBACK_MAX_WORKERS, max(1, total))
        deadline = time.monotonic() + self.DEEP_FALLBACK_DEADLINE_SECONDS

        if progress:
            progress(
                "Проверяю резервные DVR",
                f"Не закрыто {sum(g.duration for g in missing):.0f} сек · кандидатов из B2O: {total}",
            )

        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="archive-player-fallback")
        futures = {
            pool.submit(self._probe_hls_host, host, camera, probe_start, probe_end, 5): host
            for host in candidates
        }
        pending = set(futures)
        timed_out = 0
        try:
            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    for item in pending:
                        item.cancel()
                    raise OperationCancelled("Операция отменена пользователем")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = len(pending)
                    break

                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    host = futures[future]
                    checked_count += 1
                    checked_hosts.append(host)
                    try:
                        info, _errs = future.result()
                    except Exception:
                        info = None
                    if info:
                        found.extend(info.get("slices") or [])
                        learned = list(info.get("hosts") or [])
                        if learned:
                            self._remember_global_hosts(resolved_operator, learned)

                    current_plan = self._build_plan(list(known_slices) + found, start_ts, end_ts)
                    if current_plan and not self._gaps(current_plan, start_ts, end_ts):
                        for item in pending:
                            item.cancel()
                        pending.clear()
                        break

                    if progress and (checked_count % 6 == 0 or info):
                        detail = f"Проверено {checked_count}/{total}"
                        if info:
                            detail += f" · найдено на {info.get('host')}"
                        progress("Проверяю резервные DVR", detail)

            if pending:
                timed_out = max(timed_out, len(pending))
                for item in pending:
                    item.cancel()
                pending.clear()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if progress and timed_out:
            progress(
                "Fallback ограничен по времени",
                f"Проверено {checked_count}/{total}; ещё {timed_out} медленных серверов не задерживают результат",
            )
        return found, checked_hosts, checked_count
