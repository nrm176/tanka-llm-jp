# validator ルール重み A/B 比較: repeated_word (3 vs 12)

**実験概要**: `TANKA_W_REPEATED_WORD` の重みを 3 (既定) から 12 に上げた場合、
repeated_word 違反がどれだけ抑制されるか、かつ全体スコアにどのような影響を及ぼすか測定する。

---

## 前提条件

### Step 0: 環境の確認

```bash
# backend と LM Studio の状態確認
curl -s http://localhost:8001/api/health | python3 -m json.tool

# 出力例:
# {
#   "status": "ok",
#   "lm_studio_ok": true,
#   "model_loaded": true,
#   "configured_model": "llm-jp-4-8b-thinking"
# }
```

**確認項目**:
- `status: ok` / `lm_studio_ok: true` / `model_loaded: true`
- `configured_model` が `llm-jp-4-8b-thinking` であること (UI での切替がないか確認)

### Step 1: LM Studio fresh reload (重要)

```bash
# LM Studio がこれまで連続負荷を受けていたら、fresh reload する必要がある
# LM Studio のログを確認し、20 分以上アイドルなら reload 不要
# 直近で eval や手動生成を行っていたら必ず reload を実施

# LM Studio の CLI から (ホスト側)
lms stop
lms server start

# または LM Studio UI の「Stop server」→「Start server」で実施

# backend が再接続するまで 10 秒待つ
sleep 10
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
```

---

## 実験設計 (Step 1 — 事前登録)

| 項目 | 値 |
|---|---|
| **問い** | `TANKA_W_REPEATED_WORD` の重みを 4 倍 (3→12) に上げた場合、repeated_word 違反の出現頻度と最終スコアへの影響は? |
| **変更の種類** | 環境変数 (env 切替) |
| **お題セット** | 50 (`app/backend/eval/eval_themes.json`) ※ 比較の確定判断なので full size |
| **判定基準** | 50 お題 (n=50) 平均スコア差 **±2.7 を超えたら有意**と判定。差がこの幅内なら「ノイズ内 = 判定不能」と報告 |
| **主要指標** | (a) `violation_frequency` の `repeated_word` 残存率、(b) 共通重み (既定値で再採点) したスコア |

### 交絡対策

1. **failures リセット**: 重み変更は長期失敗記憶の lesson 選定に交絡するため、各 variant run 直前に必ず実施
   ```bash
   curl -s http://localhost:8001/api/failures | jq .count  # 現在の蓄積を確認
   curl -X DELETE http://localhost:8001/api/failures        # リセット
   curl -s http://localhost:8001/api/failures | jq .count   # 0 になったか確認
   ```

2. **LM Studio 劣化**: variant ごとに fresh reload は実施済み（上記）

---

## Step 2: 変更の適用

### 2a. docker-compose.yml に環境変数を追加

下記の `TANKA_W_REPEATED_WORD` の行を `docker-compose.yml` の `backend.environment` に追記:

```yaml
environment:
  LM_STUDIO_URL: http://host.docker.internal:1234/v1
  LM_STUDIO_MODEL: ${LM_STUDIO_MODEL:-llm-jp-4-8b-thinking}
  MONGO_URL: mongodb://mongo:27017
  MONGO_DB: tanka_chat
  REDIS_URL: redis://redis:6379/0
  TANKA_DYNAMIC_FEWSHOT: ${TANKA_DYNAMIC_FEWSHOT:-1}
  TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-1}
  TANKA_RAG: ${TANKA_RAG:-1}
  TANKA_HARD_CAP: ${TANKA_HARD_CAP:-50}
  TANKA_PLATEAU_WINDOW: ${TANKA_PLATEAU_WINDOW:-3}
  TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}  # ← 追加
```

**理由**: `TANKA_W_*` や `TANKA_PASS_THRESHOLD` といったルール重み系は現在
`docker-compose.yml` に列挙されていないため、ホストで `export TANKA_W_REPEATED_WORD=12`
しても backend コンテナに届かない (passthrough 罠 ⚠️ SKILL.md §2 参照)。

### 2b. baseline (既定値 3) での測定

```bash
# 既定値で起動 (TANKA_W_REPEATED_WORD=3)
cd /Users/nrm176p/GitHub2/LLM/app
docker compose up -d backend

# コンテナに環境変数が届いたか確認
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD
# 出力例: TANKA_W_REPEATED_WORD=3

# DB の失敗記憶を リセット
sleep 5
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
curl -X DELETE http://localhost:8001/api/failures
curl -s http://localhost:8001/api/failures | jq .count

# 50 お題を生成 (variant 名: w-repeated-3)
cd backend/eval
./eval.sh w-repeated-3 eval_themes.json  # 約 25~60 分
```

