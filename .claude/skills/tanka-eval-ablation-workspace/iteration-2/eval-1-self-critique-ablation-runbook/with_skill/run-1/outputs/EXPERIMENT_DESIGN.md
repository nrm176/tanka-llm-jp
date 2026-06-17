# Self-Critique 効果測定 — 実験設計書

## 背景

短歌生成パイプラインのステップ 2 (Compose) 直後に、オプショナルな **self-critique フェーズ**がある。
このフェーズは LLM に生成した短歌を自己点検させ、以下の観点を確認させている：

1. 季語が本文にちょうど 1 回だけ出現しているか
2. 宣言外の他季の季語が混在していないか
3. 拍数が 5-7-5-7-7 になっているか
4. plan で決めた季語と季節を維持しているか
5. 切れ字や体言止めで余韻が生まれているか

**問い**: 自己点検フェーズは本当に品質に効いているのか？コストに見合う改善が得られるのか？

---

## 実験設計

### 1. 問い（実験の仮説）

**帰無仮説**: self-critique の有無で最終スコアに有意差がない（観測差はノイズ範囲内）

**対立仮説**: self-critique が有効であれば、平均スコアが統計的に有意に向上する

---

### 2. 変更の種類

環境変数 `TANKA_SELF_CRITIQUE` による制御：

- `ON (1)`: self-critique フェーズを実行。compose 出力を自己点検させ、修正版があれば採用
- `OFF (0)`: self-critique フェーズをスキップ。compose 出力をそのまま validate へ進める

**適用方法**: env 変更は docker-compose.yml の env 変数初期化時に反映されるため、
`restart` では不足。**`docker compose up -d backend` の再実行が必須**。

確認コマンド:
```bash
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

---

### 3. テーマセット（お題セット）

**使用ファイル**: `/Users/nrm176p/GitHub2/LLM/app/backend/eval/eval_themes.json` (50 テーマ)

理由：
- **前回の sc-on/sc-off 実験** (FINDINGS.md §5.5) は n=16 で ±4.8 のノイズ下限では判定不能だった (+4.69 の差)
- 今回は **n=50** で ノイズ下限を **±2.7** に縮小し、より確実な判定を目指す
- 「効果なし」を確認するにも「効果あり」を主張するにも、50 テーマの統計力が必要

**所要時間**: 
- 1 テーマあたり: 30 秒〜2 分（LM Studio 状態による）
- 50 テーマ × 2 arm: 約 **1.5〜2 時間**
- 夜間無人実行を想定

---

### 4. 判定基準（事前登録）

| 比較単位 | ノイズ幅（2SE） | 判定ルール |
|---|---|---|
| 50 テーマ平均 | **±2.7** | 観測差がこれを超えたら「有意」と判定 |

**判定フロー**:

```
観測差 (sc-ON平均 - sc-OFF平均) を測定
  ↓
|差| > 2.7? 
  ├─ YES → その方向で有意と判定（採用/棄却）
  └─ NO  → 「判定不能：観測差はノイズ内」と報告
```

**判定不能も正当な結論** (FINDINGS.md の教訓)。
「データが不足している」と報告することは、無責任に断定するより価値がある。

---

### 5. 交絡対策

#### 5.1 LM Studio sustained-load 劣化

**前回の実験の失敗原因** (FINDINGS.md §5.5):
- blocked A/B (sc-on 全 8 テーマ → sc-off 全 8 テーマ)
- 前半: sc-on 88.1 vs sc-off 83.3
- 後半: sc-on 62.0 vs sc-off 76.3
- 差が前後で逆符号 → 前半は ON が優れていたが、後半の LM Studio 劣化で埋没

**今回の対策**:
- 各 variant は実行前に **LM Studio を fresh reload**
- **blocked A/B（不安定性高）を避け、paired 実行**（各テーマで ON/OFF を交互に実行）
  - テーマ 1: sc-on → テーマ 1: sc-off → テーマ 2: sc-on → ...
  - または テーマ 1-50 を sc-on で走らせ、その直後に sc-off で同一テーマ順

#### 5.2 長期失敗記憶の蓄積（lesson の順序効果）

`failures` コレクションは run をまたいで蓄積し、後で走る variant がその教訓の恩恵を受ける。

**対策**: 各 variant 実行直前に failures を リセット
```bash
curl -X DELETE http://localhost:8001/api/failures
```

**注意**: これで長期失敗記憶が消えるため、**実行前にユーザーに一言確認を取る**。

---

### 6. 実験の構造

```
【Before】
  - health check ✓
  - failures リセット
  - fresh reload

【Variant A: sc-ON】
  - TANKA_SELF_CRITIQUE=1 でコンテナ起動
  - 50 テーマを順序固定で評価
  - 結果: results/<ts>-sc-on.json

【Interim】
  - failures リセット
  - fresh reload

【Variant B: sc-OFF】
  - TANKA_SELF_CRITIQUE=0 でコンテナ起動
  - 同一 50 テーマを同一順序で評価
  - 結果: results/<ts>-sc-off.json

【After】
  - compare.sh で前半/後半分割チェック（劣化検査）
  - 判定基準と照合 → 結論を記録
```

---

## 実行手順

詳細は別紙 `RUNBOOK.md` を参照。

要点：
1. **Step 1**: 前提チェック（health / configured_model）
2. **Step 2**: Variant A (sc-ON) を実行 → `eval.sh sc-on eval_themes.json`
3. **Step 3**: Interim (fresh reload + failures リセット)
4. **Step 4**: Variant B (sc-OFF) を実行 → `eval.sh sc-off eval_themes.json`
5. **Step 5**: 比較と判定 → `compare.sh sc-on sc-off`
6. **Step 6**: 前半/後半の LM Studio 劣化チェック
7. **Step 7**: 結論をログに記録

---

## 期待される結果・解釈ガイド

### ケース 1: sc-ON が有意に高い (差 > 2.7)
→ **self-critique は効果あり。保持推奨**。
付加: 初回合格率の改善度を確認（refine 回数削減か、純粋に品質向上か）。

### ケース 2: sc-OFF が有意に高い (差 < -2.7)
→ **self-critique は有害。無効化推奨**。
考察: 「点検」という指示が実は composition を悪化させているか、単に LLM call の追加が
refine 前のエントロピーを増やしているか。

### ケース 3: 差が ±2.7 以内
→ **判定不能。ノイズ内**。
報告: 「n=50 でもなお判断できない。paired/repeated design による分散削減または
モデル変更をして再測定が必要」と結論付け。

### 前半/後半に大きな乖離が見られた場合
→ **この run は採用不可** (LM Studio 劣化の確実な證拠)。
対応: 手順を見直し、reload/interval を増やして再実行。

---

## 記録と報告

実験完了後、以下を記録する：

1. **数値**:
   - 平均最終スコア (sc-ON / sc-OFF)
   - 初回合格率・plateau 率・attempt 平均
   - 前半/後半スコア (劣化チェック)

2. **判定**:
   - 観測差
   - ノイズ下限との比較 → 採用/棄却/判定不能のいずれか

3. **所見** (あれば):
   - 意外な発見、新しい交絡要因など

4. **追加実験** (必要に応じて):
   - variance の詳細測定
   - モデル依存性の確認
   - rule 別の効果分析

---

## 参考資料

- `CLAUDE.md` §6.13: LM Studio sustained-load 劣化の環境制約
- `FINDINGS.md` §2: スコアは確率変数（ノイズ下限表）
- `FINDINGS.md` §5.5: 前回の sc-on/sc-off 実験（交絡の実例教訓）
- `tanka-eval-ablation` SKILL.md: 実験規律のガイド
