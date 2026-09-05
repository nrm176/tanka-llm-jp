"""末尾切れ JSON の救済 (#65) のテスト。

背景 (FINDINGS §5.6): 50 題 × 2 arm の実測で全生成の 12% が score 0 になり、その全てが
schema_invalid。その 83% は「モデルが最後の `}` を出さない」末尾切れで、閉じ括弧を補うだけで
0 点生成の 75% (9/12) が回復する。ここでは (a) 救済ロジックのパターン網羅、(b) **現在成功している
パスが一切変わらない**こと、(c) 実データ 1 件の回帰、を固定する。
"""

from __future__ import annotations

import json

import validator

# ── 実データ (2026-09-05 の paired A/B、sc-on「散りゆく桜」の compose 出力) ──
# 末尾の `}` が無い。これが 12% の score 0 の正体。
REAL_TRUNCATED = (
    '{\n  "kigo": "桜",\n  "season": "春",\n  "lines": [\n'
    '    {\n      "body": "桜散り",\n      "reading": "さくらちり"\n    },\n'
    '    {\n      "body": "風にまかせて",\n      "reading": "かぜにまかせて"\n    },\n'
    '    {\n      "body": "水面ゆれ",\n      "reading": "みなもゆれ"\n    },\n'
    '    {\n      "body": "遠風の音",\n      "reading": "とおかぜのおと"\n    },\n'
    '    {\n      "body": "新芽待ちて",\n      "reading": "しんめまちて"\n    }\n  ],\n'
    '  "image": "薄紅の桜が風に揺れ、川面へと静かに散る光景。",\n'
    '  "emotion": "別れの寂しさと新たな芽吹きへの期待が胸に交錯する。"'
)

VALID_JSON = (
    '{"kigo": "蛍", "season": "夏", "lines": ['
    '{"body": "夏の夜に", "reading": "なつのよるに"},'
    '{"body": "蛍ひとつが", "reading": "ほたるひとつが"},'
    '{"body": "川辺行く", "reading": "かわべゆく"},'
    '{"body": "光の跡を", "reading": "ひかりのあとを"},'
    '{"body": "静かに残す", "reading": "しずかにのこす"}],'
    '"image": "川辺を蛍が飛ぶ", "emotion": "静かな余韻"}'
)


# ─── 現在成功しているパスは変えない (最重要) ───

def test_valid_json_unchanged_and_not_flagged():
    meta: dict = {}
    t = validator.parse_tanka_json(VALID_JSON, meta=meta)
    assert not isinstance(t, tuple)
    assert t.kigo == "蛍" and len(t.lines) == 5
    assert meta == {}  # 救済は発動していない


def test_meta_is_optional():
    # 既存の呼び出し (meta なし) がそのまま動く
    assert not isinstance(validator.parse_tanka_json(VALID_JSON), tuple)


def test_prose_and_fence_still_work():
    fenced = "```json\n" + VALID_JSON + "\n```"
    assert not isinstance(validator.parse_tanka_json(fenced), tuple)
    prose = "以下が結果です。\n" + VALID_JSON + "\nよろしくお願いします。"
    assert not isinstance(validator.parse_tanka_json(prose), tuple)


def test_non_json_still_reports_not_found():
    r = validator.parse_tanka_json("これは JSON ではありません")
    assert isinstance(r, tuple) and "見つかりません" in r[1]


# ─── 実データ回帰 ───

def test_real_truncated_output_is_recovered():
    meta: dict = {}
    t = validator.parse_tanka_json(REAL_TRUNCATED, meta=meta)
    assert not isinstance(t, tuple), f"救済に失敗: {t}"
    assert t.kigo == "桜" and t.season == "春"
    assert [l.body for l in t.lines][0] == "桜散り"
    assert t.emotion.startswith("別れの寂しさ")
    assert meta["json_repaired"] is True


def test_real_truncated_output_fails_without_repair():
    # 救済前の挙動を固定: rfind("}") が lines 内の要素を拾い「Expecting ',' delimiter」になる
    start = REAL_TRUNCATED.find("{"); end = REAL_TRUNCATED.rfind("}")
    try:
        json.loads(REAL_TRUNCATED[start:end + 1])
        raise AssertionError("旧方式で parse できてしまった (前提が崩れている)")
    except json.JSONDecodeError as e:
        assert "delimiter" in e.msg


# ─── repair_truncated_json のパターン網羅 ───

def test_repairs_missing_object_brace():
    assert validator.repair_truncated_json('{"a": 1, "b": 2') == {"a": 1, "b": 2}


def test_repairs_missing_array_and_object_close():
    assert validator.repair_truncated_json('{"a": [1, 2') == {"a": [1, 2]}


def test_repairs_nested_in_correct_order():
    # 逆順に閉じる必要がある ("}]}"): 単純な "]"*n + "}"*n では壊れる
    assert validator.repair_truncated_json('{"a": [{"b": 1') == {"a": [{"b": 1}]}


def test_repairs_truncation_inside_string():
    assert validator.repair_truncated_json('{"a": "とちゅうで') == {"a": "とちゅうで"}


def test_repairs_trailing_comma():
    assert validator.repair_truncated_json('{"a": 1,') == {"a": 1}


def test_brace_inside_string_is_not_counted():
    assert validator.repair_truncated_json('{"a": "}}}"') == {"a": "}}}"}


def test_escaped_quote_does_not_confuse_scanner():
    assert validator.repair_truncated_json('{"a": "1\\"2"') == {"a": '1"2'}


def test_returns_none_when_already_closed():
    # 閉じ切れている = 末尾切れではない → 救済対象外 (別の理由で壊れている)
    assert validator.repair_truncated_json('{"a": 1}') is None
    assert validator.repair_truncated_json('{"a": bad}') is None


def test_returns_none_on_crossed_brackets():
    assert validator.repair_truncated_json('{"a": [1}') is None


def test_returns_none_on_dangling_key():
    # 値を書き始める前に切れた場合は復元不能
    assert validator.repair_truncated_json('{"a": 1, "b":') is None


def test_returns_none_for_non_object():
    assert validator.repair_truncated_json('[1, 2') is None
