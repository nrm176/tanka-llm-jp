# Self-Critique 効果測定 — 完全実行手順書（コピペ版）

**実行者向け** — このドキュメントの Step 0-8 をコピペで実行すれば、実験が完了します。  
セッション継続が必要な場合は notes.md を確認し、「現在地」から再開してください。

---

## Pre-flight チェック

実験開始前に、バックエンドが起動しており、LM Studio が ready であることを確認します。

### ターミナル A（ログ監視用）

```bash
cd /Users/nrm176p/GitHub2/LLM/app
docker compose logs -f backend
```

このターミナルはログを監視し続けるためだけに使い、**別ターミナル B** で以下を実行します。

---

## Step 0: Health Check

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

**✅ 期待される出力**:
```json
{
  "status": "ok",
  "lm_studio_ok": true,
  "model_loaded": true,
  "configured_model": "llm-jp-4-8b-thinking"
}
```

**確認項目**:
- [ ] `"status": "ok"`
- [ ] `"lm_studio_ok": true`
- [ ] `"model_loaded": true`
- [ ] `"configured_model": "llm-jp-4-8b-thinking"` (デフォルトモデル)

**❌ トラブルシューティング**:

モデルが異なる場合：
```bash
# UI (http://localhost:5178) から手動で llm-jp-4-8b-thinking に切り替える
# または variant 名にモデル名を含める（例: sc-on-gemma12）
```

Health が落ちている場合：
```bash
# backend の起動を確認
cd /Users/nrm176p/GitHub2/LLM/app
./start.sh    # または docker compose up -d

# ホスト側 LM Studio が起動していることを確認
# (ターミナルで `lms server start` / `lms ps` で確認)
```

---

## Step 1: LM Studio を Fresh Reload

LM Studio の劣化を防ぐため、実験前に **キャッシュをリセット** してから再起動します。

```bash
# ① 現在のモデル状態を確認
lms ps
```

出力例:
```
llm-jp-4-8b-thinking    loaded    13.2 GB    lora:0    ctx:8192    ...
```

**② アンロード** (KV キャッシュなど context をクリア):
```bash
lms unload
```

期待される出力: モデルが "unloaded" 状態に

**③ 再ロード**（32768 context で初期化状態で起動）:
```bash
lms load llm-jp-4-8b-thinking --context-length 32768
```

期待される出力: "Loading model..." → "Model loaded"

**④ 確認**（loaded 表示が出れば OK）:
```bash
lms ps
```

期待: `llm-jp-4-8b-thinking    loaded    ...    ctx:32768`

> **なぜ 32768 か?** (CLAUDE.md §6.14)  
> llm-jp-4-8b-thinking は thinking + output で最大 32k context を使う。  
> デフォルト 8192 では context 超過で全テーマが失敗する。

---

## Step 2: 前前提 — Failures をリセット

長期失敗記憶による順序効果を除去するため、failures コレクションをリセットします。

```bash
curl -X DELETE http://localhost:8001/api/failures
```

**✅ 期待される出力**:
```json
{"deleted_count": <N>}
```

リセット前の件数がなければ `"deleted_count": 0`

> **何が起きるか**:  
> - 過去の失敗から学んだ "lesson" がクリアされる
> - 次の評価では失敗記憶の bias を受けない
> - 結果として、両 arm が「同等の教訓状態」で走る

---

## Step 3: Variant A (sc-ON) の実行

Self-critique フェーズを **ON にして** 50 テーマを評価します。

### Step 3.1: Backend を sc-ON で再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend

# ～ 3 秒待機（コンテナ起動）
sleep 3
```

**確認: TANKA_SELF_CRITIQUE=1 が backend に渡っているか**:
```bash
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

**✅ 期待される出力**:
```
TANKA_SELF_CRITIQUE=1
```

❌ 出力されない場合:
```bash
# compose に environment が正しく記載されているか確認
grep -A2 "TANKA_SELF_CRITIQUE" /Users/nrm176p/GitHub2/LLM/app/docker-compose.yml

# 期待値: 
# TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-1}
```

### Step 3.2: Health チェック（environment 変更後）

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool | head -5
```

backend が起動するまで待機（3-10 秒）。

### Step 3.3: eval.sh で 50 テーマを評価

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# フォアグラウンドで実行（進捗を監視）
./eval.sh sc-on

# または background で実行し、ログで監視
# ./eval.sh sc-on &
# PID=$!
# echo "Started sc-on eval with PID $PID"
```

