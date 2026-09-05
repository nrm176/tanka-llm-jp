"""generate_tanka_pipeline の self_critique per-request 上書き (#57) の統合テスト。

_run_llm_phase を stub (LLM 不要)、db.recent_failures / rag を無効化して、パイプラインの
フェーズ構成だけを観測する。paired A/B (eval-paired.sh) はこの上書きでお題ごとに ON/OFF を
交互実行するため、「上書きが phases に反映されること」が測定の前提になる (skill Step 2 の事後検証)。
"""

from __future__ import annotations

import asyncio
import json

import config
import db
import rag
import tanka

_TANKA_JSON = json.dumps({
    "kigo": "蛍", "season": "夏",
    "lines": [
        {"body": "夏の夜に", "reading": "なつのよるに"},
        {"body": "蛍ひとつが", "reading": "ほたるひとつが"},
        {"body": "川辺行く", "reading": "かわべゆく"},
        {"body": "光の跡を", "reading": "ひかりのあとを"},
        {"body": "静かに残す", "reading": "しずかにのこす"},
    ],
    "image": "川辺を一匹の蛍が飛ぶ", "emotion": "静かな余韻",
}, ensure_ascii=False)


def _stub_phase(phase, messages, *, attempt=None, model=None, rescue_json=True):
    async def gen():
        yield {"type": "phase_start", "phase": phase, **({"attempt": attempt} if attempt is not None else {})}
        text = "季語: 蛍\n季節: 夏\n情景: 川辺\n心情: 余韻" if phase == "plan" else _TANKA_JSON
        yield {"type": "phase_end", "phase": phase, "text": text, "raw": text, "duration_seconds": 0.0,
               **({"attempt": attempt} if attempt is not None else {})}
    return gen()


def _phases(monkeypatch, **kwargs) -> list[str]:
    monkeypatch.setattr(tanka, "_run_llm_phase", _stub_phase)
    monkeypatch.setattr(db, "recent_failures", lambda **k: [])
    monkeypatch.setattr(rag, "RAG_ENABLED", False)

    async def run():
        return [ev["phase"] async for ev in tanka.generate_tanka_pipeline("夏の川", max_refines=0, model="m", **kwargs)
                if ev["type"] == "phase_start"]
    return asyncio.run(run())


def test_override_false_skips_self_critique(monkeypatch):
    monkeypatch.setattr(config, "SELF_CRITIQUE_ENABLED", True)  # env は ON でも per-request で OFF にできる
    assert _phases(monkeypatch, self_critique=False) == ["plan", "compose"]


def test_override_true_runs_self_critique(monkeypatch):
    monkeypatch.setattr(config, "SELF_CRITIQUE_ENABLED", False)  # env は OFF でも per-request で ON にできる
    assert _phases(monkeypatch, self_critique=True) == ["plan", "compose", "self_critique"]


def test_no_override_follows_config(monkeypatch):
    # 未指定 (None) は従来どおり env 設定に従う = 既存挙動バイト同一の保証
    monkeypatch.setattr(config, "SELF_CRITIQUE_ENABLED", True)
    assert _phases(monkeypatch) == ["plan", "compose", "self_critique"]
    monkeypatch.setattr(config, "SELF_CRITIQUE_ENABLED", False)
    assert _phases(monkeypatch) == ["plan", "compose"]
