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


def test_kigo_dict_missing_fails_loud(tmp_path, monkeypatch):
    """季語辞書の欠損は、原因不明の import 失敗ではなく対処可能な明示エラーで落ちる。

    silent degrade (空 dict) にすると全季語チェックが黙って素通りしスコアが静かに壊れる
    ため、fail-loud が正しい挙動 (fresh clone で data 未コミットを即検知する安全網)。"""
    monkeypatch.setattr(validator, "KIGO_DATA_PATH", tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match="季語辞書が見つかりません"):
        validator._load_kigo_dict()


def test_kigo_dict_corrupt_fails_loud(tmp_path, monkeypatch):
    """壊れた JSON も明示エラーで落ちる。"""
    bad = tmp_path / "kigo.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(validator, "KIGO_DATA_PATH", bad)
    with pytest.raises(RuntimeError, match="JSON が壊れています"):
        validator._load_kigo_dict()


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


# ─── no_other_kigo (宣言外季語の厳格禁止) ───

def test_no_other_kigo_cross_season():
    # 宣言は夏 (蛍) だが、本文に紅葉 (秋) が混入 → 季違い critical
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("蛍の火", "ほたるのひ"),
        ("紅葉の影に", "もみぢのかげに"),
        ("ゆれてゐる", "ゆれてゐる"),
        ("夜風はこぶ", "よかぜはこぶ"),
        ("遠き面影", "とほきおもかげ"),
    ])
    result = validator.evaluate(t)
    fired = {v.rule for v in result.violations}
    assert "no_other_kigo_cross" in fired
    # critical なので 1 つで合格不能
    cross = next(v for v in result.violations if v.rule == "no_other_kigo_cross")
    assert cross.severity == "critical"


def test_no_other_kigo_same_season_now_blocks():
    # 宣言は春 (桜) だが本文に同季の別季語「梅」が混入 → 厳格化で critical、合格不能
    t = make_tanka(kigo="桜", season="春", lines=[
        ("桜咲く", "さくらさく"),
        ("丘の向かうに", "おかのむかうに"),
        ("梅も咲き", "うめもさき"),
        ("春の日永し", "はるのひながし"),
        ("風やはらかし", "かぜやはらかし"),
    ])
    result = validator.evaluate(t)
    fired = {v.rule for v in result.violations}
    assert "no_other_kigo_same" in fired
    same = next(v for v in result.violations if v.rule == "no_other_kigo_same")
    assert same.severity == "critical"
    assert not result.passed  # 宣言外季語 1 つで合格を割る


def test_bare_season_label_allowed():
    # 宣言季と同じ季の裸の季節名 (春) はラベル扱いで違反にしない
    t = make_tanka(kigo="桜", season="春", lines=[
        ("春の野に", "はるののに"),
        ("桜ひとひら", "さくらひとひら"),
        ("舞ひ落ちて", "まひおちて"),
        ("光あつめて", "ひかりあつめて"),
        ("土に還りぬ", "つちにかへりぬ"),
    ])
    result = validator.evaluate(t)
    fired = {v.rule for v in result.violations}
    # 「春」はラベル、「桜」は宣言季語 → 宣言外季語なし
    assert "no_other_kigo_same" not in fired
    assert "no_other_kigo_cross" not in fired


def test_cross_season_bare_label_still_flagged():
    # 宣言は冬だが本文に「春」(別季の季節名) → 季違いとして検出する
    t = make_tanka(kigo="雪", season="冬", lines=[
        ("雪の朝", "ゆきのあさ"),
        ("春を待ちつつ", "はるをまちつつ"),
        ("身を縮め", "みをちぢめ"),
        ("白き world に", "しろきせかいに"),
        ("息白く立つ", "いきしろくたつ"),
    ])
    result = validator.evaluate(t)
    assert "no_other_kigo_cross" in {v.rule for v in result.violations}


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


# ─── Plan 抽出 (テキスト形式 + JSON 形式) ───

def test_extract_from_json_plan():
    # 8B が Plan を JSON で返すケース (実際に踏んだバグ): JSON からも抽出できる
    plan = '{"kigo": "蝉時雨", "season": "夏", "image": "...夕暮れ。", "emotion": "郷愁"}'
    assert validator.extract_season_from_plan(plan) == "夏"
    assert validator.extract_kigo_from_plan(plan) == "蝉時雨"


