#!/usr/bin/env python3
"""repeated_word 重み A/B (TANKA_W_REPEATED_WORD: 3 vs 12) 専用の事後分析。

読み取り専用 (HTTP GET のみ)。eval.sh の結果 JSON 2 つを受け取り、
各 _session_id のセッションを GET /api/sessions/{sid} で取得して分析する。

なぜ compare.sh だけでは不足か:
1. 重みを変えると score の物差し自体が変わる。最終稿に重複が残った歌は w=12 側で
   一律 -9 低く出るため、生の avg_final_score 比較は同一品質でも「劣化」に見える。
   → 共通スケール (w=3 換算) に補正した平均を出す。_rule_repeated_word は
   1 評価あたり最大 1 件しか報告しない (validator.py L439 で即 return) ので、
   補正は「最終 attempt に repeated_word があれば +(W_after - 3)」の正確な算術。
2. violation_frequency は全 attempt 横断 (探索過程の分布)。知りたいのは
   「最終成果物に重複が残ったか」。tanka.py の best は score > best_score の
   strict 比較 (タイは先勝ち) なので、final_score に最初に到達した attempt を
   最終成果物とみなしてその violations を見る。
3. 永続化された violations には weight が入っている (tanka.py L132)。
   before に 3 以外 / after に 12 以外の repeated_word weight が観測されたら
   env passthrough 事故 (同じものを 2 回測った) として測定を無効化できる。

使い方:
  python3 analyze_final_repeats.py results/<ts>-w-repeated-3.json results/<ts>-w-repeated-12.json
  (オプション)  --backend http://localhost:8001  --before-weight 3  --after-weight 12
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def fetch_session(backend: str, sid: str) -> dict:
    with urllib.request.urlopen(f"{backend}/api/sessions/{sid}", timeout=30) as r:
        return json.load(r)


def final_attempt_of(msg: dict) -> dict | None:
    """final_score に最初に到達した validation entry (= 採用された attempt) を返す。"""
    fs = msg.get("final_score")
    if fs is None:
        return None
    for v in msg.get("validations") or []:
        if v.get("score") == fs:
            return v
    return None


def analyze_variant(path: str, backend: str, declared_weight: int) -> dict:
    res = json.load(open(path))
    sid = res.get("_session_id")
    if not sid:
        sys.exit(f"ERROR: {path} に _session_id がありません")
    sess = fetch_session(backend, sid)
    msgs = [m for m in sess.get("messages", []) if m.get("kind") == "tanka"]

    n_scored = 0
    n_final_repeat = 0
    n_ambiguous = 0          # final_score に到達した attempt が複数 (タイ) だった件数
    n_clamped = 0            # final_score == 0 (max(0,…) クランプで補正が近似になる)
    total_attempts = 0
    repeat_fires_all_attempts = 0
    observed_weights: set[int] = set()
    raw_scores: list[float] = []
    adj_scores: list[float] = []   # w=3 共通スケール換算

    for m in msgs:
        validations = m.get("validations") or []
        total_attempts += len(validations)
        for v in validations:
            for viol in v.get("violations") or []:
                if viol.get("rule") == "repeated_word":
                    repeat_fires_all_attempts += 1
                    if isinstance(viol.get("weight"), int):
                        observed_weights.add(viol["weight"])

        fs = m.get("final_score")
        if not isinstance(fs, (int, float)):
            continue
        n_scored += 1
        raw_scores.append(fs)
        if fs == 0:
            n_clamped += 1

        fin = final_attempt_of(m)
        ties = sum(1 for v in validations if v.get("score") == fs)
        if ties > 1:
            n_ambiguous += 1
        has_repeat = bool(fin) and any(
            viol.get("rule") == "repeated_word" for viol in fin.get("violations") or []
        )
        if has_repeat:
            n_final_repeat += 1
        # 共通スケール (w=3): 発火 1 回 × (declared_weight - 3) を足し戻す
        adj_scores.append(fs + (declared_weight - 3) if has_repeat else fs)

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    h = len(raw_scores) // 2
    return {
        "path": path,
        "variant": res.get("_variant"),
        "themes_file": res.get("_themes_file"),
        "n": res.get("total"),
        "n_scored": n_scored,
        "declared_weight": declared_weight,
        "observed_repeat_weights": sorted(observed_weights) or ["(発火なし)"],
        "final_repeat_rate": round(n_final_repeat / n_scored, 3) if n_scored else None,
        "n_final_repeat": n_final_repeat,
        "repeat_fires_all_attempts": repeat_fires_all_attempts,
        "repeat_per_attempt": round(repeat_fires_all_attempts / total_attempts, 3)
                              if total_attempts else None,
        "total_attempts": total_attempts,
        "avg_final_score_raw": avg(raw_scores),
        "avg_final_score_w3scale": avg(adj_scores),
        "half_raw": (avg(raw_scores[:h]), avg(raw_scores[h:])) if h else (None, None),
        "half_w3scale": (avg(adj_scores[:h]), avg(adj_scores[h:])) if h else (None, None),
        "n_ambiguous_tie": n_ambiguous,
        "n_clamped_zero": n_clamped,
        "metrics_violation_frequency_repeated_word":
            (res.get("violation_frequency") or {}).get("repeated_word", 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", help="w=3 側の結果 JSON (results/<ts>-w-repeated-3.json)")
    ap.add_argument("after", help="w=12 側の結果 JSON (results/<ts>-w-repeated-12.json)")
    ap.add_argument("--backend", default="http://localhost:8001")
    ap.add_argument("--before-weight", type=int, default=3)
    ap.add_argument("--after-weight", type=int, default=12)
    args = ap.parse_args()

    b = analyze_variant(args.before, args.backend, args.before_weight)
    a = analyze_variant(args.after, args.backend, args.after_weight)

    print(f"\nBEFORE: {b['variant']}  (n={b['n']}, {b['path']})")
    print(f"AFTER:  {a['variant']}  (n={a['n']}, {a['path']})")
    print("=" * 76)

    # ─── 0. 測定の有効性ガード ───
    invalid = False
    if b["themes_file"] != a["themes_file"]:
        print("✗ 無効: _themes_file が一致しません (同一お題セットで再測定すること)")
        print(f"   before: {b['themes_file']}\n   after:  {a['themes_file']}")
        invalid = True
    if b["n"] != a["n"]:
        print(f"✗ 警告: n が一致しません (before={b['n']}, after={a['n']})")
        invalid = True
    for side, expect in ((b, args.before_weight), (a, args.after_weight)):
        ow = side["observed_repeat_weights"]
        if ow != ["(発火なし)"] and ow != [expect]:
            print(f"✗ 無効 (passthrough 事故): {side['variant']} の repeated_word 実測 weight = {ow}, "
                  f"期待 = [{expect}]。env が backend に届いていない run が混ざっています。")
            invalid = True
    if not invalid:
        print(f"✓ 有効性ガード通過: themes_file 一致 / n 一致 / "
              f"実測 weight before={b['observed_repeat_weights']} after={a['observed_repeat_weights']}")

    # ─── 1. 効果 (主要評価): 最終成果物の repeated_word 残存率 ───
    print("\n[ 1. 最終成果物の repeated_word 残存率 (これが下がるのが狙い) ]")
    print(f"  before: {b['n_final_repeat']}/{b['n_scored']} = {b['final_repeat_rate']}")
    print(f"  after:  {a['n_final_repeat']}/{a['n_scored']} = {a['final_repeat_rate']}")

    # ─── 2. 品質ガード: 共通スケール比較 ───
    print("\n[ 2. avg_final_score — 生値は物差しが違うので比較禁止、w=3 換算で比較する ]")
    print(f"  raw      : {b['avg_final_score_raw']} -> {a['avg_final_score_raw']}   (参考値。直接比較は無効)")
    print(f"  w=3 換算 : {b['avg_final_score_w3scale']} -> {a['avg_final_score_w3scale']}"
          f"   差 = {round((a['avg_final_score_w3scale'] or 0) - (b['avg_final_score_w3scale'] or 0), 2)}")
    print("  → この差を事前登録したノイズ下限 (n=50: ±2.7 / n=8: ±6.8) と照合すること")

    # ─── 3. コスト: 探索過程での発火と attempt 数 ───
    print("\n[ 3. 探索過程 (全 attempt 横断; 最終成果ではない点に注意) ]")
    print(f"  repeated_word 発火/attempt : {b['repeat_per_attempt']} -> {a['repeat_per_attempt']}")
    print(f"  総 attempt 数              : {b['total_attempts']} -> {a['total_attempts']}")
    print(f"  (metrics の violation_frequency.repeated_word: "
          f"{b['metrics_violation_frequency_repeated_word']} -> {a['metrics_violation_frequency_repeated_word']})")

    # ─── 4. 交絡チェック: 前半/後半 ───
    print("\n[ 4. 前半/後半 (LM Studio 劣化交絡; 符号が変わるなら run を捨てて再測定) ]")
    print(f"  before w=3換算: 前半 {b['half_w3scale'][0]}  後半 {b['half_w3scale'][1]}")
    print(f"  after  w=3換算: 前半 {a['half_w3scale'][0]}  後半 {a['half_w3scale'][1]}")

    # ─── 5. 補正の信頼性メモ ───
    notes = []
    if b["n_ambiguous_tie"] or a["n_ambiguous_tie"]:
        notes.append(f"final_score タイ (採用 attempt の特定が先勝ち推定): "
                     f"before={b['n_ambiguous_tie']}, after={a['n_ambiguous_tie']} 件")
    if b["n_clamped_zero"] or a["n_clamped_zero"]:
        notes.append(f"score=0 クランプ (換算が近似になる): "
                     f"before={b['n_clamped_zero']}, after={a['n_clamped_zero']} 件")
    if notes:
        print("\n[ 5. 補正の信頼性メモ ]")
        for s in notes:
            print(f"  - {s}")
    print()


if __name__ == "__main__":
    main()
