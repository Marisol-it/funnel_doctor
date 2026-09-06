"""Визуальная тема под стилистику «Потока» (см. images_potok/): светлый фон,
белые карточки с мягкой тенью, тёмно-синяя навигация, насыщенный синий акцент,
зелёные/жёлтые/красные статус-пилюли. CSS внедряется один раз в main()."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

POTOK_BLUE = "#2F80ED"
POTOK_BLUE_DARK = "#1B64C4"
POTOK_NAVY = "#434B56"
POTOK_NAV_MUTED = "#716D6B"
POTOK_BG = "#F4F6F9"
POTOK_CARD = "#FFFFFF"
POTOK_BORDER = "#E6E9EF"
POTOK_TEXT = "#1F2430"
POTOK_TEXT_MUTED = "#6B7280"
POTOK_GREEN = "#1FAA59"
POTOK_AMBER = "#E8A23D"
POTOK_RED = "#E5484D"

HEALTH_STYLE = {
    "green": (POTOK_GREEN, "Воронка в норме"),
    "yellow": (POTOK_AMBER, "Есть задержка"),
    "red": (POTOK_RED, "Требует внимания"),
}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}

.stApp {{
    background-color: {POTOK_BG};
}}

[data-testid="stSidebar"] {{
    background-color: {POTOK_CARD};
    border-right: 1px solid {POTOK_BORDER};
}}

[data-testid="stSidebar"] {{
    width: 366px !important;
    min-width: 366px !important;
    max-width: 366px !important;
}}

[data-testid="stSidebar"] > div {{
    width: 366px !important;
}}

/* Сворачивание сайдбара конфликтует с фиксированной шириной выше (контент
не занимает освободившееся место, стрелка разворачивания остаётся не на
своём месте) — поведение зависит от недокументированной внутренней
механики конкретной сборки Streamlit, воспроизводимо не почини́ть через
CSS-подбор. Проще и надёжнее убрать саму возможность свернуть. */
[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapseButton"] {{
    display: none !important;
}}

[data-testid="stMainBlockContainer"],
[data-testid="stAppViewBlockContainer"],
.main .block-container {{
    padding-top: 36px !important;
}}

/* Большой пустой блок сверху сайдбара — это stSidebarHeader (шапка с кнопкой
сворачивания), а не padding контента. Схлопываем её и задаём отступ на самом
контенте (имена testid проверены в установленном пакете streamlit==1.62.0). */
[data-testid="stSidebarHeader"] {{
    min-height: 0 !important;
    height: auto !important;
    padding: 8px 0 0 0 !important;
}}

[data-testid="stSidebar"] > div,
[data-testid="stSidebarUserContent"],
[data-testid="stSidebarContent"],
[data-testid="stSidebar"] .block-container {{
    padding-top: 36px !important;
}}

h1, h2, h3 {{
    color: {POTOK_TEXT};
    font-weight: 700;
}}

[data-testid="stCaptionContainer"] {{
    color: {POTOK_TEXT_MUTED};
}}

div[data-testid="stVerticalBlockBorderWrapper"] {{
    background-color: {POTOK_CARD};
    border: 1px solid {POTOK_BORDER};
    border-radius: 14px;
    box-shadow: 0 1px 3px rgba(16, 24, 40, 0.04);
}}

div[data-testid="stMetric"] {{
    background-color: {POTOK_CARD};
    border: 1px solid {POTOK_BORDER};
    border-radius: 12px;
    padding: 14px 16px;
}}

div[data-testid="stMetricLabel"] {{
    color: {POTOK_TEXT_MUTED};
}}

div[data-testid="stMetricValue"] {{
    color: {POTOK_TEXT};
}}

.stButton > button {{
    border-radius: 10px;
    font-weight: 600;
    border: 1px solid {POTOK_BORDER};
}}

.stButton > button[kind="primary"] {{
    background-color: {POTOK_BLUE};
    border-color: {POTOK_BLUE};
}}

.stButton > button[kind="primary"]:hover {{
    background-color: {POTOK_BLUE_DARK};
    border-color: {POTOK_BLUE_DARK};
}}

/* Навигация в сайдбаре — обычные кнопки вместо st.radio (см. streamlit_app.py) */
[data-testid="stSidebar"] .stButton {{
    width: 100% !important;
}}

[data-testid="stSidebar"] .stButton > button {{
    width: 100% !important;
    justify-content: flex-start !important;
    text-align: left !important;
    margin-bottom: 4px;
}}

[data-testid="stSidebar"] .stButton > button div,
[data-testid="stSidebar"] .stButton > button p,
[data-testid="stSidebar"] .stButton > button span {{
    justify-content: flex-start !important;
    text-align: left !important;
    width: auto !important;
}}

[data-testid="stSidebar"] .stButton > button[kind="secondary"] {{
    background-color: transparent;
    border-color: transparent;
    color: {POTOK_NAV_MUTED};
}}

[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {{
    background-color: {POTOK_BG};
    border-color: {POTOK_BG};
    color: {POTOK_NAV_MUTED};
}}

[data-testid="stSidebar"] .stButton > button[kind="primary"] {{
    background-color: {POTOK_NAVY};
    border-color: {POTOK_NAVY};
    color: #FFFFFF;
}}

[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
    background-color: {POTOK_NAVY};
    border-color: {POTOK_NAVY};
}}

.fd-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    color: #FFFFFF;
}}

.fd-breadcrumb {{
    color: {POTOK_TEXT_MUTED};
    font-size: 14px;
    margin-bottom: 4px;
}}

.fd-breadcrumb a {{
    color: {POTOK_BLUE};
    text-decoration: none;
}}
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def health_badge_html(health: str) -> str:
    color, label = HEALTH_STYLE[health]
    return f'<span class="fd-badge" style="background-color:{color}">{label}</span>'


HEALTH_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def health_dot(health: str) -> str:
    """Плейн-текстовый индикатор для мест, где HTML не рендерится —
    например, подписи опций в st.selectbox."""
    return HEALTH_DOT[health]


def breadcrumb(*parts: str) -> None:
    st.markdown(f'<div class="fd-breadcrumb">{" / ".join(parts)}</div>', unsafe_allow_html=True)


def stage_funnel_chart(stage_snapshot: list[dict]) -> go.Figure:
    """Горизонтальный бар-чарт в духе «Воронка по рекрутерам» из Потока —
    трек светло-серый, значение сплошным синим, подпись числа в конце бара."""
    stages = list(reversed(stage_snapshot))
    names = [s["name"] for s in stages]
    values = [s["active_applicants"] for s in stages]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=values,
            y=names,
            orientation="h",
            marker=dict(color=POTOK_BLUE),
            text=values,
            textposition="outside",
            textfont=dict(color=POTOK_TEXT, size=13),
            hovertemplate="%{y}: %{x}<extra></extra>",
        )
    )
    fig.update_layout(
        plot_bgcolor=POTOK_CARD,
        paper_bgcolor=POTOK_CARD,
        margin=dict(l=0, r=30, t=10, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(tickfont=dict(color=POTOK_TEXT, size=13)),
        height=max(220, 46 * len(stages)),
        font=dict(family="Inter, sans-serif"),
    )
    return fig
