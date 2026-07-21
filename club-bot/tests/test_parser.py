"""日時パースの単体テスト（仕様 20.1）。

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.parser import parse_datetime, parse_deadline, InvalidDatetimeError  # noqa: E402


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


def test_parse_deadline_valid():
    assert parse_deadline("2026-07-02 23:59").minute == 59
    # 日付のみは 23:59 補完
    d = parse_deadline("2026-07-02")
    assert d.hour == 23 and d.minute == 59


def test_parse_deadline_invalid_raises_invalid_datetime_error():
    # 過去に TypeError になっていたバグの回帰テスト:
    # 不正入力では InvalidDatetimeError が上がること
    for bad in ["", "abc", "2026/07/02", "07-02 23:59"]:
        try:
            parse_deadline(bad)
            assert False, f"{bad} は例外になるべき"
        except InvalidDatetimeError:
            pass


if __name__ == "__main__":
    test_valid_formats()
    test_invalid_raises()
    test_parse_deadline_valid()
    test_parse_deadline_invalid_raises_invalid_datetime_error()
    print("test_parser: OK")
