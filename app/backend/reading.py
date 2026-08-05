"""読み・拍数のドメインヘルパー (葉モジュール、コード依存なし。data/kogo_yomi.json のみ読む)。

漢字→ひらがな変換と拍数計算。モデル提供の読みを信頼せず、漢字本体から独立に算出する。
tanka.py と validator.py の両方がここに依存する (どちらにも依存されない葉)。
以前は tanka.py に置かれ validator が tanka を import する逆流があったが、ここに切り出して解消。

pykakasi (現代辞書) は詩語・古語を誤読する (月→がつ、紅葉→こうよう、日→にち 等。CLAUDE.md §6.3)。
古典コーパス 31 首の実測で 14 句が誤読 flag となったため、エビデンス駆動でキュレートした
override 辞書 (data/kogo_yomi.json) を pykakasi の前段に適用する
(詳細: app/docs/verifier-accuracy-pykakasi-vs-mecab.md §4-1)。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pykakasi import kakasi

_kks = kakasi()

# 拗音などの小書き仮名は前のモーラに吸収されるため拍数に数えない。
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")

KOGO_YOMI_PATH = Path(__file__).parent / "data" / "kogo_yomi.json"


def _load_kogo_yomi() -> dict[str, str]:
    # 欠損は fail-loud (validator の kigo.json と同方針)。silent degrade は
    # 拍数判定が黙って劣化し、コーパス既知の偽陽性が静かに復活するため不採用。
    try:
        raw = json.loads(KOGO_YOMI_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RuntimeError(
            f"古語読み辞書が見つかりません: {KOGO_YOMI_PATH}\n"
            "これは正規読み算出に必須のキュレーション IP で、版管理されている必要があります "
            "(.gitignore の carve-out を確認)。"
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"古語読み辞書の JSON が壊れています: {KOGO_YOMI_PATH}: {e}") from e
    return dict(raw.get("overrides", {}))


KOGO_YOMI: dict[str, str] = _load_kogo_yomi()
# 最長一致で先に置換するためのソート済みキー (夕月夜 > 月夜 > 月 の順。花火→花 型の誤爆回避)
_KOGO_SORTED = sorted(KOGO_YOMI.keys(), key=len, reverse=True)


def _apply_kogo_overrides(text: str) -> str:
    """キュレート済み詩語・古語を、pykakasi にかける前にひらがな読みへ置換する。

    各位置で最長一致を試し、マッチしたら読みを出力して表記分だけ進む。
    ひらがなは pykakasi を素通りするため、置換済み部分の読みはそのまま保存される。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        for surf in _KOGO_SORTED:
            if text.startswith(surf, i):
                out.append(KOGO_YOMI[surf])
                i += len(surf)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def kanji_to_hira(text: str) -> str:
    """漢字を含むテキストを全てひらがなに変換する (古語 override → pykakasi の順)。"""
    return "".join(item["hira"] for item in _kks.convert(_apply_kogo_overrides(text)))


def count_moras(kana: str) -> int:
    """ひらがな列の拍数を数える。拗音は前のモーラに吸収、促音/撥音/長音は各1拍。"""
    cleaned = re.sub(r"[\s、。「」・]", "", kana)
    return sum(1 for ch in cleaned if ch not in SMALL_KANA)
