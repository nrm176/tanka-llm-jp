# Self-Critique 効果測定 — 実行手順書

**実行者向け** — このドキュメントをコピペで実行すれば、実験が完了します。
セッション継続が必要な場合は notes.md を確認後、「現在地」から再開してください。

---

## Pre-flight チェック

実験開始前に、バックエンドが起動しており、LM Studio が ready であることを確認します。

```bash
# ターミナル A（全体監視用）
cd /Users/nrm176p/GitHub2/LLM/app
docker compose logs -f backend
```

**別ターミナル B** で以下を実行:

```bash
# Step 0: Health Check
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

✅ 期待される出力:
```json
{
  "status": "ok",
  "lm_studio_ok": true,
  "model_loaded": true,
  "configured_model": "llm-jp-4-8b-thinking"
}
```

**確認事項**:
- [ ] `status: ok`
- [ ] `lm_studio_ok: true`
- [ ] `model_loaded: true`
- [ ] `configured_model` が `llm-jp-4-8b-thinking` (デフォルトモデル)

❌ モデルが異なる場合:
```bash
# UI (http://localhost:5178) から手動で切り替えたか、
# または別セッションで異なるモデルで実験を走らせた可能性
# この場合、variant 名にモデルを含める（例: `sc-on-gemma12`）
# または UI で `llm-jp-4-8b-thinking` に戻す
```

❌ health が落ちている場合:
```bash
# backend の起動を確認
cd /Users/nrm176p/GitHub2/LLM/app
./start.sh    # または docker compose up -d

# ホスト側 LM Studio が起動していることを確認
# (ターミナルで `lms server start` / `lms ps` で確認)
```

---

## Step 1: LM Studio を Fresh Reload

LM Studio の劣化を防ぐため、実験前に同じターミナルで reload します。

```bash
# LM Studio プロセスを確認
lms ps
```

出力例:
```
llm-jp-4-8b-thinking    loaded    13.2 GB    lora:0    ctx:8192    ...
```

キャッシュをリセットするため、**モデルをアンロード＆再ロード**:

```bash
# ① アンロード (KV キャッシュなど context をクリア)
lms unload

# ② 再ロード（初期化 context で新規スタート）
lms load llm-jp-4-8b-thinking --context-length 32768

# ③ 確認（loaded 表示が出れば OK）
lms ps
```

> **なぜ 32768 か?** 
> llm-jp-4-8b-thinking は thinking + output で最大 32k context を使う。
> デフォルト 8192 では context 超過で全テーマが失敗する (CLAUDE.md §6.14)

---

## Step 2: 前前提 — failures リセット

長期失敗記憶の順序効果を除去するため、failures コレクションをリセットします。

```bash
curl -X DELETE http://localhost:8001/api/failures
```

✅ 期待される出力:
```json
{"deleted_count": <N>}
```

> **何が起きるか**:
> - 過去の失敗から学んだ "lesson" がクリアされる
> - 次の評価では失敗記憶の bias を受けない
> - 結果として、両 arm が「同等の教訓状態」で走る

---

## Step 3: Variant A (sc-ON) の実行

self-critique フェーズを **ON にして** 50 テーマを評価します。

### 3.1. Backend を sc-ON で再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend

# ～ 3 秒待機（コンテナ起動）
sleep 3

# 確認: 実際に TANKA_SELF_CRITIQUE=1 が backend に渡っているか
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

✅ 期待される出力:
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

### 3.2. Health チェック（environment 変更後）

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

✅ `status: ok` を確認してから進む。

### 3.3. Variant A の評価を実行

50 テーマで self-critique ON の評価を走らせます。**この実行は 1-2 時間かかります。**

背景実行（ターミナルを解放）:
```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# 実行開始
nohup ./eval.sh sc-on eval_themes.json > sc-on.log 2>&1 &
PID=$!
echo "Started Variant A (sc-ON). PID=$PID"
echo "Monitor with: tail -f sc-on.log"
```

> **タイムアウト対応**:
> - eval.sh は 1 時間では終わらない可能性が高い
> - Bash tool には 10 分の timeout があるため、`run_in_background` や
>   `nohup ... &` で背景実行が必須
> - 進捗確認は `tail -f sc-on.log` で、「Test X of 50」の行で判定

**進捗の確認方法**:

ターミナル C を開き、定期的に以下を実行:
```bash
# オプション 1: ログ尾部を見る
tail -20 /Users/nrm176p/GitHub2/LLM/app/backend/eval/sc-on.log