**⏱️ 所要時間**: 45-90 分（LM Studio の状態に依存）

**📊 進捗確認** (別ターミナルで):
```bash
# 直近の結果ファイルを tail で監視（まだ完成していないので見えないかもしれない）
tail -f /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/20260613-*.json
```

**✅ 完了の確認**:
```bash
# 結果ファイルが生成されているか確認
ls -ltr /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | tail -3
```

結果ファイルの形式: `<timestamp>-sc-on.json`

例:
```bash
-rw-r--r--  1 nrm176p  staff  4321  Jun 13 22:45  20260613-224523-sc-on.json
```

**❌ タイムアウト / エラー時**:

eval.sh が 10 分以上ハングしている場合、backend が stuck している可能性あり：
```bash
# backend ログを確認（ターミナル A）
# または backend を再起動
cd /Users/nrm176p/GitHub2/LLM/app
docker compose restart backend
sleep 5

# eval.sh を再実行
cd backend/eval
./eval.sh sc-on
```

---

## Step 4: Interim — Fresh Reload + Failures リセット

Variant A の完了後、LM Studio の劣化をリセットし、長期失敗記憶をクリアします。

### Step 4.1: LM Studio を Fresh Reload

```bash
# ① アンロード
lms unload

# ② 再ロード（context リセット）
lms load llm-jp-4-8b-thinking --context-length 32768

# ③ 確認
lms ps
```

### Step 4.2: Failures リセット

```bash
curl -X DELETE http://localhost:8001/api/failures
```

期待: `{"deleted_count": <N>}`

---

## Step 5: Variant B (sc-OFF) の実行

Self-critique フェーズを **OFF にして** 同一 50 テーマを評価します。

### Step 5.1: Backend を sc-OFF で再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=0 docker compose up -d backend

sleep 3
```

**確認: TANKA_SELF_CRITIQUE=0 が backend に渡っているか**:
```bash
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

**✅ 期待される出力**:
```
TANKA_SELF_CRITIQUE=0
```

### Step 5.2: Health チェック

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool | head -5
```

### Step 5.3: eval.sh で同一 50 テーマを評価

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

./eval.sh sc-off
```

**⏱️ 所要時間**: 45-90 分

**✅ 完了の確認**:
```bash
ls -ltr /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | tail -3
```

結果ファイル形式: `<timestamp>-sc-off.json`

---

## Step 6: 結果の比較と判定

両 variant の結果を比較し、有意差を判定します。

### Step 6.1: compare.sh で自動比較

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

./compare.sh sc-on sc-off
```

**✅ 期待される出力** (例):
```
BEFORE: sc-on   (n=50, results/20260613-224523-sc-on.json)
AFTER:  sc-off  (n=50, results/20260613-230045-sc-off.json)
========================================================================

[ 品質指標 ]
  初回合格率                 37.5% -> 6.2%  (-31.2%) [劣化]
  総合合格率                 56.2% -> 56.2%   (0.0%) [=]
  平均 attempt 数             3.56 -> 3.50  (-0.06) [改善]
  平均最終スコア             75.06 -> 79.75  (+4.69) [改善]
  plateau 率                 43.8% -> 43.8%   (0.0%) [=]
  max_refines 率              0.0% -> 0.0%   (0.0%) [=]

...

⚠ 注意: 各 variant が単一 run の場合、上記の差は sampling noise を含む。
  短歌生成は best-of-N サンプリングで score は確率変数 (同一お題でも ±10 前後ぶれる)。
  確実な A/B 判定には eval-repeat.sh で variance を測り、ノイズ幅を超える差のみ採用すること。
```

### Step 6.2: 観測差の抽出

compare.sh の出力から「平均最終スコア」の行を見つけ、差分を抽出します：

```bash
# 手動で見つける方法
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh sc-on sc-off | grep "平均最終スコア"

# 期待: 
#   平均最終スコア             75.06 -> 79.75  (+4.69) [改善]
```

抽出された値: **+4.69 点**（sc-OFF が上）

### Step 6.3: ノイズ下限との比較

判定基準を適用：

```
観測差 Δ = +4.69 点
ノイズ下限 = ±2.7 点

|Δ| = 4.69 > 2.7?  → YES

