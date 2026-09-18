"""ルール重みの per-request 上書き (weights) — 重み実験の paired A/B 用。

evaluate → score / passed / critique に反映され、パイプライン経由でも届くこと。未知ルールは main で 422。"""
import asyncio
import json

import pytest
from pydantic import ValidationError

import db
import rag
import tanka
import validator
from validator import Tanka, TankaLine

# 2 句目 +1 (字余り) と 4 句目 +1: 現行重み (3+3) なら 94 で合格、12/12 なら 76 で不合格
_LINES = [
    {"body": "蛍の火", "reading": "ほたるのひ"},
    {"body": "川面に揺れつつ", "reading": "かわもにゆれつつ"},
    {"body": "闇の中", "reading": "やみのなか"},
    {"body": "ひとり佇みて", "reading": "ひとりたたずみて"},
    {"body": "風を感じぬ", "reading": "かぜをかんじぬ"},
]
_TANKA_JSON = json.dumps({"kigo": "蛍", "season": "夏", "lines": _LINES,
                          "image": "川辺を一匹の蛍が飛ぶ", "emotion": "静かな余韻"}, ensure_ascii=False)
_W = {"mora_count_off_by_one": 12, "mora_count_disputed": 12, "mora_count": 25}


def _tanka():
    return Tanka(kigo="蛍", season="夏", image="情景", emotion="心情", lines=[TankaLine(**l) for l in _LINES])


def test_evaluate_applies_overrides_to_score_and_pass():
    base = validator.evaluate(_tanka())
    over = validator.evaluate(_tanka(), weight_overrides=_W)
    off = [v for v in over.violations if v.rule == "mora_count_off_by_one"]
    assert len(off) == 2 and all(v.weight == 12 for v in off)
    assert base.score - over.score == 2 * (12 - 3)
    assert base.passed and not over.passed


def test_overrides_do_not_touch_other_rules():
    over = validator.evaluate(_tanka(), weight_overrides={"repeated_word": 50})
    assert {v.rule for v in over.violations} == {"mora_count_off_by_one"}
    assert all(v.weight == 3 for v in over.violations)


def test_pipeline_threads_weight_overrides(monkeypatch):
    def stub(phase, messages, *, attempt=None, model=None, rescue_json=True):
        async def gen():
            yield {"type": "phase_start", "phase": phase}
            text = "季語: 蛍\n季節: 夏\n情景: 川辺\n心情: 余韻" if phase == "plan" else _TANKA_JSON
            yield {"type": "phase_end", "phase": phase, "text": text, "raw": text, "duration_seconds": 0.0}
        return gen()
    monkeypatch.setattr(tanka, "_run_llm_phase", stub)
    monkeypatch.setattr(db, "recent_failures", lambda **k: [])
    monkeypatch.setattr(rag, "RAG_ENABLED", False)

    async def run(**kw):
        return [ev async for ev in tanka.generate_tanka_pipeline("夏の川", max_refines=0, model="m",
                                                                 self_critique=False, **kw)
                if ev["type"] == "validation"]
    assert asyncio.run(run())[0]["score"] == 94
    assert asyncio.run(run(weight_overrides=_W))[0]["score"] == 76


def test_request_rejects_unknown_rule():
    import main
    with pytest.raises(ValidationError):
        main.TankaRequest(theme="夏", weights={"not_a_rule": 5})
    with pytest.raises(ValidationError):
        main.TankaRequest(theme="夏", weights={"mora_count": 500})
    assert main.TankaRequest(theme="夏", weights=_W).weights == _W
