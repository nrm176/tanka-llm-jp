# 拍数重み実験 — base (3/3/10) vs heavy (12/12/25) の paired A/B (2026-09-18)

> 背景: #76/#77/#78 の後、最終短歌の **76% が 5-7-5-7-7 を外している** (逸脱 113 句/100 首、89 句が ±1)。
> それでも平均 ~90 点・初回合格 63% なのは、`off_by_one` / `disputed` が -3 で合格ライン 80 を割らないから
> (2 句 ±1 + repeated_word でも 91)。1 句 2 拍以上の `mora_count` -10 すら単独では合格する。
> 問い: 拍数逸脱に refine を強制すれば、モデルは直せるのか、直せないならコストだけ増えるのか。

## 0. 事前登録 (実行前に確定 — 後から動かさない)

| 項目 | 値 |
|---|---|
| 変える変数 | per-request `weights` のみ。A = base (上書きなし: off_by_one 3 / disputed 3 / mora_count 10) / B = heavy `{"mora_count_off_by_one": 12, "mora_count_disputed": 12, "mora_count": 25}` |
| heavy の根拠 | 合格ライン 80 に対し **1 句の 字余り (88) は許容、2 句 (76) は不合格、2 拍以上の 1 句 (75) は単独で不合格**。古典の「字余りは一首に一度」を重みで表現。昨夜の attempt 0 に当てると初回合格 63% → 38% (refine 要 37 → 62 首)。D 案 (1 句でも不合格、21/21/25) は refine 87% で高コスト・古典の許容とも矛盾するため不採用 |
| お題 | `eval_themes.json` 50 題、ABBA 交互 |
| **主指標 1** | 最終短歌の **5-7-5-7-7 完全一致率** (pykakasi 基準) の paired Δ (heavy − base)。参考ノイズ: 昨夜の 2 arm を擬似 arm にすると Δ +0.04、**2SE 0.16** |
| **主指標 2** | 最終短歌の**拍数逸脱句数**の paired Δ。参考ノイズ 2SE 0.33 (前回と同じ) |
| 判定 | 各指標 \|Δ\| > 2SE で有意。**両方ノイズ内なら「重みでは直らない」が結論** — その場合 heavy はコストだけ増やすので採用しない |
| 副指標 | 共通重み (base) で再採点した最終スコア / 平均 attempt・所要秒 (コスト) / 初回合格率 / plateau 打ち切り数 / 拍数系違反句数の初回→最終 (refine が実際に減らしたか) |
| 採用条件 | 主指標 1 または 2 が有意に改善 **かつ** 所要秒の増加が +50% 以内 → heavy を既定に (env `TANKA_W_*` の既定値変更、別 PR)。改善が有意でも所要 +50% 超なら保留して報告 |
| 交絡対策 | ABBA / fresh reload / 前半後半チェック。failures はリセットしない (paired で両 arm が同じ記憶を見るため対称。重み変更は lesson 選択 (最大重み優先) に影響するが、記憶は共有なので arm 間の差にはならない。絶対値には影響しうる — 解釈で言及) |
| 事後検証 | arm A の `mora_count_off_by_one` violation の weight が {3}、arm B が {12}。self_critique phase 0/0 |
| 所要 | 昨夜 190 s/生成 (refine 37%)。heavy は refine 62% + 回数増で ~300 s → 50 × 2 ≈ **7〜9 h** |
| コード | branch `feat/mora-weights-ab` (main `2caa996` + per-request weights)。実行中は `.py` を保存しない |

## 1. 実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
lms unload llm-jp-4-8b-thinking 2>/dev/null; lms load llm-jp-4-8b-thinking --context-length 32768
nohup caffeinate -dims ./eval-paired.sh w-base '{}' w-heavy '{"weights":{"mora_count_off_by_one":12,"mora_count_disputed":12,"mora_count":25}}' \
  > results/paired-weights.log 2>&1 &
# 停止: kill $(cat results/eval-paired.pid)
```

## 2. 判定

`uv run python eval/experiments/mora-weights/analyze-weights.py <sid_base> <sid_heavy>`

## 3. 結果 (2026-09-18 13:47 完了、6.6h)

sessions: base `6aac651a4df2c0d6727ef299` / heavy `6aac651a4df2c0d6727ef29a`。results: `20260918-070930-*`。
逸脱句数 Δ −0.42 (2SE 0.29) **有意**、完全一致率 +18 pp (2SE 21) 判定不能、所要 +40.6% (< +50%) → **採用条件を満たす**。
効果は前半 (春〜秋) に偏る (前半 −0.84 / 後半 0.00、冬は base が例外的に当たった)。詳細 `eval/FINDINGS.md §5.9`。
