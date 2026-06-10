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
    歌を主役に提示し、狙い (note) を後注として添える。模倣防止の注意書きも含める。"""
    if not poems:
        return ""
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
