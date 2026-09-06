from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` execute этот файл напрямую, а не как часть пакета — сам
# добавляет в sys.path только папку файла (funnel_doctor/), а не корень
# проекта. Добавляем корень явно, чтобы `import funnel_doctor` работал
# независимо от того, откуда запущен `streamlit run`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from funnel_doctor import actions, api, interview_prep, metrics, theme
from funnel_doctor.config import Config
from funnel_doctor.hypotheses import diagnose
from funnel_doctor.potok_client import PotokClient

st.set_page_config(page_title="Дашборд воронок подбора", page_icon="🩺", layout="wide")


def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True

    expected = st.secrets.get("APP_PASSWORD")
    if not expected:
        # Локальный запуск без секрета пароля — не блокируем разработчика.
        st.session_state["authenticated"] = True
        return True

    st.title("Дашборд воронок подбора")
    password = st.text_input("Пароль", type="password")
    if st.button("Войти"):
        if password == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Неверный пароль")
    return False


@st.cache_resource
def get_config() -> Config:
    return Config.load()


@st.cache_data(ttl=300)
def cached_jobs(_config: Config) -> list[dict]:
    with PotokClient(_config) as client:
        return list(api.list_jobs(client, by_scope="all"))


@st.cache_data(ttl=600)
def cached_job_metrics(_config: Config, job_id: int) -> metrics.JobMetrics:
    with PotokClient(_config) as client:
        return metrics.compute_job_metrics(client, job_id)


# Этапы, для которых имеет смысл готовиться к интервью — само интервью и всё,
# что ему предшествует. После interview_client готовиться уже не к чему:
# кандидат либо на согласовании оффера, либо принят.
PRE_INTERVIEW_STAGE_TYPES = {"applied", "sourced", "screening", "interview_hr", "interview_client"}


@st.cache_data(ttl=300)
def cached_job_candidates(_config: Config, job_id: int) -> list[dict]:
    """Активные кандидаты вакансии на этапах интервью и раньше — ajs_joins
    не отдают имя, поэтому дозапрашиваем карточку по каждому (то же самое
    N+1, что и в compute_job_metrics, но для интерфейса выбора это ок)."""
    with PotokClient(_config) as client:
        candidates = []
        for ajs in api.list_ajs_joins(client, job_id):
            if not ajs.get("active", True):
                continue
            stage_type = (ajs.get("stage") or {}).get("stage_type")
            if stage_type not in PRE_INTERVIEW_STAGE_TYPES:
                continue
            applicant = api.get_applicant(client, ajs["applicant_id"])
            name = f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}".strip()
            candidates.append({"applicant_id": ajs["applicant_id"], "name": name or f"#{ajs['applicant_id']}", "stage_type": stage_type})
        return candidates




def render_dashboard(config: Config) -> None:
    st.title("Дашборд воронок подбора")
    st.caption("Наблюдает за воронкой найма через API Потока и сам находит проблемные вакансии")

    jobs = cached_jobs(config)
    if not jobs:
        st.info("В тенанте пока нет вакансий.")
        return

    cols = st.columns(3)
    for i, job in enumerate(jobs):
        snapshot = metrics.quick_snapshot(job)
        with cols[i % 3]:
            render_job_card(snapshot)


def render_job_card(snapshot: metrics.QuickSnapshot) -> None:
    with st.container(border=True):
        st.markdown(theme.health_badge_html(snapshot.health), unsafe_allow_html=True)
        st.markdown(f"**{snapshot.job_name}**")
        st.caption(f"Активных: {snapshot.active_applicants} · Всего: {snapshot.total_applicants}")
        if snapshot.reason:
            st.caption(f"⚠️ {snapshot.reason}")
        if st.button("Открыть", key=f"open-{snapshot.job_id}"):
            st.session_state["selected_job_id"] = snapshot.job_id
            st.session_state["page"] = "Вакансия"
            st.rerun()


def render_job_detail(config: Config) -> None:
    jobs = cached_jobs(config)
    if not jobs:
        st.info("В тенанте пока нет вакансий.")
        return

    theme.breadcrumb("Главная", "Вакансия")
    job_by_id = {j["id"]: j["name"] for j in jobs}
    default_id = st.session_state.get("selected_job_id", jobs[0]["id"])
    job_id = st.selectbox(
        "Вакансия",
        options=list(job_by_id.keys()),
        format_func=lambda jid: job_by_id[jid],
        index=list(job_by_id.keys()).index(default_id) if default_id in job_by_id else 0,
    )
    st.session_state["selected_job_id"] = job_id

    m = cached_job_metrics(config, job_id)
    render_metrics_summary(m)


def render_metrics_summary(m: metrics.JobMetrics) -> None:
    st.subheader(m.job_name)
    cols = st.columns(4)
    cols[0].metric("Активных", m.active_applicants)
    cols[1].metric("Отклонено", f"{m.declined_count} ({m.decline_rate:.0%})")
    cols[2].metric("Дней на этапе", _fmt(m.avg_stage_duration_days))
    cols[3].metric("Отклик рекрутера, ч", _fmt(m.avg_recruiter_response_hours))

    if m.decline_reason_breakdown:
        st.caption("Причины отказа: " + ", ".join(f"{k} ({v})" for k, v in m.decline_reason_breakdown.items()))

    if m.stage_snapshot:
        st.plotly_chart(theme.stage_funnel_chart(m.stage_snapshot), width="stretch", config={"displayModeBar": False})


def _fmt(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def render_investigate(config: Config) -> None:
    theme.breadcrumb("Главная", "Аналитика", "Исследование воронок")
    st.title("Исследование воронок")
    jobs = cached_jobs(config)
    if len(jobs) < 2:
        st.info("Нужно минимум две вакансии для сравнения.")
        return

    job_by_id = {j["id"]: j["name"] for j in jobs}
    snapshots = {j["id"]: metrics.quick_snapshot(j) for j in jobs}
    all_ids = list(job_by_id.keys())
    # Эталоном может быть только здоровая или условно здоровая вакансия —
    # сравнивать "плохое с плохим" бессмысленно, диагноз получится ни о чём.
    baseline_ids = [jid for jid in all_ids if snapshots[jid].health in ("green", "yellow")]

    def format_with_health(jid: int) -> str:
        return f"{theme.health_dot(snapshots[jid].health)} {job_by_id[jid]}"

    col1, col2 = st.columns(2)
    job_id = col1.selectbox("Исследуемая вакансия", all_ids, format_func=format_with_health)

    if not baseline_ids:
        col2.warning("Нет вакансий с зелёным или жёлтым статусом для сравнения.")
        return
    baseline_id = col2.selectbox(
        "Эталон",
        baseline_ids,
        index=min(1, len(baseline_ids) - 1),
        format_func=format_with_health,
    )

    if job_id == baseline_id:
        st.warning("Выберите две разные вакансии.")
        return

    if st.button("Исследовать", type="primary"):
        with st.spinner("Считаю метрики и строю гипотезы..."):
            job_metrics = cached_job_metrics(config, job_id)
            baseline_metrics = cached_job_metrics(config, baseline_id)
            diagnosis = diagnose(config, job_metrics, baseline_metrics)
        st.session_state["diagnosis"] = diagnosis
        st.session_state["diagnosis_job_id"] = job_id

        col1, col2 = st.columns(2)
        with col1:
            render_metrics_summary(job_metrics)
        with col2:
            render_metrics_summary(baseline_metrics)

    diagnosis = st.session_state.get("diagnosis")
    if diagnosis and st.session_state.get("diagnosis_job_id") == job_id:
        render_diagnosis(config, job_id, diagnosis)


def render_diagnosis(config: Config, job_id: int, diagnosis) -> None:
    st.markdown(f"### {diagnosis.summary}")
    for h in sorted(diagnosis.hypotheses, key=lambda h: h["confidence"], reverse=True):
        with st.container(border=True):
            st.markdown(f"**{h['title']}** — {h['confidence']:.0%}")
            st.progress(h["confidence"])
            st.caption(h["evidence"])
            st.markdown(f"➡️ {h['recommended_action']}")

    if not diagnosis.hypotheses:
        return

    st.divider()
    if st.session_state.get("confirm_apply"):
        st.warning("Это опубликует комментарии активным кандидатам вакансии и поставит напоминание рекрутеру в реальном Потоке.")
        c1, c2 = st.columns(2)
        if c1.button("Да, опубликовать в Поток", type="primary"):
            with PotokClient(config) as client:
                outcome = actions.apply_diagnosis(client, job_id, diagnosis)
            st.session_state["confirm_apply"] = False
            st.success(f"Опубликовано комментариев: {len(outcome['comments_posted'])}" + (", напоминание создано" if outcome["reminder"] else ""))
        if c2.button("Отмена"):
            st.session_state["confirm_apply"] = False
            st.rerun()
    else:
        if st.button("Применить"):
            st.session_state["confirm_apply"] = True
            st.rerun()


COMPETENCY_STATUS_LABEL = {"confirmed": "✅ Подтверждено", "partial": "⚠️ Частично", "unknown": "❔ Нет данных"}


def render_interview_prep(config: Config) -> None:
    theme.breadcrumb("Главная", "Подготовка к интервью")
    st.title("Подготовка к интервью")
    st.caption("Материал для интервьюера на основе истории кандидата — не готовое решение о найме")

    jobs = cached_jobs(config)
    if not jobs:
        st.info("В тенанте пока нет вакансий.")
        return

    job_by_id = {j["id"]: j["name"] for j in jobs}
    job_id = st.selectbox("Вакансия", list(job_by_id.keys()), format_func=lambda jid: job_by_id[jid])

    candidates = cached_job_candidates(config, job_id)
    if not candidates:
        st.info("На этой вакансии нет активных кандидатов на этапах интервью и раньше.")
        return

    candidate_by_id = {c["applicant_id"]: c for c in candidates}
    applicant_id = st.selectbox(
        "Кандидат",
        list(candidate_by_id.keys()),
        format_func=lambda aid: f"{candidate_by_id[aid]['name']} — {candidate_by_id[aid]['stage_type']}",
    )

    if st.button("Подготовить интервью", type="primary"):
        with st.spinner("Читаю историю кандидата и готовлю вопросы..."):
            with PotokClient(config) as client:
                prep = interview_prep.prepare_interview(config, client, job_id, applicant_id)
        st.session_state["interview_prep"] = prep
        st.session_state["interview_prep_key"] = (job_id, applicant_id)

    prep = st.session_state.get("interview_prep")
    if prep and st.session_state.get("interview_prep_key") == (job_id, applicant_id):
        render_interview_prep_result(prep)


def render_interview_prep_result(prep: interview_prep.InterviewPrep) -> None:
    st.markdown(f"### {prep.summary}")

    st.subheader("Компетенции")
    for c in prep.competencies:
        with st.container(border=True):
            st.markdown(f"**{c['name']}** — {COMPETENCY_STATUS_LABEL.get(c['status'], c['status'])}")
            st.caption(c["evidence"])

    groups_with_items = [g for g in prep.questions if g.get("items")]
    if groups_with_items:
        st.subheader("Вопросы к интервью")
        for group in groups_with_items:
            with st.container(border=True):
                st.markdown(f"**{group.get('competency', '')}**")
                for i, q in enumerate(group["items"], start=1):
                    st.markdown(f"{i}. ❓ {q.get('question', '')}")
                    st.caption(f"↳ Follow-up: {q.get('follow_up', '')}")
    elif not prep.questions:
        st.info("Все ключевые компетенции уже подтверждены историей — дополнительных вопросов не требуется.")
    else:
        st.warning("Модель не смогла сформировать вопросы в этом прогоне — нажмите «Подготовить интервью» ещё раз.")


def main() -> None:
    theme.inject_theme()
    if not check_password():
        return

    config = get_config()
    # Обычные кнопки вместо st.radio: у радио-виджета скрытый декоративный
    # индикатор рендерится через несколько вложенных div с нестабильными
    # автогенерируемыми классами — его невозможно надёжно спрятать через CSS.
    # У кнопки такого индикатора нет в принципе.
    nav_icons = {
        "Дашборд": "dashboard",
        "Вакансия": "work",
        "Исследование воронок": "search",
        "Подготовка к интервью": "quiz",
    }
    if "page" not in st.session_state:
        st.session_state["page"] = "Дашборд"
    for label, icon in nav_icons.items():
        is_active = st.session_state["page"] == label
        if st.sidebar.button(
            label,
            icon=f":material/{icon}:",
            key=f"nav-{label}",
            type="primary" if is_active else "secondary",
            width="stretch",
        ):
            st.session_state["page"] = label
            st.rerun()
    page = st.session_state["page"]

    if page == "Дашборд":
        render_dashboard(config)
    elif page == "Вакансия":
        render_job_detail(config)
    elif page == "Исследование воронок":
        render_investigate(config)
    else:
        render_interview_prep(config)


main()
