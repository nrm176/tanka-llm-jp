"""validator.py の単体テスト (Phase 2 A1)。

validator は副作用ゼロの純関数集合なので、DB/Redis/LLM なしで完全にテストできる。
全 13 ルール + Plan 整合 + JSON パース + スコアリングを網羅する。

実行:
    cd app/backend && uv run pytest -v
"""

from __future__ import annotations

import pytest

import validator
from validator import Tanka, TankaLine


# ─── ヘルパ: テスト用 Tanka を組み立てる ───

def make_tanka(
    *,
    kigo: str = "花",
    season: str = "春",
    lines: list[tuple[str, str]] | None = None,
    image: str = "情景",
    emotion: str = "心情",
) -> Tanka:
    """(body, reading) のタプル列から Tanka を作る。デフォルトは古今集の正例。"""
    if lines is None:
        lines = [
            ("ひさかたの", "ひさかたの"),
            ("光のどけき", "ひかりのどけき"),
            ("春の日に", "はるのひに"),
            ("しづ心なく", "しづこころなく"),
            ("花の散るらむ", "はなのちるらむ"),
        ]
    return Tanka(
        kigo=kigo,
        season=season,
        lines=[TankaLine(body=b, reading=r) for b, r in lines],
        image=image,
        emotion=emotion,
    )


def rules_fired(result) -> set[str]:
    return {v.rule for v in result.violations}


# ─── 季語辞書のロード ───

def test_kigo_dict_loaded():
    assert len(validator.KIGO_DICT) > 200
    assert validator.KIGO_DICT.get("桜") == "春"
    assert validator.KIGO_DICT.get("五月雨") == "夏"
    assert validator.KIGO_DICT.get("紅葉") == "秋"
    assert validator.KIGO_DICT.get("雪") == "冬"


def test_find_kigo_longest_match():
    # 「秋風」を「秋」+「風」に分割せず 1 季語として拾う
    found = validator.find_kigo_in_text("秋風そよぐ")
    kigos = [k for k, _ in found]
    assert "秋風" in kigos


# ─── JSON パース ───

CLEAN_JSON = (
    '{"kigo":"花","season":"春","lines":['
    '{"body":"あ","reading":"あ"},{"body":"い","reading":"い"},'
    '{"body":"う","reading":"う"},{"body":"え","reading":"え"},'
    '{"body":"お","reading":"お"}],"image":"x","emotion":"y"}'
)


def test_parse_clean_json():
    result = validator.parse_tanka_json(CLEAN_JSON)
    assert isinstance(result, Tanka)
    assert result.kigo == "花"


def test_parse_fenced_json():
    fenced = "```json\n" + CLEAN_JSON + "\n```"
    result = validator.parse_tanka_json(fenced)
    assert isinstance(result, Tanka)


def test_parse_json_with_prose():
    prose = "はい、できました:\n\n" + CLEAN_JSON + "\n\nどうぞ。"
    result = validator.parse_tanka_json(prose)
    assert isinstance(result, Tanka)


def test_parse_garbage_returns_error():
    result = validator.parse_tanka_json("これは JSON ではありません")
    assert isinstance(result, tuple)
    assert result[0] is None
    assert isinstance(result[1], str)


def test_parse_wrong_line_count_rejected():
    bad = (
        '{"kigo":"花","season":"春","lines":['
        '{"body":"あ","reading":"あ"},{"body":"い","reading":"い"}],'
        '"image":"x","emotion":"y"}'
    )  # 5 句でなく 2 句
    result = validator.parse_tanka_json(bad)
    assert isinstance(result, tuple)
    assert result[0] is None


def test_parse_invalid_season_rejected():
    bad = CLEAN_JSON.replace('"season":"春"', '"season":"梅雨"')
    result = validator.parse_tanka_json(bad)
    assert isinstance(result, tuple)


# ─── 正例: 違反なし or minor のみで合格 ───

def test_valid_tanka_passes():
    result = validator.evaluate(make_tanka())
    assert result.passed
    assert result.score >= validator.PASS_THRESHOLD


# ─── kigo_present ───

def test_kigo_present_violation():
    # kigo="桜" だが本文に桜が出ない
    t = make_tanka(kigo="桜", season="春", lines=[
        ("春の野に", "はるののに"),
        ("光あふれて", "ひかりあふれて"),
        ("風そよぐ", "かぜそよぐ"),
        ("鳥も歌ひて", "とりもうたいて"),
        ("心はづむ", "こころはづむ"),
    ])
    result = validator.evaluate(t)
    assert "kigo_present" in rules_fired(result)


# ─── kigo_unique ───

def test_kigo_unique_violation():
    # 「花」が 2 句に出る
    t = make_tanka(kigo="花", season="春", lines=[
        ("花の色は", "はなのいろは"),
        ("うつりにけりな", "うつりにけりな"),
        ("いたづらに", "いたづらに"),
        ("わが身世にふる", "わがみよにふる"),
        ("花散る朝に", "はなちるあさに"),
    ])
    result = validator.evaluate(t)
    assert "kigo_unique" in rules_fired(result)


