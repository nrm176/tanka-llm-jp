# repeated_word 重み A/B 実験セットアップ

## 概要

このディレクトリには、validator ルール `repeated_word` の重み値を **3 → 12** に変更した場合の
効果測定 (A/B) を行うための完全な準備物が含まれています。

**実際の eval 実行と docker 操作は未実施** — セットアップと手順書のみを提供します。

---

## ファイル構成

```
outputs/
├── README.md                      ← このファイル
├── docker-compose-diff.patch      ← docker-compose.yml の差分 (環境変数追加)
├── experiment-design-summary.txt  ← 実験設計のサマリー (決定事項)
├── EVAL_RUNBOOK.md                ← 詳細な実行手順書 (Step 0～5)
└── run-experiment.sh              ← 自動化スクリプト (オプション)
```

---

## クイックスタート

### 1️⃣ 前提条件の確認

```bash
# backend と LM Studio の状態確認
curl -s http://localhost:8001/api/health | python3 -m json.tool

# 出力に以下が含まれることを確認:
# - "status": "ok"
# - "lm_studio_ok": true
# - "model_loaded": true
# - "configured_model": "llm-jp-4-8b-thinking"
```

### 2️⃣ docker-compose.yml を編集

`docker-compose-diff.patch` に示す行をリポジトリの `app/docker-compose.yml` に追加:

```yaml
environment:
  ...
  TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}  # ← 追加
```

**なぜ必要か**: `TANKA_W_REPEATED_WORD` のような重み系環境変数は現在
docker-compose.yml に列挙されていないため、ホストで export しても backend に届かない
(**passthrough 罠** — SKILL.md §2 参照)。

### 3️⃣ 実験を実行

**オプション A: 自動スクリプト** (推奨)

```bash
bash run-experiment.sh | tee experiment.log
```

スクリプトが以下を自動で実行します:
- Baseline (w=3) での 50 お題生成
- Treatment (w=12) での 50 お題生成
- 両者の比較

**オプション B: 手動実行**

`EVAL_RUNBOOK.md` の Step 2～4 に従い、コマンドを手動で実行してください。

### 4️⃣ 結果を分析

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# 比較レポート
./compare.sh w-repeated-3 w-repeated-12

# 判定基準: スコア差 ±2.7 を超えたら有意
#   > +2.7: 改善
#   ±2.7内: 判定不能 (ノイズ内)
#   < -2.7: 劣化
```

### 5️⃣ 結果を記録

採用判断の有無に関わらず、`app/backend/eval/FINDINGS.md` に実験結果を追記してください。

---

## 重要な注意点

### ⚠️ passthrough 罠 (SKILL.md §2)

```bash
# ❌ これは効かない (backend に届かない)
export TANKA_W_REPEATED_WORD=12
docker compose up -d backend

# ✅ これが正しい (コンテナを再作成しながら env を渡す)
cd app
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# 確認
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD
```

### ⚠️ failures リセット (SKILL.md §1 — Step 1.6)

重み変更は long-term failure memory の lesson 選定に交絡するため、各 variant 前に必ず実施:

```bash
curl -X DELETE http://localhost:8001/api/failures
sleep 2
curl -s http://localhost:8001/api/failures | jq .count  # 0 になったか確認
```

### ⚠️ LM Studio fresh reload

連続負荷下で LM Studio が劣化し、スコアの信頼性が落ちる (CLAUDE.md §6.13):

```bash
# LM Studio が直近 20 分以上アイドルなら reload 不要
# 直前に eval や手動生成があれば必ず実施

lms stop
lms server start
sleep 10
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
```

### ⚠️ 判定基準の事前登録

SKILL.md §1 の Step 1.4 に従い、実行 **前に** ノイズ下限を決めておくこと:

| n (お題数) | 2SE 幅 | 判定 |
|---|---|---|
| 50 | ±2.7 | **スコア差がこの範囲内なら「判定不能」** |
| 16 | ±4.8 | (subset 実験用) |
| 8 | ±6.8 | (試行錯誤用) |

この幅を超えて初めて「改善/劣化」と結論する (p-hacking 防止)。

---

## 実験設計 (事前登録)

```
問い:
  repeated_word の重みを 3→12 に上げた場合、違反出現率と全体スコアへの影響は?

