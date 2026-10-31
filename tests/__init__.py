"""시험 중에는 GitHub 주석(::notice::)을 찍지 않는다. verify 실행의 주석 한도(단계당 10개)를 아끼려는 것.
시험용 계정 이름은 가짜 값이다. publisher를 불러오기 전에 넣어야 config가 읽는다."""
import os

os.environ.setdefault("THREADS_USERNAME", "example.account")

from publisher import cli as _cli  # noqa: E402

_cli._note = lambda ok, msg: None
