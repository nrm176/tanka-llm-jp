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
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

import tanka  # 既存のモーラ計算等を利用

log = logging.getLogger("validator")

PASS_THRESHOLD = 80


# ─── Pydantic スキーマ ───

Season = Literal["春", "夏", "秋", "冬", "新年", "雑"]


class TankaLine(BaseModel):
    body: str = Field(..., min_length=1, description="漢字・かな混じり本文")
    reading: str = Field(..., min_length=1, description="ひらがな読み")


class Tanka(BaseModel):
    kigo: str = Field(..., min_length=1, description="一首で用いる季語 (ちょうど 1 つ)")
    season: Season
    lines: list[TankaLine] = Field(..., min_length=5, max_length=5)
    image: str = Field(..., min_length=1, description="一文の情景")
    emotion: str = Field(..., min_length=1, description="一文の心情")


# ─── 季語辞書のロード ───

KIGO_DATA_PATH = Path(__file__).parent / "data" / "kigo.json"

# 表記 → season の flat dict
_SEASON_MAP = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬", "new_year": "新年",
}


def _load_kigo_dict() -> dict[str, str]:
    raw = json.loads(KIGO_DATA_PATH.read_text(encoding="utf-8"))
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

    pykakasi は現代漢字辞書ベースなので、古典読みや一字多音 (例: 日=ひ/にち) で
    モデル提供の読みと食い違うことがある。そこで:
    - pykakasi == model 両方とも違う → critical (-10): 本物の拍数違反 (gaming も含む)
    - pykakasi だけ違って model 提供読みは正しい → minor (-3): 古典読み等の疑い
    """
    expected = [5, 7, 5, 7, 7]
    out: list[Violation] = []
    for i, line in enumerate(t.lines):
        canonical = tanka.kanji_to_hira(line.body)
        actual = tanka.count_moras(canonical)
        if actual == expected[i]:
            continue
        model_count = tanka.count_moras(line.reading)
        if model_count == expected[i]:
            # モデル提供の読みは正しい拍数。pykakasi の辞書違いの可能性が高い。
            out.append(_violation(
                "mora_count_disputed", "minor", 3,
                f"{i+1}句目「{line.body}」は pykakasi 計算で {actual} 拍 ({canonical}) だが、"
                f"モデル提供の読み「{line.reading}」では {expected[i]} 拍。"
                f"古典読み等で判定が分かれた可能性があります。読みを再確認してください。"
            ))
        else:
            note = ""
            if model_count != actual:
                note = f"（モデル提供読み「{line.reading}」は {model_count} 拍と主張）"
            out.append(_violation(
                "mora_count", "critical", 10,
                f"{i+1}句目「{line.body}」は実際は {actual} 拍 (正規読み: {canonical})。"
                f"{expected[i]} 拍に整えてください。{note}"
            ))
    return out


def _rule_kigo_present(t: Tanka) -> list[Violation]:
    """宣言された季語が短歌本体 (5 句の連結) に登場しているか。"""
    body_text = "".join(line.body for line in t.lines)
    if t.kigo not in body_text:
        return [_violation(
            "kigo_present", "critical", 25,
            f"宣言された季語「{t.kigo}」が短歌本文に現れません。本文か kigo フィールドを修正してください。"
        )]
    return []


def _rule_kigo_unique(t: Tanka) -> list[Violation]:
    """季語が一首中に 1 回だけ現れているか。複数なら違反。"""
    body_text = "".join(line.body for line in t.lines)
    n = body_text.count(t.kigo)
    if n > 1:
        return [_violation(
            "kigo_unique", "major", 15,
            f"季語「{t.kigo}」が短歌内で {n} 回現れています。"
            f"一句一季語の原則に従い、{n - 1} 箇所を別の語に置換してください。"
        )]
    return []


def _rule_kigo_in_dictionary(t: Tanka) -> list[Violation]:
    """宣言された季語が手元の歳時記辞書に登録されているか (確からしさのチェック)。
    辞書は限定的なので不在 = 必ずしも誤りではないが、レビューを促す軽い警告にする。"""
    if t.kigo not in KIGO_DICT:
        return [_violation(
            "kigo_in_dictionary", "minor", 5,
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
            "season_consistent", "major", 20,
            f"宣言された季節「{t.season}」と季語「{t.kigo}」(辞書記載: {expected_season}) が一致しません。"
        )]
    return []


def _rule_no_other_kigo(t: Tanka) -> list[Violation]:
    """本体に、宣言された季語以外の (辞書登録された) 季語が現れていないか。
    特に異なる季の季語が混在 (季違い) していないかを検出する。"""
    body_text = "".join(line.body for line in t.lines)
    found = find_kigo_in_text(body_text)
    out: list[Violation] = []
    seen_other: dict[str, str] = {}  # kigo -> season
    for kigo, season in found:
        if kigo == t.kigo:
            continue
        seen_other[kigo] = season

    if not seen_other:
        return out

    # 季違い (declared と違う季の他季語) は major、同季の他季語は minor
    for kigo, season in seen_other.items():
        if season != t.season:
            out.append(_violation(
                "no_other_kigo", "major", 15,
                f"宣言外の季語「{kigo}」({season}) が本文に含まれており、"
                f"宣言季「{t.season}」と異なる季違いです。"
            ))
        else:
            out.append(_violation(
                "no_other_kigo", "minor", 5,
                f"宣言外の同季季語「{kigo}」({season}) が本文に含まれています。"
                f"季語が複数あると焦点がぼやけるため、いずれかに整理することを推奨します。"
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
                    "repeated_word", "minor", 3,
                    f"「{s}」が複数の句に登場しています。表現の重複を避けると引き締まります。"
                ))
                return out  # 一件報告すれば十分
    return out


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
]


# ─── 評価本体 ───

def evaluate(
    t: Tanka,
    *,
    expected_season: str | None = None,
    expected_kigo: str | None = None,
) -> ValidationResult:
    """通常ルール + (任意で) Plan 由来の期待値との整合チェック。

    expected_season / expected_kigo を渡すと、それと一致しない場合に大きな違反として
    score を下げる。Plan ステップ → Compose ステップの間で季節や季語がすり替わる
    現象 (8B モデルにありがち) を強制矯正するためのもの。"""
    violations: list[Violation] = []
    for rule in RULES:
        violations.extend(rule(t))

    if expected_season and t.season != expected_season:
        violations.append(_violation(
            "season_matches_plan", "critical", 30,
            f"構想で決めた季節「{expected_season}」と出力の season「{t.season}」が一致しません。"
            f"構想を勝手に書き換えず、季節「{expected_season}」の歌にしてください。"
        ))
    if expected_kigo and t.kigo != expected_kigo:
        violations.append(_violation(
            "kigo_matches_plan", "major", 20,
            f"構想で決めた季語「{expected_kigo}」と出力の kigo「{t.kigo}」が一致しません。"
            f"構想で選んだ季語をそのまま使ってください。"
        ))

    score = max(0, 100 - sum(v.weight for v in violations))
    return ValidationResult(
        score=score,
        violations=violations,
        passed=score >= PASS_THRESHOLD,
    )


# ─── JSON パース (LLM 出力からの取り出し) ───

_FENCED_RE = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)


def parse_tanka_json(text: str) -> Tanka | tuple[None, str]:
    """LLM の生出力から JSON を取り出して Tanka に変換する。
    成功なら Tanka、失敗なら (None, error_message) を返す。
    code fence や前後の散文に robust。"""
    text = text.strip()
    # fenced code block 除去
    text = _FENCED_RE.sub("", text)
    text = re.sub(r"\n?```\s*$", "", text)

    # 最初の { と最後の } を探す
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "JSON オブジェクトが見つかりません ({…} を出力してください)"

    payload = text[start:end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        return None, f"JSON パース失敗: {e.msg} (位置 {e.pos})"
    try:
        return Tanka.model_validate(data)
    except ValidationError as e:
        # ユーザーフレンドリな短いメッセージを作る
        first_error = e.errors()[0]
        loc = ".".join(str(x) for x in first_error.get("loc", []))
        return None, f"JSON スキーマ違反 ({loc}): {first_error.get('msg', 'unknown')}"


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
    "no_other_kigo": "宣言した季と異なる季の季語を本文に含めない (季違い回避)",
    "kigo_present": "kigo フィールドで宣言した語を、必ず本文 lines のどこかに登場させる",
    "season_consistent": "宣言する季節は、選んだ季語の本来の季と一致させる",
    "season_matches_plan": "構想ステップで決めた季節を勝手に変更しない",
    "kigo_matches_plan": "構想ステップで決めた季語をそのまま使う",
    "kigo_in_dictionary": "なるべく一般的に通用する季語を選ぶ",
    "mora_count": "拍数 5-7-5-7-7 を厳守する。漢字の現代読みでも数えられるようにする",
    "mora_count_disputed": "古典読みに頼る句では、現代読みでも拍数が崩れないよう調整するか、より平易な表記にする",
    "repeated_word": "目立つ語の重複を避け、表現を引き締める",
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


def format_long_term_failures(failures: list[dict]) -> str:
    """過去の失敗群を、compose プロンプト先頭に挿入する anti-example ブロックに整形。

    failures は db.recent_failures(...) の戻り値 (list[dict]) を想定。
    各 entry は {theme, parsed, violations, score, ts, ...} を含む。"""
    if not failures:
        return ""
    lines = [
        "",
        "【長期失敗記憶 — 過去にあなたがやらかした違反パターン。同じことを繰り返さないこと】",
    ]
    for i, f in enumerate(failures, 1):
        theme = f.get("theme", "?")
        score = f.get("score", "?")
        lesson = _lesson_for_violations(f.get("violations") or [])
        kigo_note = ""
        parsed = f.get("parsed") or {}
        if parsed.get("kigo"):
            kigo_note = f" (宣言季語「{parsed.get('kigo')}」)"
        lines.append(f"  失敗 {i}: お題「{theme}」{kigo_note} で score={score} → 教訓: {lesson}")
    return "\n".join(lines) + "\n"


_SEASON_RE = re.compile(r"季節[:：]\s*(春|夏|秋|冬|新年|雑)")
# 季語は (空白/改行) までの 1 トークンとして抽出。括弧や読み仮名注釈は捨てる。
_KIGO_RE = re.compile(r"季語[:：]\s*([^\s\n、。()（）]+)")


def extract_season_from_plan(plan_text: str) -> str | None:
    """plan ステップの出力から季節文字を抽出する。失敗したら None。"""
    if not plan_text:
        return None
    m = _SEASON_RE.search(plan_text)
    return m.group(1) if m else None


def extract_kigo_from_plan(plan_text: str) -> str | None:
    """plan ステップの出力から季語を抽出する。失敗したら None。"""
    if not plan_text:
        return None
    m = _KIGO_RE.search(plan_text)
    return m.group(1) if m else None
