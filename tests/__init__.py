"""시험 중에는 GitHub 주석(::notice::)을 찍지 않는다. verify 실행의 주석 한도(단계당 10개)를 아끼려는 것."""
from publisher import cli as _cli

_cli._note = lambda ok, msg: None