def test_kigo_unique_ok_when_single():
    result = validator.evaluate(make_tanka())  # 花は結句のみ
    assert "kigo_unique" not in rules_fired(result)


# ─── season_consistent ───

def test_season_consistent_violation():
    # 蛍 (夏) を秋と宣言
    t = make_tanka(kigo="蛍", season="秋", lines=[
        ("蛍とぶ", "ほたるとぶ"),
        ("水辺の宵に", "みづべのよひに"),
        ("ひとりゐて", "ひとりゐて"),
        ("光を追へば", "ひかりをおへば"),
        ("夏は逝くなり", "なつはゆくなり"),
    ])
    result = validator.evaluate(t)
    assert "season_consistent" in rules_fired(result)


# ─── no_other_kigo (季違い) ───

def test_no_other_kigo_cross_season():
    # 宣言は夏 (蛍) だが、本文に紅葉 (秋) が混入
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("蛍の火", "ほたるのひ"),
        ("紅葉の影に", "もみぢのかげに"),
        ("ゆれてゐる", "ゆれてゐる"),
        ("夜風はこぶ", "よかぜはこぶ"),
        ("遠き面影", "とほきおもかげ"),
    ])
    result = validator.evaluate(t)
    assert "no_other_kigo_cross" in rules_fired(result)


# ─── mora_count: 3 段階 (Phase 1 B5b) ───

def test_mora_count_exact_ok():
    result = validator.evaluate(make_tanka())
    assert "mora_count" not in rules_fired(result)


def test_mora_count_off_by_one_is_minor():
    # 2句目を 8 拍 (期待 7、+1) にする → off_by_one minor
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("夏の夜は", "なつのよは"),         # 5
        ("螢の光が", "ほたるのひかりが"),     # 8 (+1)
        ("舞ふ庭に", "まうにわに"),          # 5
        ("ひとり佇み", "ひとりたたずみ"),     # 7
        ("夜風感ず", "よかぜかんず"),        # 6 (-1)
    ])
    result = validator.evaluate(t)
    fired = rules_fired(result)
    assert "mora_count_off_by_one" in fired
    # critical な mora_count は出ない (±1 のみだから)
    assert "mora_count" not in fired


def test_mora_count_off_by_two_is_critical():
    # 3 拍以上ずれる句を含める
    t = make_tanka(kigo="花", season="春", lines=[
        ("ひさかたの", "ひさかたの"),                  # 5
        ("光のどけき春の", "ひかりのどけきはるの"),       # 10 (+3) critical
        ("日に", "ひに"),                            # 2 (-3) critical
        ("しづ心なく", "しづこころなく"),               # 7
        ("花の散るらむ", "はなのちるらむ"),             # 7
    ])
    result = validator.evaluate(t)
    assert "mora_count" in rules_fired(result)


# ─── kireji / 体言止め (Phase 1 B5a + B5c) ───

def test_kireji_present_no_violation():
    # 切れ字「や」あり
    t = make_tanka(kigo="月", season="秋", lines=[
        ("秋の夜や", "あきのよや"),
        ("月のひかりは", "つきのひかりは"),
        ("澄み渡り", "すみわたり"),
        ("音もたえたる", "おともたえたる"),
        ("空に冴えゆく", "そらにさえゆく"),
    ])
    result = validator.evaluate(t)
    assert "kireji_absent" not in rules_fired(result)


def test_taigendome_no_violation():
    # 結句が体言「花」
    t = make_tanka(kigo="花", season="春", lines=[
        ("春の野の", "はるののの"),
        ("風吹き渡る", "かぜふきわたる"),
        ("野を行けば", "のをゆけば"),
        ("散りそそぎたる", "ちりそそぎたる"),
        ("桃の花", "もものはな"),
    ])
    result = validator.evaluate(t)
    assert "kireji_absent" not in rules_fired(result)


def test_kireji_absent_violation():
    # 切れ字なし + 結句が動詞 (体言止めでない)
    t = make_tanka(kigo="春", season="春", lines=[
        ("春の野に", "はるののに"),
        ("風はそよぎて", "かぜはそよぎて"),
        ("鳥はうたひ", "とりはうたひ"),
        ("陽はあたたかく", "ひはあたたかく"),
        ("我は歩めり", "われはあゆめり"),
    ])
    result = validator.evaluate(t)
    assert "kireji_absent" in rules_fired(result)


# ─── Plan 整合 (evaluate の引数経由) ───

def test_season_matches_plan_violation():
    t = make_tanka(kigo="花", season="春")
    result = validator.evaluate(t, expected_season="夏", expected_kigo="蝉")
    fired = rules_fired(result)
    assert "season_matches_plan" in fired
    assert "kigo_matches_plan" in fired