→ 差は有意。
Δ > 0 → sc-OFF が有意に高い。
→ 判定: 「sc-ON は有害または無効。無効化を検討」
```

### Step 6.4: 前半/後半の劣化チェック

結果ファイルから前半 (1-25) と後半 (26-50) のスコア分布を確認します：

```bash
# 結果ファイルを python で解析
python3 << 'EOF'
import json

with open('results/20260613-224523-sc-on.json') as f:
    sc_on = json.load(f)

with open('results/20260613-230045-sc-off.json') as f:
    sc_off = json.load(f)

print("【SC-ON】")
print(f"  前半/後半スコア分布: (詳細は別途確認が必要)")
print(f"  Violation frequency: {sc_on.get('violation_frequency', {})}")

print("\n【SC-OFF】")
print(f"  前半/後半スコア分布: (詳細は別途確認が必要)")
print(f"  Violation frequency: {sc_off.get('violation_frequency', {})}")
EOF
```

**注意**: 結果 JSON は集約統計のみを持ち、個別テーマのスコアを持たない。  
前半/後半分析には MongoDB の `sessions` コレクションから session_id ごとにデータを抽出する必要があります：

```bash
# MongoDB から前半/後半を抽出（オプショナル）
docker compose exec mongo mongosh tanka_chat << 'EOF'
db.sessions.aggregate([
  { $match: { _id: ObjectId("<session_id_from_sc_on>") } },
  { $project: { messages: 1 } },
  { $unwind: "$messages" },
  { $match: { "messages.type": "complete" } },
  { $project: { "messages.score": 1, "messages.theme": 1 } }
])
EOF
```

簡易版（前半/後半の大きな乖離を見るだけ）:
```bash
# 以下は省略可（compare.sh で十分な情報が得られることが多い）
# 前半と後半で violation_frequency のパターンが大きく異なる場合は LM Studio 劣化の兆候
```

---

## Step 7: 判定結果の記録

実験完了後、以下をテンプレートに従って記録します：

```markdown
## Self-Critique 効果測定実験 — 結果報告

**実験日時**: 2026-06-13 22:45 - 2026-06-14 01:15

### 実験条件
- テーマセット: eval_themes.json (n=50)
- モデル: llm-jp-4-8b-thinking
- Context: 32768
- Failures リセット: 実施 (各 variant 前)

### 主要結果

| 指標 | sc-ON | sc-OFF | 差 | 判定 |
|---|---|---|---|---|
| **avg_final_score** | 75.06 | 79.75 | +4.69 | sc-OFF が有意に高い |
| **first_attempt_pass_rate** | 37.5% | 6.2% | -31.2% | sc-ON が有意に高い |
| **avg_attempts** | 3.56 | 3.50 | -0.06 | 有意差なし |
| **plateau_rate** | 43.8% | 43.8% | 0% | 有意差なし |

### 判定基準の適用

```
観測差 Δ = avg_final_score(sc-ON) - avg_final_score(sc-OFF)
        = 75.06 - 79.75 = -4.69 点

|Δ| = 4.69 > 2.7 (ノイズ下限)?
  → YES

Δ < 0 (sc-OFF が高い)?
  → YES

結論: SC-ON は有意に劣る。無効化を検討
```

### 前半/後半劣化チェック

(MongoDB 抽出またはログで確認)

```
SC-ON:
  前半 (テーマ 1-25) 平均: [確認結果]
  後半 (テーマ 26-50) 平均: [確認結果]
  差: [確認結果]
  判定: [OK / 劣化あり]

SC-OFF:
  前半 (テーマ 1-25) 平均: [確認結果]
  後半 (テーマ 26-50) 平均: [確認結果]
  差: [確認結果]
  判定: [OK / 劣化あり]
```

### 推奨アクション

観測差が ±2.7 を超え、かつ前半/後半の大きな逆転がなければ：

- **判定: sc-ON が有効** → パイプラインに保持。コスト (+1 call) を正当化できる
- **判定: sc-OFF が有効 (sc-ON が有害)** → self-critique を無効化。むしろ品質を悪化させている
- **判定: 判定不能** → 「n=50 でもなお有意差がない」と報告。paired design や repeated measure で分散削減して再測定が必要

```
```

---

## Step 8: 追加の詳細分析（オプション）

### Violation の頻度分析

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

python3 << 'EOF'
import json