**実行中の禁則**:
- backend のコードを変更しない
- LM Studio へ診断リクエストを投げない
- 複数 eval を並行実行しない

### 2c. variant (重み 12) での測定

```bash
# 環境変数を 12 に設定して再作成
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# 確認
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD
# 出力例: TANKA_W_REPEATED_WORD=12

# DB の失敗記憶を リセット (重要: 重みが変わったため)
sleep 5
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
curl -X DELETE http://localhost:8001/api/failures
curl -s http://localhost:8001/api/failures | jq .count

# 同一お題セット (50 題) を生成
cd backend/eval
./eval.sh w-repeated-12 eval_themes.json  # 約 25~60 分
```

---

## Step 3: 実行手順（実行スクリプト）

実行を簡便にするため、以下のシェルスクリプトを活用できます:

```bash
#!/bin/bash
set -e

PROJECT_DIR="/Users/nrm176p/GitHub2/LLM"
BACKEND_DIR="$PROJECT_DIR/app/backend"
EVAL_DIR="$BACKEND_DIR/eval"

# 前提チェック
echo "=== 前提チェック ==="
curl -s http://localhost:8001/api/health | python3 -m json.tool | grep -E 'status|lm_studio_ok|model_loaded'

# variant 1: baseline (既定値 3)
echo ""
echo "=== Baseline run: TANKA_W_REPEATED_WORD=3 ==="
cd "$PROJECT_DIR/app"
docker compose up -d backend
sleep 5
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD

curl -X DELETE http://localhost:8001/api/failures
sleep 2
curl -s http://localhost:8001/api/failures | jq .count

cd "$EVAL_DIR"
./eval.sh w-repeated-3 eval_themes.json

# variant 2: treatment (重み 12)
echo ""
echo "=== Treatment run: TANKA_W_REPEATED_WORD=12 ==="
cd "$PROJECT_DIR/app"
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD

curl -X DELETE http://localhost:8001/api/failures
sleep 2
curl -s http://localhost:8001/api/failures | jq .count

cd "$EVAL_DIR"
./eval.sh w-repeated-12 eval_themes.json

# 比較
echo ""
echo "=== 比較結果 ==="
./compare.sh w-repeated-3 w-repeated-12
```

---

## Step 4: 比較と判定 (実行後)

### 4a. 基本統計の確認

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh w-repeated-3 w-repeated-12
```

**チェック項目**:
1. `_themes_file` と `n` (お題数) が両 variant で一致しているか確認
   - 不一致の場合は比較が無効 (別のお題セットで測られている)
   
2. `avg_final_score` の差分
   - **しかし注意**: 重み変更は評点の物差し自体が変わるため、直接比較は無効
   - 代わりに下記の方法で再採点したスコアを使う

### 4b. 共通重み (既定値) での再採点

重み変更の効果を正しく測るため、**両 variant の最終成果物を既定重み (w=3) で再採点**:

```bash
# 結果ファイルのパス例 (タイムスタンプは実際のものに置き換え)
BASELINE_FILE="results/20260612_143000-w-repeated-3.json"
TREATMENT_FILE="results/20260612_190000-w-repeated-12.json"

# Python で再採点スクリプト
cd /Users/nrm176p/GitHub2/LLM/app/backend
python3 << 'PYSCRIPT'
import json
import sys
sys.path.insert(0, '.')
from validator import evaluate, Tanka, parse_tanka_json, RULE_WEIGHTS

baseline_file = "eval/results/20260612_143000-w-repeated-3.json"
treatment_file = "eval/results/20260612_190000-w-repeated-12.json"

def rescore(result_file, label):
    with open(result_file) as f:
        data = json.load(f)
    
    messages = data.get('messages', [])
    tankas = [m for m in messages if m.get('kind') == 'tanka' and m.get('tanka')]
    
    scores = []
    for msg in tankas:
        tanka_json = msg['tanka']
        t = parse_tanka_json(json.dumps(tanka_json))
        if t:
            result = evaluate(t)
            scores.append(result.score)
    
    avg = sum(scores) / len(scores) if scores else 0
    print(f"{label}: avg={avg:.1f}, n={len(scores)}")
    return scores

baseline_scores = rescore(baseline_file, "Baseline (w=3)")
treatment_scores = rescore(treatment_file, "Treatment (w=12)")

# 差分
diff = sum(treatment_scores) / len(treatment_scores) - sum(baseline_scores) / len(baseline_scores)
print(f"\nDifference (Treatment - Baseline): {diff:.1f}")
print(f"Noise threshold (n=50): ±2.7")
if abs(diff) <= 2.7:
    print("→ Indeterminate (within noise)")
