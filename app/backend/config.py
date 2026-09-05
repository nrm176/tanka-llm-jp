"""tanka パイプラインの設定 (環境変数で上書き可能、葉モジュール)。

以前は tanka.py のあちこちに散在し、_env_int_early と _env_int という重複ヘルパーが
あった。ここに集約して単一の env ヘルパー + 全 TANKA_* 定数を一箇所で管理する。

接続系 (LM_STUDIO_URL 等) は llm.py が、ルール重みは validator.py が持つ。
ここはパイプライン挙動 (timeout / refine / self-critique) に絞る。"""

from __future__ import annotations

import os


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ── LLM 接続 ──
LM_STUDIO_URL = env_str("LM_STUDIO_URL", "http://localhost:1234/v1")
MODEL = env_str("LM_STUDIO_MODEL", "llm-jp-4-8b-thinking")

# ── ストリーミング timeout (秒) ──
# read = チャンク間の最大待ち時間。thinking モデルは生成中ずっとトークンを出すので、
# read を超える「無音」はストリームのハングを意味する (timeout 無しで 14 分ハングした事故あり)。
LLM_CONNECT_TIMEOUT = env_int("TANKA_LLM_CONNECT_TIMEOUT", 15)
LLM_READ_TIMEOUT = env_int("TANKA_LLM_READ_TIMEOUT", 120)

# ── refine ループ ──
PLATEAU_WINDOW = env_int("TANKA_PLATEAU_WINDOW", 3)   # 改善が止まったと判断する窓幅
HARD_CAP = env_int("TANKA_HARD_CAP", 50)              # 暴走防止の安全上限

# ── 自己点検フェーズ (Phase 1 B4) ──
SELF_CRITIQUE_ENABLED = env_bool("TANKA_SELF_CRITIQUE", True)

# ── 動的 few-shot (春アトラクター対策) ──
# True: plan の季に一致する few-shot 1 ペアのみを compose に見せ、他季 (特に春の散る桜) の
# 磁石を隠す。横断分析で「四季を全部見せても非春 plan の 37% が春へ regress、写し元は #1 のみ
# (#2-4 は 0 件)」が判明したため既定 ON。A/B は TANKA_DYNAMIC_FEWSHOT=0 で従来 (四季全部) に戻す。
DYNAMIC_FEWSHOT = env_bool("TANKA_DYNAMIC_FEWSHOT", True)

# 生成フェーズ (plan/compose/self_critique/refine) の completion token 上限 (#22)。
# thinking モデルの自己検証暴走 (拍数再計算・存在しない空白の検証ループ) を打ち切り、
# context 圧迫 (§6.10) と latency を抑える。打ち切られた出力は harmony final マーカーを
# 持たないため schema_invalid → 既存の refine / best-so-far が fail-clean に回収する。
# 既定 8192 は「安全弁」位置: A/B (四季 12 題 ×2) で 4096 は正常な長考まで打ち切り、
# 合格率 83%→67% / plateau 率 2 倍 / 所要時間は不変 (打ち切り → 捨て attempt → もう一周) と
# 悪化したため不採用。8192 (≈16-24k chars) は観測分布 (p90=13.7k chars, max 20k+ censored) の
# 検閲された尾 = 真の暴走 (実測 31k chars 超のループ) と context 超過だけを止める。0 で無効。
MAX_COMPLETION_TOKENS = env_int("TANKA_MAX_COMPLETION_TOKENS", 8192)
# 通常チャットの completion 上限 (#54)。詩歌に関する質問は chat でも thinking 暴走を誘発する
# (実測: 和歌の質問で思考 39k 字が止まらず手動キャンセル)。既定は #22 と同じ 8192。0 で無効
CHAT_MAX_COMPLETION_TOKENS = env_int("TANKA_CHAT_MAX_COMPLETION_TOKENS", 8192)
