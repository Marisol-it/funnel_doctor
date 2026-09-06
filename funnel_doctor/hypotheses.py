"""LLM-слой: получает готовый evidence-бандл (посчитанные метрики, а не сырой
API-доступ) и возвращает ранжированные гипотезы + рекомендованное действие.
Разделение "Python считает факты / LLM их интерпретирует" — осознанный выбор:
для конкурсного демо это надёжнее, чем agentic tool-loop поверх живого API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic

from .config import Config
from .metrics import JobMetrics, compare_jobs

DEFAULT_MODEL = "claude-sonnet-5"

DIAGNOSIS_TOOL = {
    "name": "submit_diagnosis",
    "description": "Вернуть ранжированный диагноз проблемы в воронке найма.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Одно предложение — что не так."},
            "hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "evidence": {"type": "string", "description": "Конкретные цифры из evidence-бандла."},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "recommended_action": {"type": "string"},
                    },
                    "required": ["title", "evidence", "confidence", "recommended_action"],
                },
            },
        },
        "required": ["summary", "hypotheses"],
    },
}

SYSTEM_PROMPT = """Ты — HR-аналитик, который объясняет проблемы в воронке найма нанимающим менеджерам
и рекрутерам простым языком, всегда опираясь на конкретные цифры из предоставленных данных.
Не выдумывай цифры, которых нет в evidence-бандле. Если данных недостаточно для гипотезы — не предлагай её.
Ранжируй гипотезы от наиболее вероятной к наименее. Рекомендуемое действие должно быть конкретным
и выполнимым (например, "сократить SLA ответа рекрутера с X до Y часов для вакансии Z"),
а не общими словами вроде "улучшить процесс"."""


@dataclass
class Diagnosis:
    summary: str
    hypotheses: list[dict[str, Any]]


def diagnose(config: Config, job_a: JobMetrics, job_b: JobMetrics) -> Diagnosis:
    evidence = {
        "vacancy_under_investigation": job_a.job_name,
        "baseline_vacancy": job_b.job_name,
        "metrics": {
            "under_investigation": _metrics_dict(job_a),
            "baseline": _metrics_dict(job_b),
        },
        "comparison": compare_jobs(job_a, job_b),
    }

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[DIAGNOSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_diagnosis"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Вот данные по двум вакансиям на близких ролях. Объясни, что не так с "
                    f"«{job_a.job_name}» относительно «{job_b.job_name}» как бейзлайна:\n\n"
                    f"{json.dumps(evidence, ensure_ascii=False, indent=2)}"
                ),
            }
        ],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    parsed = _recover_malformed_input(tool_use.input)
    hypotheses = _normalize_hypotheses(parsed.get("hypotheses", []))
    summary = parsed.get("summary") or _fallback_summary(hypotheses)
    return Diagnosis(summary=summary, hypotheses=hypotheses)


def _fallback_summary(hypotheses: list[dict[str, Any]]) -> str:
    """Модель иногда не заполняет summary, даже когда hypotheses пришёл
    нормальным массивом с содержательными гипотезами — в этом случае писать
    "данных недостаточно" было бы враньём, синтезируем резюме из топ-гипотезы."""
    if not hypotheses:
        return "Модель не смогла сформулировать резюме — данных недостаточно для диагноза."
    top = max(hypotheses, key=lambda h: h["confidence"])
    return f"{top['title']} — {top['evidence']}"


def _recover_malformed_input(raw: dict[str, Any]) -> dict[str, Any]:
    """Иногда модель вместо {"summary": ..., "hypotheses": [...]} кладёт
    ВЕСЬ объект JSON-строкой внутрь поля hypotheses (summary при этом
    отсутствует на верхнем уровне). Пробуем распаковать такой конверт,
    прежде чем разбирать поля по отдельности."""
    if "summary" in raw and raw["summary"]:
        return raw
    nested = raw.get("hypotheses")
    if isinstance(nested, str):
        try:
            unwrapped = json.loads(nested)
        except json.JSONDecodeError:
            return raw
        if isinstance(unwrapped, dict) and ("summary" in unwrapped or "hypotheses" in unwrapped):
            return unwrapped
    return raw


def _normalize_hypotheses(raw: Any) -> list[dict[str, Any]]:
    """Claude обычно следует input_schema, но не гарантированно — иногда
    кладёт в hypotheses не массив, а строку с JSON-текстом (тогда обычный
    цикл по ней пройдётся посимвольно), а элементы массива — не объекты, а
    голые строки. Приводим к ожидаемой форме, а не падаем в CLI."""
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
            normalized.append(
                {
                    "title": item.get("title") or "—",
                    "evidence": item.get("evidence") or "",
                    "confidence": _safe_confidence(item.get("confidence")),
                    "recommended_action": item.get("recommended_action") or "",
                }
            )
        else:
            normalized.append({"title": str(item), "evidence": "", "confidence": 0.0, "recommended_action": ""})
    return normalized


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _metrics_dict(m: JobMetrics) -> dict[str, Any]:
    return {
        "total_applicants": m.total_applicants,
        "active_applicants": m.active_applicants,
        "declined_count": m.declined_count,
        "decline_rate_pct": round(m.decline_rate * 100, 1),
        "avg_days_to_decline": m.avg_days_to_decline,
        "avg_stage_duration_days": m.avg_stage_duration_days,
        "avg_recruiter_response_hours": m.avg_recruiter_response_hours,
        "decline_reason_breakdown": m.decline_reason_breakdown,
        "stage_snapshot": m.stage_snapshot,
    }