else:
    print(f"→ Significant ({'+' if diff > 0 else '−'}{abs(diff):.1f})")
PYSCRIPT
```

### 4c. violation_frequency による repeated_word 効果の検証

```bash
# 主要指標: repeated_word の残存率
cd /Users/nrm176p/GitHub2/LLM/app/backend
python3 << 'PYSCRIPT'
import json

baseline_file = "eval/results/20260612_143000-w-repeated-3.json"
treatment_file = "eval/results/20260612_190000-w-repeated-12.json"

def check_violations(result_file, label):
    with open(result_file) as f:
        data = json.load(f)
    
    messages = data.get('messages', [])
    tankas = [m for m in messages if m.get('kind') == 'tanka' and m.get('final_score')]
    
    # final_score がある = 最終採択 tanka
    repeated_count = sum(1 for m in tankas if m.get('violation_frequency', {}).get('repeated_word', 0) > 0)
    total = len(tankas)
    rate = (repeated_count / total * 100) if total > 0 else 0
    
    print(f"{label}:")
    print(f"  最終採択 tanka 中 repeated_word 残存: {repeated_count}/{total} ({rate:.1f}%)")
    return rate

baseline_rate = check_violations(baseline_file, "Baseline (w=3)")
treatment_rate = check_violations(treatment_file, "Treatment (w=12)")

print(f"\nImprovement: {baseline_rate - treatment_rate:.1f}% 減")
PYSCRIPT
```

### 4d. 判定基準

| スコア差 | 判定 |
|---|---|
| `> +2.7` | 💚 **改善** (repeated_word の重みを 12 に上げることで全体品質が向上) |
| `-2.7 ≤ diff ≤ +2.7` | ⚪ **判定不能** (ノイズ内。重みの変更は効果が薄いか交絡がある) |
| `< -2.7` | 🔴 **劣化** (重みを上げることが逆効果) |

**ただし repeated_word 残存率が 20% 以上低下していれば、全体スコアは小幅な変化でも
「repeated_word 抑制には成功している」と評価可能**。

---

## Step 5: 記録

### 5a. 結果を FINDINGS.md に追記

実験完了後、`app/backend/eval/FINDINGS.md` の最後に以下形式で追記:

```markdown
### Experiment: repeated_word weight (3 vs 12)

**Date**: 2026-06-12  
**Model**: llm-jp-4-8b-thinking  
**Themes**: 50 (`eval_themes.json`)  

**Baseline (w=3)**:
- avg_final_score: XX.X
- repeated_word residual rate: YY.Y%

**Treatment (w=12)**:
- avg_final_score: ZZ.Z
- repeated_word residual rate: WW.W%

**Judgment**: [改善 / 判定不能 / 劣化]  
**Reasoning**: ...

**Noise threshold**: ±2.7 (n=50)
```

### 5b. PR への記載

変更を採用する場合は PR 本文に:

```
## A/B Test: TANKA_W_REPEATED_WORD

- Baseline (w=3): avg_final_score XX.X, repeated_word residual YY.Y%
- Treatment (w=12): avg_final_score ZZ.Z, repeated_word residual WW.W%
- Difference: ±ZZ (within noise / significant)
- Decision: [採用 / 保留]
```

---

## トラブルシューティング

### Q: env 変更後も docker compose exec で 3 が見える

**A**: `docker compose up -d` ではなく `docker compose down && docker compose up -d` で
コンテナを完全再作成してください。単なる restart では env は更新されません。

### Q: 測定途中で LM Studio が遅くなった (5s/call → 2min/call)

**A**: LM Studio が sustained-load で劣化しています (CLAUDE.md §6.13)。
- eval.sh を pkill -f で止める (PID 控える)
- LM Studio を再起動 (`lms stop` → `lms server start`)
- failures リセット後に再度 eval を実施

### Q: failures リセット後、DB が「接続拒否」になった

**A**: MongoDB が起動していない可能性があります:
```bash
docker compose logs mongo
docker compose restart mongo
sleep 10
curl -s http://localhost:8001/api/health | jq .
```

### Q: results/ にファイルが出現しない

**A**: eval.sh が背景で実行中の可能性があります。
```bash
docker compose logs -f backend  # backend のログを監視
# または
ps aux | grep eval.sh
```

---

## 参考資料

- **SKILL.md**: パイプライン変更の数値検証作法全般
- **CLAUDE.md §6.13**: LM Studio sustained-load 劣化の環境制約
- **app/backend/eval/FINDINGS.md**: variance 実測データ・先行実験の報告
- **app/docs/architecture.md §9**: 拡張パターン・新ルール追加の手順

