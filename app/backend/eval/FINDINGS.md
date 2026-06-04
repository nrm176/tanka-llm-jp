# Phase 2 Findings — 測定基盤の構築とそこから得た知見

Phase 2 の目的は「品質を客観測定する基盤を作る」こと。基盤を作った結果、
**測定を始めた途端に複数の知見（と 2 つの実バグ）が surfacing した**。
これは「主観評価では見えないものが、計測で見える」という測定駆動開発の実例。

このドキュメントは研究プロトタイプ (Phase 4-5) に向けた知見ログ。

---

## 1. 確定ベースライン

variant: `baseline-clean`（self-critique ON、両信頼性修正適用後）、8 お題。

| 指標 | 値 |
|---|---|
| 平均最終スコア | **90.0** |
| 総合合格率 | 88% |
| 初回合格率 | 50% |
| 平均 attempt 数 | 1.88 |
| plateau 率 | 0% |
| スコア分布 | [90-100]×4, [80-89]×3, [60-79]×1 |

> ⚠ この「90.0」は **1 サンプル**。次節の variance を踏まえて解釈すること。

結果ファイル: `results/<ts>-baseline-clean.json`

---

## 2. 最重要知見: スコアは確率変数（best-of-N であって反復改善ではない）

同一お題が run をまたいで大きくぶれることを観測：

| お題 | run 横断のスコア | 振れ幅 |
|---|---|---|
| 散りゆく桜 | 70 → 97 → 100 | **30** |
| 通学路の桜並木 | 97 → 76 | 21 |
| 海辺の街を見下ろす坂道 | 95 → 89 | 6 |

### なぜぶれるか（コード上の理由）

`tanka.py` の refine ループは、各 attempt で前の歌を**編集**するのではなく、
critique を見て**ゼロから別の歌を生成**する。さらに最終結果は全 attempt の
**best score を採用**する。つまりこれは：

```
反復改善 (iterative refinement)  ではなく
best-of-N サンプリング           である
```

score 推移は単調降下ではなく **random walk のピーク取り**。
「6 回 refine して 70」と「1 回で 97」は、改善努力の差ではなく **引きの差**。

### 含意

- **avg_final_score の単一 run 比較は危険**。`compare.sh` は警告を出すよう修正済み
- 正しい評価には **同一お題の複数 run で mean±std** を取る必要がある → `eval-repeat.sh`
- 研究記述では "iterative refinement" と書かず "validator-guided **best-of-N sampling**" と書くべき

---

## 3. 測定が見つけた 2 つの信頼性バグ（修正済み）

| # | バグ | 症状 | 修正 | 記録 |
|---|---|---|---|---|
| 1 | refine_history 無限増加 | context 圧迫 → `Context size exceeded` で zero-output | 固定ベース+直近1ラウンド方式 + best-so-far fallback | CLAUDE.md §6.10 |
| 2 | stream silent hang | timeout 無し → stuck connection で 14 分無限ブロック (例外も出ない) | `httpx.Timeout(read=120s)` → fallback | CLAUDE.md §6.11 |

どちらも **手動テストでは踏まなかった**（短い対話では context も積まず、hang も稀）。
固定 50 お題を機械的に回して初めて顕在化した。**測定基盤の費用対効果の証拠**。

---

## 4. 評価アルゴリズムの構造的限界（コード由来）

`validator.evaluate()` は `score = max(0, 100 − Σ weight)` の **加法的減点モデル**。
詳細な考察は本リポジトリの会話ログ参照。要点：

- **報酬項がゼロ** — 欠陥の不在しか測れない。情感・調べ・余韻など「質」は未評価
  （`image`/`emotion` は非空チェックのみ、内容は不問）
- **0 への張り付き** — 違反過多で全部 0 になり勾配を失う（海辺の街が途中 score 0）
- **重み・閾値が無較正の hand-tuned 値** — 「80=良い短歌」の根拠なし
- **`repeated_word` が重複に甘い** — 3文字窓・1件返しのため `光`×3 でも軽微 (-3)
- **violation_frequency は全 attempt 横断** — 最終成果でなく探索過程の分布。難テーマにバイアス

---

## 5. 環境的制約: LM Studio の不安定性（host 側、コードではない）

このホストで LM Studio は sustained load 下で性能が大きく oscillate する：

```
fresh:    ~15s/call (健全)
degraded: ~3.5min/call + 時折 HTTP 400 (context rejection)
idle後:   回復
```

- 自動 eval が大きいプロンプトを連続投入 → KV キャッシュ/context 圧迫 → 劣化
- **コードは堅牢化済み**（両 timeout/fallback で無限ハングは起きない）が、
  劣化中は 1 テーマ 7 分かかるなど **eval が遅く不安定になる**
- 自前の診断 streaming 呼び出しが **load を上乗せして劣化を悪化させた**（教訓: eval 中は probe しない）

### 推奨運用

- 複数 variant を回す A/B 実験は、**モデルを fresh reload 後**、できれば**専用ターミナル**で
- eval 中はバックグラウンドの LM Studio 診断を避ける

---

## 6. Phase 3 への入口（次の実験候補）

| 実験 | ツール | 問い |
|---|---|---|
| self-critique の効果 | `TANKA_SELF_CRITIQUE=0` + `eval.sh` + `compare.sh` | 自己点検フェーズは本当に効くか |
| variance の定量化 | `eval-repeat.sh "<お題>" 5` | best-of-N の N をいくつにすべきか、ノイズ下限は |
| 報酬軸の追加 | (新規 LLM-as-judge) | ルールスコアと「質」の相関は |
| 重みの較正 | 人間評価ラベル + grid search | hand-tuned 重みは妥当か |

**前提**: variance（§2）を踏まえ、どの実験も **ノイズ幅を超える差のみ** を有意と判定すること。