# ─── テーマ時間帯の整合 (theme_time_mismatch) ───

def test_theme_time_dusk_vs_morning_flagged():
    # お題「夏の夕暮れ」なのに朝の情景 → 矛盾 (朝と夕は 2 バンド離れている)
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("朝光さす", "あさひかりさす"),
        ("川辺に蛍", "かわべにほたる"),
        ("舞ひにけり", "まいにけり"),
        ("風やはらかく", "かぜやわらかく"),
        ("夢の名残り", "ゆめのなごり"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    fired = {v.rule for v in result.violations}
    assert "theme_time_mismatch" in fired
    assert not result.passed  # critical なので合格不能


def test_theme_time_dusk_with_dusk_ok():
    # お題「夕暮れ」で夕の語を含む → OK
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("夕暮れに", "ゆうぐれに"),
        ("川辺に蛍", "かわべにほたる"),
        ("舞ひにけり", "まいにけり"),
        ("風やはらかく", "かぜやわらかく"),
        ("夢の名残り", "ゆめのなごり"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    assert "theme_time_mismatch" not in {v.rule for v in result.violations}


def test_theme_time_adjacent_ok():
    # お題「夕暮れ」で夜の語 → 隣接 (夕→夜) なので自然な移ろい、許容
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("宵闇に", "よいやみに"),
        ("川辺に蛍", "かわべにほたる"),
        ("舞ひにけり", "まいにけり"),
        ("風やはらかく", "かぜやわらかく"),
        ("夢の名残り", "ゆめのなごり"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    assert "theme_time_mismatch" not in {v.rule for v in result.violations}


# ─── テーマ時刻の「欠如」(theme_time_uncovered) ───

def test_theme_time_uncovered_flagged_when_no_time_word():
    # お題「夏の夕暮れ」だが本文に時刻語が皆無 → minor で uncovered (矛盾ではなく欠如)
    t = make_tanka(kigo="花", season="春", lines=[
        ("花が散る", "はながちる"),
        ("水面光る", "みなもひかる"),
        ("山の影に", "やまのかげに"),
        ("風そよぐなり", "かぜそよぐなり"),
        ("遠き面影", "とおきおもかげ"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    fired = {v.rule for v in result.violations}
    assert "theme_time_uncovered" in fired
    assert "theme_time_mismatch" not in fired  # 矛盾ではなく欠如


def test_theme_time_uncovered_silent_with_dusk_imagery():
    # 「茜」(夕の景物) を含めば uncovered は出ない (imagery で時刻を暗示)
    t = make_tanka(kigo="花", season="春", lines=[
        ("茜さす", "あかねさす"),
        ("空のかなたに", "そらのかなたに"),
        ("花散りて", "はなちりて"),
        ("風のそよげば", "かぜのそよげば"),
        ("遠き山並み", "とおきやまなみ"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    assert "theme_time_uncovered" not in {v.rule for v in result.violations}


def test_theme_time_uncovered_silent_when_theme_timeless():
    # お題に時刻指定が無ければ発火しない
    t = make_tanka(kigo="花", season="春", lines=[
        ("花が散る", "はながちる"),
        ("水面光る", "みなもひかる"),
        ("山の影に", "やまのかげに"),
        ("風そよぐなり", "かぜそよぐなり"),
        ("遠き面影", "とおきおもかげ"),
    ])
    result = validator.evaluate(t, theme="春の花野")
    assert "theme_time_uncovered" not in {v.rule for v in result.violations}


def test_theme_time_uncovered_is_minor():
    # 単独では合格を妨げない軽微な減点 (false positive を許容できる安全弁)
    t = make_tanka(kigo="花", season="春", lines=[
        ("花が散る", "はながちる"),
        ("水面光る", "みなもひかる"),
        ("山の影に", "やまのかげに"),
        ("風そよぐなり", "かぜそよぐなり"),
        ("遠き面影", "とおきおもかげ"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    unc = [v for v in result.violations if v.rule == "theme_time_uncovered"]
    assert unc and unc[0].severity == "minor"


def test_theme_time_no_time_in_theme():
    # お題に時刻指定がなければチェックしない
    t = make_tanka(kigo="蛍", season="夏", lines=[
        ("朝光さす", "あさひかりさす"),
        ("川辺に蛍", "かわべにほたる"),
        ("舞ひにけり", "まいにけり"),
        ("風やはらかく", "かぜやわらかく"),
        ("夢の名残り", "ゆめのなごり"),
    ])
    result = validator.evaluate(t, theme="蛍")
    assert "theme_time_mismatch" not in {v.rule for v in result.violations}


# ─── お題の主題 (語彙・モチーフ) 遵守 (theme_motif_uncovered) ───

def test_theme_topic_kanji_excludes_season_and_time():
    # 季節・時間帯の漢字は主題から除外され、事物の漢字だけが残る
    assert validator._theme_topic_kanji("夏の夕暮れ") == set()
    topic = validator._theme_topic_kanji("海辺の街を見下ろす坂道")
    assert {"海", "坂", "道"} <= topic


def test_theme_motif_uncovered_flagged_when_off_topic():
    # お題は「海辺…坂道」だが、歌も情景も心情も春の花でお題の事物が皆無 → minor
    t = make_tanka(kigo="花", season="春",
        lines=[
            ("花が散る", "はながちる"),
            ("風に舞ひて", "かぜにまいて"),
            ("地に落ちぬ", "ちにおちぬ"),
            ("春の名残を", "はるのなごりを"),
            ("惜しむ頃かな", "おしむころかな"),
        ],
        image="春の野に桜が静かに散る", emotion="花への憐れみ")
    result = validator.evaluate(t, theme="海辺の街を見下ろす坂道")
    assert "theme_motif_uncovered" in {v.rule for v in result.violations}


def test_theme_motif_uncovered_silent_when_motif_present():
    # 「海」「坂」「道」等が情景・本文にあれば不問 (漢字 1 つでも一致で OK = 緩い条件)
    t = make_tanka(kigo="蛍", season="夏",
        lines=[
            ("坂の上", "さかのうえ"),
            ("蛍ひとつ", "ほたるひとつ"),
            ("流れ星", "ながれぼし"),
            ("海を見る夜", "うみをみるよ"),
            ("更けゆく時", "ふけゆくとき"),
        ],
        image="海辺の坂道を下る", emotion="郷愁")
    result = validator.evaluate(t, theme="海辺の街を見下ろす坂道")
    assert "theme_motif_uncovered" not in {v.rule for v in result.violations}


def test_theme_motif_uncovered_silent_when_theme_is_season_time_only():
    # お題が季節・時刻だけ (主題漢字なし) なら発火しない (季節/時刻は専用ルールに委ねる)
    t = make_tanka(kigo="花", season="春", lines=[
        ("花が散る", "はながちる"),
        ("水面光る", "みなもひかる"),
        ("山の影に", "やまのかげに"),
        ("風そよぐなり", "かぜそよぐなり"),
        ("遠き面影", "とおきおもかげ"),
    ])
    result = validator.evaluate(t, theme="夏の夕暮れ")
    assert "theme_motif_uncovered" not in {v.rule for v in result.violations}


def test_theme_motif_uncovered_is_minor():
    # 単独では合格を妨げない軽微な減点 (NER なしの保守的近似なので minor)
    t = make_tanka(kigo="花", season="春",
        lines=[
            ("花が散る", "はながちる"),
            ("風に舞ひて", "かぜにまいて"),
            ("地に落ちぬ", "ちにおちぬ"),
            ("春の名残を", "はるのなごりを"),
            ("惜しむ頃かな", "おしむころかな"),
        ],
        image="春の野に桜が散る", emotion="花への憐れみ")
    result = validator.evaluate(t, theme="海辺の街を見下ろす坂道")
    mv = [v for v in result.violations if v.rule == "theme_motif_uncovered"]
    assert mv and mv[0].severity == "minor"


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
        "season_matches_plan", "kigo_matches_plan", "theme_time_mismatch",
        "theme_time_uncovered", "theme_motif_uncovered", "schema_invalid",
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
