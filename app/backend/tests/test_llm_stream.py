"""llm.ReasoningMerger / rescue_json_from_text (#30 分離型 thinking モデル対応) のテスト。

ネットワーク不要の純関数のみを対象とする (llm.py の import はクライアント生成のみで接続しない)。

最重要の不変条件: **reasoning_content が一度も来ないストリーム (llm-jp / gpt-oss 等の
harmony-in-content モデル) では、入力を 1 バイトも変えずに素通しする**。
issue #30 の注意「llm-jp の既存挙動を変えないこと」の根拠はこのテスト群にある。
"""

from __future__ import annotations

import asyncio

import llm
import tanka


# ─── ReasoningMerger: 非分離モデルの素通し (llm-jp 不変条件) ───

def test_content_only_stream_is_passed_through_byte_identical():
    # llm-jp: harmony マーカー込みで content に流れる。マージャは何も足さない
    m = llm.ReasoningMerger()
    deltas = ["思考…", "<|channel|>final<|message|>", '{"kigo":', '"蝉"}']
    out = "".join(m.feed(d, "") for d in deltas)
    assert out == "".join(deltas)  # バイト同一
    assert m.saw_reasoning is False


def test_empty_deltas_produce_nothing():
    m = llm.ReasoningMerger()
    assert m.feed("", "") == ""
    assert m.feed(None, None) == ""
    assert m.saw_reasoning is False and m.saw_content is False


# ─── ReasoningMerger: 分離型モデルの合流 ───

def test_reasoning_then_content_injects_marker_once():
    # Qwen3 系: reasoning が先に流れ、content が後から始まる
    m = llm.ReasoningMerger()
    out = m.feed("", "考え中…")
    out += m.feed("", "まだ考え中…")
    out += m.feed('{"kigo":', "")
    out += m.feed('"蛍"}', "")
    assert out == "考え中…まだ考え中…" + llm.HARMONY_FINAL_MARKER + '{"kigo":"蛍"}'
    assert out.count(llm.HARMONY_FINAL_MARKER) == 1
    assert m.saw_reasoning is True


def test_merged_stream_splits_back_via_split_harmony():
    # 合流結果は既存の split_harmony で (思考, 回答) に正しく分離できること
    m = llm.ReasoningMerger()
    raw = m.feed("", "五七五を検討") + m.feed('{"season": "夏"}', "")
    thinking, answer = llm.split_harmony(raw)
    assert thinking == "五七五を検討"
    assert answer == '{"season": "夏"}'


def test_reasoning_only_stream_yields_reasoning_without_marker():
    # think 不到達型 (#28 の暴走): content が一度も来ない → マーカーは注入されない
    m = llm.ReasoningMerger()
    out = m.feed("", "無限に拍数を数え直す…") + m.feed("", "また数え直す…")
    assert llm.HARMONY_FINAL_MARKER not in out
    assert m.saw_reasoning is True and m.saw_content is False


def test_reasoning_after_content_started_is_dropped():
    # content 開始後の reasoning は回答を汚すため捨てる (実モデルでは未観測の並び)
    m = llm.ReasoningMerger()
    out = m.feed("", "先行思考") + m.feed("回答", "") + m.feed("", "後追い思考")
    assert "後追い思考" not in out
    assert out == "先行思考" + llm.HARMONY_FINAL_MARKER + "回答"


def test_same_chunk_carrying_both_reasoning_and_content():
    # 1 チャンクに reasoning と content が同居しても順序 (思考→マーカー→回答) を保つ
    m = llm.ReasoningMerger()
    out = m.feed("回答", "思考")
    assert out == "思考" + llm.HARMONY_FINAL_MARKER + "回答"


# ─── rescue_json_from_text: reasoning 末尾からの JSON 救済 ───

def test_rescue_finds_json_at_tail():
    text = "…と考えたので、最終的な出力は以下。\n" '{"kigo": "蝉", "season": "夏"}'
    assert llm.rescue_json_from_text(text) == '{"kigo": "蝉", "season": "夏"}'


def test_rescue_prefers_last_valid_json_over_earlier_drafts():
    # 思考中の下書き JSON (前方) ではなく、末尾の完成稿を取る。
    # parse_tanka_json の「最初の { 〜 最後の }」方式ではこのケースを救えない
    # (下書きと完成稿をまたいだスライスになり loads が失敗する)
    text = (
        '下書き: {"kigo": "桜"} は季節が違う。修正して…\n'
        '完成: {"kigo": "蛍", "season": "夏"}'
    )
    assert llm.rescue_json_from_text(text) == '{"kigo": "蛍", "season": "夏"}'


