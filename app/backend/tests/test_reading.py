"""reading.py の単体テスト — 古語読み override 層 (床固め #2)。

エビデンス: 古典コーパス 31 首スキャンで pykakasi 誤読 14 句 (うち critical 偽陽性 2)、
n=18 プローブで 11/18。override 適用後はプローブ 18/18・コーパス critical 0。
数値の出典は app/docs/verifier-accuracy-pykakasi-vs-mecab.md。

実行:
    cd app/backend && uv run pytest -v tests/test_reading.py
"""

from __future__ import annotations

import json

import pytest

import reading


def hira(text: str) -> str:
    return reading.kanji_to_hira(text)


def moras(text: str) -> int:
    return reading.count_moras(reading.kanji_to_hira(text))


# ─── 辞書のロード ───

def test_kogo_yomi_loaded():
    assert len(reading.KOGO_YOMI) >= 20
    assert reading.KOGO_YOMI["月"] == "つき"
    assert reading.KOGO_YOMI["紅葉"] == "もみじ"


def test_kogo_yomi_missing_fails_loud(tmp_path, monkeypatch):
    """辞書欠損は明示 RuntimeError (validator の kigo.json と同方針の fail-loud)。"""
    monkeypatch.setattr(reading, "KOGO_YOMI_PATH", tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match="古語読み辞書が見つかりません"):
        reading._load_kogo_yomi()


def test_kogo_yomi_corrupt_fails_loud(tmp_path, monkeypatch):
    bad = tmp_path / "kogo_yomi.json"
    bad.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(reading, "KOGO_YOMI_PATH", bad)
    with pytest.raises(RuntimeError, match="JSON が壊れています"):
        reading._load_kogo_yomi()


# ─── override 本体: コーパス/プローブで実証された誤読の修正 ───

def test_tsuki_reads_tsuki_not_gatsu():
    # pykakasi は文脈により 月→がつ (有明の月と→ありあけのがつと)
    assert hira("月やあらぬ") == "つきやあらぬ"
    assert hira("有明の月と") == "ありあけのつきと"


def test_momiji_not_kouyou():
    assert hira("紅葉踏み分け") == "もみじふみわけ"
    assert moras("紅葉の錦") == 7


def test_hi_not_nichi():
    # CLAUDE.md §6.3 の例そのもの (春の日に→はるのにちに だった)
    assert hira("春の日に") == "はるのひに"


def test_classical_words():
    assert hira("かりほの庵の") == "かりほのいおの"      # 庵=いお (いおり でなく)
    assert hira("東風吹かば") == "こちふかば"            # 東風=こち (菅公歌)
    assert hira("名こそ惜しけれ") == "なこそおしけれ"     # 名=な (めい でなく)
    assert hira("香ににほひける") == "かににほひける"     # 香=か (かおり でなく)
    assert hira("わが身世にふる") == "わがみよにふる"     # 身世=みよ (しんせい でなく)
    assert hira("夜半の月かな") == "よわのつきかな"       # 夜半=よわ (やはん でなく)
    assert hira("幣も取りあへず") == "ぬさもとりあへず"   # 幣=ぬさ (へい でなく)
    assert hira("生ふる松") == "おふるまつ"              # 生ふ=おふ (なまふ でなく)


def test_modern_heteronyms():
    assert hira("今日は雨") == "きょうはあめ"            # こんにち でなく
    assert hira("行方も知らず") == "ゆくえもしらず"       # なめがた でなく


# ─── 最長一致: 複合語の保護 (月→つき の単純置換で壊れないこと) ───

def test_longest_match_protects_compounds():
    assert hira("五月雨をあつめ") == "さみだれをあつめ"   # 五つき雨 にならない
    assert hira("神無月") == "かんなづき"
    assert hira("夕月夜") == "ゆうづくよ"                # 夕+月夜 に分割されない
    assert hira("三日月") == "みかづき"
    assert hira("名残の雪") == "なごりのゆき"            # な+残 にならない
    assert hira("香りの中") == "かおりのなか"            # か+り にならない
    assert hira("風香る") == "かぜかおる"                # か+る にならない
    assert hira("日々の暮らし") == "ひびのくらし"
    assert hira("春日の里") == "かすがのさと"            # 春+ひ にならない
    assert hira("明日を待つ") == "あすをまつ"


# ─── 退行防止: 現代語は無変化 ───

def test_modern_text_unchanged():
    assert moras("桜の花びら") == 8
    assert moras("静かな夜に") == 7
    assert moras("小さな声で") == 7


def test_yo_yoru_deliberately_excluded():
    """「夜」は override 対象外 (現代短歌で よ/よる 両読みが正当なため)。

    古典側 (夜=よ) の拍ずれは ±1 で mora_count_disputed / off_by_one が救済する。
    ここに 夜→よ を追加すると現代読みの歌が逆に壊れる。意図的な除外を固定する。"""
    assert "夜" not in reading.KOGO_YOMI
    assert hira("静かな夜に") == "しずかなよるに"  # 現代読みは現状維持


# ─── コーパスレベルの回帰ピン: 名歌 31 首に critical 偽陽性ゼロ ───

def test_classical_corpus_no_critical_false_positives():
    """RAG が模範注入する古典コーパス全句で、正規読みの拍数が規定から ±2 以上
    ずれない (critical 偽陽性ゼロ)。残る ±1 は真の字余り (13句) と 夜 の意図的除外 (3句)
    のみで、off_by_one (minor) として許容される正しい挙動。"""
    poems = json.loads(
        (reading.KOGO_YOMI_PATH.parent / "classical_tanka.json").read_text(encoding="utf-8")
    )["poems"]
    expected = [5, 7, 5, 7, 7]
    critical = []
    flagged = 0
    for p in poems:
        for i, ln in enumerate(p["text"].split("\n")):
            m = moras(ln)
            if m != expected[i]:
                flagged += 1
                if abs(m - expected[i]) >= 2:
                    critical.append((p["author"], ln, m))
    assert critical == [], f"critical 偽陽性が復活: {critical}"
    assert flagged <= 16, f"偽陽性が増加 (期待 ≤16 = 字余り13 + 夜3): {flagged}"
