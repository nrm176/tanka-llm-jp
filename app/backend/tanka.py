"""短歌生成パイプラインのオーケストレーション。

Plan → Compose(JSON) → (self-critique) → Validate → Refine* のループを組み立て、
各イベントを辞書として yield する (SSE 化は main.py 側)。

責務分離:
- LLM 通信        → llm.py
- プロンプト        → prompts.py
- 読み/拍数        → reading.py
- 設定 (env)       → config.py
- 検証/スコア       → validator.py (遅延 import)
- 長期失敗記憶      → db.py (遅延 import)
- 古典作例 RAG     → rag.py (遅延 import)
ここはそれらを束ねて「いつ何を呼ぶか」だけを持つ。

互換のため LM_STUDIO_URL / MODEL / client / split_harmony / chat_stream を re-export する
(main.py / tasks.py が tanka.* で参照しているため)。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import config
import llm
import prompts
from reading import count_moras, kanji_to_hira

log = logging.getLogger("tanka")

# ── 後方互換の re-export (既存の呼び出し側が tanka.X で参照している) ──
LM_STUDIO_URL = llm.LM_STUDIO_URL
MODEL = llm.MODEL
client = llm.client
split_harmony = llm.split_harmony
stream_completion = llm.stream_completion


# ────────────────────────── ストリーミングのフェーズ実行 ──────────────────────────

async def _run_llm_phase(phase: str, messages: list[dict], *, attempt: int | None = None
                         ) -> AsyncIterator[dict[str, Any]]:
    """1 回の LLM 呼び出しを 1 フェーズとして実行する共通ジェネレータ。
    phase_start → chunk* → phase_end を yield する。最終テキストは phase_end.text に載る
    (呼び出し側は再 yield しつつ phase_end を覗いて結果を取得する)。
    raw は thinking 込みの全文。永続化用 (tasks.py が拾い、SSE へは流さない)。"""
    extra = {"attempt": attempt} if attempt is not None else {}
    yield {"type": "phase_start", "phase": phase, **extra}
    raw = ""
    async for delta in llm.stream_completion(messages):
        raw += delta
        yield {"type": "chunk", "phase": phase, "text": delta, **extra}
    _, text = llm.split_harmony(raw)
    yield {"type": "phase_end", "phase": phase, "text": text, "raw": raw, **extra}


# ────────────────────────── 副作用ヘルパー (イベントを出さない) ──────────────────────────

def _load_long_term_failures(db, season_hint: str | None) -> list[dict]:
    """plan の季節に一致する失敗を優先取得。なければグローバル直近。失敗しても空で続行。"""
    try:
        failures: list[dict] = []
        if season_hint:
            failures = db.recent_failures(limit=3, season=season_hint)
        if not failures:
            failures = db.recent_failures(limit=3)
        if failures:
            log.info("injecting %d long-term failure example(s) (season=%s)", len(failures), season_hint)
        return failures
    except Exception as e:
        log.warning("recent_failures lookup failed: %s", e)
        return []


def _build_rag(rag, validator, theme: str, plan: str,
               season_hint: str | None, kigo_hint: str | None) -> tuple[str, list[dict]]:
    """RAG で古典作例を取得し (整形済ブロック, 作例リスト) を返す。
    Plan 抽出が失敗していたら、お題+plan を季語辞書でスキャンして hint を回収する。"""
    if not rag.RAG_ENABLED:
        return "", []
    try:
        rag_season, rag_kigo = season_hint, kigo_hint
        if not rag_kigo or not rag_season:
            found = validator.find_kigo_in_text(f"{theme} {plan}")
            if found:
                fk, fs = found[0]
                rag_kigo = rag_kigo or fk
                rag_season = rag_season or fs
        examples = rag.retrieve(rag_season, rag_kigo)
        if examples:
            log.info("RAG: retrieved %d example(s) for season=%s kigo=%s",
                     len(examples), rag_season, rag_kigo)
        return rag.format_examples(examples), examples
    except Exception as e:
        log.warning("RAG retrieval failed: %s", e)
        return "", []


def _rag_event(examples: list[dict]) -> dict[str, Any]:
    return {
        "type": "rag",
        "examples": [
            {"text": p.get("text"), "author": p.get("author"), "source": p.get("source"),
             "kigo": p.get("kigo"), "season": p.get("season")}
            for p in examples
        ],
    }


def _score_one(validator, composition: str, season_hint: str | None, kigo_hint: str | None,
               theme: str) -> dict[str, Any]:
    """1 つの出力を検証して、validation イベントに必要な要素を dict で返す。"""
    parsed = validator.parse_tanka_json(composition)
    if isinstance(parsed, tuple):  # JSON/スキーマ違反 → score 0
        _, err_msg = parsed
        return {
            "tanka_obj": None, "score": 0,
            "errors": [f"スキーマ違反: {err_msg}"], "warnings": [],
            "violations": [{"rule": "schema_invalid", "severity": "critical", "weight": 100, "message": err_msg}],
            "critique": validator.format_schema_critique(err_msg),
            "failure_summary": f"score=0, schema_invalid: {err_msg}",
        }
    result = validator.evaluate(parsed, expected_season=season_hint, expected_kigo=kigo_hint, theme=theme)
    return {
        "tanka_obj": parsed, "score": result.score,
        "errors": result.errors, "warnings": result.warnings,
        "violations": [{"rule": v.rule, "severity": v.severity, "weight": v.weight, "message": v.message}
                       for v in result.violations],
        "critique": validator.format_critique(result),
        "failure_summary": validator.format_failure_summary(result),
    }


def _complete_event(plan: str, tanka_obj, score: int, fallback_text: str) -> dict[str, Any]:
    if tanka_obj is not None:
        return {
            "type": "complete",
            "tanka": "\n".join(line.body for line in tanka_obj.lines),
            "plan": plan,
            "moras": [count_moras(kanji_to_hira(line.body)) for line in tanka_obj.lines],
            "kigo": tanka_obj.kigo, "season": tanka_obj.season,
            "image": tanka_obj.image, "emotion": tanka_obj.emotion,
            "score": score,
        }
    # スキーマすら通らなかった: 生テキストをベストエフォートで返す
    return {
        "type": "complete", "tanka": fallback_text, "plan": plan, "moras": [],
        "kigo": None, "season": None, "image": None, "emotion": None, "score": 0,
    }


# ────────────────────────── パイプライン本体 ──────────────────────────

async def generate_tanka_pipeline(theme: str, max_refines: int | None = None
                                  ) -> AsyncIterator[dict[str, Any]]:
    """短歌生成パイプライン。改善が続く限り refine し、全 attempt の最高 score を最終結果に採用する。"""
    import db          # 循環 import 回避のため遅延
    import rag
    import validator

    log.info("tanka pipeline start: theme=%s", theme)

    # ── Step 1: Plan ──
    plan = ""
    async for ev in _run_llm_phase("plan", prompts.build_plan_messages(theme)):
        if ev["type"] == "phase_end":
            plan = ev["text"]
        yield ev

    season_hint = validator.extract_season_from_plan(plan)
    kigo_hint = validator.extract_kigo_from_plan(plan)
    log.info("plan extracted: season=%s kigo=%s", season_hint, kigo_hint)

    # ── 注入ブロックの準備 (長期失敗記憶 + RAG) ──
    long_term_block = validator.format_long_term_failures(_load_long_term_failures(db, season_hint))
    rag_block, rag_examples = _build_rag(rag, validator, theme, plan, season_hint, kigo_hint)
    if rag_examples:
        yield _rag_event(rag_examples)

    plan_constraint = prompts.build_plan_constraint(season_hint, kigo_hint)
    failure_history: list[str] = []  # 同一生成内の短期失敗記憶

    def compose_base() -> list[dict]:
        # 固定文脈。self-critique / refine はこれをベースに直近 1 ラウンドだけ足す
        # (context を attempt 数に依存させない = "Context size exceeded" 対策)。
        return prompts.build_compose_messages(
            theme, plan,
            plan_constraint=plan_constraint, rag_block=rag_block,
            long_term_block=long_term_block,
            failure_block=prompts.format_failure_history_block(failure_history),
            season_hint=season_hint, dynamic_fewshot=config.DYNAMIC_FEWSHOT,
        )

    # ── Step 2: Compose ──
    compose_messages = compose_base()
    composition = ""
    async for ev in _run_llm_phase("compose", compose_messages):
        if ev["type"] == "phase_end":
            composition = ev["text"]
        yield ev

    # ── self-critique (Phase 1 B4, 任意。失敗しても初稿で続行) ──
    if config.SELF_CRITIQUE_ENABLED:
        sc_messages = compose_messages + [
            {"role": "assistant", "content": composition},
            {"role": "user", "content": prompts.SELF_CRITIQUE_USER},
        ]
        try:
            async for ev in _run_llm_phase("self_critique", sc_messages):
                if ev["type"] == "phase_end" and ev["text"].strip():
                    composition = ev["text"]
                yield ev
        except Exception as e:
            log.warning("self_critique skipped due to error: %s", e)

    # ── Step 3: Validate & Refine ループ ──
    best_obj = None
    best_score = -1
    final_obj = None
    final_score = 0
    score_history: list[int] = []
    attempt = 0

    while True:
        r = _score_one(validator, composition, season_hint, kigo_hint, theme)
        score = r["score"]
        score_history.append(score)
        if r["tanka_obj"] is not None and score > best_score:
            best_score, best_obj = score, r["tanka_obj"]

        resolved = r["tanka_obj"] is not None and score >= validator.PASS_THRESHOLD
        yield {
            "type": "validation", "attempt": attempt, "score": score,
            "errors": r["errors"], "warnings": r["warnings"], "violations": r["violations"],
            "parsed_tanka": r["tanka_obj"].model_dump() if r["tanka_obj"] else None,
            "raw_output": composition, "resolved": resolved,
        }

        if resolved:
            final_obj, final_score = r["tanka_obj"], score
            break

        # plateau: 直近 PLATEAU_WINDOW で best が更新されなければ打ち切り
        if len(score_history) > config.PLATEAU_WINDOW:
            if max(score_history[-config.PLATEAU_WINDOW:]) <= max(score_history[:-config.PLATEAU_WINDOW]):
                yield {"type": "plateau_reached", "best_score": best_score, "history": score_history}
                final_obj = best_obj if best_obj else r["tanka_obj"]
                final_score = best_score if best_obj else score
                break

        cap = max_refines if max_refines is not None else config.HARD_CAP
        if attempt >= cap:
            yield {"type": "max_refines_reached", "best_score": best_score, "history": score_history}
            final_obj = best_obj if best_obj else r["tanka_obj"]
            final_score = best_score if best_obj else score
            break

        if r["failure_summary"]:
            failure_history.append(r["failure_summary"])

        refine_messages = compose_messages + [
            {"role": "assistant", "content": composition},
            {"role": "user", "content": prompts.build_refine_user(
                r["critique"], prompts.format_failure_history_block(failure_history))},
        ]
        attempt += 1
        try:
            async for ev in _run_llm_phase("refine", refine_messages, attempt=attempt):
                if ev["type"] == "phase_end":
                    composition = ev["text"]
                yield ev
        except Exception as e:
            # コンテキスト超過/LLM エラー → best-so-far にフォールバック (zero-output 防止)
            log.warning("refine attempt %d failed (%s); falling back to best-so-far", attempt, e)
            yield {"type": "llm_error", "phase": "refine", "attempt": attempt,
                   "message": str(e), "recovered": best_obj is not None}
            final_obj = best_obj
            final_score = best_score if best_obj else 0
            break

    yield _complete_event(plan, final_obj, final_score, composition)


# ────────────────────────── 通常チャット ──────────────────────────

async def chat_stream(user_messages: list[dict], mode: str = "normal") -> AsyncIterator[dict[str, Any]]:
    """単発チャットをストリームし chunk と complete を yield する。"""
    system = prompts.TANKA_SYSTEM_PROMPT if mode == "tanka" else prompts.NORMAL_SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}, *user_messages]
    raw = ""
    async for delta in llm.stream_completion(messages):
        raw += delta
        yield {"type": "chunk", "text": delta}
    reasoning, answer = llm.split_harmony(raw)
    yield {"type": "complete", "thinking": reasoning, "answer": answer}