def test_rescue_handles_trailing_prose_after_json():
    text = '{"kigo": "雪"} これで完成だ。'
    assert llm.rescue_json_from_text(text) == '{"kigo": "雪"}'


def test_rescue_handles_nested_objects():
    text = 'result: {"outer": {"inner": [1, 2]}, "ok": true}'
    assert llm.rescue_json_from_text(text) == '{"outer": {"inner": [1, 2]}, "ok": true}'


def test_rescue_skips_invalid_tail_and_finds_earlier_valid():
    # 末尾の { } もどきが invalid でも、その前の valid JSON まで遡る
    text = '{"kigo": "月"} のあとに {壊れた: json}'
    assert llm.rescue_json_from_text(text) == '{"kigo": "月"}'


def test_rescue_returns_none_without_json():
    assert llm.rescue_json_from_text("五七五を数え直す。JSON はまだ書かない。") is None
    assert llm.rescue_json_from_text("") is None


def test_rescue_returns_none_when_only_invalid_json():
    assert llm.rescue_json_from_text("{ここは json ではない}") is None


# ─── _run_llm_phase の救済ゲート (分離型のみ発動、llm-jp は不変) ───

def _stub_stream(deltas, separated):
    async def stub(messages, temperature=0.3, model=None, max_tokens=None, meta=None):
        for d in deltas:
            yield d
        if meta is not None:
            meta["reasoning_separated"] = separated
    return stub


def _drain_phase(**kwargs):
    async def run():
        return [ev async for ev in tanka._run_llm_phase("compose", [], **kwargs)]
    return asyncio.run(run())


def test_phase_rescues_json_when_separated_and_no_content(monkeypatch):
    # 分離型で content が一度も来ない (マーカーなし) → reasoning 末尾の完成 JSON を救済
    deltas = ['下書き {"kigo": "桜"} は違う…', '完成: {"kigo": "蛍", "season": "夏"}']
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=True))
    end = _drain_phase()[-1]
    assert end["type"] == "phase_end"
    assert end["text"] == '{"kigo": "蛍", "season": "夏"}'
    assert end.get("rescued") is True


def test_phase_rescues_json_when_separated_and_content_blank(monkeypatch):
    # 分離型で content が空白のみ (マーカーは注入済み) → reasoning 側から救済
    deltas = ['思考の末尾に {"kigo": "月"}', llm.HARMONY_FINAL_MARKER, "  "]
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=True))
    end = _drain_phase()[-1]
    assert end["text"] == '{"kigo": "月"}'
    assert end.get("rescued") is True


def test_phase_no_rescue_for_non_separated_model(monkeypatch):
    # 非分離ストリームの不変条件: マーカー欠落 (= text が全文) でも一切手を加えない
    deltas = ['思考のみで JSON 下書き {"kigo": "桜"} を含むがマーカー無し']
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=False))
    end = _drain_phase()[-1]
    assert end["text"] == deltas[0]  # 従来どおり全文が text
    assert "rescued" not in end


def test_phase_separated_no_content_no_json_yields_empty_text(monkeypatch):
    # 分離型で content が来ず救済 JSON も無い (think 不到達型 #28) → text は空のまま。
    # 従来の「content 空」の意味論を保存する — text に思考全文を流すと self_critique の
    # 「text が空なら初稿を保持」ガードを思考ガベージが突破し、compose 初稿を失う退行になる
    deltas = ["拍数を数え直す…", "また数え直す…"]
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=True))
    end = _drain_phase()[-1]
    assert end["text"] == ""
    assert end["raw"] == "".join(deltas)  # 思考は raw に残り診断可能
    assert "rescued" not in end


def test_phase_no_rescue_when_content_present(monkeypatch):
    # 分離型でも content が正常に来ていれば救済しない (content が常に優先)
    deltas = ['思考 {"kigo": "桜"}', llm.HARMONY_FINAL_MARKER, '{"kigo": "蛍"}']
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=True))
    end = _drain_phase()[-1]
    assert end["text"] == '{"kigo": "蛍"}'
    assert "rescued" not in end


def test_phase_rescue_json_false_disables(monkeypatch):
    # plan (自由文) は rescue_json=False: JSON 救済はせず、content 空の意味論 (text="") は保存
    deltas = ['思考のみ {"kigo": "蛍"}']
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(deltas, separated=True))
    end = _drain_phase(rescue_json=False)[-1]
    assert end["text"] == ""
    assert "rescued" not in end