def test_matches_plan_ok_when_consistent():
    t = make_tanka(kigo="花", season="春")
    result = validator.evaluate(t, expected_season="春", expected_kigo="花")
    fired = rules_fired(result)
    assert "season_matches_plan" not in fired
    assert "kigo_matches_plan" not in fired


def test_plan_mismatch_forces_refine():
    # Plan 不一致は score を 80 未満に落として refine を強制する
    t = make_tanka(kigo="花", season="春")
    result = validator.evaluate(t, expected_season="夏", expected_kigo="蝉")
    assert not result.passed
    assert result.score < validator.PASS_THRESHOLD


# ─── スコアリング ───

def test_score_never_negative():
    # わざと多重違反させても score は 0 で下げ止まる
    t = make_tanka(kigo="桜", season="冬", lines=[
        ("あああああああ", "あああああああ"),  # 7 (期待5、+2 critical)
        ("いいい", "いいい"),                # 3 (期待7、-4 critical)
        ("ううううう", "ううううう"),          # 5
        ("ええ", "ええ"),                    # 2 (期待7、-5 critical)
        ("おおお", "おおお"),                # 3 (期待7、-4 critical)
    ])
    result = validator.evaluate(t, expected_season="夏", expected_kigo="蝉")
    assert result.score == 0
    assert not result.passed


def test_score_weights_from_config():
    # RULE_WEIGHTS に登録された重みが減点に反映される
    t = make_tanka(kigo="花", season="春", lines=[
        ("花の色は", "はなのいろは"),
        ("うつりにけりな", "うつりにけりな"),
        ("いたづらに", "いたづらに"),
        ("わが身世にふる", "わがみよにふる"),
        ("花散る朝に", "はなちるあさに"),
    ])  # kigo_unique 違反
    result = validator.evaluate(t)
    kigo_unique_violations = [v for v in result.violations if v.rule == "kigo_unique"]
    assert len(kigo_unique_violations) == 1
    assert kigo_unique_violations[0].weight == validator.RULE_WEIGHTS["kigo_unique"]


# ─── critique 整形 ───

def test_format_critique_includes_score():
    t = make_tanka(kigo="花", season="春")
    result = validator.evaluate(t, expected_season="夏", expected_kigo="蝉")
    critique = validator.format_critique(result)
    assert "/100" in critique
    assert "season_matches_plan" in critique or "季節" in critique


def test_format_critique_empty_when_clean():
    result = validator.evaluate(make_tanka())
    # 合格でも minor が出る場合があるので、違反ゼロのときだけ "違反は検出されませんでした"
    if not result.violations:
        assert "違反は検出されませんでした" in validator.format_critique(result)


# ─── Plan 抽出 ───

@pytest.mark.parametrize("text,expected", [
    ("季語: 蝉\n季節: 夏\n情景: ...", "夏"),
    ("季語：桜\n季節：春", "春"),
    ("季節: 秋 です", "秋"),
    ("no season", None),
])
def test_extract_season(text, expected):
    assert validator.extract_season_from_plan(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("季語: 蝉\n季節: 夏", "蝉"),
    ("季語：五月雨\n季節：夏", "五月雨"),
    ("季語: 桜（八重桜）\n季節: 春", "桜"),
    ("no kigo", None),
])
def test_extract_kigo(text, expected):
    assert validator.extract_kigo_from_plan(text) == expected


# ─── 教訓 (LESSONS) ───

def test_lesson_for_violations():
    violations = [
        {"rule": "kigo_unique", "severity": "major", "weight": 15, "message": "..."},
        {"rule": "mora_count", "severity": "critical", "weight": 10, "message": "..."},
    ]
    lesson = validator._lesson_for_violations(violations)
    # 最重要 (weight 最大) は kigo_unique
    assert lesson == validator.LESSONS["kigo_unique"]


def test_every_rule_has_a_lesson():
    # RULES + Plan ルール + schema_invalid のすべてに LESSONS エントリがあること
    rule_names = {
        "mora_count", "mora_count_off_by_one", "mora_count_disputed",
        "kigo_present", "kigo_unique", "kigo_in_dictionary",
        "season_consistent", "no_other_kigo_cross", "no_other_kigo_same",
        "repeated_word", "kireji_absent",
        "season_matches_plan", "kigo_matches_plan", "schema_invalid",
    }
    missing = rule_names - set(validator.LESSONS.keys())
    assert not missing, f"LESSONS に欠けているルール: {missing}"


# ─── 長期失敗記憶の整形 ───

def test_format_long_term_failures():
    failures = [
        {"theme": "夏の夜", "score": 65, "parsed": {"kigo": "蛍", "season": "夏"},
         "violations": [{"rule": "kigo_unique", "severity": "major", "weight": 15, "message": "x"}]},
    ]
    text = validator.format_long_term_failures(failures)
    assert "夏の夜" in text
    assert "蛍" in text
    assert validator.LESSONS["kigo_unique"] in text


def test_format_long_term_failures_empty():
    assert validator.format_long_term_failures([]) == ""
