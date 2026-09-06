from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import actions, metrics
from .config import Config
from .hypotheses import diagnose
from .potok_client import PotokClient
from .seed import STATE_PATH

console = Console()


def _default_job_ids() -> tuple[int, int] | None:
    if not STATE_PATH.exists():
        return None
    payload = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
    jobs = payload.get("jobs", {})
    if "sick" in jobs and "healthy" in jobs:
        return jobs["sick"], jobs["healthy"]
    return None


def cmd_report(args: argparse.Namespace) -> None:
    config = Config.load()
    with PotokClient(config) as client:
        m = metrics.compute_job_metrics(client, args.job_id)
    _print_metrics_table(m)


def cmd_investigate(args: argparse.Namespace) -> None:
    config = Config.load()
    job_id, baseline_id = args.job_id, args.baseline_job_id
    if job_id is None or baseline_id is None:
        defaults = _default_job_ids()
        if defaults is None:
            raise SystemExit("Укажите --job-id и --baseline-job-id (или сначала запустите seed.py init)")
        job_id, baseline_id = defaults

    with PotokClient(config) as client:
        console.print(f"[bold]Собираю метрики...[/bold] job={job_id}, baseline={baseline_id}")
        job_metrics = metrics.compute_job_metrics(client, job_id)
        baseline_metrics = metrics.compute_job_metrics(client, baseline_id)

        _print_metrics_table(job_metrics, title="Исследуемая вакансия")
        _print_metrics_table(baseline_metrics, title="Эталон для сравнения")

        console.print("[bold]Строю гипотезы (Claude)...[/bold]")
        result = diagnose(config, job_metrics, baseline_metrics)

        console.print(f"\n[bold red]{result.summary}[/bold red]\n")
        table = Table(title="Гипотезы", show_lines=True)
        table.add_column("Уверенность")
        table.add_column("Гипотеза")
        table.add_column("Доказательство")
        table.add_column("Рекомендация")
        for h in result.hypotheses:
            table.add_row(f"{h['confidence']:.0%}", h["title"], h["evidence"], h["recommended_action"])
        console.print(table)

        if args.apply:
            console.print("\n[bold yellow]Публикую находку в Потоке...[/bold yellow]")
            outcome = actions.apply_diagnosis(client, job_id, result)
            console.print(json.dumps(outcome, ensure_ascii=False, indent=2))


def _print_metrics_table(m: metrics.JobMetrics, title: str | None = None) -> None:
    table = Table(title=title or m.job_name)
    table.add_column("Метрика")
    table.add_column("Значение")
    table.add_row("Всего кандидатов", str(m.total_applicants))
    table.add_row("Активных", str(m.active_applicants))
    table.add_row("Отклонено", f"{m.declined_count} ({m.decline_rate:.0%})")
    table.add_row("Дней до отказа (среднее)", _fmt(m.avg_days_to_decline))
    table.add_row("Дней на этапе (среднее)", _fmt(m.avg_stage_duration_days))
    table.add_row("Отклик рекрутера, часов (среднее)", _fmt(m.avg_recruiter_response_hours))
    if m.decline_reason_breakdown:
        table.add_row("Причины отказа", ", ".join(f"{k}: {v}" for k, v in m.decline_reason_breakdown.items()))
    console.print(table)


def _fmt(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "нет данных"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="funnel-doctor")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Метрики по одной вакансии")
    report.add_argument("--job-id", type=int, required=True)
    report.set_defaults(func=cmd_report)

    investigate = sub.add_parser("investigate", help="Сравнить вакансию с эталоном и построить диагноз")
    investigate.add_argument("--job-id", type=int, default=None, help="Исследуемая вакансия")
    investigate.add_argument("--baseline-job-id", type=int, default=None, help="Вакансия-эталон для сравнения")
    investigate.add_argument("--apply", action="store_true", help="Опубликовать находку в Потоке (комментарии + напоминание)")
    investigate.set_defaults(func=cmd_investigate)

    return parser


def main() -> None:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
