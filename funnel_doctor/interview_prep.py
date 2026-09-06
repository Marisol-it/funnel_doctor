"""Подготовка к интервью — идея 2 из брейншторма: агент читает вакансию,
профиль кандидата и историю взаимодействий, отмечает, какие компетенции уже
подтверждены/под вопросом, и готовит конкретные вопросы под пробелы.

Только "до интервью" — оценка после интервью (надиктовка → скоринг) в этой
версии не реализована, чтобы уложиться в оставшееся до дедлайна время."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic

from . import api
from .config import Config
from .metrics import STAGE_CHANGE_EVENT_TYPE
from .potok_client import PotokClient

DEFAULT_MODEL = "claude-sonnet-5"

PREP_TOOL = {
    "name": "submit_interview_prep",
    "description": "Вернуть разбор компетенций кандидата и вопросы к интервью.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Одно предложение — общий вывод по готовности кандидата."},
            "competencies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "status": {"type": "string", "enum": ["confirmed", "partial", "unknown"]},
                        "evidence": {"type": "string", "description": "На чём основан статус — цитата/факт из истории или 'нет данных'."},
                    },
                    "required": ["name", "status", "evidence"],
                },
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "competency": {"type": "string"},
                        "question": {"type": "string"},
                        "follow_up": {"type": "string", "description": "Уточняющий вопрос на типичный уклончивый ответ."},
                    },
                    "required": ["competency", "question", "follow_up"],
                },
            },
        },
        "required": ["summary", "competencies", "questions"],
    },
}

SYSTEM_PROMPT = """Ты помогаешь интервьюеру подготовиться к разговору с конкретным кандидатом.
По описанию вакансии определи 4-6 ключевых компетенций для роли. Для каждой оцени статус
на основе ИСТОРИИ ВЗАИМОДЕЙСТВИЙ (комментарии рекрутера, прошлые интервью) — только то, что
реально там написано, ничего не выдумывай. Если истории по компетенции нет — статус "unknown"
и evidence "нет данных в истории", а не догадка.
Вопросы задавай только под компетенции со статусом "partial" или "unknown" — то, что уже
"confirmed", повторно спрашивать незачем. follow_up — конкретный уточняющий вопрос на
вероятный уклончивый ответ, не общая фраза вроде "расскажите подробнее".
Это материал для подготовки интервьюера, а не готовое решение о найме."""


@dataclass
class InterviewPrep:
    summary: str
    competencies: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    current_stage: str | None


def prepare_interview(config: Config, client: PotokClient, job_id: int, applicant_id: int) -> InterviewPrep:
    job = api.get_job(client, job_id)
    applicant = api.get_applicant(client, applicant_id)
    ajs = next(iter(api.list_ajs_joins(client, job_id, applicant_id=applicant_id)), None)
    history = _build_history(client, job_id, applicant_id)

    evidence = {
        "job": {"name": job["name"], "description_html": job.get("description") or ""},
        "candidate": {
            "name": f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}".strip(),
            "title": applicant.get("title"),
            "tags": applicant.get("tags", []),
            "source": applicant.get("source_name"),
        },
        "current_stage": (ajs.get("stage") or {}).get("stage_type") if ajs else None,
        "interaction_history": history,
    }

    anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = anthropic_client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[PREP_TOOL],
        tool_choice={"type": "tool", "name": "submit_interview_prep"},
        messages=[
            {
                "role": "user",
                "content": f"Подготовь интервью со кандидатом:\n\n{json.dumps(evidence, ensure_ascii=False, indent=2)}",
            }
        ],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    parsed = _recover_malformed_input(tool_use.input)
    return InterviewPrep(
        summary=parsed.get("summary") or "Недостаточно данных для содержательной подготовки.",
        competencies=_normalize_list(parsed.get("competencies", []), ("name", "status", "evidence")),
        questions=_normalize_list(parsed.get("questions", []), ("competency", "question", "follow_up")),
        current_stage=evidence["current_stage"],
    )


def _build_history(client: PotokClient, job_id: int, applicant_id: int) -> list[dict[str, Any]]:
    events = [e for e in api.list_events(client, applicant_id) if e.get("job_id") == job_id]
    events.sort(key=lambda e: e["created_at"])
    history = []
    for e in events:
        if e["type"] == STAGE_CHANGE_EVENT_TYPE:
            history.append({"when": e["created_at"], "type": "переход этапа", "text": e.get("body") or ""})
        elif e["type"] in {"Event::Comment", "Event::Call", "Event::Meeting", "Event::Email"}:
            history.append({"when": e["created_at"], "type": "комментарий", "text": e.get("body") or ""})
    return history


def _recover_malformed_input(raw: dict[str, Any]) -> dict[str, Any]:
    """Тот же паттерн защиты, что и в hypotheses.py: Claude иногда пакует
    весь объект JSON-строкой в одно из полей вместо структурированного
    tool-вызова целиком."""
    if raw.get("summary"):
        return raw
    for field in ("competencies", "questions"):
        nested = raw.get(field)
        if isinstance(nested, str):
            try:
                unwrapped = json.loads(nested)
            except json.JSONDecodeError:
                continue
            if isinstance(unwrapped, dict) and "summary" in unwrapped:
                return unwrapped
    return raw


def _normalize_list(raw: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    normalized = []
    for item in raw:
        if isinstance(item, dict):
            normalized.append({k: item.get(k) or "" for k in keys})
        else:
            normalized.append({keys[0]: str(item), **{k: "" for k in keys[1:]}})
    return normalized
