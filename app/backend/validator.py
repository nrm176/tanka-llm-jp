"""短歌の構造化出力に対する多軸検証 + スコアリング。

設計方針:
- LLM の出力は JSON。`Tanka` Pydantic モデルでスキーマ検証する。
- ルールは関数オブジェクトとして RULES に列挙し、各々が違反メッセージを返す。
- 100 点から減点していき、PASS_THRESHOLD 以上で合格。
- 違反は重要度順にソートして critique 文字列に整形 → refine プロンプトに投入。

外部から使う API:
- parse_tanka_json(text) -> Tanka | None
- evaluate(tanka) -> ValidationResult
- format_critique(result) -> str
- format_failure_summary(result) -> str  # 失敗履歴注入用 (短い)
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError

import reading  # 読み・拍数の葉モジュール (tanka への逆依存を解消)

log = logging.getLogger("validator")


# ─── 設定 (環境変数オーバーライド可能) ───
# Phase 2 のアブレーション実験で重みやしきい値を変えて A/B 比較するために、
# ハードコードではなく一箇所に集約し、環境変数で上書きできる形にしてある。

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


PASS_THRESHOLD = _env_int("TANKA_PASS_THRESHOLD", 80)

# ルール重み (減点)。値が大きいほど refine 強制力が強い。
RULE_WEIGHTS: dict[str, int] = {
    # critical
    "mora_count":            _env_int("TANKA_W_MORA_COUNT", 10),
    "kigo_present":          _env_int("TANKA_W_KIGO_PRESENT", 25),
    "season_matches_plan":   _env_int("TANKA_W_SEASON_MATCHES_PLAN", 30),
    # major
    "kigo_unique":           _env_int("TANKA_W_KIGO_UNIQUE", 15),
    "season_consistent":     _env_int("TANKA_W_SEASON_CONSISTENT", 20),
    "kigo_matches_plan":     _env_int("TANKA_W_KIGO_MATCHES_PLAN", 20),
    "no_other_kigo_cross":   _env_int("TANKA_W_NO_OTHER_KIGO_CROSS", 30),
    # minor
    "no_other_kigo_same":    _env_int("TANKA_W_NO_OTHER_KIGO_SAME", 25),
    "kigo_in_dictionary":    _env_int("TANKA_W_KIGO_IN_DICTIONARY", 5),
    "repeated_word":         _env_int("TANKA_W_REPEATED_WORD", 3),
    "mora_count_disputed":   _env_int("TANKA_W_MORA_DISPUTED", 3),
    # 新規 (Phase 1)
    "mora_count_off_by_one": _env_int("TANKA_W_MORA_OFF_BY_ONE", 3),   # B5b: 字余り/字足らず
    "kireji_absent":         _env_int("TANKA_W_KIREJI_ABSENT", 3),     # B5a+B5c: 句切れ・体言止め
    # お題との整合 (theme-aware)
    "theme_time_mismatch":   _env_int("TANKA_W_THEME_TIME_MISMATCH", 25),  # 夕暮れのお題に朝 等
    "theme_time_uncovered":  _env_int("TANKA_W_THEME_TIME_UNCOVERED", 5),  # お題の時刻が本文に皆無 (欠如)
    "theme_motif_uncovered": _env_int("TANKA_W_THEME_MOTIF_UNCOVERED", 5), # お題の主題が本文に皆無
}


def _w(rule: str) -> int:
    """ルール重みを引く (未登録なら 5)。"""
    return RULE_WEIGHTS.get(rule, 5)


# ─── Pydantic スキーマ ───

Season = Literal["春", "夏", "秋", "冬", "新年", "雑"]


class TankaLine(BaseModel):
    body: str = Field(..., min_length=1, description="漢字・かな混じり本文")
    reading: str = Field(..., min_length=1, description="ひらがな読み")


# モデルが実際に出力する `emotion` キーの綴り誤り (#68)。
# failures 全 625 件 (valid JSON 412 件) の横断調査で **emotion にのみ集中**しており、
# emoton 33 件 / "emotio n" 1 件 = valid JSON 失敗の 8.3%。kigo / season / lines / image の
# 4 キーには誤記が 1 件も無い。5 キー中で最も長い英単語であり、日本語特化モデルの
# サブワード境界 (emo + tion → emo + ton) が崩れているという仮説と整合する。
# **受け入れるが握り潰さない**: 発動は meta["key_typo"] で通知し、発生率を追えるようにする
# (握り潰すと誤記が永続化し、後で気づけなくなる)。
EMOTION_ALIASES = ("emotion", "emoton", "emotio n")
EMOTION_TYPOS = frozenset(EMOTION_ALIASES[1:])


class Tanka(BaseModel):
    kigo: str = Field(..., min_length=1, description="一首で用いる季語 (ちょうど 1 つ)")
    season: Season
    lines: list[TankaLine] = Field(..., min_length=5, max_length=5)
    image: str = Field(..., min_length=1, description="一文の情景")
    emotion: str = Field(..., min_length=1, description="一文の心情",
                         validation_alias=AliasChoices(*EMOTION_ALIASES))


# 構想下書き (#63) の季節。季語必須の経路なので「雑」は非対応 (ManualPlan と同じ制約)
PlanSeason = Literal["春", "夏", "秋", "冬", "新年"]


class PlanDraft(BaseModel):
    """LLM が下書きした構想 (#63)。人がレビュー・加筆して manual_plan (kigo/season/image/emotion) に
    確定させるための中間物。image は候補複数、background は人が読む素材 (compose には渡さない)。"""
    kigo: str = Field(..., min_length=1, max_length=20)
    season: PlanSeason
    image_candidates: list[str] = Field(..., min_length=1, max_length=5)
    emotion: str = Field(..., min_length=1)
    background: str = ""


# ─── 季語辞書のロード ───

KIGO_DATA_PATH = Path(__file__).parent / "data" / "kigo.json"

# 表記 → season の flat dict
_SEASON_MAP = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬", "new_year": "新年",
}


def _load_kigo_dict() -> dict[str, str]:
    # 季語辞書は検証に必須のキュレーション IP。欠損時は原因不明の import エラーで
    # backend を落とすのではなく、対処可能な明示メッセージで fail-loud にする。
    # (空 dict へ silent degrade はしない — 全季語チェックが黙って素通りし、
    #  スコアが静かに壊れるほうが有害。CLAUDE.md「No silent caps」に従う)
    try:
        raw = json.loads(KIGO_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RuntimeError(
            f"季語辞書が見つかりません: {KIGO_DATA_PATH}\n"
            "これは検証に必須のキュレーション IP で、版管理されている必要があります。"
            "fresh clone で欠ける場合は data がまだコミットされていません "
            "(.gitignore の carve-out と `git add -f` を確認)。"
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"季語辞書の JSON が壊れています: {KIGO_DATA_PATH}: {e}") from e
    flat: dict[str, str] = {}
    for key, season_label in _SEASON_MAP.items():
        for kigo in raw.get(key, []):
            flat[kigo] = season_label
    return flat


KIGO_DICT: dict[str, str] = _load_kigo_dict()
# 長い季語から先にマッチさせるためのソート済みキー (花火→花 のような誤マッチ回避)
_KIGO_SORTED = sorted(KIGO_DICT.keys(), key=lambda k: -len(k))


def find_kigo_in_text(text: str) -> list[tuple[str, str]]:
    """テキストから辞書登録された季語を抽出 (longest-match-first)。
    返り値: [(kigo, season), ...]"""
    consumed = [False] * len(text)
    found: list[tuple[str, str]] = []
    for kigo in _KIGO_SORTED:
        i = 0
        while True:
            j = text.find(kigo, i)
            if j == -1:
                break
            if not any(consumed[j:j + len(kigo)]):
                found.append((kigo, KIGO_DICT[kigo]))
                for k in range(j, j + len(kigo)):
                    consumed[k] = True
                i = j + len(kigo)
            else:
                i = j + 1
    return found


# 季節名そのものも季語として辞書に載るが、プリフィルとしては最も無情報
_SEASON_NAME_KIGO = set(_SEASON_MAP.values())


def extract_plan_prefill(text: str) -> dict:
    """自由文の構想から手動構想フォームのプリフィル候補を作る (#43)。
    季語辞書スキャンのみで LLM は使わない — LLM の「理解」は Plan フェーズの復活であり、
    手動モードが排除した解釈誤りを再導入するため。心情の抽出も意図的にしない。
    候補順は辞書スキャン順 (longest-match-first) だが、季節名そのものの季語 (「夏」等) は
    より具体的なヒットがあるとき後回しにする (「夏の朝…蝉の声」で 蝉 を選ぶための機械的規則)。
    返り値: {kigo, season, candidates: [{kigo, season}, ...]}。ヒットなしは kigo/season が None。"""
    seen: set[str] = set()
    candidates = []
    for kigo, season in find_kigo_in_text(text):
        if kigo in seen:  # 同一季語の複数出現は 1 候補に畳む
            continue
        seen.add(kigo)
        candidates.append({"kigo": kigo, "season": season})
    candidates.sort(key=lambda c: c["kigo"] in _SEASON_NAME_KIGO)  # stable sort: 季節名だけ後ろへ
    first = candidates[0] if candidates else None
    return {
        "kigo": first["kigo"] if first else None,
        "season": first["season"] if first else None,
        "candidates": candidates,
    }


# ─── ルール: 違反 / 重要度 / 重み ───

Severity = Literal["critical", "major", "minor"]


@dataclass
class Violation:
    rule: str
    severity: Severity
    weight: int
    message: str


@dataclass
class ValidationResult:
    score: int
    violations: list[Violation] = field(default_factory=list)
    passed: bool = False

    @property
    def errors(self) -> list[str]:
        return [v.message for v in self.violations if v.severity in ("critical", "major")]

    @property
    def warnings(self) -> list[str]:
        return [v.message for v in self.violations if v.severity == "minor"]


# 各ルールは Tanka を受け取り、違反 (Violation) のリストを返す。
RuleFunc = Callable[[Tanka], list[Violation]]


def _violation(rule: str, severity: Severity, weight: int, message: str) -> Violation:
    return Violation(rule=rule, severity=severity, weight=weight, message=message)


def _rule_mora_count(t: Tanka) -> list[Violation]:
    """各句の拍数 (5-7-5-7-7) を漢字本体から pykakasi で独立計算したもので検証する。

    3 段階の判定 (Phase 1 B5b):
    - pykakasi だけ違って model 提供読みは正しい  → mora_count_disputed (-3 minor) 古典読み疑い
    - 1 拍だけずれている (字余り/字足らず)          → mora_count_off_by_one (-3 minor) 許容範囲
    - 2 拍以上違う                                 → mora_count (-10 critical) 本物の違反
    """
    expected = [5, 7, 5, 7, 7]
    out: list[Violation] = []
    for i, line in enumerate(t.lines):
        canonical = reading.kanji_to_hira(line.body)
        actual = reading.count_moras(canonical)
        if actual == expected[i]:
            continue
        model_count = reading.count_moras(line.reading)

        if model_count == expected[i]:
            # モデル提供の読みは正しい拍数。pykakasi の辞書違いの可能性が高い。
            out.append(_violation(
                "mora_count_disputed", "minor", _w("mora_count_disputed"),
                f"{i+1}句目「{line.body}」は pykakasi 計算で {actual} 拍 ({canonical}) だが、"
                f"モデル提供の読み「{line.reading}」では {expected[i]} 拍。"
                f"古典読み等で判定が分かれた可能性があります。読みを再確認してください。"
            ))
            continue

        diff = abs(actual - expected[i])
        if diff == 1:
            # 字余り/字足らず。古典短歌でも許容される技法。
            label = "字余り" if actual > expected[i] else "字足らず"
            out.append(_violation(
                "mora_count_off_by_one", "minor", _w("mora_count_off_by_one"),
                f"{i+1}句目「{line.body}」は {actual} 拍 ({canonical})、{expected[i]} 拍が標準。"
                f"{label} は意図的な技法として許容されるが、特に意味がなければ整えてください。"
            ))
        else:
            note = ""
            if model_count != actual:
                note = f"（モデル提供読み「{line.reading}」は {model_count} 拍と主張）"
            out.append(_violation(
                "mora_count", "critical", _w("mora_count"),
                f"{i+1}句目「{line.body}」は実際は {actual} 拍 (正規読み: {canonical})。"
                f"{expected[i]} 拍に整えてください。{note}"
            ))
    return out


def _rule_kigo_present(t: Tanka) -> list[Violation]:
    """宣言された季語が短歌本体 (5 句の連結) に登場しているか。"""
    body_text = "".join(line.body for line in t.lines)
    if t.kigo not in body_text:
        return [_violation(
            "kigo_present", "critical", _w("kigo_present"),
            f"宣言された季語「{t.kigo}」が短歌本文に現れません。本文か kigo フィールドを修正してください。"
        )]
    return []


def _rule_kigo_unique(t: Tanka) -> list[Violation]:
    """季語が一首中に 1 回だけ現れているか。複数なら違反。"""
    body_text = "".join(line.body for line in t.lines)
    n = body_text.count(t.kigo)
    if n > 1:
        return [_violation(
            "kigo_unique", "major", _w("kigo_unique"),
            f"季語「{t.kigo}」が短歌内で {n} 回現れています。"
            f"一句一季語の原則に従い、{n - 1} 箇所を別の語に置換してください。"
        )]
    return []


def _rule_kigo_in_dictionary(t: Tanka) -> list[Violation]:
    """宣言された季語が手元の歳時記辞書に登録されているか (確からしさのチェック)。
    辞書は限定的なので不在 = 必ずしも誤りではないが、レビューを促す軽い警告にする。"""
    if t.kigo not in KIGO_DICT:
        return [_violation(
            "kigo_in_dictionary", "minor", _w("kigo_in_dictionary"),
            f"季語「{t.kigo}」は手元の歳時記辞書に登録されていません。"
            f"より一般的な季語に置き換えるか、宣言が正しいことを再確認してください。"
        )]
    return []


def _rule_season_consistent(t: Tanka) -> list[Violation]:
    """宣言された季節が、宣言された季語の辞書記載季と一致するか (辞書にある場合のみ)。"""
    if t.kigo not in KIGO_DICT:
        return []
    expected_season = KIGO_DICT[t.kigo]
    if t.season != expected_season:
        return [_violation(
            "season_consistent", "major", _w("season_consistent"),
            f"宣言された季節「{t.season}」と季語「{t.kigo}」(辞書記載: {expected_season}) が一致しません。"
        )]
    return []


# 裸の季節名。宣言季と同じならただの季節ラベルなので「競合する季語」とは見なさない
# (例: 季語=桜 の春の歌で本文に「春」が出るのは正常)。ただし宣言季と違えば季違いとして検出する
# (例: 冬の歌に「春」が出るのは誤り)。
_SEASON_LABEL_WORDS = {"春", "夏", "秋", "冬", "新年"}


# ─── 時間帯 (お題整合チェック用) ───
# お題が時刻を指定している (例「夕暮れ」) のに、短歌が反対の時刻 (例「朝」) で詠まれる
# 失敗を捉える。朝(0)→昼(1)→夕(2)→夜(3) の順で並べ、2 バンド以上離れたら矛盾とみなす
# (夕→夜 のような隣接は自然な移ろいなので許容、朝↔夕/朝↔夜/昼↔夜 のみ矛盾)。
_TIME_BANDS: list[tuple[str, list[str]]] = [
    ("朝", ["朝", "朝光", "朝日", "朝空", "朝靄", "曙", "暁", "夜明け", "あけぼの", "しののめ", "東雲"]),
    ("昼", ["昼", "真昼", "日中", "白昼", "正午"]),
    ("夕", ["夕", "夕暮", "夕焼", "夕映", "夕日", "夕闇", "黄昏", "たそがれ", "暮れ", "茜", "入日"]),
    ("夜", ["夜", "夜半", "夜更け", "真夜中", "深夜", "宵", "月夜"]),
]


def _detect_time_bands(text: str) -> set[int]:
    """テキストに現れる時間帯のインデックス集合を返す (朝=0, 昼=1, 夕=2, 夜=3)。"""
    bands: set[int] = set()
    for idx, (_, words) in enumerate(_TIME_BANDS):
        if any(w in text for w in words):
            bands.add(idx)
    return bands


def _check_theme_time(t: Tanka, theme: str) -> list[Violation]:
    """お題が時刻を含むのに、短歌が 2 バンド以上離れた時刻を詠んでいたら違反。"""
    theme_bands = _detect_time_bands(theme)
    if not theme_bands:
        return []  # お題に時刻指定なし
    body = "".join(line.body for line in t.lines)
    body_bands = _detect_time_bands(body)
    if not body_bands or (theme_bands & body_bands):
        return []  # 短歌に時刻語なし、または お題の時刻と一致する語を含む → OK
    # 最も近いバンド距離を見る。2 以上離れていれば矛盾
    min_dist = min(abs(tb - bb) for tb in theme_bands for bb in body_bands)
    if min_dist < 2:
        return []  # 隣接 (夕→夜 等) は自然な移ろいとして許容
    theme_names = "・".join(_TIME_BANDS[i][0] for i in sorted(theme_bands))
    body_names = "・".join(_TIME_BANDS[i][0] for i in sorted(body_bands))
    return [_violation(
        "theme_time_mismatch", "critical", _w("theme_time_mismatch"),
        f"お題は時間帯「{theme_names}」を指しているのに、短歌は「{body_names}」の情景になっています。"
        f"お題の時刻に合わせて詠み直してください。"
    )]


def _check_theme_time_uncovered(t: Tanka, theme: str) -> list[Violation]:
    """お題が時間帯 (夕暮れ・朝・夜 等) を明示しているのに、短歌の本文に時刻を感じさせる語が
    一切無い場合に minor で軽く促す。theme_time_mismatch が「矛盾」を見るのに対し、こちらは
    「欠如」を見る (お題の場面設定を完全に無視したケースの穴埋め。6a24d988 で踏んだ穴)。

    短歌はイメージで時刻を暗示することも多いため、誤検出を避けて低 weight (minor) に留め、
    本文に何らかの時刻語があれば (矛盾の有無は mismatch 側の管轄) ここでは発火しない。"""
    theme_bands = _detect_time_bands(theme)
    if not theme_bands:
        return []  # お題に時刻指定なし
    body = "".join(line.body for line in t.lines)
    if _detect_time_bands(body):
        return []  # 何らかの時刻語あり → 欠如ではない
    theme_names = "・".join(_TIME_BANDS[i][0] for i in sorted(theme_bands))
    return [_violation(
        "theme_time_uncovered", "minor", _w("theme_time_uncovered"),
        f"お題は時間帯「{theme_names}」を指していますが、短歌に時刻を感じさせる語が見当たりません。"
        f"「{theme_names}」の光・空の色・影など、時刻が伝わる景物を一つ取り入れるとお題に忠実になります。"
    )]


# 季節ラベル + 時間帯語に含まれる漢字。主題チェックでは除外し、季節/時刻は専用ルールに委ねる。
_SEASON_TIME_KANJI: set[str] = set("春夏秋冬新") | {
    c for _, words in _TIME_BANDS for w in words for c in w if "一" <= c <= "鿿"
}


def _theme_topic_kanji(theme: str) -> set[str]:
    """お題から「主題」を表す漢字集合を返す (季節・時間帯の漢字は除外)。"""
    return {c for c in theme if "一" <= c <= "鿿"} - _SEASON_TIME_KANJI


def _check_theme_motif(t: Tanka, theme: str) -> list[Violation]:
    """お題の「主題」(季節・時刻以外の事物・場面) が短歌に全く反映されていなければ minor で促す。

    お題から主題を表す漢字を集め (季節・時間帯の漢字は theme_time 系に委ねて除外)、その漢字が
    本文・情景(image)・心情(emotion) のどこにも 1 つも現れなければ「お題無視」とみなす。
    短歌はイメージで詠むため **漢字 1 つでも一致すれば不問** という極めて緩い条件にして誤検出を
    抑え (低 weight の minor)、egregious に的外れな生成だけを拾う。日本語 NER が無い制約下での
    保守的な近似であり、imagery 偏重の佳作を稀に拾うことは minor 重みで許容する。"""
    topic = _theme_topic_kanji(theme)
    if not topic:
        return []  # 季節・時刻のみのお題 (主題漢字なし) → 専用ルールに委ねる
    haystack = "".join(line.body for line in t.lines) + (t.image or "") + (t.emotion or "")
    if any(c in haystack for c in topic):
        return []  # 主題漢字が一つでも本文/情景/心情にあれば OK
    return [_violation(
        "theme_motif_uncovered", "minor", _w("theme_motif_uncovered"),
        f"お題「{theme}」の主題が、短歌にも情景・心情にも見当たりません。"
        f"お題の場面・事物を一つは詠み込んでください。"
    )]


def _rule_no_other_kigo(t: Tanka) -> list[Violation]:
    """一首一季語の厳格運用: 宣言した季語**以外の季語を一切含めない**。

    宣言外の季語が見つかったら、季違い(cross)・季重なり(same) のいずれも critical 違反とする。
    重みは 1 つでも合格 (PASS_THRESHOLD) を割るよう設定してあり、refine ループが
    宣言外季語を必ず除去するまで回る。

    例外: 宣言季と同じ季の「裸の季節名」(春/夏/秋/冬/新年) は季節ラベルとして許容する
    (競合する景物ではないため)。宣言季と異なる季節名は季違いとして検出する。"""
    body_text = "".join(line.body for line in t.lines)
    found = find_kigo_in_text(body_text)
    out: list[Violation] = []
    seen_other: dict[str, str] = {}  # kigo -> season
    for kigo, season in found:
        if kigo == t.kigo:
            continue
        # 宣言季と同じ季の裸の季節名はラベル扱いで許容
        if kigo in _SEASON_LABEL_WORDS and season == t.season:
            continue
        seen_other[kigo] = season

    if not seen_other:
        return out

    for kigo, season in seen_other.items():
        if season != t.season:
            out.append(_violation(
                "no_other_kigo_cross", "critical", _w("no_other_kigo_cross"),
                f"宣言外の季語「{kigo}」({season}) が本文に含まれています。"
                f"宣言季「{t.season}」と異なる季違いであり、宣言した季語以外は禁止です。"
                f"該当語を無季の言葉に置き換えてください。"
            ))
        else:
            out.append(_violation(
                "no_other_kigo_same", "critical", _w("no_other_kigo_same"),
                f"宣言外の季語「{kigo}」({season}) が本文に含まれています。"
                f"一首一季語の原則により、宣言した季語「{t.kigo}」以外の季語は禁止です。"
                f"該当語を無季の言葉に置き換えてください。"
            ))
    return out


def _rule_repeated_word(t: Tanka) -> list[Violation]:
    """目立つ語の重複。kana 読みベースで 3 文字以上の重複を緩く検出。
    (純粋な助詞などは除外したいが、簡易判定で十分)"""
    out: list[Violation] = []
    # 各句を比較し、3 文字以上の共通文字列が複数ある場合のみ報告
    readings = [line.reading for line in t.lines]
    for length in (4, 3):
        substr_counter: Counter[str] = Counter()
        for r in readings:
            seen_in_this = set()
            for i in range(len(r) - length + 1):
                s = r[i:i + length]
                if s not in seen_in_this:
                    substr_counter[s] += 1
                    seen_in_this.add(s)
        for s, c in substr_counter.items():
            if c >= 2 and not _is_trivial_substring(s):
                out.append(_violation(
                    "repeated_word", "minor", _w("repeated_word"),
                    f"「{s}」が複数の句に登場しています。表現の重複を避けると引き締まります。"
                ))
                return out  # 一件報告すれば十分
    return out


# ─── Phase 1 B5a + B5c: 句切れ / 体言止め ───
# 「句切れ」は短歌の余韻を生む作法。少なくとも一つあるのが定型。
# 結句が体言止めの場合は、それ自体が独立した収束マーカーなので OK 扱いにする。

# 切れ字や終止形助動詞の末尾パターン (簡易判定; 形態素解析を使わずに到達できる範囲)
_KIREJI_TAIL_PATTERNS = (
    "や", "かな", "けり", "なり", "たり", "ぞ", "らむ", "けむ",
    "らし", "まじ", "まし", "む", "ぬ", "ず", "つ", "ね", "よ", "を",
    "ゆ", "る", "き",
)

# 結句が体言で終わる場合の典型末尾 (ひらがな + 代表的漢字)
_TAIGEN_TAIL_HIRAGANA = (
    "もの", "こと", "ひと", "とき", "ところ", "ゆめ", "おもひ", "こころ",
    "あめ", "かぜ", "つき", "はな", "ゆき", "ほし", "うた", "こゑ",
    "おと", "いろ", "みち", "やま", "うみ", "そら", "くも", "ひ", "よ",
    "かげ", "ねこ", "とり",
)
_TAIGEN_TAIL_KANJI = (
    "月", "花", "風", "雨", "雪", "星", "声", "歌", "音", "色", "道",
    "山", "海", "空", "雲", "影", "鳥", "夢", "人", "心", "時", "里",
    "野", "光", "影",
)


def _ends_with_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(text.endswith(p) for p in patterns)


def _rule_kireji_or_taigendome(t: Tanka) -> list[Violation]:
    """1-4 句のどこかに句切れがあるか、または 5 句目が体言止めで終わっているか。
    どちらも検出できなければ minor 警告。

    形態素解析を持たないので末尾文字列の近似検出。8B モデル特有の "全部が流れる
    散文っぽい短歌" を抑制するための軽い注意喚起ルール。"""
    # 1-4 句に切れ字／終止形候補があるか (kana 読み末尾で判定)
    has_mid_kireji = any(
        _ends_with_any(line.reading.strip(), _KIREJI_TAIL_PATTERNS)
        for line in t.lines[:-1]
    )
    if has_mid_kireji:
        return []

    # 結句が体言止めか
    last = t.lines[-1]
    body_end = last.body.strip()
    reading_end = last.reading.strip()
    has_taigen_dome = (
        _ends_with_any(body_end, _TAIGEN_TAIL_KANJI) or
        _ends_with_any(reading_end, _TAIGEN_TAIL_HIRAGANA)
    )
    if has_taigen_dome:
        return []

    return [_violation(
        "kireji_absent", "minor", _w("kireji_absent"),
        "句切れも体言止めも検出できません。切れ字 (や / かな / けり等) を入れるか、"
        "結句を体言で締めると、短歌に区切れと余韻が生まれます。"
    )]


_TRIVIAL = re.compile(r"^[のはがをにでとへもやかな、。]+$")


def _is_trivial_substring(s: str) -> bool:
    """助詞や繋ぎ語ばかりの substring は重複として報告しない。"""
    return bool(_TRIVIAL.match(s))


RULES: list[RuleFunc] = [
    _rule_mora_count,
    _rule_kigo_present,
    _rule_kigo_unique,
    _rule_kigo_in_dictionary,
    _rule_season_consistent,
    _rule_no_other_kigo,
    _rule_repeated_word,
    _rule_kireji_or_taigendome,    # NEW: Phase 1 B5a + B5c
]


# ─── 評価本体 ───

def evaluate(
    t: Tanka,
    *,
    expected_season: str | None = None,
    expected_kigo: str | None = None,
    theme: str | None = None,
) -> ValidationResult:
    """通常ルール + (任意で) Plan 由来の期待値・お題との整合チェック。

    expected_season / expected_kigo を渡すと、それと一致しない場合に大きな違反として
    score を下げる (Plan→Compose のすり替え矯正)。
    theme を渡すと、お題が指定する時間帯 (夕暮れ等) と短歌の時刻矛盾を検出する。"""
    violations: list[Violation] = []
    for rule in RULES:
        violations.extend(rule(t))

    if expected_season and t.season != expected_season:
        violations.append(_violation(
            "season_matches_plan", "critical", _w("season_matches_plan"),
            f"構想で決めた季節「{expected_season}」と出力の season「{t.season}」が一致しません。"
            f"構想を勝手に書き換えず、季節「{expected_season}」の歌にしてください。"
        ))
    if expected_kigo and t.kigo != expected_kigo:
        violations.append(_violation(
            "kigo_matches_plan", "major", _w("kigo_matches_plan"),
            f"構想で決めた季語「{expected_kigo}」と出力の kigo「{t.kigo}」が一致しません。"
            f"構想で選んだ季語をそのまま使ってください。"
        ))
    if theme:
        violations.extend(_check_theme_time(t, theme))
        violations.extend(_check_theme_time_uncovered(t, theme))
        violations.extend(_check_theme_motif(t, theme))

    score = max(0, 100 - sum(v.weight for v in violations))
    return ValidationResult(
        score=score,
        violations=violations,
        passed=score >= PASS_THRESHOLD,
    )


# ─── JSON パース (LLM 出力からの取り出し) ───

_FENCED_RE = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)


_NOT_FOUND_MSG = "JSON オブジェクトが見つかりません ({…} を出力してください)"


def repair_truncated_json(fragment: str) -> dict | None:
    """末尾が切れた JSON オブジェクトを、未閉じの括弧を補って復元する (#65)。

    `fragment` は最初の `{` から **テキスト末尾まで** (`rfind("}")` で切らないこと)。
    復元できたら dict、できなければ None を返す。

    背景 (FINDINGS §5.6): 50 題 × 2 arm の実測で全生成の 12% が score 0 になり、その全てが
    schema_invalid だった。正体は **モデルが最後の `}` を出力しない**こと。成功時は `..."\n}` で
    終わるのに対し失敗時は `..."` で終わる。parse_tanka_json は「最初の `{` 〜 最後の `}`」で
    切り出すため、閉じ括弧が無いと **lines 配列内の最後の要素の `}`** を拾って不均衡な文字列を
    json.loads に渡し、「Expecting ',' delimiter」という中身が壊れているように見える
    エラーになる (実体は末尾切れ)。この関数はその 1 パターンだけを機械的に直す。
    """
    stack: list[str] = []
    in_str = esc = False
    for ch in fragment:
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if not stack or stack[-1] != ("{" if ch == "}" else "["):
                    return None  # 括弧が交差している = 末尾切れ以外の破損
                stack.pop()
    if not stack:
        return None  # 閉じ切れている = 末尾切れではない (別の理由で parse に失敗している)

    repaired = fragment + ('"' if in_str else "")
    if not in_str:
        repaired = repaired.rstrip()
        # 末尾のカンマ (次の要素を書き始める前に切れた) を落とす。
        # 末尾がコロンの場合は値が無いので復元不能 — 下の loads が弾く
        if repaired.endswith(","):
            repaired = repaired[:-1]
    for ch in reversed(stack):
        repaired += "}" if ch == "{" else "]"
    try:
        data = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _load_json_object(text: str, meta: dict | None = None) -> tuple[dict | None, str | None]:
    """LLM の生出力から最初の { 〜 最後の } を JSON として読む (parse_tanka_json / parse_plan_draft_json 共通)。
    code fence や前後の散文に robust。成功なら (dict, None)、失敗なら (None, error_message)。

    通常の抽出が失敗したときだけ、末尾切れの救済 (repair_truncated_json) を試す (#65)。
    meta に dict を渡すと、救済が発動した場合に "json_repaired" が True で入る
    (呼び出し側が validation イベントに載せて事後分析できるようにするため)。"""
    text = text.strip()
    # fenced code block 除去
    text = _FENCED_RE.sub("", text)
    text = re.sub(r"\n?```\s*$", "", text)

    # 最初の { と最後の } を探す
    start = text.find("{")
    if start == -1:
        return None, _NOT_FOUND_MSG
    end = text.rfind("}")

    data = None
    err = _NOT_FOUND_MSG
    if end > start:
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            err = f"JSON パース失敗: {e.msg} (位置 {e.pos})"

    if data is None:
        # 末尾切れの救済 (#65)。現状 parse に失敗しているケースでのみ発動するため、
        # 成功していたパスの挙動は変わらない
        data = repair_truncated_json(text[start:])
        if data is None:
            return None, err
        if meta is not None:
            meta["json_repaired"] = True
        log.info("_load_json_object: repaired truncated JSON (%d chars)", len(text) - start)
    return data, None


def _schema_error_message(e: ValidationError) -> str:
    """ユーザーフレンドリな短いメッセージを作る。"""
    first_error = e.errors()[0]
    loc = ".".join(str(x) for x in first_error.get("loc", []))
    return f"JSON スキーマ違反 ({loc}): {first_error.get('msg', 'unknown')}"


def parse_tanka_json(text: str, meta: dict | None = None) -> Tanka | tuple[None, str]:
    """LLM の生出力から JSON を取り出して Tanka に変換する。
    成功なら Tanka、失敗なら (None, error_message) を返す。
    JSON の取り出しと末尾切れ救済 (#65) は _load_json_object に委ねる (meta["json_repaired"])。"""
    data, err = _load_json_object(text, meta)
    if data is None:
        return None, err or _NOT_FOUND_MSG

    # キー名の綴り誤り (#68) を検出して記録。alias で受け入れるが、発生率は計測できるようにする
    typos = [k for k in data if k in EMOTION_TYPOS] if isinstance(data, dict) else []
    if typos:
        if meta is not None:
            meta["key_typo"] = typos[0]
        log.info("parse_tanka_json: accepted misspelled key %r as 'emotion'", typos[0])

    try:
        return Tanka.model_validate(data)
    except ValidationError as e:
        return None, _schema_error_message(e)


def parse_plan_draft_json(text: str) -> PlanDraft | tuple[None, str]:
    """LLM の生出力から構想下書き JSON (#63) を取り出して PlanDraft に変換する。
    成功なら PlanDraft、失敗なら (None, error_message)。parse_tanka_json と同じ robust さ。"""
    data, err = _load_json_object(text)
    if data is None:
        return None, err or "JSON を読めませんでした"
    try:
        return PlanDraft.model_validate(data)
    except ValidationError as e:
        return None, _schema_error_message(e)


def finalize_plan_draft(draft: PlanDraft, *, fixed_kigo: str | None = None
                        ) -> tuple[PlanDraft, list[dict]]:
    """構想下書きを確定し、人のレビュー用の警告を付ける (純関数、#63)。

    - fixed_kigo (人が固定した季語) と LLM の kigo が違えば **上書き** して kigo_overridden を警告
      (季語は人の決定が正。8B は指示された季語を変える癖がある = §6.4)
    - 辞書に載る季語なら **季節は辞書が正** (LLM の season 主張が違えば season_corrected)。
      辞書外なら kigo_not_in_dictionary を警告し、上書きはしない (人がレビューで直す)
    - 各情景候補に宣言季語以外の辞書季語が含まれていれば other_kigo (候補 index 付き)。
      宣言季語そのものと、それを含む長い辞書語 (蝉 → 蝉しぐれ) は季重なりと見ない。
      compose が候補文を写すと no_other_kigo (critical) を踏むため、ここで先に見せる
    警告は {"type": ..., ...} の list。順序: kigo 系 → 候補ごとの other_kigo。"""
    warnings: list[dict] = []
    kigo = draft.kigo
    if fixed_kigo and fixed_kigo != kigo:
        warnings.append({"type": "kigo_overridden", "kigo": kigo, "fixed_kigo": fixed_kigo})
        kigo = fixed_kigo

    season = draft.season
    dict_season = KIGO_DICT.get(kigo)
    if dict_season is None:
        warnings.append({"type": "kigo_not_in_dictionary", "kigo": kigo})
    elif dict_season != season:
        warnings.append({"type": "season_corrected", "kigo": kigo,
                         "from": season, "to": dict_season})
        season = dict_season

    for i, candidate in enumerate(draft.image_candidates):
        for found, found_season in find_kigo_in_text(candidate):
            if found == kigo or kigo in found:
                continue
            warnings.append({"type": "other_kigo", "candidate": i, "kigo": found, "season": found_season})

    final = draft.model_copy(update={"kigo": kigo, "season": season})
    return final, warnings


# ─── critique フォーマッタ ───

def format_critique(result: ValidationResult) -> str:
    """LLM への refine prompt に投げ込むための、優先度順 critique。"""
    if not result.violations:
        return "違反は検出されませんでした。"
    sorted_v = sorted(result.violations, key=lambda v: -v.weight)
    lines = [f"前回の評点: {result.score}/100  (合格ライン: {PASS_THRESHOLD})", ""]

    top = sorted_v[:2]
    rest = sorted_v[2:]
    if top:
        lines.append("**最も重要な違反 (これを必ず直す)**:")
        for v in top:
            lines.append(f"  ✗ [-{v.weight} / {v.severity}] {v.message}")

    if rest:
        lines.append("")
        lines.append("**その他**:")
        for v in rest:
            lines.append(f"  - [-{v.weight}] {v.message}")
    return "\n".join(lines)


def format_failure_summary(result: ValidationResult) -> str:
    """失敗履歴に蓄積する短い要約 (次回 attempt の prompt に注入)。"""
    if not result.violations:
        return ""
    rule_counts = Counter(v.rule for v in result.violations)
    parts = [f"{rule}({cnt})" if cnt > 1 else rule for rule, cnt in rule_counts.items()]
    return f"score={result.score}, 違反: {', '.join(parts)}"


def format_schema_critique(error_msg: str) -> str:
    """JSON パース失敗時の特殊な critique。"""
    return (
        f"前回の評点: 0/100  (合格ライン: {PASS_THRESHOLD})\n\n"
        f"**致命的な違反**: 出力が JSON として読めませんでした。\n"
        f"  詳細: {error_msg}\n\n"
        f"再度、JSON の前後に説明を一切付けず、{{ から }} で完結する 1 オブジェクトを出力してください。"
    )


# ─── 長期記憶: 違反 → 教訓へのマッピング ───

# ルール名 → 「次回に活かすべき短い教訓」。anti-example の本文で使う。
LESSONS: dict[str, str] = {
    "kigo_unique": "宣言した季語は本文中ちょうど 1 回だけ出現させる (再利用禁止)",
    "no_other_kigo_cross": "宣言した季語以外の季語を本文に一切入れない。特に異なる季の季語は厳禁",
    "no_other_kigo_same": "宣言した季語以外の季語は同季でも禁止。一首には季語をちょうど一つだけ",
    "kigo_present": "kigo フィールドで宣言した語を、必ず本文 lines のどこかに登場させる",
    "season_consistent": "宣言する季節は、選んだ季語の本来の季と一致させる",
    "season_matches_plan": "構想ステップで決めた季節を勝手に変更しない",
    "kigo_matches_plan": "構想ステップで決めた季語をそのまま使う",
    "theme_time_mismatch": "お題が指す時間帯 (夕暮れ・朝・夜 等) に合った情景を詠む",
    "theme_time_uncovered": "お題が時間帯を指すときは、その時刻が伝わる景物 (光・空の色・影 等) を一つは詠み込む",
    "theme_motif_uncovered": "お題が指す場面・事物 (海辺・坂道・団欒 等) を短歌に一つは詠み込む",
    "kigo_in_dictionary": "なるべく一般的に通用する季語を選ぶ",
    "mora_count": "拍数 5-7-5-7-7 を厳守する。漢字の現代読みでも数えられるようにする",
    "mora_count_off_by_one": "字余り・字足らずは ±1 まで許容されるが、特に意図がなければ整える",
    "mora_count_disputed": "古典読みに頼る句では、現代読みでも拍数が崩れないよう調整するか、より平易な表記にする",
    "repeated_word": "目立つ語の重複を避け、表現を引き締める",
    "kireji_absent": "切れ字 (や / かな / けり等) を入れるか、結句を体言で締めて余韻を作る",
    "schema_invalid": "JSON 形式厳守。前後に説明文を付けない",
}


def _lesson_for_violations(violations: list[dict]) -> str:
    """Violation の dict 列から、最重要違反の lesson を返す。
    入力は dict (DB から取得した形式) を想定。"""
    if not violations:
        return "validator のいずれかのルールに違反"
    primary = max(violations, key=lambda v: v.get("weight", 0))
    rule = primary.get("rule", "")
    return LESSONS.get(rule, f"{rule} 違反を避ける")


def long_term_failure_entries(failures: list[dict]) -> list[dict]:
    """db.recent_failures(...) の戻り値を構造化教訓エントリへ変換する純関数。
    prompt ブロック (format_lesson_entries) と SSE "lessons" イベントの共通ソース。
    UI に見せるものとプロンプトに注入するものを必ず一致させるため、両者はここを経由する。"""
    entries = []
    for f in failures:
        violations = f.get("violations") or []
        primary = max(violations, key=lambda v: v.get("weight", 0)) if violations else None
        parsed = f.get("parsed") or {}
        entries.append({
            "theme": f.get("theme"),
            "kigo": parsed.get("kigo"),
            "season": parsed.get("season"),
            "score": f.get("score"),
            "rule": primary.get("rule") if primary else None,
            "lesson": _lesson_for_violations(violations),
            "ts": f.get("ts"),  # db._serialize 済み = isoformat str。SSE にそのまま載る
        })
    return entries


def format_lesson_entries(entries: list[dict]) -> str:
    """構造化教訓エントリを、compose プロンプト先頭に挿入する anti-example ブロックに整形。"""
    if not entries:
        return ""
    lines = [
        "",
        "【長期失敗記憶 — 過去にあなたがやらかした違反パターン。同じことを繰り返さないこと】",
    ]
    for i, e in enumerate(entries, 1):
        theme = e.get("theme") if e.get("theme") is not None else "?"
        score = e.get("score") if e.get("score") is not None else "?"
        kigo_note = f" (宣言季語「{e['kigo']}」)" if e.get("kigo") else ""
        lines.append(f"  失敗 {i}: お題「{theme}」{kigo_note} で score={score} → 教訓: {e['lesson']}")
    return "\n".join(lines) + "\n"


def format_long_term_failures(failures: list[dict]) -> str:
    """過去の失敗群を anti-example ブロックに整形 (long_term_failure_entries の薄い wrapper)。

    failures は db.recent_failures(...) の戻り値 (list[dict]) を想定。
    各 entry は {theme, parsed, violations, score, ts, ...} を含む。"""
    return format_lesson_entries(long_term_failure_entries(failures))


_SEASON_RE = re.compile(r"季節[:：]\s*(春|夏|秋|冬|新年|雑)")
# 季語は (空白/改行) までの 1 トークンとして抽出。括弧や読み仮名注釈は捨てる。
_KIGO_RE = re.compile(r"季語[:：]\s*([^\s\n、。()（）]+)")
# JSON 形式の Plan ("season": "夏" / "kigo": "蝉時雨") も拾う。
# 8B モデルは Plan を指示形式 (季語:) でなく JSON で返すことがあり、その場合に
# テキスト regex だと抽出失敗 → Plan-Compose 整合ガードが無効化される事故が起きた。
_JSON_SEASON_RE = re.compile(r'["\']season["\']\s*[:：]\s*["\'](春|夏|秋|冬|新年|雑)["\']')
_JSON_KIGO_RE = re.compile(r'["\']kigo["\']\s*[:：]\s*["\']([^"\']+)["\']')


def extract_season_from_plan(plan_text: str) -> str | None:
    """plan の出力から季節を抽出する。テキスト形式 (季節: 夏) と JSON 形式の両方に対応。"""
    if not plan_text:
        return None
    m = _SEASON_RE.search(plan_text) or _JSON_SEASON_RE.search(plan_text)
    return m.group(1) if m else None


def extract_kigo_from_plan(plan_text: str) -> str | None:
    """plan の出力から季語を抽出する。テキスト形式 (季語: 蝉) と JSON 形式の両方に対応。"""
    if not plan_text:
        return None
    m = _KIGO_RE.search(plan_text) or _JSON_KIGO_RE.search(plan_text)
    return m.group(1).strip() if m else None
