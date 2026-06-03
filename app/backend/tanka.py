"""短歌生成パイプライン本体。

main.py (FastAPI) からインポートして使う。CLI 依存なし、純粋な関数とジェネレータの集まり。
パイプラインはイベント辞書を yield するジェネレータとして実装し、SSE 化はエンドポイント側で行う。"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

from openai import AsyncOpenAI, OpenAI
from pykakasi import kakasi

log = logging.getLogger("tanka")

LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1")
MODEL = os.environ.get("LM_STUDIO_MODEL", "llm-jp-4-8b-thinking")

# sync client: ヘルスチェック用 (models.list)
client = OpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")
# async client: 実際のストリーミング用
async_client = AsyncOpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")

_kks = kakasi()

# ── ハーモニーフォーマット（思考トレースの leak）処理 ──
HARMONY_FINAL_MARKER = "<|channel|>final<|message|>"
SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]+\|>")


def split_harmony(content: str) -> tuple[str | None, str]:
    if HARMONY_FINAL_MARKER not in content:
        return None, content.strip()
    reasoning, _, answer = content.partition(HARMONY_FINAL_MARKER)
    reasoning = SPECIAL_TOKEN_RE.sub("", reasoning).strip()
    answer = SPECIAL_TOKEN_RE.sub("", answer).strip()
    return reasoning or None, answer


# ── 拍数計算（モデル提供読みを信頼せず、漢字本体から独立に算出） ──
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
TANKA_LINE_RE = re.compile(r"([^\n()（）]+?)\s*[（(]\s*([぀-ヿー]+)\s*[）)]")


def kanji_to_hira(text: str) -> str:
    return "".join(item["hira"] for item in _kks.convert(text))


def count_moras(kana: str) -> int:
    cleaned = re.sub(r"[\s、。「」・]", "", kana)
    return sum(1 for ch in cleaned if ch not in SMALL_KANA)


def parse_tanka(text: str) -> list[tuple[str, str]] | None:
    matches = TANKA_LINE_RE.findall(text)
    if len(matches) != 5:
        return None
    return [(body.strip(), reading.strip()) for body, reading in matches]


def verify_tanka(parsed: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    expected = [5, 7, 5, 7, 7]
    errors: list[str] = []
    warnings: list[str] = []
    for i, (body, model_reading) in enumerate(parsed):
        canonical_reading = kanji_to_hira(body)
        canonical_count = count_moras(canonical_reading)
        model_count = count_moras(model_reading)

        if canonical_count != expected[i]:
            mismatch_note = ""
            if model_count != canonical_count:
                mismatch_note = f"（モデル提供読み「{model_reading}」は{model_count}拍と主張）"
            errors.append(
                f"{i+1}句目「{body}」は実際は{canonical_count}拍"
                f"（正規読み：{canonical_reading}）。{expected[i]}拍に整えてください。{mismatch_note}"
            )
        elif canonical_count != model_count:
            warnings.append(
                f"{i+1}句目: モデル提供読み「{model_reading}」({model_count}拍) と"
                f"正規読み「{canonical_reading}」({canonical_count}拍) が不一致"
            )
    return errors, warnings


def render_clean_tanka(parsed: list[tuple[str, str]]) -> str:
    return "\n".join(body for body, _ in parsed)


# ── プロンプト類 ──
NORMAL_SYSTEM_PROMPT = (
    "あなたは知的で丁寧な日本語アシスタントです。状況に応じて適切な役割を担ってください。"
)

TANKA_SYSTEM_PROMPT = """あなたは熟練した歌人です。短歌の作法を熟知し、季語と情景を大切にして詠みます。

【短歌の作法】
- 一首には季語を必ず一つだけ用いる（一句一季語の原則）
- 異なる季節の季語を混在させない（季違い・季重なりは厳禁）
- 一首は一つの情景・一つの心情に焦点を絞る
- 形式は五七五七七の三十一拍
- 一首において、宣言した季語は本文中に **ちょうど一回だけ** 出現する（再利用禁止）