変数:
  TANKA_W_REPEATED_WORD: 3 (baseline) vs 12 (treatment)

お題:
  50 (eval_themes.json)

判定基準:
  スコア差 ±2.7 を超えたら有意

交絡対策:
  1. failures リセット (各 variant 直前)
  2. LM Studio fresh reload
  3. docker-compose.yml に env を追加 (passthrough 罠回避)
```

詳細は `experiment-design-summary.txt` を参照。

---

## ファイル別ガイド

| ファイル | 用途 |
|---|---|
| `docker-compose-diff.patch` | app/docker-compose.yml への差分。環境変数を追加 |
| `experiment-design-summary.txt` | 実験の事前登録: 問い、変数、判定基準、交絡対策 |
| `EVAL_RUNBOOK.md` | Step 0～5 の詳細実行手順。コマンド、スクリーンショット例、トラブル対応を含む |
| `run-experiment.sh` | 自動化スクリプト。baseline → treatment → 比較を一気に実施 |
| `README.md` | このファイル。全体の概要と quick start |

---

## 実行フロー

```
┌─ 前提チェック ─────────────────────┐
│ curl health                        │
│ LM Studio 起動確認                 │
└────────────────────────────────────┘
        ↓
┌─ Baseline (w=3) ────────────────────┐
│ 1. failures リセット                │
│ 2. docker compose up (w=3)          │
│ 3. 50 お題を生成 (./eval.sh)       │
│ 所要: 25～60 分                     │
└────────────────────────────────────┘
        ↓
┌─ Treatment (w=12) ──────────────────┐
│ 1. failures リセット                │
│ 2. docker compose up (w=12)         │
│ 3. 50 お題を生成 (./eval.sh)       │
│ 所要: 25～60 分                     │
└────────────────────────────────────┘
        ↓
┌─ 比較・判定 ────────────────────────┐
│ 1. ./compare.sh w-repeated-3 ...    │
│ 2. スコア差を判定基準と照合         │
│ 3. repeated_word 残存率を確認       │
│ 4. FINDINGS.md に追記               │
└────────────────────────────────────┘
```

---

## トラブルシューティング

### Q: `docker compose up -d backend` 後も env が 3 のまま

**A**: `docker compose restart` では env は更新されません。以下を実施:

```bash
cd app
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
```

### Q: eval.sh 実行時 "Context size has been exceeded" エラー

**A**: LM Studio が JIT ロード (初回) または劣化中の可能性があります:

```bash
# モデルを明示的に再ロード
lms stop
lms load llm-jp-4-8b-thinking --context-length 32768
lms server start
sleep 10
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
```

### Q: results/ に eval ファイルが出現しない

**A**: eval.sh がバックグラウンドで実行中の可能性があります:

```bash
# ログで進捗を確認
docker compose logs -f backend | grep -E 'theme|evaluating|refine'

# または
ps aux | grep eval.sh
```

eval の所要時間は 25～60 分です。気長に待つか、`docker compose logs` で進捗を確認してください。

---

## 参考資料

- **SKILL.md** (tanka-eval-ablation): パイプライン変更の数値検証作法 (この実験の根拠)
- **CLAUDE.md §6.13**: LM Studio sustained-load 劣化
- **CLAUDE.md §6.14**: LM Studio の JIT ロードと context 超過
- **app/backend/eval/FINDINGS.md**: variance 実測、先行実験の報告
- **app/backend/eval/README.md**: eval ハーネスの仕様・メトリクス定義

---

## チェックリスト

実行前に:

- [ ] backend が起動している (`curl http://localhost:8001/api/health`)
- [ ] LM Studio が起動している (`curl http://localhost:8001/api/health | jq .lm_studio_ok`)
- [ ] LM Studio が 20 分以上アイドルか確認 (必要なら fresh reload)
- [ ] docker-compose.yml に `TANKA_W_REPEATED_WORD` の行を追加した
- [ ] 判定基準 (±2.7) を確認して納得した
- [ ] 失敗時の対応方法を理解した

---

## コンタクト

実験中に判断が必要な場合や予期しないエラーが発生した場合は、
EVAL_RUNBOOK.md のトラブルシューティングセクションを参照するか、
claude とのセッションで相談してください。

