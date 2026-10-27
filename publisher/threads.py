"""Threads 공식 API 최소 클라이언트.

문서: https://developers.facebook.com/documentation/threads/ (2026-10-27 확인)
- 주소는 graph.threads.com (graph.threads.net도 되지만 문서 예시는 .com)
- 컨테이너 만들기 → status FINISHED 확인 → threads_publish
- 캐러셀 2~20장, 본문 500자(이모지는 UTF-8 바이트), 고유 링크 5개
- 답글: media_type=TEXT + reply_to_id
토큰은 로그에 찍지 않는다. 에러 메시지에서도 지운다.
"""
from __future__ import annotations

import os
import time

import requests

BASE = os.environ.get("THREADS_API_BASE", "https://graph.threads.com/v1.0")
TIMEOUT = 30


class ThreadsError(RuntimeError):
    pass


class Threads:
    def __init__(self, token: str, user_id: str | None = None):
        if not token:
            raise ThreadsError("THREADS_TOKEN이 없다 (GitHub Secrets 확인)")
        self._token = token
        self.user_id = user_id or "me"

    # ---- 기본 요청 ----
    def _clean(self, msg: str) -> str:
        return msg.replace(self._token, "***") if self._token else msg

    def _req(self, method: str, path: str, params: dict | None = None, retries: int = 3) -> dict:
        url = f"{BASE}/{path.lstrip('/')}"
        data = dict(params or {})
        data["access_token"] = self._token
        last = ""
        for attempt in range(retries):
            try:
                if method == "GET":
                    r = requests.get(url, params=data, timeout=TIMEOUT)
                else:
                    r = requests.post(url, data=data, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = self._clean(f"연결 실패: {e}")
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = self._clean(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(10 * (attempt + 1))
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"raw": r.text[:300]}
            if r.status_code >= 400 or "error" in body:
                err = body.get("error", body)
                raise ThreadsError(self._clean(f"{method} {path} 실패 (HTTP {r.status_code}): {err}"))
            return body
        raise ThreadsError(f"{method} {path} 재시도 끝에 실패: {last}")

    # ---- 계정 ----
    def me(self) -> dict:
        return self._req("GET", "me", {"fields": "id,username"})

    def followers_count(self) -> int | None:
        try:
            body = self._req("GET", f"{self.user_id}/threads_insights", {"metric": "followers_count"})
        except ThreadsError:
            return None
        for item in body.get("data", []):
            if item.get("name") == "followers_count":
                tv = item.get("total_value") or {}
                if "value" in tv:
                    return int(tv["value"])
                vals = item.get("values") or []
                if vals:
                    return int(vals[-1].get("value", 0))
        return None

    def publishing_limit(self) -> dict:
        return self._req(
            "GET",
            f"{self.user_id}/threads_publishing_limit",
            {"fields": "quota_usage,config,reply_quota_usage,reply_config"},
        )

    def recent_posts(self, limit: int = 10) -> list[dict]:
        body = self._req(
            "GET",
            f"{self.user_id}/threads",
            {"fields": "id,text,timestamp,permalink,media_type", "limit": limit},
        )
        return body.get("data", [])

    # ---- 컨테이너 ----
    def create_image_item(self, image_url: str, carousel_item: bool, alt_text: str | None = None) -> str:
        p = {"media_type": "IMAGE", "image_url": image_url}
        if carousel_item:
            p["is_carousel_item"] = "true"
        if alt_text:
            p["alt_text"] = alt_text[:1000]
        return self._req("POST", f"{self.user_id}/threads", p)["id"]

    def create_post_container(self, text: str, image_urls: list[str], alt_texts: list[str] | None = None) -> str:
        alt_texts = alt_texts or [None] * len(image_urls)
        if not image_urls:
            return self._req("POST", f"{self.user_id}/threads", {"media_type": "TEXT", "text": text})["id"]
        if len(image_urls) == 1:
            p = {"media_type": "IMAGE", "image_url": image_urls[0], "text": text}
            if alt_texts[0]:
                p["alt_text"] = alt_texts[0][:1000]
            return self._req("POST", f"{self.user_id}/threads", p)["id"]
        children = []
        for url, alt in zip(image_urls, alt_texts):
            cid = self.create_image_item(url, carousel_item=True, alt_text=alt)
            children.append(cid)
        for cid in children:
            self.wait_ready(cid)
        return self._req(
            "POST",
            f"{self.user_id}/threads",
            {"media_type": "CAROUSEL", "children": ",".join(children), "text": text},
        )["id"]

    def create_reply_container(self, text: str, reply_to_id: str) -> str:
        return self._req(
            "POST",
            f"{self.user_id}/threads",
            {"media_type": "TEXT", "text": text, "reply_to_id": reply_to_id},
        )["id"]

    def status(self, container_id: str) -> dict:
        return self._req("GET", container_id, {"fields": "status,error_message"})

    def wait_ready(self, container_id: str, max_wait: int = 300) -> None:
        """문서 권장: 평균 30초. 최대 5분 동안 확인한다."""
        waited = 0
        delay = 5
        while waited <= max_wait:
            st = self.status(container_id)
            s = st.get("status")
            if s == "FINISHED":
                return
            if s in ("ERROR", "EXPIRED"):
                raise ThreadsError(f"컨테이너 {container_id} 상태 {s}: {st.get('error_message')}")
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 30)
        raise ThreadsError(f"컨테이너 {container_id}가 {max_wait}초 안에 준비되지 않았다")

    def publish(self, container_id: str) -> str:
        return self._req("POST", f"{self.user_id}/threads_publish", {"creation_id": container_id}, retries=1)["id"]

    def permalink(self, media_id: str) -> str:
        body = self._req("GET", media_id, {"fields": "permalink,shortcode"})
        return body.get("permalink", "")

    # ---- 여러 쪽 읽기 ----
    def _paged(self, path: str, params: dict, max_pages: int = 10) -> list[dict]:
        out, after = [], None
        for _ in range(max_pages):
            p = dict(params)
            if after:
                p["after"] = after
            body = self._req("GET", path, p)
            out += body.get("data", [])
            after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (body.get("paging") or {}).get("next"):
                break
        return out

    def my_posts(self, max_pages: int = 4) -> list[dict]:
        return self._paged(f"{self.user_id}/threads", {"fields": "id,permalink,timestamp,text", "limit": 50}, max_pages)

    # ---- 답글·지표 ----
    def replies(self, media_id: str) -> list[dict]:
        return self._paged(
            f"{media_id}/conversation",
            {"fields": "id,text,username,timestamp,is_reply_owned_by_me", "reverse": "false", "limit": 100},
        )

    def media_insights(self, media_id: str) -> dict:
        """views·shares는 문서에 '개발 중'으로 적혀 있어, 한꺼번에 실패하면 하나씩 다시 묻는다."""
        metrics = ["views", "likes", "replies", "reposts", "quotes", "shares"]

        def parse(body: dict) -> dict:
            out = {}
            for item in body.get("data", []):
                vals = item.get("values") or []
                tv = item.get("total_value") or {}
                if vals:
                    out[item["name"]] = vals[0].get("value")
                elif "value" in tv:
                    out[item["name"]] = tv["value"]
            return out

        try:
            return parse(self._req("GET", f"{media_id}/insights", {"metric": ",".join(metrics)}))
        except ThreadsError:
            out = {}
            for m in metrics:
                try:
                    out.update(parse(self._req("GET", f"{media_id}/insights", {"metric": m}, retries=1)))
                except ThreadsError:
                    pass
            return out