【思考の順序（必ずこの順で思考すること）】
1. お題から喚起される一つの季節を選ぶ
2. その季節を象徴する季語を一つだけ選ぶ
3. 季語を中心とした一つの情景を描く
4. その情景に伴う一つの心情を定める
5. 上記を踏まえて五七五七七で詠む

拍数の最終検証はプログラム側で行う。あなたは拍数の試行錯誤に時間を割かず、季語・情景・心情の選定に集中すること。

【作歌時の出力形式】
短歌を詠む際は **必ず JSON 形式** で出力する。説明文・前置き・コードフェンスは付けず、
{ から } までで完結する 1 オブジェクトのみを出す。スキーマは以下:

{
  "kigo": "用いた季語 (本文中にちょうど 1 回出現する文字列)",
  "season": "春 | 夏 | 秋 | 冬 | 新年 | 雑",
  "lines": [
    {"body": "1 句目 (漢字・かな混じり)", "reading": "1 句目の全ひらがな読み"},
    {"body": "2 句目", "reading": "2 句目の読み"},
    {"body": "3 句目", "reading": "3 句目の読み"},
    {"body": "4 句目", "reading": "4 句目の読み"},
    {"body": "5 句目", "reading": "5 句目の読み"}
  ],
  "image": "一文の情景描写",
  "emotion": "一文の心情描写"
}

各句の "reading" は漢字を全てひらがなに展開した正確な読みにする。

【検証の仕組み】
出力後、プログラムが構造・拍数・季語の唯一性・季違い等を機械的に採点する。
100 点満点から減点され、80 点以上で合格。違反があった場合はあなたに critique が返るので、
スコアを上げるよう書き直すこと。同じ違反を繰り返さないこと。

【質問への応答】
過去に詠んだ短歌について質問されたときは、歌人の視点から構造（拍数・句切れ・季語）、用いた言葉、情景、感情の機微を説明する。
ビジネスや実務への応用といった解釈は一切しない。純粋に詩・文学として論じる。

