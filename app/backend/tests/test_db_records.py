"""db.tanka_record_from_message / iter_tanka_records (短歌一覧用の変換) のテスト。

純関数のみを対象とするので MongoDB 接続は不要 (db.py の import は接続を張らない)。
"""

from __future__ import annotations

from datetime import datetime, timezone

import db


def _tanka_msg(**over) -> dict:
    base = {
        "kind": "tanka",
        "theme": "夏の夕暮れ",
        "tanka": "夕立の\nあとの静けさ\n蝉しぐれ\nとぎれとぎれに\n夏は暮れゆく",
        "kigo": "蝉",
        "season": "夏",
        "moras": [5, 7, 5, 7, 7],
        "image": "夕立あがりの街",
        "emotion": "静けさへの安堵",
        "final_score": 85,
        "validations": [{"resolved": False}, {"resolved": True}],
        "plateau_reached": False,
        "max_refines_reached": False,
        "created_at": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(over)
    return base


def test_converts_tanka_message_to_flat_record():
    rec = db.tanka_record_from_message("sid1", "雑談セッション", _tanka_msg(), 3)
    assert rec is not None
    assert rec["session_id"] == "sid1"
    assert rec["session_title"] == "雑談セッション"
    assert rec["message_index"] == 3
    assert rec["theme"] == "夏の夕暮れ"
    assert rec["tanka"].startswith("夕立の")
    assert rec["kigo"] == "蝉"
    assert rec["season"] == "夏"
    assert rec["score"] == 85
    assert rec["attempts"] == 2
    # datetime は isoformat 文字列へ (一覧のソートキー兼 JSON 化)
    assert rec["created_at"] == "2026-06-01T12:00:00+00:00"


def test_non_tanka_message_returns_none():
    assert db.tanka_record_from_message("s", "t", {"kind": "user", "content": "hi"}, 0) is None
    assert db.tanka_record_from_message("s", "t", {"kind": "assistant", "content": "x"}, 0) is None


def test_tanka_without_text_returns_none():
    # complete に到達しなかった (本文なし) レコードは一覧に出さない
    assert db.tanka_record_from_message("s", "t", _tanka_msg(tanka=None), 0) is None
    assert db.tanka_record_from_message("s", "t", _tanka_msg(tanka=""), 0) is None


def test_missing_optional_fields_do_not_crash():
    rec = db.tanka_record_from_message("s", "t", {"kind": "tanka", "tanka": "あ"}, 0)
    assert rec is not None
    assert rec["score"] is None
    assert rec["attempts"] == 0
    assert rec["moras"] == []
    assert rec["created_at"] is None


def test_naive_created_at_is_treated_as_utc():
    # Mongo は naive UTC datetime を返す。UTC を明示して isoformat しないと
    # フロントの new Date() がローカル時刻として解釈してしまう
    rec = db.tanka_record_from_message(
        "s", "t", _tanka_msg(created_at=datetime(2026, 6, 10, 5, 48, 25)), 0
    )
    assert rec["created_at"] == "2026-06-10T05:48:25+00:00"


def test_already_serialized_created_at_passes_through():
    # _serialize 済み (isoformat 文字列) のメッセージが来ても二重変換しない
    rec = db.tanka_record_from_message(
        "s", "t", _tanka_msg(created_at="2026-05-01T00:00:00+00:00"), 0
    )
    assert rec["created_at"] == "2026-05-01T00:00:00+00:00"


def test_non_int_score_is_normalized_to_none():
    rec = db.tanka_record_from_message("s", "t", _tanka_msg(final_score="85"), 0)
    assert rec["score"] is None


def test_iter_tanka_records_uses_array_index_not_tanka_ordinal():
    # message_index は「何首目か」ではなく messages 配列の添字。
    # フロントが ':scope > .msg' の DOM 位置と 1:1 で突き合わせるので、
    # user メッセージや本文なし tanka を挟んでもずれてはいけない
    msgs = [
        {"kind": "user", "content": "tanka:夏の夕暮れ"},
        _tanka_msg(),
        {"kind": "user", "content": "tanka:冬の朝"},
        _tanka_msg(theme="冬の朝", tanka=None),  # 本文なし → skip されるが添字は消費
        _tanka_msg(theme="冬の朝 (再)"),
    ]
    recs = list(db.iter_tanka_records("s", "セッション", msgs))
    assert [r["message_index"] for r in recs] == [1, 4]
    assert recs[0]["theme"] == "夏の夕暮れ"
    assert recs[1]["theme"] == "冬の朝 (再)"


def test_iter_tanka_records_empty_messages():
    assert list(db.iter_tanka_records("s", "t", [])) == []
