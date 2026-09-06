from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

try:
    import streamlit as st

    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except (ImportError, FileNotFoundError):
    # CLI/seed-скрипты не запущены под `streamlit run` — секретов нет, это ожидаемо.
    pass


@dataclass(frozen=True)
class Config:
    potok_base_url: str
    potok_token: str
    anthropic_api_key: str

    @classmethod
    def load(cls) -> "Config":
        base_url = os.environ.get("POTOK_BASE_URL", "").rstrip("/")
        token = os.environ.get("POTOK_TOKEN", "")
        if not base_url or not token:
            raise RuntimeError(
                "POTOK_BASE_URL и POTOK_TOKEN обязательны — задайте их в .env (см. .env.example)"
            )
        return cls(
            potok_base_url=base_url,
            potok_token=token,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