【思考言語】
思考も日本語で行うこと。"""

# Few-shot は四季全てを揃える (季節バランスを取らないと 8B モデルは最初の例の季にひきずられる)。
# 構想セクションは実際の Plan 出力と同じインデントなしフォーマットに揃える。
TANKA_COMPOSE_FEW_SHOT: list[dict] = [
    {"role": "user", "content": (
        "お題: 散る桜\n"
        "構想:\n"
        "季語: 花\n"
        "季節: 春\n"
        "情景: 春のうららかな日に、桜が静かに散ってゆく\n"
        "心情: 花の散り急ぐ様への憐れみと、自然の理への諦観\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "花", "season": "春", '
        '"lines": ['
        '{"body": "ひさかたの", "reading": "ひさかたの"}, '
        '{"body": "光のどけき", "reading": "ひかりのどけき"}, '
        '{"body": "春の日に", "reading": "はるのひに"}, '
        '{"body": "しづ心なく", "reading": "しづこころなく"}, '
        '{"body": "花の散るらむ", "reading": "はなのちるらむ"}'
        '], '
        '"image": "春のうららかな日に、桜が静かに散ってゆく", '
        '"emotion": "花の散り急ぐ様への憐れみと、自然の理への諦観"}'
    )},
    {"role": "user", "content": (
        "お題: 夏の夜\n"
        "構想:\n"
        "季語: 夏の夜\n"
        "季節: 夏\n"
        "情景: 短い夏の夜、まだ宵のうちに明けてしまい、月はどこに宿るのか\n"
        "心情: 過ぎゆく夏の夜への惜別の念\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "夏の夜", "season": "夏", '
        '"lines": ['
        '{"body": "夏の夜は", "reading": "なつのよは"}, '
        '{"body": "まだ宵ながら", "reading": "まだよひながら"}, '
        '{"body": "明けぬるを", "reading": "あけぬるを"}, '
        '{"body": "雲のいづこに", "reading": "くものいづこに"}, '
        '{"body": "月宿るらむ", "reading": "つきやどるらむ"}'
        '], '
        '"image": "短い夏の夜、まだ宵のうちに夜が明けてしまう", '
        '"emotion": "過ぎゆく夏の夜への惜別の念"}'
    )},
    {"role": "user", "content": (
        "お題: 秋風\n"
        "構想:\n"
        "季語: 秋風\n"
        "季節: 秋\n"
        "情景: 目には見えない秋の到来を、風の音から感じ取る瞬間\n"
        "心情: 季節の移ろいへの繊細な驚きと感慨\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "秋風", "season": "秋", '
        '"lines": ['
        '{"body": "秋来ぬと", "reading": "あききぬと"}, '
        '{"body": "目にはさやかに", "reading": "めにはさやかに"}, '
        '{"body": "見えねども", "reading": "みえねども"}, '
        '{"body": "風の音にぞ", "reading": "かぜのおとにぞ"}, '
        '{"body": "おどろかれぬる", "reading": "おどろかれぬる"}'
        '], '
        '"image": "目には見えない秋の到来を、風の音から感じ取る瞬間", '
        '"emotion": "季節の移ろいへの繊細な驚きと感慨"}'
    )},
    {"role": "user", "content": (
        "お題: 冬の寂しさ\n"
        "構想:\n"
        "季語: 冬\n"
        "季節: 冬\n"
        "情景: 山里に冬が深まり、人の気配も草木も消えてゆく\n"
        "心情: 寂寥のなかに見出す静かな受容\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "冬", "season": "冬", '
        '"lines": ['
        '{"body": "山里は", "reading": "やまざとは"}, '
        '{"body": "冬ぞさびしさ", "reading": "ふゆぞさびしさ"}, '
        '{"body": "まさりける", "reading": "まさりける"}, '
        '{"body": "人目も草も", "reading": "ひとめもくさも"}, '
        '{"body": "かれぬと思へば", "reading": "かれぬとおもへば"}'
        '], '
        '"image": "山里に冬が深まり、人の気配も草木も消えてゆく", '
        '"emotion": "寂寥のなかに見出す静かな受容"}'
    )},
]


# ── ストリーミング ヘルパー (async) ──
async def stream_completion(messages: list[dict], temperature: float = 0.3) -> AsyncIterator[str]:
    """LM Studio に投げて、生のテキスト delta を yield する (async)。"""
    stream = await async_client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


# ── パイプライン本体 ──

def _format_failure_history_block(history: list[str]) -> str:
    if not history:
        return ""
    lines = [
        "",
        "【過去の試行で起きた違反 (繰り返さないこと)】",
    ]
    for i, summary in enumerate(history, 1):
        lines.append(f"  試行 {i}: {summary}")
    lines.append("")
    return "\n".join(lines)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Plateau 判定の窓: 直近 N attempt で best score が改善しなければ打ち切る
PLATEAU_WINDOW = _env_int("TANKA_PLATEAU_WINDOW", 3)
# 暴走防止の安全上限 (滅多に踏まない想定; LM Studio のコンテキスト枯渇対策)
HARD_CAP = _env_int("TANKA_HARD_CAP", 50)
# Phase 1 B4: Compose 直後の自己点検フェーズを有効にするか (Phase 2 でアブレーション)
SELF_CRITIQUE_ENABLED = _env_bool("TANKA_SELF_CRITIQUE", True)


def _is_context_error(exc: Exception) -> bool:
    """LM Studio / OpenAI 互換 API のコンテキスト超過エラーを判定する。
    エラーメッセージ文字列で広めに拾う (プロバイダにより文言が異なるため)。"""
    msg = str(exc).lower()
    return any(s in msg for s in (
        "context size", "context length", "context window",
        "maximum context", "too many tokens", "exceed",
    ))


async def generate_tanka_pipeline(theme: str, max_refines: int | None = None) -> AsyncIterator[dict[str, Any]]:
    """Plan → Compose (JSON) → Validate → (Refine *) のパイプライン。
    Compose は構造化 JSON で出力させ、validator.py でスコアリング検証する。"""

    # 循環 import 回避のため遅延 import
    import db
    import validator

    log.info("tanka pipeline start: theme=%s", theme)

    # ── Step 1: Plan ──
    yield {"type": "phase_start", "phase": "plan"}
    plan_messages = [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"お題: {theme}\n\n"
            "このお題で詠む短歌の構想を立ててください。短歌本体はまだ書かないでください。\n"
            "以下の形式で構想だけを出力してください:\n"
            "季語: (一つだけ、一首中にちょうど一回出る語にすること)\n"
            "季節: (季語に対応する一つの季節)\n"
            "情景: (一つの場面を一文で)\n"
            "心情: (一つの感情を一文で)"
        )},
    ]
    plan_raw = ""
    async for delta in stream_completion(plan_messages):
        plan_raw += delta
        yield {"type": "chunk", "phase": "plan", "text": delta}
    _, plan = split_harmony(plan_raw)
    yield {"type": "phase_end", "phase": "plan", "text": plan}

    # ── Plan から期待値を抽出 (Compose/Validate の整合チェック用) ──
    season_hint = validator.extract_season_from_plan(plan)
    kigo_hint = validator.extract_kigo_from_plan(plan)
    log.info("plan extracted: season=%s kigo=%s", season_hint, kigo_hint)

    # ── 長期失敗記憶のロード (Phase 6) ──
    # plan から推定した季節と一致する失敗を優先取得。なければグローバルな直近を返す。
    long_term_failures: list[dict] = []
    try:
        if season_hint:
            long_term_failures = db.recent_failures(limit=3, season=season_hint)
        if not long_term_failures:
            long_term_failures = db.recent_failures(limit=3)
    except Exception as e:
        log.warning("recent_failures lookup failed: %s", e)

    if long_term_failures:
        log.info("injecting %d long-term failure example(s) (season_hint=%s)",
                 len(long_term_failures), season_hint)

    # ── Step 2: Compose (JSON) ──
    failure_history: list[str] = []  # 各 attempt の失敗要約 (短期記憶)

    # 構想に従わせるための強調文 (Plan→Compose の slippage 対策)
    plan_constraint = ""
    if season_hint or kigo_hint:
        parts = []
        if kigo_hint:
            parts.append(f'kigo は必ず "{kigo_hint}"')
        if season_hint:
            parts.append(f'season は必ず "{season_hint}"')
        plan_constraint = (
            f"\n【重要・必須】出力 JSON では {' / '.join(parts)} とすること。"
            "構想で決めた季節や季語を勝手に別の季のものに変更してはならない。\n"
        )

    def build_compose_messages() -> list[dict]:
        return [
            {"role": "system", "content": TANKA_SYSTEM_PROMPT},
            *TANKA_COMPOSE_FEW_SHOT,
            {"role": "user", "content": (
                f"お題: {theme}\n"
                f"構想:\n{plan}"
                + plan_constraint
                + validator.format_long_term_failures(long_term_failures)
                + _format_failure_history_block(failure_history)
                + "\nJSON 形式で短歌を出力してください (前後に説明は付けない)。"
                "構想で決めた kigo と season を絶対に変えないこと。"
            )},
        ]

    yield {"type": "phase_start", "phase": "compose"}
    compose_messages = build_compose_messages()
    composition_raw = ""
    async for delta in stream_completion(compose_messages):
        composition_raw += delta
        yield {"type": "chunk", "phase": "compose", "text": delta}
    _, composition = split_harmony(composition_raw)
    yield {"type": "phase_end", "phase": "compose", "text": composition}

    # コンテキスト肥大を防ぐため、ever-growing な会話履歴は持たない。
    # refine/self-critique の各ラウンドは「固定ベース (compose_messages) + 直近 1 ラウンド」で
    # 組み立てる。validator critique が問題点を伝えるので、過去全 attempt を保持する必要はない。
    # これにより context は attempt 数に依存せずほぼ一定に保たれる
    # (theme 1 で観測した "Context size exceeded" 失敗への対策)。

    # ── Phase 1 B4: 自己点検フェーズ ──
    # Compose 直後に LLM 自身に「初稿に問題ないか確認し、必要なら修正版を出せ」と促す。
    # validator が動く前の自己フィルタ。Phase 2 で SELF_CRITIQUE_ENABLED により on/off。
    # コンテキスト超過等で失敗しても致命的でないので、その場合は初稿をそのまま採用する。
    if SELF_CRITIQUE_ENABLED:
        yield {"type": "phase_start", "phase": "self_critique"}
        self_critique_messages = compose_messages + [
            {"role": "assistant", "content": composition},
            {"role": "user", "content": (
                "上記の短歌について自己点検してください。次の観点を確認し、問題があれば修正版を JSON で、"
                "問題なければ同じ JSON を JSON 形式でそのまま出力してください (前後の説明は付けない)。\n\n"
                "1. kigo フィールドで宣言した語が、本文 (lines.body) のどこかにちょうど 1 回だけ出現しているか\n"
                "2. 宣言外の他の季の季語が混在していないか\n"
                "3. 各句の拍数が 5-7-5-7-7 になっているか (拗音は 1 拍、促音/撥音/長音は各 1 拍)\n"
                "4. 構想で決めた kigo と season を維持しているか\n"
                "5. 切れ字や体言止めで余韻が生まれているか"
            )},
        ]
        try:
            composition_raw = ""
            async for delta in stream_completion(self_critique_messages):
                composition_raw += delta
                yield {"type": "chunk", "phase": "self_critique", "text": delta}
            _, composition_revised = split_harmony(composition_raw)
            if composition_revised.strip():
                composition = composition_revised  # 自己修正後を以降の基準にする
            yield {"type": "phase_end", "phase": "self_critique", "text": composition}
        except Exception as e:
            # 自己点検は任意ステップ。失敗しても初稿で続行する。
            log.warning("self_critique skipped due to error: %s", e)
            yield {"type": "phase_end", "phase": "self_critique", "text": composition}

    # ── Step 3: Validate (& Refine loop) ──
    # 改善が見られる限り refine し続ける。max_refines=None なら HARD_CAP まで。
    # 全 attempt の中で最高 score を記録し、合格できなかった場合はそれを最終結果として返す。
    final_tanka_obj: validator.Tanka | None = None
    final_score: int = 0
    best_tanka_obj: validator.Tanka | None = None
    best_score: int = -1
    score_history: list[int] = []

    attempt = 0
    while True:
        parsed = validator.parse_tanka_json(composition)

        if isinstance(parsed, tuple):
            # JSON パース or スキーマ違反 → 致命的扱いでスコア 0
            _, err_msg = parsed
            critique = validator.format_schema_critique(err_msg)
            failure_summary = f"score=0, schema_invalid: {err_msg}"
            errors = [f"スキーマ違反: {err_msg}"]
            warnings: list[str] = []
            score = 0
            violations_payload: list[dict] = [{
                "rule": "schema_invalid",
                "severity": "critical",
                "weight": 100,
                "message": err_msg,
            }]
            tanka_obj = None
        else:
            tanka_obj = parsed
            result = validator.evaluate(
                tanka_obj,
                expected_season=season_hint,
                expected_kigo=kigo_hint,
            )
            score = result.score
            errors = result.errors
            warnings = result.warnings
            violations_payload = [
                {"rule": v.rule, "severity": v.severity, "weight": v.weight, "message": v.message}
                for v in result.violations
            ]
            critique = validator.format_critique(result)
            failure_summary = validator.format_failure_summary(result)

        score_history.append(score)
        if tanka_obj is not None and score > best_score:
            best_score = score
            best_tanka_obj = tanka_obj

        yield {
            "type": "validation",
            "attempt": attempt,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "violations": violations_payload,
            "parsed_tanka": tanka_obj.model_dump() if tanka_obj else None,
            "raw_output": composition,  # tasks.py 側で長期記憶に保存するための原データ
            "resolved": tanka_obj is not None and score >= validator.PASS_THRESHOLD,
        }

        # 合格 → 即終了
        if tanka_obj is not None and score >= validator.PASS_THRESHOLD:
            final_tanka_obj = tanka_obj
            final_score = score
            break

        # Plateau 判定: 直近 PLATEAU_WINDOW 試行で best_score が更新されていなければ打ち切り
        # (十分な履歴があるときだけ評価する)
        if len(score_history) > PLATEAU_WINDOW:
            window_max = max(score_history[-PLATEAU_WINDOW:])
            baseline = max(score_history[:-PLATEAU_WINDOW])
            if window_max <= baseline:
                yield {"type": "plateau_reached", "best_score": best_score, "history": score_history}
                final_tanka_obj = best_tanka_obj if best_tanka_obj else tanka_obj
                final_score = best_score if best_tanka_obj else score
                break

        # 安全上限 (max_refines 明示指定 or HARD_CAP)
        cap = max_refines if max_refines is not None else HARD_CAP
        if attempt >= cap:
            yield {"type": "max_refines_reached", "best_score": best_score}
            final_tanka_obj = best_tanka_obj if best_tanka_obj else tanka_obj
            final_score = best_score if best_tanka_obj else score
            break

        # 短期記憶に違反パターンを記録
        if failure_summary:
            failure_history.append(failure_summary)

        # Refine: 固定ベース + 直近の出力 + critique のみで組み立てる (context 一定)。
        refine_messages = compose_messages + [
            {"role": "assistant", "content": composition},
            {"role": "user", "content": (
                f"{critique}\n\n"
                f"季語の宣言と本文の整合 (kigo フィールドの語が本文中にちょうど 1 回登場すること)、"
                f"季違いの回避、拍数 (5-7-5-7-7) を守ったうえで、JSON 形式で再出力してください。"
                f"{_format_failure_history_block(failure_history)}"
            )},
        ]
        attempt += 1
        yield {"type": "phase_start", "phase": "refine", "attempt": attempt}
        try:
            composition_raw = ""
            async for delta in stream_completion(refine_messages):
                composition_raw += delta
                yield {"type": "chunk", "phase": "refine", "attempt": attempt, "text": delta}
            _, composition = split_harmony(composition_raw)
            yield {"type": "phase_end", "phase": "refine", "attempt": attempt, "text": composition}
        except Exception as e:
            # コンテキスト超過や LLM エラーで refine できない場合は、ここまでの best を採用する。
            # (zero-output 失敗を防ぐ。theme 1 で観測した失敗モードへの保険)
            log.warning("refine attempt %d failed (%s); falling back to best-so-far", attempt, e)
            yield {
                "type": "llm_error",
                "phase": "refine",
                "attempt": attempt,
                "message": str(e),
                "recovered": best_tanka_obj is not None,
            }
            final_tanka_obj = best_tanka_obj
            final_score = best_score if best_tanka_obj else 0
            break

    # ── 完成 ──
    if final_tanka_obj is not None:
        clean = "\n".join(line.body for line in final_tanka_obj.lines)
        moras = [count_moras(kanji_to_hira(line.body)) for line in final_tanka_obj.lines]
        yield {
            "type": "complete",
            "tanka": clean,
            "plan": plan,
            "moras": moras,
            "kigo": final_tanka_obj.kigo,
            "season": final_tanka_obj.season,
            "image": final_tanka_obj.image,
            "emotion": final_tanka_obj.emotion,
            "score": final_score,
        }
    else:
        # スキーマすら通らなかった: 生のテキストだけ返す (ベストエフォート)
        yield {
            "type": "complete",
            "tanka": composition,
            "plan": plan,
            "moras": [],
            "kigo": None,
            "season": None,
            "image": None,
            "emotion": None,
            "score": 0,
        }


# ── 通常チャット用 ストリーマー (async) ──
async def chat_stream(user_messages: list[dict], mode: str = "normal") -> AsyncIterator[dict[str, Any]]:
    """単発のチャット呼び出しをストリームし、chunk と complete を yield する (async)。"""
    system = TANKA_SYSTEM_PROMPT if mode == "tanka" else NORMAL_SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}, *user_messages]

    raw = ""
    async for delta in stream_completion(messages):
        raw += delta
        yield {"type": "chunk", "text": delta}

    reasoning, answer = split_harmony(raw)
    yield {"type": "complete", "thinking": reasoning, "answer": answer}
