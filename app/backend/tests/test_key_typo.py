"""`emotion` キーの綴り誤りを受け入れる alias (#68) のテスト。

背景: failures 全 625 件 (valid JSON 412 件) の横断調査で、キー名の誤記は **emotion にのみ集中**。
`emoton` 33 件 / `emotio n` 1 件 = valid JSON 失敗の 8.3%。他の 4 キーは誤記ゼロ。
JSON としては valid なので #65 の末尾切れ救済では直らず、Pydantic だけが落ちていた。

方針は「受け入れるが握り潰さない」: alias で通しつつ meta["key_typo"] で通知し、
発生率を本番データから追えるようにする (握り潰すと誤記が永続化して気づけなくなる)。
"""

from __future__ import annotations

import json

import validator

LINES = ",".join(['{"body": "あ", "reading": "あ"}'] * 5)


def _payload(emotion_key: str) -> str:
    return (
        '{"kigo": "蛍", "season": "夏", "lines": [' + LINES + '],'
        ' "image": "川辺を蛍が飛ぶ", "' + emotion_key + '": "静かな余韻"}'
    )


# ─── 正規のキーは従来どおり (最重要: 既存パスを変えない) ───

def test_canonical_key_still_works_and_not_flagged():
    meta: dict = {}
    t = validator.parse_tanka_json(_payload("emotion"), meta=meta)
    assert not isinstance(t, tuple)
    assert t.emotion == "静かな余韻"
    assert "key_typo" not in meta  # 誤記ではないので記録されない


def test_model_dump_always_uses_canonical_name():
    # alias で受けても、下流 (parsed_tanka / DB / UI) には正規名で流れる
    for key in ("emotion", "emoton", "emotio n"):
        t = validator.parse_tanka_json(_payload(key))
        assert not isinstance(t, tuple), key
        assert sorted(t.model_dump().keys()) == ["emotion", "image", "kigo", "lines", "season"]


# ─── 誤記を受け入れ、かつ記録する ───

def test_emoton_is_accepted_and_recorded():
    meta: dict = {}
    t = validator.parse_tanka_json(_payload("emoton"), meta=meta)
    assert not isinstance(t, tuple), f"alias が効いていない: {t}"
    assert t.emotion == "静かな余韻"
    assert meta["key_typo"] == "emoton"


def test_emotion_with_space_is_accepted_and_recorded():
    meta: dict = {}
    t = validator.parse_tanka_json(_payload("emotio n"), meta=meta)
    assert not isinstance(t, tuple)
    assert t.emotion == "静かな余韻"
    assert meta["key_typo"] == "emotio n"


def test_canonical_wins_when_both_present():
    both = (
        '{"kigo": "蛍", "season": "夏", "lines": [' + LINES + '],'
        ' "image": "い", "emotion": "正しい方", "emoton": "誤記の方"}'
    )
    t = validator.parse_tanka_json(both)
    assert not isinstance(t, tuple)
    assert t.emotion == "正しい方"


def test_unknown_key_is_still_a_schema_error():
    # 未知の綴り (辞書に無いもの) まで受け入れてしまわないこと
    r = validator.parse_tanka_json(_payload("emosion"))
    assert isinstance(r, tuple)
    assert "emotion" in r[1]


def test_missing_emotion_entirely_is_still_an_error():
    missing = '{"kigo": "蛍", "season": "夏", "lines": [' + LINES + '], "image": "い"}'
    r = validator.parse_tanka_json(missing)
    assert isinstance(r, tuple) and "emotion" in r[1]


def test_typo_survives_truncation_repair():
    # #65 の末尾切れ救済と併用できる (末尾の } が無く、かつキーが誤記)
    truncated = _payload("emoton").rstrip()[:-1]
    meta: dict = {}
    t = validator.parse_tanka_json(truncated, meta=meta)
    assert not isinstance(t, tuple)
    assert t.emotion == "静かな余韻"
    assert meta["json_repaired"] is True and meta["key_typo"] == "emoton"


def test_alias_list_and_typo_set_stay_in_sync():
    # 片方だけ増やす事故を防ぐ (alias に足したら検出集合にも入る)
    assert validator.EMOTION_ALIASES[0] == "emotion"
    assert set(validator.EMOTION_ALIASES[1:]) == set(validator.EMOTION_TYPOS)


# ─── 案 3: プロンプト側でキー名を明示する ───

def test_prompt_states_exact_key_names():
    import prompts
    assert "一字一句そのまま" in prompts.TANKA_SYSTEM_PROMPT
    for key in ("kigo", "season", "lines", "image", "emotion"):
        assert key in prompts.TANKA_SYSTEM_PROMPT


def test_prompt_does_not_show_the_misspelling():
    # 誤記そのものをプロンプトに書かない: 提示した例はモデルに写される (§6.4 春アトラクター)。
    # 「やるな」と示すつもりが誤記を prime してしまう危険があるため、正しい綴りのみ提示する
    import prompts
    for typo in validator.EMOTION_TYPOS:
        assert typo not in prompts.TANKA_SYSTEM_PROMPT
        assert typo not in prompts.build_compose_messages(
            "夏", "季語: 蛍\n季節: 夏", plan_constraint="", season_hint="夏")[0]["content"]
