"""rag.py の単体テスト。コーパス読み込み + 構造的検索の検証 (LM Studio 不要)。"""

from __future__ import annotations

import rag


def test_corpus_loaded():
    assert len(rag.CORPUS) > 20
    # 各 poem に必須フィールドがある
    for p in rag.CORPUS:
        assert p.get("text")
        assert p.get("author")
        assert "season" in p
        assert isinstance(p.get("kigo"), list)


def test_retrieve_by_exact_kigo():
    # 「花」の季語で引くと、花を詠んだ歌が返る
    poems = rag.retrieve("春", "花", limit=2)
    assert len(poems) >= 1
    assert all("花" in (p["kigo"]) for p in poems[:1])  # 先頭は kigo exact


def test_retrieve_kigo_priority_over_season():
    # 季語一致が季節一致より優先される
    poems = rag.retrieve("秋", "紅葉", limit=1)
    assert len(poems) == 1
    assert "紅葉" in poems[0]["kigo"]


def test_retrieve_season_fallback():
    # 辞書にない季語でも、同季の歌が fallback で返る
    poems = rag.retrieve("冬", "存在しない季語", limit=2)
    assert len(poems) >= 1
    assert all(p["season"] == "冬" for p in poems)


def test_retrieve_zatsu_explicit():
    # season が明示的に "雑" なら雑カテゴリ (恋・追憶) が返る
    poems = rag.retrieve("雑", None, limit=2)
    assert len(poems) >= 1
    assert all(p["season"] == "雑" for p in poems)


def test_retrieve_none_does_not_return_zatsu():
    # 回帰テスト: Plan 抽出失敗 (season=None, kigo=None) のとき、
    # 無関係な雑歌 (恋歌) を返してはいけない → 空であるべき
    # (冬のお題に恋歌が付くバグの再発防止)
    poems = rag.retrieve(None, None, limit=2)
    assert poems == []


def test_retrieve_winter_gets_winter_not_zatsu():
    # 冬+雪 はちゃんと冬の歌を返す (雑歌ではない)
    poems = rag.retrieve("冬", "雪", limit=2)
    assert len(poems) >= 1
    assert all(p["season"] != "雑" for p in poems)
    assert "雪" in poems[0]["kigo"]


def test_retrieve_respects_limit():
    poems = rag.retrieve("春", "花", limit=1)
    assert len(poems) == 1


def test_retrieve_no_duplicates():
    poems = rag.retrieve("秋", "秋の夕暮れ", limit=3)
    texts = [p["text"] for p in poems]
    assert len(texts) == len(set(texts))


def test_retrieve_exclude_texts():
    first = rag.retrieve("春", "花", limit=1)[0]
    # 除外すると別の歌が返る
    poems = rag.retrieve("春", "花", limit=1, exclude_texts=(first["text"],))
    assert poems[0]["text"] != first["text"]


def test_format_examples_includes_anti_plagiarism():
    poems = rag.retrieve("春", "花", limit=2)
    block = rag.format_examples(poems)
    assert "剽窃" in block  # 模倣防止の注意書き
    assert "古典の名歌" in block
    # 作者・出典が含まれる
    assert any(p["author"] in block for p in poems)


def test_format_examples_empty():
    assert rag.format_examples([]) == ""


# ── metadata-first 整形 (春アトラクター対策: 表層トークンを枠で中和する) ──

def test_metadata_first_has_structured_frame():
    poems = rag.retrieve("夏", "蛍", limit=2)  # exact miss → 夏 fallback (夏の夜は, 春過ぎて)
    block = rag._format_metadata_first(poems)
    assert "季=" in block and "季語=" in block   # メタデータが主役の枠
    assert "剽窃" in block                        # 模倣防止は維持
    assert any(p["author"] in block for p in poems)


def test_metadata_first_disarms_off_season_token():
    # 「春過ぎて…」(season=夏) は本文に「春」を含む → 無効化注釈が付くこと
    haru = next(p for p in rag.CORPUS if p["text"].startswith("春過ぎて"))
    block = rag._format_metadata_first([haru])
    assert "夏の歌" in block      # 「これは夏の歌」と明示
    assert "「春」" in block       # 春トークンを名指しで中和
    assert "引っ張られず" in block


def test_metadata_first_no_disarm_when_clean():
    # 「夏の夜は…」(season=夏) は本文に異季の語が無い → 無効化注釈は付かない
    natsu = next(p for p in rag.CORPUS if p["text"].startswith("夏の夜は"))
    block = rag._format_metadata_first([natsu])
    assert "引っ張られず" not in block


def test_format_examples_dispatch_legacy_vs_meta_differ():
    # 同じ歌でも legacy と metadata-first は異なる整形になる (A/B が成立する)
    poems = rag.retrieve("夏", "蛍", limit=2)
    assert rag._format_examples_legacy(poems) != rag._format_metadata_first(poems)
