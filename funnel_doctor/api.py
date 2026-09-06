"""Доменные обёртки над PotokClient — по одной функции на операцию из документации API."""
from __future__ import annotations

from typing import Any, Iterator

from .potok_client import PotokClient

# ---- Jobs ------------------------------------------------------------


def create_job(client: PotokClient, **fields: Any) -> dict[str, Any]:
    return client.post("/api/v3/jobs", json=fields)


def get_job(client: PotokClient, job_id: int) -> dict[str, Any]:
    return client.get(f"/api/v3/jobs/{job_id}")


def assign_executive_recruiter(client: PotokClient, job_id: int, email: str) -> dict[str, Any]:
    return client.post(f"/api/v3/jobs/{job_id}/assign_executive_recruiter", json={"email": email})


def list_jobs(client: PotokClient, by_scope: str = "active") -> Iterator[dict[str, Any]]:
    yield from client.paginate_pages("/api/v3/jobs", params={"by_scope": by_scope, "per_page": 100})


# ---- Applicants --------------------------------------------------------


def create_applicant(client: PotokClient, **fields: Any) -> dict[str, Any]:
    return client.post("/api/v3/applicants", json=fields)


def get_applicant(client: PotokClient, applicant_id: int) -> dict[str, Any]:
    return client.get(f"/api/v3/applicants/{applicant_id}")


# ---- AJS Joins ----------------------------------------------------------


def list_ajs_joins(
    client: PotokClient, job_id: int, applicant_id: int | None = None
) -> Iterator[dict[str, Any]]:
    params: dict[str, Any] = {"page_size": 100}
    if applicant_id is not None:
        params["applicant_id"] = applicant_id
    yield from client.paginate_cursor(f"/api/v3/jobs/{job_id}/ajs_joins", params=params)


def get_ajs_join_for_applicant(
    client: PotokClient, job_id: int, applicant_id: int
) -> dict[str, Any] | None:
    for ajs in list_ajs_joins(client, job_id, applicant_id=applicant_id):
        return ajs
    return None


def move_to_next_stage(client: PotokClient, ajs_id: int) -> dict[str, Any]:
    return client.post(f"/api/v3/ajs_joins/{ajs_id}/move_to_next_stage")


def move_to_stage_type(client: PotokClient, ajs_id: int, to_stage_type: str) -> dict[str, Any]:
    return client.post(
        f"/api/v3/ajs_joins/{ajs_id}/move_to_stage_type", json={"to_stage_type": to_stage_type}
    )


def decline_applicant(
    client: PotokClient, job_id: int, applicant_id: int, declination_reason_id: int
) -> dict[str, Any]:
    return client.post(
        f"/api/v3/jobs/{job_id}/{applicant_id}/decline.json",
        json={"declination_reason_id": declination_reason_id},
    )


# ---- Events (v2) ---------------------------------------------------------


def post_event(
    client: PotokClient,
    applicant_id: int,
    body: str,
    event_type: str = "Event::Comment",
    job_id: int | None = None,
) -> dict[str, Any]:
    """type должен быть в namespaced-форме (`Event::Comment`), не `comment` —
    05-events.md приводит пример с укороченным именем, но реальный тенант
    (и Рецепт 12 в 13-recipes.md) требует полное имя класса события."""
    payload: dict[str, Any] = {"applicant_id": applicant_id, "body": body, "type": event_type}
    if job_id is not None:
        payload["job_id"] = job_id
    return client.post("/api/v2/events", json=payload)


def stream_events(
    client: PotokClient,
    by_created_at: tuple[str, str] | None = None,
    applicant_id: int | None = None,
) -> Iterator[dict[str, Any]]:
    params: dict[str, Any] = {"page_size": 100}
    if applicant_id is not None:
        params["applicant_id"] = applicant_id
    if by_created_at is not None:
        params["by_created_at[]"] = list(by_created_at)
    yield from client.paginate_cursor("/api/v2/events_stream", params=params)


# ---- Dictionaries ---------------------------------------------------------


def list_declination_reasons(client: PotokClient) -> list[dict[str, Any]]:
    return client.get("/api/v2/declination_reasons")


# ---- Calendar ---------------------------------------------------------


def create_reminder(
    client: PotokClient,
    from_iso: str,
    applicant_id: int,
    author_id: int,
    body: str | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    reminder: dict[str, Any] = {"from": from_iso, "applicant_id": applicant_id, "author_id": author_id}
    if body:
        reminder["body"] = body
    if job_id is not None:
        reminder["job_id"] = job_id
    return client.post("/api/v3/calendar/reminders", json={"reminder": reminder})


def create_schedule(
    client: PotokClient,
    from_iso: str,
    to_iso: str,
    applicant_id: int,
    job_id: int,
    author_id: int,
    body: str,
) -> dict[str, Any]:
    schedule = {
        "from": from_iso,
        "to": to_iso,
        "applicant_id": applicant_id,
        "job_id": job_id,
        "author_id": author_id,
        "body": body,
    }
    return client.post("/api/v3/calendar/schedules", json={"schedule": schedule})


def list_events(client: PotokClient, applicant_id: int) -> Iterator[dict[str, Any]]:
    yield from client.paginate_pages(
        "/api/v2/events", params={"applicant_id": applicant_id, "per_page": 50}, items_key="data"
    )


# ---- Users ---------------------------------------------------------


def list_users(client: PotokClient) -> Iterator[dict[str, Any]]:
    yield from client.paginate_pages("/api/v3/users", params={"per_page": 100}, items_key="objects")
