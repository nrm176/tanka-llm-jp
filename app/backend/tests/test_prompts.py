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
}
SPRING_MAGNET_PHRASES = ["散る桜", "花の散るらむ", "自然の理への諦観"]


def _blob(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


# ── FEW_SHOT_BY_SEASON のスライス健全性 (並べ替え事故の検知) ──

def test_by_season_has_all_four():
    assert set(prompts.FEW_SHOT_BY_SEASON) == {"春", "夏", "秋", "冬"}


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
    for fp in SEASON_FINGERPRINT.values():
        assert fp in blob


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
    for season in (None, "雑", "新年", "梅雨"):
        got = prompts.select_few_shot(season, dynamic=True)
        assert got is prompts.TANKA_COMPOSE_FEW_SHOT, f"{season} は四季フォールバックすべき"


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
    # 引数を渡さなければ従来どおり四季全部 (既存の呼び出し・挙動を壊さない)
    msgs = prompts.build_compose_messages("お題", "構想")
    blob = _blob(msgs)
    for fp in SEASON_FINGERPRINT.values():
        assert fp in blob
