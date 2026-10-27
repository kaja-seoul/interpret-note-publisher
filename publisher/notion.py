"""Notion API 최소 클라이언트 (데이터 소스 API, Notion-Version 2025-09-03).

콘텐츠 캘린더와 게시 로그 데이터 소스 ID는 config.py에 있다.
"""
from __future__ import annotations

import os
import time

import requests

API = "https://api.notion.com/v1"
VERSION = os.environ.get("NOTION_VERSION", "2025-09-03")
TIMEOUT = 30


class NotionError(RuntimeError):
    pass


class Notion:
    def __init__(self, token: str):
        if not token:
            raise NotionError("NOTION_TOKEN이 없다 (GitHub Secrets 확인)")
        self._h = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }

    def _req(self, method: str, path: str, json: dict | None = None, retries: int = 4) -> dict:
        url = f"{API}/{path.lstrip('/')}"
        last = ""
        for attempt in range(retries):
            try:
                r = requests.request(method, url, headers=self._h, json=json, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = f"연결 실패: {e}"
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                wait = float(r.headers.get("Retry-After", 3 * (attempt + 1)))
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise NotionError(f"{method} {path} 실패 (HTTP {r.status_code}): {r.text[:500]}")
            return r.json()
        raise NotionError(f"{method} {path} 재시도 끝에 실패: {last}")

    # ---- 읽기 ----
    def query(self, data_source_id: str, filter: dict | None = None, sorts: list | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            res = self._req("POST", f"data_sources/{data_source_id}/query", body)
            out += res.get("results", [])
            if not res.get("has_more"):
                return out
            cursor = res.get("next_cursor")

    def page(self, page_id: str) -> dict:
        return self._req("GET", f"pages/{page_id}")

    # ---- 쓰기 ----
    def update(self, page_id: str, properties: dict) -> dict:
        return self._req("PATCH", f"pages/{page_id}", {"properties": properties})

    def create(self, data_source_id: str, properties: dict) -> dict:
        return self._req(
            "POST",
            "pages",
            {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": properties},
        )

    def comment(self, page_id: str, text: str) -> None:
        chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [""]
        self._req(
            "POST",
            "comments",
            {"parent": {"page_id": page_id}, "rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]},
        )


# ---- 속성 읽기 도우미 ----
def prop(page: dict, name: str):
    return (page.get("properties") or {}).get(name) or {}


def text_of(page: dict, name: str) -> str:
    p = prop(page, name)
    items = p.get("title") if p.get("type") == "title" else p.get("rich_text")
    return "".join(i.get("plain_text", "") for i in (items or []))


def select_of(page: dict, name: str) -> str:
    p = prop(page, name)
    v = p.get("select") or p.get("status") or {}
    return v.get("name", "") if isinstance(v, dict) else ""


def number_of(page: dict, name: str):
    return prop(page, name).get("number")


def url_of(page: dict, name: str) -> str:
    return prop(page, name).get("url") or ""


def date_start(page: dict, name: str) -> str:
    d = prop(page, name).get("date") or {}
    return d.get("start") or ""


def files_of(page: dict, name: str) -> list[dict]:
    """[{name, url}] 순서 그대로. Notion 업로드 파일 주소는 1시간 뒤 만료된다."""
    out = []
    for f in prop(page, name).get("files") or []:
        kind = f.get("type")
        url = (f.get(kind) or {}).get("url", "")
        out.append({"name": f.get("name", ""), "url": url})
    return out


# ---- 속성 쓰기 도우미 ----
def rt(s: str) -> list[dict]:
    s = s or ""
    return [{"type": "text", "text": {"content": s[i : i + 1900]}} for i in range(0, len(s), 1900)] or []