# オプション 2: 完了したか確認
ls -lh /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | grep sc-on
```

**完了の判定**:
```bash
# ✅ 完了時は results/ に sc-on の JSON ファイルが現れる
# 例: 20260612-140530-sc-on.json

# ファイルサイズが 900+ bytes なら成功
ls -lh /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/*sc-on*.json
```

**もし失敗したら**:
```bash
# ログの末尾を確認
tail -50 /Users/nrm176p/GitHub2/LLM/app/backend/eval/sc-on.log

# よくあるエラー:
# - "Connection refused" → backend が起動していない
# - "Context size exceeded" → context 不足。lms load ... --context-length 32768 で再初期化
# - "timeout" → LM Studio が slow mode 中（劣化）。別日に再実行 or モデルを fresh reload
```

---

## Step 4: Interim — Fresh Reload + Failures リセット

Variant A の完了を確認したら、Variant B 前に再度 fresh reload を行います。

```bash
# Step 4.1: LM Studio リセット（前回と同一）
lms unload
sleep 2
lms load llm-jp-4-8b-thinking --context-length 32768
lms ps    # 確認

# Step 4.2: Failures リセット
curl -X DELETE http://localhost:8001/api/failures
```

---

## Step 5: Variant B (sc-OFF) の実行

self-critique フェーズを **OFF にして** 同一 50 テーマを評価します。

### 5.1. Backend を sc-OFF で再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=0 docker compose up -d backend

sleep 3

# 確認
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

✅ 期待される出力:
```
TANKA_SELF_CRITIQUE=0
```

### 5.2. Health チェック

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

### 5.3. Variant B の評価を実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

nohup ./eval.sh sc-off eval_themes.json > sc-off.log 2>&1 &
PID=$!
echo "Started Variant B (sc-OFF). PID=$PID"
```

**進捗監視と完了判定は Step 3 と同じ**:
```bash
tail -20 sc-off.log
ls -lh results/*sc-off*.json
```

---

## Step 6: 比較と判定

両 variant の完了を確認したら、results/ に出現したファイル名を特定して比較します。

### 6.1. 結果ファイルを特定

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# 最新の sc-on / sc-off ファイルを探す
ls -lhtr results/ | grep 'sc-on\|sc-off'
```

出力例:
```
20260612-140530-sc-on.json    970 bytes
20260612-155800-sc-off.json   968 bytes
```

### 6.2. 基本的な比較

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# variant 名だけを指定（results/ 内で時刻が最新のファイルを自動選択）
./compare.sh sc-on sc-off
```

出力の見方:
```
=== Comparison: sc-on vs sc-off ===

Theme file: eval_themes.json
sc-on:  n=50, avg_final_score=80.12, pass_rate=76%
sc-off: n=50, avg_final_score=77.85, pass_rate=72%

Difference: +2.27 points (ON > OFF)
Violation distribution:
  ...
```

### 6.3. 観測差をノイズ下限と照合

**判定基準**: n=50 のときノイズ下限は **±2.7 点**

```
観測差 +2.27 → |-2.27| = 2.27 < 2.7 なので **判定不能（ノイズ内）**
```

**判定ロジック**:
- | 観測差 | > 2.7 → **有意**（方向で採用/棄却を判定）
- | 観測差 | ≤ 2.7 → **判定不能**（ノイズ範囲内、結論保留）

---

## Step 7: 前半/後半の LM Studio 劣化チェック

（例）差が ±2.7 に近い場合など、交絡がないか確認します。

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# results/ から最新のファイルを確認
RESULT_FILE=$(ls -1tr results/*sc-on*.json | tail -1)
echo "Checking: $RESULT_FILE"

# session ID を抽出
SID=$(python3 -c "import json; print(json.load(open('$RESULT_FILE'))['_session_id'])")

# 前半/後半のスコア平均を計算
curl -s "http://localhost:8001/api/sessions/$SID" | python3 -c "
import sys, json
data = json.load(sys.stdin)
sc = [m['final_score'] for m in data.get('messages', [])
      if m.get('kind') == 'tanka' and m.get('final_score') is not None]
if len(sc) >= 2:
    h = len(sc) // 2
    first_half = sum(sc[:h]) / len(sc[:h]) if h > 0 else 0
    second_half = sum(sc[h:]) / (len(sc) - h) if len(sc) - h > 0 else 0
    print(f'sc-on  前半 (1-{h}): {first_half:.1f}  後半 ({h+1}-{len(sc)}): {second_half:.1f}')
else:
    print('Not enough data')
"

# sc-off も同様に確認
RESULT_FILE=$(ls -1tr results/*sc-off*.json | tail -1)
SID=$(python3 -c "import json; print(json.load(open('$RESULT_FILE'))['_session_id'])")
curl -s "http://localhost:8001/api/sessions/$SID" | python3 -c "
import sys, json
data = json.load(sys.stdin)
sc = [m['final_score'] for m in data.get('messages', [])
      if m.get('kind') == 'tanka' and m.get('final_score') is not None]
if len(sc) >= 2:
    h = len(sc) // 2
    first_half = sum(sc[:h]) / len(sc[:h]) if h > 0 else 0
    second_half = sum(sc[h:]) / (len(sc) - h) if len(sc) - h > 0 else 0
    print(f'sc-off 前半 (1-{h}): {first_half:.1f}  後半 ({h+1}-{len(sc)}): {second_half:.1f}')
else:
    print('Not enough data')
"
```

**劣化判定**:
```
sc-on  前半: 82.5  後半: 77.8   ← 4.7 点低下（許容範囲）
sc-off 前半: 80.2  後半: 75.5   ← 4.7 点低下（同等）

→ 両者で劣化パターンが対称 → LM Studio 劣化は **両 arm で同等に作用**
→ 観測差は self-critique の実効果を反映している可能性が高い
```

**異常な劣化パターン** (確認が必要):
```
sc-on  前半: 88.0  後半: 65.0   ← 23.0 点低下 ⚠️ 異常
sc-off 前半: 82.0  後半: 79.0   ← 3.0 点低下

→ sc-on の後半が崩壊 → この run は採用不可（再測定必須）
→ 原因: reload が不十分か、LM Studio の劣化が sc-on で顕著
```

---

## Step 8: 結論を記録

実験完了後、以下の情報をまとめて記録します。

### 記録テンプレート

```markdown
## Self-Critique 効果測定 — 最終報告

**実行日**: 2026-06-12 夜間
**テーマセット**: eval_themes.json (n=50)
**モデル**: llm-jp-4-8b-thinking
**context 設定**: 32768

### 観測データ

| 指標 | sc-ON | sc-OFF | 差 |
|---|---|---|---|
| 平均最終スコア | XX.XX | YY.YY | ±Z.ZZ |
| 初回合格率 | A% | B% | ±C% |
| 平均 attempt 数 | 1.XX | 1.YY | − |
| plateau 率 | P% | Q% | − |

### LM Studio 劣化チェック

sc-ON:   前半 XX.X  後半 YY.Y (差: ±Z.Z)
sc-OFF:  前半 AA.A  後半 BB.B (差: ±C.C)

→ [劣化が対称 / 異常 / 要再測定] の判定

### 判定結果

**観測差**: ±Z.ZZ 点
**ノイズ下限 (n=50)**: ±2.7 点
**判定**: 
  - [ ] 有意（sc-ON が優位）→ 保持推奨
  - [ ] 有意（sc-OFF が優位）→ 無効化推奨
  - [x] 判定不能（ノイズ内）→ 次回実験設計へ

### 考察

[自由記述]

### Next Steps

[実験の次段階、追加測定計画など]
```

---

## トラブルシューティング

### Q: eval.sh が timeout する

**A**: 
- eval.sh は 1 時間以上かかることがあります
- Bash tool の 10 分 timeout に引っかかりません（`run_in_background` 使用）
- ただし nohup で背景実行している場合は、ターミナルを閉じても実行は継続します
- 進捗は `tail -f <variant>.log` で確認

### Q: health check で `model_loaded: false` が出た

**A**:
```bash
# LM Studio が crash した可能性
# ホスト側で LM Studio を再起動
lms server stop
lms server start
lms load llm-jp-4-8b-thinking --context-length 32768
```

### Q: compare.sh が「n が一致しない」と警告する

**A**:
- 両 variant で異なるテーマファイルを指定したか確認
- 例: sc-on は eval_themes.json, sc-off は eval_themes_quick8.json など
- **必ず同一ファイルで両方を実行** (指定しなければ自動選択される)

### Q: 「テーマ X で null が出た」「schema_invalid が多い」

**A**:
- thinking モデルの出力が max_tokens で途中打ち切りされた可能性
- context 不足の可能性も
```bash
# context の確認
lms ps

# 32768 未満なら再ロード
lms load llm-jp-4-8b-thinking --context-length 32768
```

---

## クイックリファレンス

実験再開用の短版コマンドリスト：

```bash
# 全体リセット
lms unload && sleep 2 && lms load llm-jp-4-8b-thinking --context-length 32768
curl -X DELETE http://localhost:8001/api/failures

# Variant A 実行
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend && sleep 3
cd backend/eval && ./eval.sh sc-on eval_themes.json

# Interim（Variant B 前）
lms unload && sleep 2 && lms load llm-jp-4-8b-thinking --context-length 32768
curl -X DELETE http://localhost:8001/api/failures

# Variant B 実行
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=0 docker compose up -d backend && sleep 3
cd backend/eval && ./eval.sh sc-off eval_themes.json

# 比較
./compare.sh sc-on sc-off
```

---

## 実行中の禁則

⛔ eval 実行中（Step 3, 5）は以下をしないこと：

1. **backend の設定・コードを変更しない** (測定対象が変わる)
2. **LM Studio へ診断 streaming や手動チャット入力をしない**
   - 「動作確認したい」という衝動が、eval の負荷と混合し、劣化が加速する
   - FINDINGS.md §5 に実例あり
3. **複数の eval を並行実行しない**
   - 同一 LM Studio を奪い合い、両方の測定が壊れる

---

## 完了確認チェックリスト

実験完了時に以下をチェック：

- [ ] Step 0 (health check) 完了
- [ ] Step 1 (fresh reload) 完了
- [ ] Step 2 (failures リセット) 完了
- [ ] Step 3 (sc-ON eval) 完了 → `results/*sc-on*.json` 存在
- [ ] Step 4 (interim) 完了
- [ ] Step 5 (sc-OFF eval) 完了 → `results/*sc-off*.json` 存在
- [ ] Step 6 (compare.sh) 実行済み → 数値を記録
- [ ] Step 7 (劣化チェック) 実行済み → 対称性を確認
- [ ] Step 8 (結論記録) 完了 → 判定を記録

---

**これで実験は完了です。**
結論を FINDINGS.md に追記し、必要に応じて PR を作成してください。
