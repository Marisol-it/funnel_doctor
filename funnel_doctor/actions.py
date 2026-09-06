"""Слой действий — агент не просто пишет отчёт, а оставляет след в самом
Потоке. Комментарии в API всегда привязаны к кандидату (applicant_id
обязателен в POST /api/v2/events), поэтому находку публикуем на карточках
кандидатов, которые сейчас активны на проблемной вакансии, а не "в воздух".
Никаких деструктивных операций (decline, смена этапа) — только комментарий
и напоминание, решение остаётся за рекрутером."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import api
from .hypotheses import Diagnosis
from .potok_client import PotokClient


def find_active_candidates(client: PotokClient, job_id: int, limit: int = 3) -> list[dict]:
    active = [a for a in api.list_ajs_joins(client, job_id) if a.get("active", True)]
    active.sort(key=lambda a: a.get("datetime_of_create", ""))
    return active[:limit]


def apply_diagnosis(client: PotokClient, job_id: int, diagnosis: Diagnosis, candidate_limit: int = 3) -> dict:
    """Публикует находку комментариями на активных кандидатах вакансии и
    ставит рекрутеру напоминание разобрать вакансию. Возвращает сводку
    выполненных действий — для отчёта пользователю, не для тихого лога."""
    top = diagnosis.hypotheses[0] if diagnosis.hypotheses else None
    body = _format_comment(diagnosis, top)

    targets = find_active_candidates(client, job_id, limit=candidate_limit)
    posted = []
    for ajs in targets:
        event = api.post_event(client, ajs["applicant_id"], body, job_id=job_id)
        posted.append({"applicant_id": ajs["applicant_id"], "event_id": event.get("id")})

    reminder = None
    job = api.get_job(client, job_id)
    recruiter = job.get("executive_recruiter")
    if recruiter and targets:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        reminder = api.create_reminder(
            client,
            from_iso=tomorrow,
            applicant_id=targets[0]["applicant_id"],
            author_id=recruiter["id"],
            job_id=job_id,
            body=f"Recruitment Funnel Doctor: разобрать вакансию «{job['name']}» — {diagnosis.summary}",
        )

    return {"comments_posted": posted, "reminder": reminder}


def _format_comment(diagnosis: Diagnosis, top: dict | None) -> str:
    lines = ["[Recruitment Funnel Doctor] " + diagnosis.summary]
    if top:
        lines.append(f"Вероятная причина: {top['title']} — {top['evidence']}")
        lines.append(f"Рекомендация: {top['recommended_action']}")
    return "\n".join(lines)