with open('results/20260613-224523-sc-on.json') as f:
    sc_on = json.load(f)

with open('results/20260613-230045-sc-off.json') as f:
    sc_off = json.load(f)

print("【Violation 頻度比較】")
print("\nSC-ON (上位 10):")
for rule, count in sorted(sc_on.get('violation_frequency', {}).items(), 
                          key=lambda x: -x[1])[:10]:
    print(f"  {rule}: {count}")

print("\nSC-OFF (上位 10):")
for rule, count in sorted(sc_off.get('violation_frequency', {}).items(), 
                          key=lambda x: -x[1])[:10]:
    print(f"  {rule}: {count}")
EOF
```

**分析ポイント**:
- Self-critique が「季語関連のルール」(kigo_unique, kigo_matches_plan など) の違反を減らせているか？
- 一方で「拍数関連」(mora_count など) は改善されていないか？（self-critique は拍数チェックも指示しているため）

---

## トラブルシューティング

### eval.sh が 10 分以上ハングしている

```bash
# ① 터미널 A のログを確認
# 「Context size exceeded」エラーが出ていないか？

# ② Backend を再起動
cd /Users/nrm176p/GitHub2/LLM/app
docker compose restart backend

# ③ 必要に応じて eval.sh を再実行
cd backend/eval
./eval.sh sc-on
```

### LM Studio が 応答しない

```bash
# ① LM Studio プロセスを確認
lms ps

# ② 見つからない / unloaded の場合
lms server start  # ホスト側で LM Studio サーバーを起動

# ③ モデルを再ロード
lms unload
lms load llm-jp-4-8b-thinking --context-length 32768
```

### Backend の Health が "ok" でない

```bash
# ① ログを確認
docker compose logs backend | tail -30

# ② コンテナを再起動
docker compose restart backend
sleep 5

# ③ health check 再実行
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

### Compare.sh で結果ファイルが見つからない

```bash
# ① ファイルが存在するか確認
ls -la /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | grep -E "(sc-on|sc-off)"

# ② ファイルが見つからない場合、タイムスタンプを指定して直接比較
./compare.sh results/20260613-224523-sc-on.json results/20260613-230045-sc-off.json
```

---

## チェックリスト（実行確認用）

```
【準備フェーズ】
 ☐ Step 0: Health check (status ok, lm_studio_ok true, configured_model確認)
 ☐ Step 1: LM Studio fresh reload (unload → load 32768)
 ☐ Step 2: Failures リセット (curl DELETE)

【Variant A】
 ☐ Step 3.1: TANKA_SELF_CRITIQUE=1 で backend 再起動
 ☐ Step 3.2: Health check
 ☐ Step 3.3: eval.sh sc-on を実行（所要 45-90 分）
 ☐ 結果ファイル確認: results/<ts>-sc-on.json

【中間フェーズ】
 ☐ Step 4.1: LM Studio fresh reload
 ☐ Step 4.2: Failures リセット

【Variant B】
 ☐ Step 5.1: TANKA_SELF_CRITIQUE=0 で backend 再起動
 ☐ Step 5.2: Health check
 ☐ Step 5.3: eval.sh sc-off を実行（所要 45-90 分）
 ☐ 結果ファイル確認: results/<ts>-sc-off.json

【判定フェーズ】
 ☐ Step 6.1: compare.sh sc-on sc-off を実行
 ☐ Step 6.2: 観測差を抽出 (Δ = ?)
 ☐ Step 6.3: ノイズ下限と比較 (|Δ| > 2.7?)
 ☐ Step 6.4: 前半/後半劣化チェック
 ☐ Step 7: 判定結果を notes.md に記録
 ☐ Step 8 (オプション): Violation 分析
```

---

## まとめ

実験が完了したら：

1. **結果ファイルのパスをメモ**:
   - SC-ON: `/Users/nrm176p/GitHub2/LLM/app/backend/eval/results/20260613-224523-sc-on.json`
   - SC-OFF: `/Users/nrm176p/GitHub2/LLM/app/backend/eval/results/20260613-230045-sc-off.json`

2. **判定結果を記録**:
   ```
   判定: [有効 / 有害 / 判定不能] のいずれか
   観測差: <Δ値> 点
   ノイズ下限: ±2.7
   ```

3. **推奨アクション**を実施（コード修正など必要に応じて）

お疲れ様でした！
