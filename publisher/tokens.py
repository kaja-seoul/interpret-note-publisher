"""Threads 토큰 발급·갱신. 토큰 값은 절대 출력하지 않는다(로그 가림 처리 후 Secrets에만 저장).

  python -m publisher.tokens exchange   # THREADS_AUTH_CODE → 단기 → 장기(60일) → THREADS_TOKEN
  python -m publisher.tokens refresh    # 장기 토큰 갱신(24시간 지난 토큰만) → THREADS_TOKEN

필요한 Secrets: THREADS_APP_ID, THREADS_APP_SECRET (exchange만), THREADS_AUTH_CODE (exchange만),
                THREADS_TOKEN (refresh), GH_PAT (Secrets 쓰기 권한이 있는 토큰)
문서: https://developers.facebook.com/documentation/threads/get-started/ (2026-10-27 확인)
"""
from __future__ import annotations

import os
import subprocess
import sys

import requests

HOST = "https://graph.threads.com"


def mask(v: str) -> None:
    if v:
        print(f"::add-mask::{v}", flush=True)


def fail(msg: str) -> int:
    print(f"::error::{msg}", flush=True)
    return 1


def set_secret(name: str, value: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    env = dict(os.environ, GH_TOKEN=os.environ.get("GH_PAT", ""))
    if not env["GH_TOKEN"]:
        raise RuntimeError("GH_PAT가 없다. Secrets에 넣어야 토큰을 저장할 수 있다.")
    subprocess.run(["gh", "secret", "set", name, "--repo", repo], input=value.encode(), env=env, check=True)


def delete_secret(name: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    env = dict(os.environ, GH_TOKEN=os.environ.get("GH_PAT", ""))
    subprocess.run(["gh", "secret", "delete", name, "--repo", repo], env=env, check=False)


def username(token: str) -> str:
    r = requests.get(f"{HOST}/v1.0/me", params={"fields": "username", "access_token": token}, timeout=30)
    return r.json().get("username", "?") if r.ok else "?"


def err_text(r: requests.Response, *secrets: str) -> str:
    t = r.text[:400]
    for s in secrets:
        if s:
            t = t.replace(s, "***")
    return f"HTTP {r.status_code}: {t}"


def exchange() -> int:
    app_id = os.environ.get("THREADS_APP_ID", "").strip()
    secret = os.environ.get("THREADS_APP_SECRET", "").strip()
    code = os.environ.get("THREADS_AUTH_CODE", "").strip()
    redirect = os.environ.get("REDIRECT_URI", "").strip()
    mask(secret)
    mask(code)
    if not (app_id and secret and code and redirect):
        return fail("THREADS_APP_ID, THREADS_APP_SECRET, THREADS_AUTH_CODE 중 빠진 것이 있다.")
    code = code.split("#")[0]

    r = requests.post(
        f"{HOST}/oauth/access_token",
        data={"client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
              "redirect_uri": redirect, "code": code},
        timeout=30,
    )
    if not r.ok:
        return fail("코드 교환 실패 (코드는 1시간·한 번만 쓸 수 있다. 다시 받아 넣기): " + err_text(r, secret, code))
    short = r.json().get("access_token", "")
    mask(short)

    r = requests.get(
        f"{HOST}/access_token",
        params={"grant_type": "th_exchange_token", "client_secret": secret, "access_token": short},
        timeout=30,
    )
    if not r.ok:
        return fail("장기 토큰 교환 실패: " + err_text(r, secret, short))
    body = r.json()
    long = body.get("access_token", "")
    mask(long)
    days = int(body.get("expires_in", 0)) // 86400

    set_secret("THREADS_TOKEN", long)
    delete_secret("THREADS_AUTH_CODE")
    print(f"장기 토큰을 Secrets THREADS_TOKEN에 저장했다. 계정 @{username(long)}, 약 {days}일 유효. 쓴 코드는 지웠다.")
    return 0


def refresh() -> int:
    token = os.environ.get("THREADS_TOKEN", "").strip()
    mask(token)
    if not token:
        return fail("THREADS_TOKEN이 없다.")
    r = requests.get(
        f"{HOST}/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=30,
    )
    if not r.ok:
        return fail("토큰 갱신 실패 (60일이 지나면 되살릴 수 없어 새로 발급해야 한다): " + err_text(r, token))
    body = r.json()
    new = body.get("access_token", "")
    mask(new)
    days = int(body.get("expires_in", 0)) // 86400
    if not new:
        return fail("갱신 응답에 토큰이 없다.")
    set_secret("THREADS_TOKEN", new)
    print(f"토큰 갱신 완료. 계정 @{username(new)}, 약 {days}일 유효.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"exchange": exchange, "refresh": refresh}.get(cmd, lambda: fail("exchange 또는 refresh"))())
