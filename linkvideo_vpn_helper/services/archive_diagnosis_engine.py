from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from linkvideo_vpn_helper.services.archive_service import ArchiveDiscovery


class DiagnosisSide(str, Enum):
    NONE = "none"
    CAMERA = "camera"
    CLIENT_SITE = "client_site"
    SERVER = "server"
    MOVE = "move"
    UNKNOWN = "unknown"


class DiagnosisConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


@dataclass(slots=True)
class DiagnosisEvidence:
    title: str
    detail: str
    kind: str = "neutral"  # success/info/warning/danger/neutral


@dataclass(slots=True)
class GapDiagnosis:
    start: float
    end: float
    side: DiagnosisSide
    confidence: DiagnosisConfidence
    title: str
    summary: str
    evidence: list[DiagnosisEvidence] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveDiagnosis:
    side: DiagnosisSide
    confidence: DiagnosisConfidence
    title: str
    summary: str
    evidence: list[DiagnosisEvidence] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    gaps: list[GapDiagnosis] = field(default_factory=list)

    @property
    def confidence_text(self) -> str:
        return {
            DiagnosisConfidence.HIGH: "Высокая",
            DiagnosisConfidence.MEDIUM: "Средняя",
            DiagnosisConfidence.LOW: "Низкая",
            DiagnosisConfidence.INSUFFICIENT: "Недостаточно данных",
        }[self.confidence]

    @property
    def side_text(self) -> str:
        return {
            DiagnosisSide.NONE: "Разрывов не обнаружено",
            DiagnosisSide.CAMERA: "Камера / локальное подключение камеры",
            DiagnosisSide.CLIENT_SITE: "Сторона адреса клиента",
            DiagnosisSide.SERVER: "Серверная сторона",
            DiagnosisSide.MOVE: "Переезд между серверами",
            DiagnosisSide.UNKNOWN: "Причина не определена",
        }[self.side]


