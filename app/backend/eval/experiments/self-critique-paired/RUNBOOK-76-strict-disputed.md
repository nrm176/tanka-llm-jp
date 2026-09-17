# #76 救済条件の厳格化 — 生成を伴う paired A/B 手順書 (2026-09-17)

> 目的: `mora_count_disputed` の救済を絞ったこと (#77) が **refine ループのモデル挙動** を変えるか。
> 再採点 (FINDINGS §5.8) では点数は不変 (best-of-N 変更 0/100) だったので、残る問いは
> 「critique が『読みを再確認』→『本文を整えろ』に変わると、モデルは本文を直すのか」だけ。
> 設計・ハーネス・pre-flight は self-critique 版 (`RUNBOOK.md`) と同一。ここでは差分だけ書く。

## 0. 事前登録 (実行前に確定 — 後から動かさない)

| 項目 | 値 |
|---|---|
| 問い | 厳格救済 (strict) は無条件救済 (loose) に対して、**最終短歌の拍数逸脱**を減らすか |
| 変える変数 | `strict_disputed` の per-request 上書きのみ。A = loose (`false`、#76 以前) / B = strict (`true`、現行既定) |
| お題 | `eval_themes.json` (50 題、両 arm 同一・同順、ABBA 交互) |
| **主指標** | **paired difference の平均 (n=50)** of **拍数逸脱句数** = 最終短歌 5 句のうち pykakasi 計算が規定拍でない句の数 (救済の有無に依らない、ルール非依存の量。0〜5)。Δ_i = B_i − A_i、負なら strict が改善 |
| 判定基準 | \|mean Δ\| > 2SE(Δ) なら有意。以下なら**判定不能 = 正当な結論**。参考: run 2 の sc-on/sc-off を疑似 arm として `analyze-76.py` に掛けると Δ −0.22、**2SE 0.34** (n=50) — つまり \|Δ\| < 0.35 句程度は偶然で出る |
| 副指標 | 共通尺度 (strict ルール) で再採点した最終スコア / 平均 attempt / 平均所要秒 / 最終短歌に disputed が残る率 / refine 後に「本文が変わった句」の割合 |
| 交絡対策 | ABBA 交互実行 / 実行前 fresh reload / 前半・後半チェック。**failures はリセットしない** (paired、RUNBOOK.md §0 の判断を踏襲。ただし本実験は違反ラベルが変わるため lesson の内容が arm 間で漏れる — 対称に作用するとみなし、結果の解釈で言及する) |
| 事後検証 | arm A (loose) の validations に「採用しません」が **0 件** / arm B に ≥1 件 (上書きが届いた証拠)。両 arm の phases に self_critique が無いこと (既定 OFF) |
| 所要時間 | run 2 実測 5.7h (sc-on 込み)。本 run は両 arm とも sc OFF なので **~4h** 見込み |
| コード | branch `feat/76-strict-disputed-override` @ `7ba7487` (= main `e028145` + per-request フラグ)。実行中は `.py` を保存しない (§8) |

`avg_final_score` の arm 間直接比較は**しない** (A と B で validator の尺度が違う: skill Step 1 特例)。

## 1. 実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
lms unload llm-jp-4-8b-thinking 2>/dev/null; lms load llm-jp-4-8b-thinking --context-length 32768
nohup caffeinate -dims ./eval-paired.sh disputed-loose '{"strict_disputed": false}' disputed-strict '{"strict_disputed": true}' \
  > results/paired-disputed.log 2>&1 &
# 停止: kill $(cat results/eval-paired.pid)
```

## 2. 判定 (主指標は結果 JSON に無いので、セッションから算出)

`experiments/self-critique-paired/analyze-76.py <sidA> <sidB>` — 最終短歌の拍数逸脱句数を pykakasi で再計算し、
paired 統計・副指標・事後検証をまとめて出す (ハーネスの `_session_id` を渡す)。

## 3. 結果 (2026-09-18 03:39 完了、5.4h)

sessions: loose `6aabe8615d2d7cdd7feaa816` / strict `6aabe8615d2d7cdd7feaa817`。results: `20260917-221721-*`。
**主指標 Δ +0.06 句 (2SE 0.33) → 判定不能**。事後検証 OK (loose 0 / strict 12 件の棄却、self_critique 0)。
詳細と結論は `eval/FINDINGS.md §5.8.1`: critique の文言を変えてもモデルは本文を直さない。#77 は指標の正しさで維持。
