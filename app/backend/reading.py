"""読み・拍数のドメインヘルパー (葉モジュール、依存なし)。

漢字→ひらがな変換と拍数計算。モデル提供の読みを信頼せず、漢字本体から独立に算出する。
tanka.py と validator.py の両方がここに依存する (どちらにも依存されない葉)。
以前は tanka.py に置かれ validator が tanka を import する逆流があったが、ここに切り出して解消。"""

from __future__ import annotations

import re

from pykakasi import kakasi

_kks = kakasi()

# 拗音などの小書き仮名は前のモーラに吸収されるため拍数に数えない。
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")


def kanji_to_hira(text: str) -> str:
    """漢字を含むテキストを全てひらがなに変換する。"""
    return "".join(item["hira"] for item in _kks.convert(text))


def count_moras(kana: str) -> int:
    """ひらがな列の拍数を数える。拗音は前のモーラに吸収、促音/撥音/長音は各1拍。"""
    cleaned = re.sub(r"[\s、。「」・]", "", kana)
    return sum(1 for ch in cleaned if ch not in SMALL_KANA)
