from __future__ import annotations

import time
from typing import Any, Iterator

import httpx
import truststore

from .config import Config

# Некоторые сети (корпоративные прокси с TLS-инспекцией) используют корневой
# сертификат, которого нет в бандле certifi, но который есть в системном
# хранилище ОС. truststore подключает системное хранилище к Python ssl.
truststore.inject_into_ssl()

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0


class PotokAPIError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Поток API error {status_code}: {payload}")


class PotokClient:
    """Тонкий клиент к API v2/v3 «Потока». Один токен для обеих версий (00-quickstart.md)."""

    def __init__(self, config: Config):
        self._base_url = config.potok_base_url
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {config.potok_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PotokClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            response = self._client.request(method, url, **kwargs)
            if response.status_code == 429:
                wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                time.sleep(wait)
                last_error = PotokAPIError(429, response.text)
                continue
            if response.status_code >= 400:
                raise PotokAPIError(response.status_code, _safe_json(response))
            if not response.content:
                return None
            return response.json()
        raise last_error or RuntimeError("Превышен лимит попыток запроса к API Потока")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self.request("PUT", path, json=json)

    def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self.request("PATCH", path, json=json)

    # ---- Пагинация -------------------------------------------------

    def paginate_pages(
        self, path: str, params: dict[str, Any] | None = None, items_key: str = "data"
    ) -> Iterator[dict[str, Any]]:
        """Постраничная пагинация (jobs, applicants v3, ...). Отдаёт элементы по одному."""
        page = 1
        params = dict(params or {})
        while True:
            params["page"] = page
            response = self.get(path, params=params)
            items = response.get(items_key, [])
            yield from items
            total_pages = response.get("pages") or response.get("total_pages") or 1
            if page >= total_pages or not items:
                return
            page += 1

    def paginate_cursor(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Курсорная пагинация (ajs_joins, events_stream, dictionaries, ...)."""
        params = dict(params or {})
        cursor: str | None = None
        while True:
            request_params = dict(params)
            if cursor:
                request_params["page_cursor"] = cursor
            response = self.get(path, params=request_params)
            objects = response.get("objects", [])
            yield from objects
            if not response.get("has_next_page"):
                return
            cursor = response.get("page_next_cursor")
            if not cursor:
                return


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
