#!/usr/bin/env python3
"""repeated_word 重み A/B (3 vs 12) の公平な事後分析スクリプト。

なぜ compare.sh だけでは不十分か:
  1. 重みを変えると score の意味そのものが変わる (100 - Σweight の加法減点)。
     avg_final_score / pass_rate / score_buckets は arm 間で同じ物差しではない。
  2. /api/metrics の violation_frequency は「全 attempt 横断」の件数。重みを上げると
     refine 圧が増えて attempt 数が増え、件数が機械的に増える (改善していても「悪化」に見える)。

このスクリプトがやること (HTTP は GET のみ / 副作用なし):
  - eval.sh の結果 JSON (results/<ts>-<variant>.json) から _session_id を読む
  - GET /api/sessions/<sid> で全 tanka メッセージの validations を取得
  - 各短歌について「採用された attempt」(score == final_score の attempt) を特定し、
      A) 最終出力に repeated_word 違反が残っている率 (本実験の主要エンドポイント)
      B) 最終出力のルール別違反数 (副作用 = モグラ叩きの検出)
      C) 共通の物差し (コード既定の重み) で再採点した平均スコア
      D) attempt 数 / plateau 率 (コスト)
    を arm 間で並べて表示する。

使い方:
  python3 analyze_repeated_word.py results/<ts>-rw3-baseline.json results/<ts>-rw12.json
  python3 analyze_repeated_word.py <baseline.json> <treatment.json> --backend http://localhost:8001

注意: BASELINE_WEIGHTS は app/backend/validator.py RULE_WEIGHTS の既定値スナップショット
      (2026-06-11 時点)。validator の既定を変えたらここも同期すること。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

# validator.py RULE_WEIGHTS のコード既定値 (env 未設定時)。共通物差しでの再採点に使う。
BASELINE_WEIGHTS: dict[str, int] = {
    "mora_count": 10,
    "kigo_present": 25,
    "season_matches_plan": 30,
    "kigo_unique": 15,
    "season_consistent": 20,
    "kigo_matches_plan": 20,
    "no_other_kigo_cross": 30,
    "no_other_kigo_same": 25,
    "kigo_in_dictionary": 5,
    "repeated_word": 3,          # ← A/B の対象。共通物差しは baseline の 3 で固定
    "mora_count_disputed": 3,
    "mora_count_off_by_one": 3,
    "kireji_absent": 3,
    "theme_time_mismatch": 25,
    "theme_time_uncovered": 5,
    "theme_motif_uncovered": 5,
}
UNKNOWN_RULE_WEIGHT = 5  # validator._w() の fallback と同じ
PASS_THRESHOLD = 80


def fetch_session(backend: str, sid: str) -> dict:
    url = f"{backend}/api/sessions/{sid}"
    with urllib.request.urlopen(url, timeout=30) as r:  # GET のみ
        return json.load(r)


def rescore(violations: list[dict]) -> int:
    """違反の rule 名から、共通 (baseline) 重みで score を再計算する。
    ルール検出ロジック自体は重みに依存しないため、保存された rule 列は arm 間で比較可能。"""
    total = sum(BASELINE_WEIGHTS.get(v.get("rule", ""), UNKNOWN_RULE_WEIGHT) for v in violations)
    return max(0, 100 - total)


def pick_selected_attempt(msg: dict) -> dict | None:
    """採用された attempt (= best score) の validation エントリを返す。
    final_score は全 attempt の best を採用する実装なので、score が一致する最初の
    エントリを「採用された出力」とみなす (同点タイは違反プロファイル比較上ほぼ無害)。"""
    fs = msg.get("final_score")
    validations = msg.get("validations") or []
    if fs is None or not validations:
        return None
    for v in validations:
        if v.get("score") == fs:
            return v
    return None


def analyze(session: dict) -> dict:
    msgs = [m for m in session.get("messages", []) if m.get("kind") == "tanka"]
    n = 0                       # 採点可能な短歌数
    no_result = 0               # final_score 無し (生成失敗等)
    attempts_sum = 0
    plateau = 0
    repeated_in_final = 0
    final_rule_counts: dict[str, int] = {}
    recorded_score_sum = 0
    rescored_selected_sum = 0   # 採用 attempt を共通重みで再採点
    rescored_best_sum = 0       # 共通重みで見た場合の best attempt (選択バイアス除去ビュー)
    rescored_pass = 0           # 共通重み + 共通閾値 80 での合格数

    for m in msgs:
        validations = m.get("validations") or []
        attempts_sum += len(validations)
        if m.get("plateau_reached"):
            plateau += 1
        sel = pick_selected_attempt(m)
        if sel is None:
            no_result += 1
            continue
        n += 1
        recorded_score_sum += m.get("final_score") or 0
        viols = sel.get("violations") or []
        if any(v.get("rule") == "repeated_word" for v in viols):
            repeated_in_final += 1
        for v in viols:
            r = v.get("rule", "unknown")
            final_rule_counts[r] = final_rule_counts.get(r, 0) + 1
        rs = rescore(viols)
        rescored_selected_sum += rs
        if rs >= PASS_THRESHOLD:
            rescored_pass += 1
        rescored_best_sum += max(
            (rescore(v.get("violations") or []) for v in validations), default=rs
        )

    total = len(msgs)
    return {
        "tanka_total": total,
        "scored": n,
        "no_result": no_result,
        "avg_attempts": attempts_sum / total if total else None,
        "plateau_rate": plateau / total if total else None,
        "repeated_word_final_rate": repeated_in_final / n if n else None,
        "repeated_word_final_count": repeated_in_final,
        "final_rule_counts": dict(sorted(final_rule_counts.items(), key=lambda kv: -kv[1])),
        "avg_recorded_final_score": recorded_score_sum / n if n else None,
        "avg_rescored_selected": rescored_selected_sum / n if n else None,
        "avg_rescored_best": rescored_best_sum / n if n else None,
        "rescored_pass_rate": rescored_pass / n if n else None,
    }


def fmt(x, pct=False):
    if x is None:
        return "n/a"
    if pct:
        return f"{x * 100:.1f}%"
    return f"{x:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline_json", help="eval.sh の結果 JSON (rw3-baseline)")
    ap.add_argument("treatment_json", help="eval.sh の結果 JSON (rw12)")
    ap.add_argument("--backend", default="http://localhost:8001")
    args = ap.parse_args()

    arms = []
    for path in (args.baseline_json, args.treatment_json):
        with open(path) as f:
            meta = json.load(f)
        sid = meta.get("_session_id")
        if not sid:
            print(f"ERROR: {path} に _session_id がありません", file=sys.stderr)
            return 1
        arms.append((meta.get("_variant", "?"), analyze(fetch_session(args.backend, sid))))

    (bn, b), (tn, t) = arms
    print(f"\n=== repeated_word weight A/B: {bn} (W=3) vs {tn} (W=12) ===\n")
    rows = [
        ("短歌数 (scored / total)", lambda a: f"{a['scored']}/{a['tanka_total']}", None),
        ("生成失敗 (no final)", lambda a: str(a["no_result"]), None),
        ("◆ 最終出力に repeated_word 残存率", lambda a: fmt(a["repeated_word_final_rate"], pct=True), "low"),
        ("平均 attempt 数 (コスト)", lambda a: fmt(a["avg_attempts"]), "low"),
        ("plateau 率", lambda a: fmt(a["plateau_rate"], pct=True), "low"),
        ("記録上の平均 final_score (物差し不一致・参考値)", lambda a: fmt(a["avg_recorded_final_score"]), None),
        ("◆ 共通重みで再採点した平均 (採用 attempt)", lambda a: fmt(a["avg_rescored_selected"]), "high"),
        ("共通重みでの best attempt 平均", lambda a: fmt(a["avg_rescored_best"]), "high"),
        ("共通重み + 閾値80 での合格率", lambda a: fmt(a["rescored_pass_rate"], pct=True), "high"),
    ]
    for label, get, _ in rows:
        print(f"  {label:42s} {get(b):>10s} -> {get(t):>10s}")

    print("\n[ 最終出力 (採用 attempt) のルール別違反数 — モグラ叩き検出 ]")
    all_rules = sorted(set(b["final_rule_counts"]) | set(t["final_rule_counts"]),
                       key=lambda r: -(b["final_rule_counts"].get(r, 0) + t["final_rule_counts"].get(r, 0)))
    for r in all_rules:
        bv = b["final_rule_counts"].get(r, 0)
        tv = t["final_rule_counts"].get(r, 0)
        mark = "  ← 注意 (悪化)" if tv > bv and r != "repeated_word" else ""
        print(f"  {r:28s} {bv:>4d} -> {tv:>4d}{mark}")

    print("""
[ 読み方 ]
  - ◆ が主要指標。repeated_word 残存率が下がり、共通重み再採点の平均が
    noise 下限 (50 題で ±2.7 / 8 題で ±6.8, FINDINGS.md §2) を超えて落ちて
    いなければ「重み 12 は有効」。
  - 他ルールの最終違反が増えていたら、重複回避のために別の制約を壊している
    (モグラ叩き)。avg_attempts / plateau 率の急増はコスト悪化。
  - 記録上の final_score は arm 間で物差しが違うため直接比較しないこと。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
