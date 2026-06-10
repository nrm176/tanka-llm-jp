"""prompts.py の動的 few-shot 選択のテスト (純関数、LLM/DB 不要)。

横断分析で「四季を全部見せても非春 plan の 37% が春 (#1 散る桜) へ regress」と判明したため、
動的 few-shot は「お題の季の例だけ」を見せて春の磁石を隠す。ここではその選択ロジックと、
TANKA_COMPOSE_FEW_SHOT の並び順 (春→夏→秋→冬) に依存する FEW_SHOT_BY_SEASON のスライスが
壊れていないことを保証する (並べ替え事故の安全網)。"""

from __future__ import annotations

import prompts


# 各季の few-shot を一意に識別できる指紋 (本文に必ず含まれる語)
SEASON_FINGERPRINT = {
    "春": "散る桜",
    "夏": "夏の夜",
    "秋": "秋風",
    "冬": "山里は",
    "新年": "玉箒",
}
# フォールバック (TANKA_COMPOSE_FEW_SHOT) に入っているのは四季のみ。
# 新年は専用定数 (issue #11) で、フォールバックには意図的に含めない。
FALLBACK_SEASONS = ("春", "夏", "秋", "冬")
SPRING_MAGNET_PHRASES = ["散る桜", "花の散るらむ", "自然の理への諦観"]


def _blob(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


# ── FEW_SHOT_BY_SEASON のスライス健全性 (並べ替え事故の検知) ──

def test_by_season_has_all_five():
    assert set(prompts.FEW_SHOT_BY_SEASON) == {"春", "夏", "秋", "冬", "新年"}


def test_by_season_each_is_user_assistant_pair():
    for season, pair in prompts.FEW_SHOT_BY_SEASON.items():
        assert len(pair) == 2, f"{season} は user/assistant の 2 メッセージであるべき"
        assert pair[0]["role"] == "user"
        assert pair[1]["role"] == "assistant"


def test_by_season_slices_point_to_correct_season():
    # 並び順が変わるとスライスがずれる → 指紋で検出
    for season, fp in SEASON_FINGERPRINT.items():
        blob = _blob(prompts.FEW_SHOT_BY_SEASON[season])
        assert fp in blob, f"{season} の few-shot に指紋 '{fp}' が無い (並び順崩れ?)"


def test_fallback_list_covers_four_seasons():
    blob = _blob(prompts.TANKA_COMPOSE_FEW_SHOT)
    for season in FALLBACK_SEASONS:
        assert SEASON_FINGERPRINT[season] in blob


def test_fallback_list_excludes_new_year():
    # 新年ペアをフォールバックに混ぜない: dynamic=OFF の A/B 統制群と
    # 季不明/雑フォールバックを従来の「四季全部」のまま保つ (issue #11)
    assert SEASON_FINGERPRINT["新年"] not in _blob(prompts.TANKA_COMPOSE_FEW_SHOT)


# ── select_few_shot の挙動 ──

def test_dynamic_returns_only_matching_season():
    pair = prompts.select_few_shot("夏", dynamic=True)
    assert pair is prompts.FEW_SHOT_BY_SEASON["夏"]
    blob = _blob(pair)
    assert "夏の夜" in blob
    # 春の磁石が一切見えないこと (これが今回の対策の核心)
    for phrase in SPRING_MAGNET_PHRASES:
        assert phrase not in blob, f"非春 plan なのに春磁石 '{phrase}' が露出している"


def test_dynamic_off_returns_all_four():
    got = prompts.select_few_shot("夏", dynamic=False)
    assert got is prompts.TANKA_COMPOSE_FEW_SHOT
    assert "散る桜" in _blob(got)  # 従来挙動: 四季全部見える


def test_unknown_season_falls_back_to_all_four():
    # 新年は #11 で専用ペアを得たのでフォールバック対象から外れた。
    # 雑は validator が kigo 必須のため意図的に未対応 (issue #11 スコープ外)
    for season in (None, "雑", "梅雨"):
        got = prompts.select_few_shot(season, dynamic=True)
        assert got is prompts.TANKA_COMPOSE_FEW_SHOT, f"{season} は四季フォールバックすべき"


def test_dynamic_new_year_returns_dedicated_pair():
    pair = prompts.select_few_shot("新年", dynamic=True)
    assert pair is prompts.TANKA_COMPOSE_FEW_SHOT_NEW_YEAR
    blob = _blob(pair)
    assert "玉箒" in blob
    # 新年 plan で春磁石が見えないこと (これが #11 の核心)
    for phrase in SPRING_MAGNET_PHRASES:
        assert phrase not in blob, f"新年 plan なのに春磁石 '{phrase}' が露出している"


def test_new_year_exemplar_is_validator_clean():
    """新年例の assistant JSON が validator に罰されないことの安全網。
    few-shot は「正解の形」を教えるので、critical/major を踏む例を見せてはいけない。
    (mora_count_disputed minor は pykakasi の古典読み限界 (CLAUDE.md §6.3) なので許容)"""
    import json

    import validator

    data = json.loads(prompts.TANKA_COMPOSE_FEW_SHOT_NEW_YEAR[1]["content"])
    t = validator.Tanka(**data)
    result = validator.evaluate(t, expected_season="新年", expected_kigo="初春", theme="新年の祝い")
    bad = [(v.rule, v.severity) for v in result.violations if v.severity in ("critical", "major")]
    assert not bad, f"新年 few-shot 例が validator に罰される: {bad}"


def test_spring_plan_still_shows_spring():
    # 春 plan のときは当然 #1 を見せてよい (regression ではなく正答)
    pair = prompts.select_few_shot("春", dynamic=True)
    assert "散る桜" in _blob(pair)


# ── build_compose_messages との結線 ──

def test_compose_messages_dynamic_hides_spring_for_summer():
    msgs = prompts.build_compose_messages(
        "夏の夕暮れ", "季語: 蛍\n季節: 夏",
        season_hint="夏", dynamic_fewshot=True,
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    blob = _blob(msgs)
    assert "夏の夜" in blob
    for phrase in SPRING_MAGNET_PHRASES:
        assert phrase not in blob


def test_compose_messages_default_is_backward_compatible():
    # 引数を渡さなければ従来どおり四季全部 (既存の呼び出し・挙動を壊さない)。
    # 新年ペアはフォールバック外なので、ここに玉箒が現れないことも従来挙動の一部
    msgs = prompts.build_compose_messages("お題", "構想")
    blob = _blob(msgs)
    for season in FALLBACK_SEASONS:
        assert SEASON_FINGERPRINT[season] in blob
    assert SEASON_FINGERPRINT["新年"] not in blob
