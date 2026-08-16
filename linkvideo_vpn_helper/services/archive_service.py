from __future__ import annotations

"""Public archive service facade for 3.0.8.

The mature implementation lives in ``archive_service_core``. This facade keeps
its API stable while making every read-only multi-host fallback daemon/deadline
bounded, so a stale DVR socket can never own Helper process lifetime.
"""

import queue
import threading
import time
from typing import Callable

from linkvideo_vpn_helper.services.archive_service_core import *  # noqa: F401,F403
from linkvideo_vpn_helper.services.archive_service_core import ArchiveService as _CoreArchiveService


class ArchiveService(_CoreArchiveService):
    DEEP_FALLBACK_MAX_HOSTS = 48
    DEEP_FALLBACK_MAX_WORKERS = 12
    DEEP_FALLBACK_DEADLINE_SECONDS = 14.0
    RESERVE_FALLBACK_DEADLINE_SECONDS = 9.0

    def _probe_hosts_daemon(
        self,
        hosts: list[str],
        camera: ArchiveCamera,
        start_ts: float,
        end_ts: float,
        *,
        socket_timeout: int,
        max_workers: int,
        deadline_seconds: float,
        cancel_event=None,
        thread_prefix: str = "archive-probe",
    ) -> tuple[list[tuple[str, dict | None, list[str]]], list[str]]:
        """Probe HLS hosts using daemon workers under one wall-clock deadline."""
        ordered = list(dict.fromkeys(str(x).strip() for x in hosts if str(x).strip()))
        if not ordered:
            return [], []

        semaphore = threading.Semaphore(max(1, min(int(max_workers), len(ordered))))
        completed: queue.Queue[tuple[str, dict | None, list[str], BaseException | None]] = queue.Queue()

        def run(host: str) -> None:
            with semaphore:
                if cancel_event is not None and cancel_event.is_set():
                    return
                try:
                    info, errors = self._probe_hls_host(host, camera, start_ts, end_ts, socket_timeout)
                    completed.put((host, info, list(errors or []), None))
                except BaseException as exc:
                    completed.put((host, None, [], exc))

        for host in ordered:
            threading.Thread(
                target=run,
                args=(host,),
                daemon=True,
                name=f"{thread_prefix}:{host}",
            ).start()

        pending = set(ordered)
        results: list[tuple[str, dict | None, list[str]]] = []
        deadline = time.monotonic() + max(0.25, float(deadline_seconds))

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Операция отменена пользователем")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                host, info, errors, exc = completed.get(timeout=min(0.15, remaining))
            except queue.Empty:
                continue
            if host not in pending:
                continue
            pending.remove(host)
            if exc is not None:
                results.append((host, None, [f"{host}: {exc}"]))
            else:
                results.append((host, info, errors))

        return results, [host for host in ordered if host in pending]

    def _deep_candidate_hosts(self, primary_server: str, operator_id: int = 241, camera_id: str = "") -> list[str]:
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

    def discover(
        self,
        camera_id: str,
        operator_id: int,
        start_local: datetime,
        end_local: datetime,
        progress: Callable[[str, str], None] | None = None,
        cancel_event=None,
    ) -> ArchiveDiscovery:
        """Discover the real DVR path without any process-owning executor waits."""
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        if progress:
            progress("Получаю данные камеры", "Запрашиваю B2O…")
        camera = self.b2o.camera(operator_id, camera_id)
        operator_id = self.b2o.resolve_operator_id(camera.label, operator_id)
        start_ts = self._local_to_epoch(start_local, camera.utc_offset_hours)
        end_ts = self._local_to_epoch(end_local, camera.utc_offset_hours)
        if end_ts <= start_ts:
            raise ValueError("Время окончания должно быть позже начала")

        checked: list[str] = []
        errors: list[str] = []

        if progress:
            progress("Определяю DVR по плейлисту", f"{camera.server} · выбранный период")
        player_info, player_errors = self._probe_hls_host(camera.server, camera, start_ts, end_ts, 8)
        errors.extend(player_errors)
        if camera.server not in checked:
            checked.append(camera.server)
        player_slices: list[ArchiveSlice] = []
        player_hosts: list[str] = []
        if player_info:
            player_slices = list(player_info.get("slices") or [])
            player_hosts = list(player_info.get("hosts") or [])
            for host in player_hosts:
                if host not in checked:
                    checked.append(host)
            plan = self._build_plan(player_slices, start_ts, end_ts)
            gaps = self._gaps(plan, start_ts, end_ts)
            if player_hosts:
                self._remember_history_hosts(camera.label, player_hosts)
                self._remember_global_hosts(operator_id, player_hosts)
            if plan and not gaps:
                return ArchiveDiscovery(
                    camera, start_ts, end_ts, plan, [], checked, errors, [],
                    hls_fallback_url=str(player_info.get("url") or ""),
                    hls_fallback_duration=float(player_info.get("duration") or 0.0),
                    hls_fallback_segments=int(player_info.get("segments") or 0),
                    hls_fallback_host=str(player_info.get("host") or camera.server),
                    hls_fallback_method=str(player_info.get("method") or "player playlist"),
                    hls_fallback_hosts=player_hosts,
                )
        else:
            plan = []
            gaps = [ArchiveGap(start_ts, end_ts)]

        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")
        if progress:
            progress("Проверяю переезды архива", "Ищу reserve-transfer только для непокрытого периода…")
        reserve_events = self.b2o.reserve_transfers(camera.server, start_ts, end_ts, max_pages=2)
        reserve_hosts: list[str] = []
        for event in reserve_events:
            for host in (event.server_to, event.server_from):
                clean = self.b2o.normalize_vcore_host(host)
                if clean and clean != camera.server and clean not in reserve_hosts:
                    reserve_hosts.append(clean)

        extra_slices: list[ArchiveSlice] = []
        if reserve_hosts:
            reserve_results, reserve_timed_out = self._probe_hosts_daemon(
                reserve_hosts,
                camera,
                start_ts,
                end_ts,
                socket_timeout=6,
                max_workers=8,
                deadline_seconds=self.RESERVE_FALLBACK_DEADLINE_SECONDS,
                cancel_event=cancel_event,
                thread_prefix="archive-reserve-player",
            )
            for host, info, probe_errors in reserve_results:
                if host not in checked:
                    checked.append(host)
                errors.extend(probe_errors)
                if info:
                    extra_slices.extend(info.get("slices") or [])
                    learned = list(info.get("hosts") or [])
                    self._remember_history_hosts(camera.label, learned)
                    self._remember_global_hosts(operator_id, learned)
            if reserve_timed_out:
                errors.append(
                    f"Reserve fallback: {len(reserve_timed_out)} медленных серверов пропущено после "
                    f"{self.RESERVE_FALLBACK_DEADLINE_SECONDS:.0f} сек"
                )

        all_slices = list(player_slices) + extra_slices
        plan = self._build_plan(all_slices, start_ts, end_ts)
        gaps = self._gaps(plan, start_ts, end_ts)

        deep_slices: list[ArchiveSlice] = []
        if gaps:
            deep_slices, deep_checked, deep_count = self._deep_search_missing(
                camera, start_ts, end_ts, all_slices, set(checked), progress, cancel_event
            )
            if deep_slices:
                all_slices.extend(deep_slices)
                plan = self._build_plan(all_slices, start_ts, end_ts)
                gaps = self._gaps(plan, start_ts, end_ts)
            checked.extend(x for x in deep_checked if x not in checked)
            if deep_count:
                errors.append(f"Fallback по реальному реестру B2O: проверено {deep_count} серверов")

        confirmed_hosts = list(dict.fromkeys([x.host for x in plan]))
        if confirmed_hosts:
            self._remember_history_hosts(camera.label, confirmed_hosts)
            self._remember_global_hosts(operator_id, confirmed_hosts)

        only_player = bool(player_info) and not extra_slices and not deep_slices
        return ArchiveDiscovery(
            camera, start_ts, end_ts, plan, gaps, checked, errors, reserve_events,
            hls_fallback_url=str(player_info.get("url") or "") if (player_info and only_player) else "",
            hls_fallback_duration=float(player_info.get("duration") or 0.0) if player_info else 0.0,
            hls_fallback_segments=int(player_info.get("segments") or 0) if player_info else 0,
            hls_fallback_host=str(player_info.get("host") or "") if player_info else "",
            hls_fallback_method=str(player_info.get("method") or "") if player_info else "",
            hls_fallback_hosts=player_hosts,
        )

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
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Операция отменена пользователем")

        current_plan = self._build_plan(list(known_slices), start_ts, end_ts)
        missing = self._gaps(current_plan, start_ts, end_ts)
        if not missing:
            return [], [], 0

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
        if progress:
            progress(
                "Проверяю резервные DVR",
                f"Не закрыто {sum(g.duration for g in missing):.0f} сек · кандидатов из B2O: {total}",
            )

        results, timed_out_hosts = self._probe_hosts_daemon(
            candidates,
            camera,
            probe_start,
            probe_end,
            socket_timeout=5,
            max_workers=self.DEEP_FALLBACK_MAX_WORKERS,
            deadline_seconds=self.DEEP_FALLBACK_DEADLINE_SECONDS,
            cancel_event=cancel_event,
            thread_prefix="archive-player-fallback",
        )

        for host, info, _errors in results:
            checked_count += 1
            checked_hosts.append(host)
            if info:
                found.extend(info.get("slices") or [])
                learned = list(info.get("hosts") or [])
                if learned:
                    self._remember_global_hosts(resolved_operator, learned)
            current_plan = self._build_plan(list(known_slices) + found, start_ts, end_ts)
            if current_plan and not self._gaps(current_plan, start_ts, end_ts):
                break
            if progress and (checked_count % 6 == 0 or info):
                detail = f"Проверено {checked_count}/{total}"
                if info:
                    detail += f" · найдено на {info.get('host')}"
                progress("Проверяю резервные DVR", detail)

        if progress and timed_out_hosts:
            progress(
                "Fallback ограничен по времени",
                f"Проверено {checked_count}/{total}; ещё {len(timed_out_hosts)} медленных серверов не задерживают результат",
            )
        return found, checked_hosts, checked_count
