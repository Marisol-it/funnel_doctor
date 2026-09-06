"""
Генератор синтетического сценария для демо: одна "здоровая" вакансия и одна
"больная" (замедленный отклик рекрутера + повышенный отсев после интервью).

Таймстемпы (created_at, stage_changed_at, ...) проставляет сам Поток в момент
запроса — их нельзя передать явно. Поэтому сценарий рассчитан на запуск
`tick` раз в день (крон) в течение 1-2 недель до демо: каждый прогон
продвигает часть кандидатов дальше по плану, и в API накапливается настоящая,
растянутая по времени история вместо кучи событий с одной датой.

Команды:
    python -m funnel_doctor.seed init                        # один раз: создать вакансии healthy/sick и кандидатов
    python -m funnel_doctor.seed add-background               # один раз: добавить фоновые вакансии для дашборда
    python -m funnel_doctor.seed tick                          # каждый день: выполнить шаги, срок которых наступил
    python -m funnel_doctor.seed status                        # посмотреть прогресс сценария
    python -m funnel_doctor.seed fast-forward <job_key> [N]    # разово: добавить N кандидатов и сразу прогнать по воронке (без растяжки по дням)
    python -m funnel_doctor.seed seed-healthy-demo             # один раз: контрольная вакансия, явно зелёная на дашборде
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import api
from .config import Config
from .potok_client import PotokClient

STATE_PATH = Path(__file__).resolve().parent.parent / "seed_state.json"

FIRST_NAMES = [
    "Анна", "Дмитрий", "Мария", "Сергей", "Ольга", "Иван", "Екатерина", "Павел",
    "Наталья", "Алексей", "Юлия", "Михаил", "Елена", "Андрей", "Светлана", "Николай",
    "Виктория", "Роман", "Татьяна", "Артём", "Ксения", "Владимир", "Дарья", "Максим",
]
LAST_NAMES = [
    "Иванов", "Петрова", "Сидоров", "Кузнецова", "Смирнов", "Попова", "Соколов",
    "Новикова", "Морозов", "Волкова", "Егоров", "Павлова", "Козлов", "Фролова",
    "Никитин", "Данилова", "Захаров", "Романова", "Белов", "Григорьева", "Титов",
    "Медведева", "Орлов", "Тарасова",
]

SCREENING_COMMENTS = [
    "Резюме соответствует требованиям, приглашаю на скрининг.",
    "Опыт релевантный, есть вопросы по стеку — обсудим на звонке.",
    "Сильное резюме, двигаем дальше.",
]
INTERVIEW_COMMENTS = [
    "Провели интервью. В целом уверенно, но есть сомнения по системному дизайну.",
    "Хороший технический бэкграунд, командная работа тоже норм.",
    "Кандидат ожидаемо силён в своей области, слабее в смежных темах.",
]
LATE_STAGE_COMMENTS = [
    "Финальное интервью с заказчиком прошло хорошо.",
    "Обсуждаем оффер, ждём решения кандидата.",
]

PROFILES: dict[str, dict[str, Any]] = {
    "healthy": {
        "job_name": "Backend Developer — команда Платежей",
        "step_delay_days": (1, 2),
        "arrival_spread_days": (0, 3),
        "outcomes": ["accepted"] * 5 + ["early"] * 3 + ["mid"] * 3 + ["late"] * 1,
    },
    "sick": {
        "job_name": "Backend Developer — команда Роста",
        "step_delay_days": (4, 7),
        "arrival_spread_days": (0, 3),
        "outcomes": ["accepted"] * 2 + ["early"] * 2 + ["mid"] * 5 + ["late"] * 3,
    },
}

# "Фоновые" вакансии — только чтобы дашборд не выглядел пустым (2 карточки
# маловато для демо). Без вытянутого сценария вроде healthy/sick — быстрее
# приходят к результату, не растягивают таймлайн до дедлайна.
BACKGROUND_PROFILES: dict[str, dict[str, Any]] = {
    "frontend": {
        "job_name": "Frontend Developer",
        "step_delay_days": (1, 2),
        "arrival_spread_days": (0, 2),
        "outcomes": ["accepted"] * 2 + ["early"] * 2 + ["mid"] * 2,
    },
    "data_analyst": {
        "job_name": "Data Analyst — команда Аналитики",
        "step_delay_days": (2, 4),
        "arrival_spread_days": (0, 2),
        "outcomes": ["accepted"] * 1 + ["early"] * 2 + ["mid"] * 3 + ["late"] * 1,
    },
    "hr_manager": {
        "job_name": "HR-менеджер",
        "step_delay_days": (1, 2),
        "arrival_spread_days": (0, 1),
        "outcomes": ["accepted"] * 3 + ["early"] * 1,
    },
}

# Число шагов move_to_next_stage перед исходом
OUTCOME_MOVES = {"early": 1, "mid": 2, "late": 3, "accepted": 4}

JOB_DEFAULTS = {
    "schedule_type": "remote",
    "experience_type": "between1And3",
    "salary_from": 250000,
    "salary_to": 400000,
    "currency_type": "RUR",
}


@dataclass
class Step:
    action: str  # create | move | comment | decline
    due: str  # ISO date
    done: bool = False
    comment: str | None = None


@dataclass
class Candidate:
    key: str
    job: str  # "healthy" | "sick"
    first_name: str
    last_name: str
    outcome: str
    plan: list[Step]
    applicant_id: int | None = None
    ajs_id: int | None = None
    cursor: int = 0  # индекс следующего невыполненного шага


@dataclass
class ScenarioState:
    jobs: dict[str, int] = field(default_factory=dict)
    declination_reason_ids: list[int] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    def to_json(self) -> str:
        payload = {
            "jobs": self.jobs,
            "declination_reason_ids": self.declination_reason_ids,
            "candidates": [
                {**asdict(c), "plan": [asdict(s) for s in c.plan]} for c in self.candidates
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "ScenarioState":
        payload = json.loads(raw)
        candidates = [
            Candidate(
                **{**c, "plan": [Step(**s) for s in c["plan"]]},
            )
            for c in payload["candidates"]
        ]
        return cls(
            jobs=payload["jobs"],
            declination_reason_ids=payload["declination_reason_ids"],
            candidates=candidates,
        )


def _save(state: ScenarioState) -> None:
    STATE_PATH.write_text(state.to_json(), encoding="utf-8")


def _load() -> ScenarioState:
    if not STATE_PATH.exists():
        raise RuntimeError("Сценарий не инициализирован — сначала запустите `init`")
    return ScenarioState.from_json(STATE_PATH.read_text(encoding="utf-8"))


def _random_date(base: date, spread_days: tuple[int, int], rng: random.Random) -> date:
    return base + timedelta(days=rng.randint(*spread_days))


def _build_plan(profile: dict[str, Any], outcome: str, start: date, rng: random.Random) -> list[Step]:
    delay_range = profile["step_delay_days"]
    plan: list[Step] = [Step(action="create", due=start.isoformat())]
    cursor = start
    moves = OUTCOME_MOVES[outcome]
    comment_pools = [SCREENING_COMMENTS, INTERVIEW_COMMENTS, LATE_STAGE_COMMENTS, LATE_STAGE_COMMENTS]
    for i in range(moves):
        cursor = cursor + timedelta(days=rng.randint(*delay_range))
        plan.append(Step(action="move", due=cursor.isoformat()))
        cursor = cursor + timedelta(days=rng.randint(*delay_range))
        pool = comment_pools[min(i, len(comment_pools) - 1)]
        plan.append(Step(action="comment", due=cursor.isoformat(), comment=rng.choice(pool)))
    if outcome != "accepted":
        cursor = cursor + timedelta(days=rng.randint(*delay_range))
        plan.append(Step(action="decline", due=cursor.isoformat()))
    return plan


def _create_profile_jobs_and_candidates(
    client: PotokClient,
    profiles: dict[str, dict[str, Any]],
    rng: random.Random,
    today: date,
    recruiter_email: str | None,
    used_names: set[tuple[str, str]],
) -> tuple[dict[str, int], list[Candidate]]:
    jobs: dict[str, int] = {}
    candidates: list[Candidate] = []
    idx = 0
    for key, profile in profiles.items():
        job = api.create_job(client, name=profile["job_name"], description="Демо-вакансия для Recruitment Funnel Doctor", **JOB_DEFAULTS)
        jobs[key] = job["id"]
        print(f"Создана вакансия «{profile['job_name']}» — id={job['id']}")
        if recruiter_email:
            api.assign_executive_recruiter(client, job["id"], recruiter_email)

        for outcome in profile["outcomes"]:
            idx += 1
            while True:
                name = (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
                if name not in used_names:
                    used_names.add(name)
                    break
            arrival = _random_date(today, profile["arrival_spread_days"], rng)
            plan = _build_plan(profile, outcome, arrival, rng)
            candidates.append(
                Candidate(key=f"{key}-{idx}", job=key, first_name=name[0], last_name=name[1], outcome=outcome, plan=plan)
            )
    return jobs, candidates


def cmd_init(seed: int = 42) -> None:
    if STATE_PATH.exists():
        raise RuntimeError(f"{STATE_PATH} уже существует — удалите файл, если хотите пересоздать сценарий")

    rng = random.Random(seed)
    config = Config.load()
    today = date.today()

    with PotokClient(config) as client:
        reasons = api.list_declination_reasons(client)
        reason_ids = [r["id"] for r in reasons if not r.get("archived")]
        if not reason_ids:
            raise RuntimeError("В компании нет активных причин отказа (GET /api/v2/declination_reasons)")

        recruiter_email = next((u["email"] for u in api.list_users(client)), None)
        jobs, candidates = _create_profile_jobs_and_candidates(client, PROFILES, rng, today, recruiter_email, set())

    state = ScenarioState(jobs=jobs, declination_reason_ids=reason_ids, candidates=candidates)
    _save(state)
    print(f"Сценарий сохранён в {STATE_PATH}. Запускайте `tick` ежедневно до демо.")


def cmd_add_background(seed: int = 43) -> None:
    """Добавляет фоновые вакансии (BACKGROUND_PROFILES) к уже существующему
    сценарию — только чтобы дашборд не выглядел пустым из двух карточек.
    Идемпотентно: профили, чьи вакансии уже есть в state, пропускаются."""
    state = _load()
    new_profiles = {k: v for k, v in BACKGROUND_PROFILES.items() if k not in state.jobs}
    if not new_profiles:
        print("Все фоновые вакансии уже созданы — нечего добавлять.")
        return

    rng = random.Random(seed)
    config = Config.load()
    today = date.today()
    used_names = {(c.first_name, c.last_name) for c in state.candidates}

    with PotokClient(config) as client:
        recruiter_email = next((u["email"] for u in api.list_users(client)), None)
        new_jobs, new_candidates = _create_profile_jobs_and_candidates(
            client, new_profiles, rng, today, recruiter_email, used_names
        )

    state.jobs.update(new_jobs)
    state.candidates.extend(new_candidates)
    _save(state)
    print(f"Добавлено вакансий: {len(new_jobs)}, кандидатов: {len(new_candidates)}")


FAST_FORWARD_OUTCOMES = ["mid", "mid", "late", "early", "accepted"]


def cmd_fast_forward(job_key: str, count: int = 5, seed: int = 100) -> None:
    """Ручной разовый довесок к сценарию: создаёт `count` кандидатов на
    вакансии `job_key` и сразу прогоняет каждого по воронке ДО КОНЦА в
    рамках одного запуска — в отличие от `tick`, который специально
    растягивает шаги на реальные дни. Здесь дни намеренно принесены в
    жертву ради того, чтобы у вакансии появились завершённые исходы
    (отказы/оффер) к демо, а не только "зависшие" activе-кандидаты."""
    state = _load()
    if job_key not in state.jobs:
        raise RuntimeError(f"Вакансия '{job_key}' не найдена в сценарии. Доступные: {list(state.jobs)}")

    job_id = state.jobs[job_key]
    rng = random.Random(seed)
    config = Config.load()
    used_names = {(c.first_name, c.last_name) for c in state.candidates}
    comment_pools = [SCREENING_COMMENTS, INTERVIEW_COMMENTS, LATE_STAGE_COMMENTS, LATE_STAGE_COMMENTS]
    outcomes = [FAST_FORWARD_OUTCOMES[i % len(FAST_FORWARD_OUTCOMES)] for i in range(count)]

    existing_idx = sum(1 for c in state.candidates if c.job == job_key)

    with PotokClient(config) as client:
        for i, outcome in enumerate(outcomes):
            while True:
                name = (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
                if name not in used_names:
                    used_names.add(name)
                    break
            key = f"{job_key}-ff{existing_idx + i + 1}"

            applicant = api.create_applicant(
                client,
                first_name=name[0],
                last_name=name[1],
                email=f"{key}@funnel-doctor.demo",
                phone=f"7900{rng.randint(1000000, 9999999)}",
                job_id=job_id,
            )
            ajs = api.get_ajs_join_for_applicant(client, job_id, applicant["id"])
            ajs_id = ajs["id"] if ajs else None
            print(f"[{key}] создан кандидат {name[0]} {name[1]}")

            moves = OUTCOME_MOVES[outcome]
            plan: list[Step] = [Step(action="create", due=date.today().isoformat(), done=True)]
            for step_i in range(moves):
                api.move_to_next_stage(client, ajs_id)
                plan.append(Step(action="move", due=date.today().isoformat(), done=True))
                print(f"[{key}] переход на следующий этап")
                comment = rng.choice(comment_pools[min(step_i, len(comment_pools) - 1)])
                api.post_event(client, applicant["id"], comment, job_id=job_id)
                plan.append(Step(action="comment", due=date.today().isoformat(), done=True, comment=comment))
                print(f"[{key}] комментарий: {comment}")

            if outcome != "accepted":
                reason_id = rng.choice(state.declination_reason_ids)
                api.decline_applicant(client, job_id, applicant["id"], reason_id)
                plan.append(Step(action="decline", due=date.today().isoformat(), done=True))
                print(f"[{key}] отклонён (reason_id={reason_id})")

            state.candidates.append(
                Candidate(
                    key=key,
                    job=job_key,
                    first_name=name[0],
                    last_name=name[1],
                    outcome=outcome,
                    plan=plan,
                    applicant_id=applicant["id"],
                    ajs_id=ajs_id,
                    cursor=len(plan),
                )
            )

    _save(state)
    print(f"Готово: {count} кандидатов на '{job_key}' прогнаны по воронке сегодня.")


HEALTHY_DEMO_JOB_NAME = "QA Engineer — контрольный пример"
# Явно разное число переходов на кандидата — размазывает активных по 5-6
# разным этапам воронки, а не пачкой на одном (как получается при обычном
# tick/fast-forward, где кандидаты одного профиля двигаются синхронно).
# Без отказов — 0% и так ниже любого порога decline_severity.
HEALTHY_DEMO_MOVES = [0, 1, 2, 2, 3, 3, 4, 5]


def cmd_seed_healthy_demo() -> None:
    """Контрольный пример явно здоровой вакансии для дашборда — кандидаты
    сразу расставлены по разным этапам воронки, без единой пачки и без
    отказов, чтобы гарантированно получить 🟢 и было с чем сравнивать
    жёлтые/красные вакансии."""
    state = _load()
    key = "healthy_demo"
    if key in state.jobs:
        print("Контрольная вакансия уже создана — нечего делать.")
        return

    rng = random.Random(300)
    config = Config.load()
    used_names = {(c.first_name, c.last_name) for c in state.candidates}
    comment_pools = [SCREENING_COMMENTS, INTERVIEW_COMMENTS, LATE_STAGE_COMMENTS, LATE_STAGE_COMMENTS]

    with PotokClient(config) as client:
        recruiter_email = next((u["email"] for u in api.list_users(client)), None)
        job = api.create_job(
            client, name=HEALTHY_DEMO_JOB_NAME, description="Демо-вакансия для Recruitment Funnel Doctor", **JOB_DEFAULTS
        )
        job_id = job["id"]
        print(f"Создана вакансия «{HEALTHY_DEMO_JOB_NAME}» — id={job_id}")
        if recruiter_email:
            api.assign_executive_recruiter(client, job_id, recruiter_email)

        candidates: list[Candidate] = []
        for i, moves in enumerate(HEALTHY_DEMO_MOVES):
            while True:
                name = (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
                if name not in used_names:
                    used_names.add(name)
                    break
            cand_key = f"{key}-{i + 1}"

            applicant = api.create_applicant(
                client,
                first_name=name[0],
                last_name=name[1],
                email=f"{cand_key}@funnel-doctor.demo",
                phone=f"7900{rng.randint(1000000, 9999999)}",
                job_id=job_id,
            )
            ajs = api.get_ajs_join_for_applicant(client, job_id, applicant["id"])
            ajs_id = ajs["id"] if ajs else None
            print(f"[{cand_key}] создан кандидат {name[0]} {name[1]}")

            plan: list[Step] = [Step(action="create", due=date.today().isoformat(), done=True)]
            for step_i in range(moves):
                api.move_to_next_stage(client, ajs_id)
                plan.append(Step(action="move", due=date.today().isoformat(), done=True))
                comment = rng.choice(comment_pools[min(step_i, len(comment_pools) - 1)])
                api.post_event(client, applicant["id"], comment, job_id=job_id)
                plan.append(Step(action="comment", due=date.today().isoformat(), done=True, comment=comment))
                print(f"[{cand_key}] переход на следующий этап + комментарий")

            candidates.append(
                Candidate(
                    key=cand_key,
                    job=key,
                    first_name=name[0],
                    last_name=name[1],
                    outcome="parked",
                    plan=plan,
                    applicant_id=applicant["id"],
                    ajs_id=ajs_id,
                    cursor=len(plan),
                )
            )

    state.jobs[key] = job_id
    state.candidates.extend(candidates)
    _save(state)
    print(f"Готово: {len(candidates)} кандидатов размазаны по воронке '{key}'.")


def cmd_tick(today: date | None = None) -> None:
    state = _load()
    today = today or date.today()
    rng = random.Random()
    config = Config.load()
    executed = 0

    with PotokClient(config) as client:
        for candidate in state.candidates:
            while candidate.cursor < len(candidate.plan):
                step = candidate.plan[candidate.cursor]
                if step.done:
                    candidate.cursor += 1
                    continue
                if date.fromisoformat(step.due) > today:
                    break
                _execute_step(client, state, candidate, step, rng)
                step.done = True
                candidate.cursor += 1
                executed += 1

    _save(state)
    print(f"Выполнено шагов: {executed}")


def _execute_step(client: PotokClient, state: ScenarioState, candidate: Candidate, step: Step, rng: random.Random) -> None:
    job_id = state.jobs[candidate.job]

    if step.action == "create":
        applicant = api.create_applicant(
            client,
            first_name=candidate.first_name,
            last_name=candidate.last_name,
            email=f"{candidate.key}@funnel-doctor.demo",
            phone=f"7900{rng.randint(1000000, 9999999)}",
            job_id=job_id,
        )
        candidate.applicant_id = applicant["id"]
        ajs = api.get_ajs_join_for_applicant(client, job_id, applicant["id"])
        candidate.ajs_id = ajs["id"] if ajs else None
        print(f"[{candidate.key}] создан кандидат {candidate.first_name} {candidate.last_name}")
        return

    if candidate.ajs_id is None:
        ajs = api.get_ajs_join_for_applicant(client, job_id, candidate.applicant_id)  # type: ignore[arg-type]
        candidate.ajs_id = ajs["id"] if ajs else None

    if step.action == "move":
        api.move_to_next_stage(client, candidate.ajs_id)  # type: ignore[arg-type]
        print(f"[{candidate.key}] переход на следующий этап")
    elif step.action == "comment":
        api.post_event(client, candidate.applicant_id, step.comment or "Комментарий рекрутера", job_id=job_id)  # type: ignore[arg-type]
        print(f"[{candidate.key}] комментарий: {step.comment}")
    elif step.action == "decline":
        reason_id = rng.choice(state.declination_reason_ids)
        api.decline_applicant(client, job_id, candidate.applicant_id, reason_id)  # type: ignore[arg-type]
        print(f"[{candidate.key}] отклонён (reason_id={reason_id})")
    else:
        raise ValueError(f"Неизвестное действие плана: {step.action}")


def cmd_status() -> None:
    state = _load()
    for key, job_id in state.jobs.items():
        cands = [c for c in state.candidates if c.job == key]
        done = sum(1 for c in cands if c.cursor >= len(c.plan))
        pending = sum(
            1
            for c in cands
            for s in c.plan
            if not s.done and date.fromisoformat(s.due) <= date.today()
        )
        print(f"{key} (job_id={job_id}): {done}/{len(cands)} кандидатов завершили план, {pending} шагов ждут запуска tick")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    commands = {
        "init": cmd_init,
        "add-background": cmd_add_background,
        "seed-healthy-demo": cmd_seed_healthy_demo,
        "tick": cmd_tick,
        "status": cmd_status,
    }
    if len(sys.argv) >= 2 and sys.argv[1] == "fast-forward":
        if len(sys.argv) < 3:
            print("Использование: python -m funnel_doctor.seed fast-forward <job_key> [count]")
            sys.exit(1)
        job_key = sys.argv[2]
        count = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        cmd_fast_forward(job_key, count)
        return
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Использование: python -m funnel_doctor.seed [{'|'.join(commands)}|fast-forward <job_key> [count]]")
        sys.exit(1)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
