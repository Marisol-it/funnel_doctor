"""Считает метрики воронки по вакансии из сырых данных API — в самом API
готовых агрегатов вроде "конверсия за период" нет (см. 13-recipes.md,
рецепты 6-8), поэтому агент строит их сам поверх ajs_joins и events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean

from . import api
from .potok_client import PotokClient

RESPONSE_EVENT_TYPES = {"Event::Comment", "Event::Call", "Event::Meeting", "Event::Email"}
# 05-events.md называет это "Event::StageChanged", но реальный тенант отдаёт
# "Event::Stage" — тот же паттерн расхождения, что и с "comment"/"Event::Comment"
# в api.post_event.
STAGE_CHANGE_EVENT_TYPE = "Event::Stage"


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class QuickSnapshot:
    """Дешёвый снимок вакансии для сетки на дашборде — только из GET /jobs,
    без обхода events по каждому кандидату (это делает compute_job_metrics).
    Три независимых, тоже бесплатных (без похода в events) сигнала:
    застревание на входе, застревание в любом одном этапе воронки и доля
    отказов — берётся худший из трёх."""

    job_id: int
    job_name: str
    state_id: str
    active_applicants: int
    total_applicants: int
    first_stage_share: float
    bottleneck_share: float
    decline_rate: float
    health: str  # "green" | "yellow" | "red"
    reason: str | None  # что именно триггернуло не-зелёный статус


ENTRY_STAGE_TYPES = {"applied", "sourced"}
TERMINAL_SUCCESS_STAGE_TYPES = {"accepted"}
MIN_SAMPLE = 3  # меньше — доли (100% от одного кандидата) не значат ничего

_HEALTH_LEVELS = ["green", "yellow", "red"]


def _severity(value: float, yellow_at: float, red_at: float) -> int:
    if value < yellow_at:
        return 0
    if value < red_at:
        return 1
    return 2


def quick_snapshot(job: dict) -> QuickSnapshot:
    counts = job.get("applicants_count", {}) or {}
    active = counts.get("active", 0)
    total = counts.get("all", 0)
    stages = job.get("stages", []) or []

    # Сигнал 1: застряли на входе, ещё не начали двигаться.
    # Кандидаты попадают на applied/sourced в зависимости от того, как были
    # добавлены (Рецепт 4/13-recipes.md) — это не всегда stages[0] по
    # порядку serial, поэтому считаем по stage_type, а не по позиции.
    entry_active = sum(s["active_applicants"] for s in stages if s.get("stage_type") in ENTRY_STAGE_TYPES)
    first_stage_share = (entry_active / active) if active else 0.0
    entry_severity = _severity(first_stage_share, 0.5, 0.8) if active >= MIN_SAMPLE else 0

    # Сигнал 2: затор в любом одном промежуточном этапе (не входном и не
    # "принят" — скопление перед финалом это хороший знак, а не проблема).
    middle_stages = [
        s for s in stages if s.get("stage_type") not in ENTRY_STAGE_TYPES | TERMINAL_SUCCESS_STAGE_TYPES
    ]
    bottleneck_stage = max(middle_stages, key=lambda s: s["active_applicants"], default=None)
    bottleneck_active = bottleneck_stage["active_applicants"] if bottleneck_stage else 0
    bottleneck_share = (bottleneck_active / active) if active else 0.0
    # Порог выше, чем для входного затора: наш собственный сидинг двигает
    # кандидатов пачками за один tick, из-за чего они и так естественно
    # скапливаются на "текущем" этапе — 0.5 слишком чувствителен к этому
    # артефакту генерации данных, а не к реальной проблеме процесса.
    bottleneck_severity = _severity(bottleneck_share, 0.65, 0.85) if active >= MIN_SAMPLE else 0

    # Сигнал 3: доля отказов — declined_applicants уже есть в ответе GET
    # /jobs по каждому этапу, дополнительный запрос не нужен.
    declined_total = sum(s.get("declined_applicants", 0) for s in stages)
    decline_rate = (declined_total / total) if total else 0.0
    # Пороги выше, чем кажется интуитивным: отсеять 40-60% кандидатов на
    # входе — нормальная воронка найма, а не признак проблемы. Красным
    # помечаем только явно аномальную долю отказов.
    decline_severity = _severity(decline_rate, 0.6, 0.8) if total >= MIN_SAMPLE else 0

    severities = [
        (entry_severity, f"{first_stage_share:.0%} активных кандидатов ещё не сдвинулись с первого этапа"),
        (
            bottleneck_severity,
            f"{bottleneck_share:.0%} активных кандидатов скопились на этапе «{bottleneck_stage['name']}»"
            if bottleneck_stage
            else None,
        ),
        (decline_severity, f"{decline_rate:.0%} кандидатов отклонено"),
    ]
    worst_severity, reason = max(severities, key=lambda item: item[0])
    health = _HEALTH_LEVELS[worst_severity]

    return QuickSnapshot(
        job_id=job["id"],
        job_name=job["name"],
        state_id=job["state_id"],
        active_applicants=active,
        total_applicants=total,
        first_stage_share=round(first_stage_share, 2),
        bottleneck_share=round(bottleneck_share, 2),
        decline_rate=round(decline_rate, 2),
        health=health,
        reason=reason if worst_severity > 0 else None,
    )


@dataclass
class JobMetrics:
    job_id: int
    job_name: str
    total_applicants: int
    active_applicants: int
    declined_count: int
    decline_rate: float
    avg_days_to_decline: float | None
    avg_stage_duration_days: float | None
    avg_recruiter_response_hours: float | None
    decline_reason_breakdown: dict[str, int] = field(default_factory=dict)
    stage_snapshot: list[dict] = field(default_factory=list)


def compute_job_metrics(client: PotokClient, job_id: int) -> JobMetrics:
    job = api.get_job(client, job_id)
    ajs_joins = list(api.list_ajs_joins(client, job_id))
    reasons_by_id = {r["id"]: r["name"] for r in api.list_declination_reasons(client)}

    active = [a for a in ajs_joins if a.get("active", True)]
    declined_count = len(ajs_joins) - len(active)

    stage_durations, response_delays, days_to_decline, reason_ids = _event_derived_metrics(
        client, job_id, ajs_joins
    )

    reason_breakdown: dict[str, int] = {}
    for reason_id in reason_ids:
        name = reasons_by_id.get(reason_id, "неизвестно")
        reason_breakdown[name] = reason_breakdown.get(name, 0) + 1

    return JobMetrics(
        job_id=job_id,
        job_name=job["name"],
        total_applicants=len(ajs_joins),
        active_applicants=len(active),
        declined_count=declined_count,
        decline_rate=(declined_count / len(ajs_joins)) if ajs_joins else 0.0,
        avg_days_to_decline=mean(days_to_decline) if days_to_decline else None,
        avg_stage_duration_days=mean(stage_durations) if stage_durations else None,
        avg_recruiter_response_hours=mean(response_delays) if response_delays else None,
        decline_reason_breakdown=reason_breakdown,
        stage_snapshot=[
            {"name": s["name"], "stage_type": s["stage_type"], "active_applicants": s["active_applicants"]}
            for s in job.get("stages", [])
        ],
    )


def _event_derived_metrics(
    client: PotokClient, job_id: int, ajs_joins: list[dict]
) -> tuple[list[float], list[float], list[float], list[int]]:
    """Ходит в события каждого кандидата за метриками, которых нет напрямую
    в ajs_join: реальный ответ API не содержит ни `declined_at`, ни
    `declination_reason_id` (расходится с 04-ajs-joins.md) — причина отказа
    и момент отказа берутся из события `Event::Decline` (`properties.
    declination_reason_id`), а не из полей самого ajs_join."""
    stage_durations: list[float] = []
    response_delays: list[float] = []
    days_to_decline: list[float] = []
    reason_ids: list[int] = []

    for ajs in ajs_joins:
        applicant_id = ajs["applicant_id"]
        events = [e for e in api.list_events(client, applicant_id) if e.get("job_id") == job_id]
        events.sort(key=lambda e: e["created_at"])

        stage_changes = [e for e in events if e["type"] == STAGE_CHANGE_EVENT_TYPE]
        for prev, curr in zip(stage_changes, stage_changes[1:]):
            delta = parse_dt(curr["created_at"]) - parse_dt(prev["created_at"])
            stage_durations.append(delta.total_seconds() / 86400)

        for change in stage_changes:
            change_time = parse_dt(change["created_at"])
            next_response = next(
                (e for e in events if e["type"] in RESPONSE_EVENT_TYPES and parse_dt(e["created_at"]) > change_time),
                None,
            )
            if next_response:
                delta = parse_dt(next_response["created_at"]) - change_time
                response_delays.append(delta.total_seconds() / 3600)

        decline_event = next((e for e in events if e["type"] == "Event::Decline"), None)
        if decline_event:
            reason_id = (decline_event.get("properties") or {}).get("declination_reason_id")
            if reason_id is not None:
                reason_ids.append(reason_id)
            if events:
                delta = parse_dt(decline_event["created_at"]) - parse_dt(events[0]["created_at"])
                days_to_decline.append(delta.total_seconds() / 86400)

    return stage_durations, response_delays, days_to_decline, reason_ids


def compare_jobs(a: JobMetrics, b: JobMetrics) -> dict:
    """Кросс-секционное сравнение двух вакансий — основной сценарий MVP-демо
    (не требует истории за много недель, работает с любого дня посева)."""
    return {
        "job_a": a.job_name,
        "job_b": b.job_name,
        "decline_rate_delta_pp": round((a.decline_rate - b.decline_rate) * 100, 1),
        "avg_stage_duration_delta_days": _delta(a.avg_stage_duration_days, b.avg_stage_duration_days),
        "avg_recruiter_response_delta_hours": _delta(
            a.avg_recruiter_response_hours, b.avg_recruiter_response_hours
        ),
        "avg_days_to_decline_delta": _delta(a.avg_days_to_decline, b.avg_days_to_decline),
    }


def _delta(x: float | None, y: float | None) -> float | None:
    if x is None or y is None:
        return None
    return round(x - y, 2)