class ArchiveDiagnosisEngine:
    """Экспертный анализ причин разрывов по подтверждённым фактам.

    Движок намеренно не выдаёт сетевые советы и не придумывает причину. Он
    сравнивает точные DVR gaps основной камеры, камеры того же адреса,
    независимые камеры текущего vcore и reserve-transfers. Результат можно
    показывать сотруднику напрямую или в будущем передавать LLM как компактный
    структурированный контекст.
    """

    ADDRESS_OVERLAP_MIN_SECONDS = 2.0
    SERVER_OVERLAP_MIN_SECONDS = 2.0
    MATCH_FRACTION_MIN = 0.35
    STRONG_MATCH_FRACTION = 0.60
    SYNCHRONOUS_START_SECONDS = 20.0
    MOVE_NEAR_GAP_SECONDS = 90.0

    @staticmethod
    def _overlap_seconds(start: float, end: float, discovery: ArchiveDiscovery) -> float:
        return sum(max(0.0, min(end, gap.end) - max(start, gap.start)) for gap in discovery.gaps)

    def _match_for_gap(self, start: float, end: float, discovery: ArchiveDiscovery) -> tuple[bool, bool, float, float]:
        """Возвращает совпадение DVR-разрыва с учётом доли и синхронности.

        Старый анализ считал совпадением любые 2 секунды пересечения, что могло
        давать ложные выводы на длинных интервалах. Теперь учитываются доля
        пересечения и близость начала разрыва.
        """
        duration = max(0.1, end - start)
        best_overlap = 0.0
        best_start_delta = float("inf")
        for gap in discovery.gaps:
            overlap = max(0.0, min(end, gap.end) - max(start, gap.start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_start_delta = abs(float(gap.start) - float(start))
        fraction = best_overlap / duration
        minimum_seconds = max(2.0, min(10.0, duration * 0.25))
        matched = best_overlap >= minimum_seconds and fraction >= self.MATCH_FRACTION_MIN
        strong = matched and fraction >= self.STRONG_MATCH_FRACTION and best_start_delta <= self.SYNCHRONOUS_START_SECONDS
        return matched, strong, best_overlap, best_start_delta

    @staticmethod
    def _problem(discovery: ArchiveDiscovery) -> bool:
        return discovery.coverage_percent < 99.5 or any(g.duration >= 5.0 for g in discovery.gaps)

    @staticmethod
    def _ratio(part: int, total: int) -> float:
        return (part / total) if total else 0.0

    def _move_for_gap(self, main: ArchiveDiscovery, start: float, end: float):
        best = None
        best_distance = None
        for event in main.reserve_events:
            if event.overlaps(start, end):
                return event, 0.0
            if event.start is None:
                continue
            distance = min(abs(event.start - start), abs(event.start - end))
            if distance <= self.MOVE_NEAR_GAP_SECONDS and (best_distance is None or distance < best_distance):
                best = event
                best_distance = distance
        return best, best_distance

    def analyze(
        self,
        main: ArchiveDiscovery,
        address_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]],
        server_compare: list[tuple[str, ArchiveDiscovery | None, Exception | None]],
    ) -> ArchiveDiagnosis:
        valid_address = [d for _, d, err in address_compare if d is not None and err is None]
        valid_server = [d for _, d, err in server_compare if d is not None and err is None]
        failed_address = sum(1 for _, d, err in address_compare if d is None or err is not None)
        failed_server = sum(1 for _, d, err in server_compare if d is None or err is not None)

        if not main.gaps or main.coverage_percent >= 99.5:
            return ArchiveDiagnosis(
                side=DiagnosisSide.NONE,
                confidence=DiagnosisConfidence.HIGH,
                title="Существенных разрывов не обнаружено",
                summary="За выбранный период Helper подтвердил практически полное покрытие DVR-архивом.",
                evidence=[
                    DiagnosisEvidence(
                        "Покрытие архива",
                        f"Подтверждено {main.coverage_percent:.1f}% выбранного периода.",
                        "success",
                    )
                ],
            )

        gap_results = [self._analyze_gap(main, gap.start, gap.end, valid_address, valid_server) for gap in main.gaps]

        # Если большая часть длительности разрывов классифицирована одинаково,
        # этот вывод становится общим. Иначе честно сообщаем о смешанной картине.
        duration_by_side: dict[DiagnosisSide, float] = {}
        total_gap = 0.0
        for gap, result in zip(main.gaps, gap_results):
            duration = max(0.0, gap.duration)
            total_gap += duration
            duration_by_side[result.side] = duration_by_side.get(result.side, 0.0) + duration

        dominant_side = max(duration_by_side, key=duration_by_side.get) if duration_by_side else DiagnosisSide.UNKNOWN
        dominant_share = (duration_by_side.get(dominant_side, 0.0) / total_gap) if total_gap else 0.0
        dominant_gaps = [g for g in gap_results if g.side == dominant_side]

        if dominant_share < 0.60 and len({g.side for g in gap_results}) > 1:
            evidence = [
                DiagnosisEvidence(
                    "Разрывы имеют разные признаки",
                    "Helper не объединяет их в одну причину. Откройте «Разрывы», чтобы увидеть вывод по каждому интервалу.",
                    "warning",
                )
            ]
            return ArchiveDiagnosis(
                side=DiagnosisSide.UNKNOWN,
                confidence=DiagnosisConfidence.LOW,
                title="Единую причину для всех разрывов определить нельзя",
                summary="В выбранном периоде найдено несколько разрывов с разными признаками. Общий вывод был бы ненадёжным.",
                evidence=evidence,
                cautions=self._comparison_cautions(valid_address, valid_server, failed_address, failed_server),
                gaps=gap_results,
            )

        confidence = self._aggregate_confidence(dominant_gaps)
        title, summary = self._side_copy(dominant_side)
        evidence = self._merge_evidence(dominant_gaps)
        cautions = self._comparison_cautions(valid_address, valid_server, failed_address, failed_server)

        return ArchiveDiagnosis(
            side=dominant_side,
            confidence=confidence,
            title=title,
            summary=summary,
            evidence=evidence,
            cautions=cautions,
            gaps=gap_results,
        )

    def _analyze_gap(
        self,
        main: ArchiveDiscovery,
        start: float,
        end: float,
        address: list[ArchiveDiscovery],
        server: list[ArchiveDiscovery],
    ) -> GapDiagnosis:
        gap_duration = max(0.1, end - start)

        move, move_distance = self._move_for_gap(main, start, end)
        if move is not None:
            if move_distance == 0:
                detail = "Время reserve-transfer пересекается с отсутствующим DVR-интервалом."
                confidence = DiagnosisConfidence.HIGH
            else:
                detail = f"Событие reserve-transfer находится примерно в {int(round(move_distance or 0))} сек от границы разрыва."
                confidence = DiagnosisConfidence.MEDIUM
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.MOVE,
                confidence,
                "Разрыв связан по времени с переездом камеры",
                "Сопоставление выполнено по epoch: время камеры и журнал reserve-transfers UTC+7 приведены к одному моменту времени.",
                [DiagnosisEvidence("Переезд / reserve-transfer", detail, "info")],
            )

        address_matches = [(d, self._match_for_gap(start, end, d)) for d in address]
        server_matches = [(d, self._match_for_gap(start, end, d)) for d in server]
        address_hits = [d for d, match in address_matches if match[0]]
        server_hits = [d for d, match in server_matches if match[0]]
        address_strong = [d for d, match in address_matches if match[1]]
        server_strong = [d for d, match in server_matches if match[1]]
        address_ratio = self._ratio(len(address_hits), len(address))
        server_ratio = self._ratio(len(server_hits), len(server))

        # Для server-side вывода нужны синхронные разрывы у независимых камер,
        # а не случайные короткие пересечения.
        server_mass = len(server_strong) >= 3 or (len(server) >= 4 and len(server_hits) >= 3 and server_ratio >= 0.50)
        address_mass = len(address_hits) >= 2 and (address_ratio >= 0.60 or len(address_strong) >= 2)

        if server_mass:
            confidence = DiagnosisConfidence.HIGH if len(server_strong) >= 3 and server_ratio >= 0.50 else DiagnosisConfidence.MEDIUM
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.SERVER,
                confidence,
                "Наиболее вероятна серверная сторона",
                "В тот же момент похожие DVR-разрывы обнаружены у нескольких других камер текущего vcore.",
                [
                    DiagnosisEvidence(
                        "Совпадение на vcore",
                        f"Совпадающий разрыв: {len(server_hits)} из {len(server)} проверенных независимых камер; синхронных по началу: {len(server_strong)}.",
                        "danger" if confidence == DiagnosisConfidence.HIGH else "warning",
                    ),
                    DiagnosisEvidence(
                        "Камеры адреса",
                        f"Совпадение на адресе: {len(address_hits)} из {len(address)} проверенных камер." if address else "Другие камеры адреса не были указаны.",
                        "neutral",
                    ),
                ],
            )

        if address_mass and not server_mass:
            confidence = DiagnosisConfidence.HIGH if len(address_hits) >= 2 and (not server or server_ratio <= 0.20) else DiagnosisConfidence.MEDIUM
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.CLIENT_SITE,
                confidence,
                "Наиболее вероятна сторона адреса клиента",
                "Несколько камер одного адреса потеряли архив одновременно, но массового совпадения на текущем vcore не видно.",
                [
                    DiagnosisEvidence(
                        "Камеры одного адреса",
                        f"Совпадающий разрыв: {len(address_hits)} из {len(address)} проверенных камер адреса; синхронных: {len(address_strong)}.",
                        "warning",
                    ),
                    DiagnosisEvidence(
                        "Текущий vcore",
                        f"Совпадение: {len(server_hits)} из {len(server)} проверенных камер." if server else "Недостаточно других камер vcore для уверенного исключения серверной стороны.",
                        "success" if server and not server_hits else "neutral",
                    ),
                ],
            )

        # Если у основной камеры есть разрыв, а проверенные соседи адреса и vcore
        # в этот момент писали, наиболее узкий оставшийся источник — конкретная
        # камера или её локальный путь до сети.
        address_healthy = bool(address) and not address_hits
        server_healthy = bool(server) and not server_hits
        if address_healthy and server_healthy:
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.CAMERA,
                DiagnosisConfidence.HIGH,
                "Наиболее вероятна конкретная камера или её локальное подключение",
                "В момент разрыва другие камеры этого адреса и проверенные камеры vcore продолжали запись.",
                [
                    DiagnosisEvidence("Камеры адреса", f"{len(address)} из {len(address)} без совпадающего разрыва.", "success"),
                    DiagnosisEvidence("Текущий vcore", f"{len(server)} из {len(server)} без совпадающего разрыва.", "success"),
                ],
            )

        if server_healthy and not address:
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.CAMERA,
                DiagnosisConfidence.MEDIUM,
                "Вероятнее проблема со стороны камеры или адреса клиента",
                "Другие проверенные камеры текущего vcore продолжали запись, поэтому массовая серверная проблема маловероятна. Камер этого адреса для разделения «камера / общий интернет адреса» недостаточно.",
                [DiagnosisEvidence("Текущий vcore", f"Совпадающих разрывов у {len(server)} проверенных камер не найдено.", "success")],
            )

        if address_healthy and not server:
            return GapDiagnosis(
                start,
                end,
                DiagnosisSide.CAMERA,
                DiagnosisConfidence.MEDIUM,
                "Вероятнее проблема конкретной камеры",
                "Другие проверенные камеры этого адреса продолжали запись. Серверную массовость проверить на достаточной выборке не удалось.",
                [DiagnosisEvidence("Камеры адреса", f"Совпадающих разрывов у {len(address)} камер адреса не найдено.", "success")],
            )

        return GapDiagnosis(
            start,
            end,
            DiagnosisSide.UNKNOWN,
            DiagnosisConfidence.INSUFFICIENT,
            "Точную сторону проблемы определить не удалось",
            "Данных недостаточно, чтобы достоверно разделить проблему камеры, адреса клиента и серверной стороны.",
            [
                DiagnosisEvidence(
                    "Сравнение",
                    f"Камер адреса проверено: {len(address)}; других камер vcore: {len(server)}.",
                    "neutral",
                )
            ],
        )

    @staticmethod
    def _aggregate_confidence(gaps: list[GapDiagnosis]) -> DiagnosisConfidence:
        if not gaps:
            return DiagnosisConfidence.INSUFFICIENT
        rank = {
            DiagnosisConfidence.HIGH: 3,
            DiagnosisConfidence.MEDIUM: 2,
            DiagnosisConfidence.LOW: 1,
            DiagnosisConfidence.INSUFFICIENT: 0,
        }
        min_rank = min(rank[g.confidence] for g in gaps)
        return next(key for key, value in rank.items() if value == min_rank)

    @staticmethod
    def _side_copy(side: DiagnosisSide) -> tuple[str, str]:
        return {
            DiagnosisSide.CAMERA: (
                "Вероятнее проблема конкретной камеры",
                "Совпадения у других камер не подтверждают общий сбой адреса или vcore. Проверьте факты ниже — Helper не делает вывод без сравнительных данных.",
            ),
            DiagnosisSide.CLIENT_SITE: (
                "Вероятнее проблема на стороне адреса клиента",
                "Разрывы совпадают у нескольких камер одного адреса, а массового совпадения на текущем vcore не обнаружено.",
            ),
            DiagnosisSide.SERVER: (
                "Вероятнее проблема на серверной стороне",
                "Одновременно похожие разрывы обнаружены у нескольких независимых камер текущего vcore.",
            ),
            DiagnosisSide.MOVE: (
                "Разрыв связан с переездом камеры между серверами",
                "Время разрыва совпадает или почти совпадает с reserve-transfer после корректного пересчёта журнала UTC+7.",
            ),
            DiagnosisSide.UNKNOWN: (
                "Причину определить недостаточно надёжно",
                "Helper не нашёл набора признаков, достаточного для уверенного вывода.",
            ),
            DiagnosisSide.NONE: (
                "Разрывов не обнаружено",
                "Архив покрывает выбранный период.",
            ),
        }[side]

    @staticmethod
    def _merge_evidence(gaps: list[GapDiagnosis]) -> list[DiagnosisEvidence]:
        merged: list[DiagnosisEvidence] = []
        seen: set[tuple[str, str]] = set()
        for gap in gaps:
            for item in gap.evidence:
                key = (item.title, item.detail)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged[:6]

    @staticmethod
    def _comparison_cautions(address: list[ArchiveDiscovery], server: list[ArchiveDiscovery], failed_address: int = 0, failed_server: int = 0) -> list[str]:
        cautions: list[str] = []
        if not address:
            cautions.append("Не указаны или не получены другие камеры этого адреса — нельзя уверенно отделить проблему одной камеры от общего интернета/питания адреса.")
        if len(server) < 3:
            cautions.append("Проверено мало других камер текущего vcore — уверенность в исключении серверной массовости снижена.")
        if failed_address:
            cautions.append(f"Не удалось проверить камер адреса: {failed_address}. Эти камеры не считаются ни исправными, ни проблемными.")
        if failed_server:
            cautions.append(f"Не удалось проверить камер vcore: {failed_server}. Helper не использует их как доказательство отсутствия серверного сбоя.")
        return cautions
