"""strict_disputed の per-request 上書き (#76 の paired A/B 用)。

既定 (None) は厳格 = 捏造読みを救済しない。False は #76 以前の無条件救済で、対照 arm 専用。
パイプライン経由で validator.evaluate まで届くことを、捏造読みの validation で確認する。"""
import asyncio
import json

import db
import rag
import tanka
import validator
from validator import Tanka, TankaLine

# 2 句目「心に灯りや」は pykakasi 8 拍、モデル読み「こころあかりや」(7) は助詞「に」脱落の捏造
_TANKA_JSON = json.dumps({
    "kigo": "蛍", "season": "夏",
    "lines": [
        {"body": "蛍の火", "reading": "ほたるのひ"},
        {"body": "心に灯りや", "reading": "こころあかりや"},
        {"body": "闇の中", "reading": "やみのなか"},
        {"body": "ひとり佇み", "reading": "ひとりたたずみ"},
        {"body": "風を感じぬ", "reading": "かぜをかんじぬ"},
    ],
    "image": "川辺を一匹の蛍が飛ぶ", "emotion": "静かな余韻",
}, ensure_ascii=False)


def _stub_phase(phase, messages, *, attempt=None, model=None, rescue_json=True):
    async def gen():
        yield {"type": "phase_start", "phase": phase}
        text = "季語: 蛍\n季節: 夏\n情景: 川辺\n心情: 余韻" if phase == "plan" else _TANKA_JSON
        yield {"type": "phase_end", "phase": phase, "text": text, "raw": text, "duration_seconds": 0.0}
    return gen()


def _rules_fired(monkeypatch, **kwargs) -> set[str]:
    monkeypatch.setattr(tanka, "_run_llm_phase", _stub_phase)
    monkeypatch.setattr(db, "recent_failures", lambda **k: [])
    monkeypatch.setattr(rag, "RAG_ENABLED", False)

    async def run():
        return [ev async for ev in tanka.generate_tanka_pipeline("夏の川", max_refines=0, model="m",
                                                                 self_critique=False, **kwargs)
                if ev["type"] == "validation"]
    evs = asyncio.run(run())
    return {v["rule"] for v in evs[0]["violations"]}


def test_default_is_strict(monkeypatch):
    fired = _rules_fired(monkeypatch)
    assert "mora_count_disputed" not in fired and "mora_count_off_by_one" in fired


def test_override_false_restores_unconditional_rescue(monkeypatch):
    fired = _rules_fired(monkeypatch, strict_disputed=False)
    assert "mora_count_disputed" in fired and "mora_count_off_by_one" not in fired


def test_evaluate_kwarg_direct():
    t = Tanka(kigo="蛍", season="夏", image="情景", emotion="心情",
              lines=[TankaLine(**l) for l in json.loads(_TANKA_JSON)["lines"]])
    assert "mora_count_disputed" not in {v.rule for v in validator.evaluate(t).violations}
    assert "mora_count_disputed" in {v.rule for v in validator.evaluate(t, strict_disputed=False).violations}
