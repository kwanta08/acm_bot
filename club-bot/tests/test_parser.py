"""日時パースの単体テスト（仕様 20.1）。

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.parser import parse_datetime, InvalidDatetimeError  # noqa: E402


def test_valid_formats():
    assert parse_datetime("2026-07-03 19:00").hour == 19
    assert parse_datetime("2026/07/03 19:00").month == 7
    assert parse_datetime("2026-07-03T19:00").day == 3


def test_invalid_raises():
    for bad in ["", "abc", "2026/13/40", "明日"]:
        try:
            parse_datetime(bad)
            assert False, f"{bad} は例外になるべき"
        except InvalidDatetimeError:
            pass


if __name__ == "__main__":
    test_valid_formats()
    test_invalid_raises()
    print("test_parser: OK")
