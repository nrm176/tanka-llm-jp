"""古典短歌コーパスからの retrieval (RAG)。

8B モデルの知識不足を補うため、Plan で決めた季節・季語に一致する古典の名歌を
取り出し、compose プロンプトに「参考作例」として注入する。

設計方針:
- embedding に依存しない **構造的検索** (季語 exact → 同季 fallback → 雑)。
  LM Studio の embedding モデルの不安定さを避け、決定的でテスト可能にする。
- 副作用なしの純関数。pytest で網羅テストできる。
- 模倣・剽窃の防止はプロンプト側 (tanka.py) で明示する。

外部 API:
- retrieve(season, kigo, limit=2, exclude_texts=()) -> list[dict]
- format_examples(poems) -> str
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("rag")

CORPUS_PATH = Path(__file__).parent / "data" / "classical_tanka.json"

# RAG を有効にするか (Phase 2 の測定基盤で A/B 可能にするため env toggle)
RAG_ENABLED = os.environ.get("TANKA_RAG", "1").strip().lower() in ("1", "true", "yes", "on")
# compose に注入する作例数の既定
RAG_LIMIT = int(os.environ.get("TANKA_RAG_LIMIT", "2") or "2")
# 作例ブロックの整形方式。metadata-first: 季・季語・狙い (メタデータ) を主役にし、歌は「実例」
# として従属させ、本文の異季トークン (例: 夏の歌に出る「春」) には無効化注釈を付ける方式。
# 狙いは「弱い 8B が表層の季語トークンに引っ張られる (春アトラクター) のを枠で中和する」こと。
#
# 【測定結果 2026-06 → 既定 OFF】統制実験 (dynamic_fewshot ON / 「春過ぎて」注入 9/9) で、
#   - 季ドリフト: rag_off=0, rag_legacy=0, rag_meta=0 → RAG アンカーはそもそもドリフトを起こさない
#     (季ドリフトの主因は few-shot #1 で、それは既に解消済)。metadata-first に季の利点なし。
#   - 季語遵守: legacy 7/9 > meta 4/9 → meta は例の季語「夏の夜」を目立たせる分かえってコピーを誘発。
# 効果が無く僅かに悪化したため **既定は OFF (実証済の legacy)**。コードは toggle として保持。
# 詳細: app/backend/eval/results/rag_framing_isolation.json
RAG_METADATA_FIRST = os.environ.get("TANKA_RAG_METADATA_FIRST", "0").strip().lower() in ("1", "true", "yes", "on")

# 季節ラベル語 (本文に出る異季トークンの検出に使う)
_SEASON_WORDS = ("春", "夏", "秋", "冬")


def _load_corpus() -> list[dict]:
    try:
        raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        return raw.get("poems", [])
    except Exception as e:
        log.warning("classical_tanka corpus load failed: %s", e)
        return []


CORPUS: list[dict] = _load_corpus()


def retrieve(
    season: str | None,
    kigo: str | None,
    *,
    limit: int = RAG_LIMIT,
    exclude_texts: tuple[str, ...] = (),
) -> list[dict]:
    """季節・季語に一致する古典作例を優先度順に返す。

    優先度:
      1. 宣言季語を kigo に含む歌 (exact kigo match)
      2. 同じ季節の歌 (season match)
      3. (season が 雑/None の場合) 雑カテゴリの歌
    重複は除外し、最大 limit 件返す。
    """
    if not CORPUS:
        return []

    excluded = set(exclude_texts)
    picked: list[dict] = []
    seen: set[str] = set()

    def _add(poem: dict) -> None:
        text = poem.get("text", "")
        if text and text not in seen and text not in excluded:
            seen.add(text)
            picked.append(poem)

    # 1. 季語 exact match
    if kigo:
        for poem in CORPUS:
            if kigo in (poem.get("kigo") or []):
                _add(poem)
                if len(picked) >= limit:
                    return picked[:limit]

    # 2. 同季 match
    if season and season != "雑":
        for poem in CORPUS:
            if poem.get("season") == season:
                _add(poem)
                if len(picked) >= limit:
                    return picked[:limit]

    # 3. 雑カテゴリ (恋・追憶など) は **season が明示的に "雑" のときだけ** 使う。
    #    season=None (Plan 抽出失敗) のときに雑歌を返すと、冬のお題に恋歌が付くなど
    #    無関係な作例で汚染する (実際に踏んだバグ)。抽出失敗時は「該当なし」で空を返す方が安全。
    if season == "雑" and len(picked) < limit:
        for poem in CORPUS:
            if poem.get("season") == "雑":
                _add(poem)
                if len(picked) >= limit:
                    break

    return picked[:limit]


def format_examples(poems: list[dict]) -> str:
    """retrieve した作例を、compose プロンプトに挿入するブロックに整形する。
    既定は metadata-first (季・季語・狙いを主役にし、歌を実例として従属させ、本文の異季
    トークンには無効化注釈を付ける)。TANKA_RAG_METADATA_FIRST=0 で旧 examples-first に戻す。
    模倣防止の注意書きは両方式とも含める。"""
    if not poems:
        return ""
    return _format_metadata_first(poems) if RAG_METADATA_FIRST else _format_examples_legacy(poems)


def _format_examples_legacy(poems: list[dict]) -> str:
    """旧方式: 歌が主役で、メタデータ (狙い) は後注。A/B の比較対象。"""
    lines = [
        "",
        "【参考: 同じ季語・季節を詠んだ古典の名歌】",
        "（語彙・情景の運び・余韻の作り方を参考にしてよい。ただし表現の丸写し・剽窃は禁止。"
        "あくまで自分の言葉で、与えられたお題の情景を詠むこと。）",
    ]
    for p in poems:
        body = (p.get("text") or "").replace("\n", " / ")
        author = p.get("author", "?")
        source = p.get("source", "")
        note = p.get("note", "")
        lines.append(f"  ・{body}  — {author}（{source}）")
        if note:
            lines.append(f"      ねらい: {note}")
    return "\n".join(lines) + "\n"


def _format_metadata_first(poems: list[dict]) -> str:
    """新方式: メタデータ (季・季語・狙い) を主役にし、歌を「実例」として従属させる。
    本文に poem の季と異なる季のラベル語があれば「これは○の歌、その語に引っ張られるな」と
    無効化注釈を付ける (例: 夏の歌「春過ぎて…」の冒頭『春』を中和)。"""
    lines = [
        "",
        "【参考】古典の名歌に学ぶ（歌の丸写しではなく、季・季語・狙いを手本にする）",
    ]
    for p in poems:
        body = (p.get("text") or "").replace("\n", " ")
        author = p.get("author", "?")
        source = p.get("source", "")
        note = p.get("note", "")
        season = p.get("season", "")
        kigo = "・".join(p.get("kigo") or []) or "—"
        frame = f"  ◆ 季={season} ／ 季語={kigo}"
        if note:
            frame += f" ／ 狙い: {note}"
        lines.append(frame)
        lines.append(f"     本歌: {body}（{author}・{source}）")
        offs = [w for w in _SEASON_WORDS if w != season and w in body]
        if offs:
            ow = "・".join(offs)
            lines.append(
                f"     ※本文の「{ow}」は対比・導入のための語で、これは{season}の歌。"
                f"「{ow}」の景物に引っ張られず、季は構想どおり保つこと。"
            )
    lines.append("（表現の丸写し・剽窃は禁止。季・季語・余韻の作り方だけを参考に、お題を自分の言葉で詠む。）")
    return "\n".join(lines) + "\n"
