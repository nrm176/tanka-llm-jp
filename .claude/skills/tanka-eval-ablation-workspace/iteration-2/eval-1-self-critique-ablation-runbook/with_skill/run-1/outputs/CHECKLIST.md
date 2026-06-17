# Self-Critique 効果測定 — 実行チェックリスト

実験当日に使用してください。段階的にチェックを入れながら進めます。

---

## 📋 実験実施前（ユーザーへの確認）

- [ ] **ユーザーに実験設計を確認した**
  - 対象: EXPERIMENT_DESIGN.md
  - 確認項目: 問い、テーマセット (n=50)、判定基準 (±2.7)、交絡対策
  
- [ ] **failures リセットについて確認を取った**
  - 実施内容: 長期失敗記憶 (lesson) をクリアする
  - 了承: [ ] 承認 / [ ] 保留 / [ ] スキップ
  
- [ ] **所要時間を伝えた**
  - 見積: 2-3.5 時間（夜間無人実行推奨）
  - 確認: [ ] 了解 / [ ] 別日に変更

---

## 🔧 Pre-flight (実験前日〜直前)

### 環境チェック

- [ ] LM Studio がホストで起動している
  ```bash
  lms server start    # 必要に応じて
  ```

- [ ] Backend が起動可能か確認
  ```bash
  cd /Users/nrm176p/GitHub2/LLM/app
  docker compose logs backend | tail -5   # error がないか
  ```

- [ ] mongo / redis が起動している
  ```bash
  docker compose ps | grep -E "mongo|redis"
  ```

### LM Studio の状態確認

- [ ] モデル `llm-jp-4-8b-thinking` がロード可能
  ```bash
  lms ps
  # → llm-jp-4-8b-thinking がロード可能な状態であること
  ```

- [ ] 直前に大規模な生成作業がなかった
  - [ ] eval 実行中でない
  - [ ] 診断 streaming を流していない
  - [ ] 長時間の連続負荷がない

---

## ⏱️ Step 0: Health Check (実験開始当日)

タイムスタンプ: `__________`

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

- [ ] `status: ok`
- [ ] `lm_studio_ok: true`
- [ ] `model_loaded: true`
- [ ] `configured_model: llm-jp-4-8b-thinking`

❌ エラー時:
```bash
# backend 再起動
cd /Users/nrm176p/GitHub2/LLM/app && ./start.sh
```

---

## 🔄 Step 1: Fresh Reload

```bash
lms ps                                  # 現在の状態確認
lms unload                              # アンロード
sleep 2
lms load llm-jp-4-8b-thinking --context-length 32768
lms ps                                  # 確認: loaded 表示
```

- [ ] `llm-jp-4-8b-thinking` がロード済み
- [ ] context が 32768 （⚠️ 8192 では不足）

---

## 🗑️ Step 2: Failures リセット

```bash
curl -X DELETE http://localhost:8001/api/failures
```

- [ ] 削除成功 (JSON response)

---

## 🟢 Step 3: Variant A (sc-ON)

### Backend 再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend
sleep 3
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

- [ ] `TANKA_SELF_CRITIQUE=1` が確認できた

### Health チェック

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

- [ ] `status: ok`

### 評価実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
nohup ./eval.sh sc-on eval_themes.json > sc-on.log 2>&1 &
PID=$!
echo "sc-on PID=$PID"
```

**記録**: 
- PID: `__________`
- 開始時刻: `__________`

### 進捗監視（定期的に確認）

```bash
tail -20 sc-on.log
# または
ls -lh results/*sc-on*.json
```

- [ ] ログが出力されている (Test X of 50)
- [ ] エラーが見えない

### 完了待機

```bash
# 1-2 時間待機
# 進捗: results/ に sc-on の JSON ファイルが出現するまで
ls -lh /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/*sc-on*.json
```

**ファイル名**: `__________`
- [ ] ファイルサイズ > 900 bytes

---

## 🔄 Step 4: Interim

### Fresh Reload

```bash
lms unload
sleep 2
lms load llm-jp-4-8b-thinking --context-length 32768
lms ps
```

- [ ] ロード完了

### Failures リセット

```bash
curl -X DELETE http://localhost:8001/api/failures
```

- [ ] 削除成功

---

## 🔴 Step 5: Variant B (sc-OFF)

### Backend 再起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=0 docker compose up -d backend
sleep 3
docker compose exec backend printenv | grep TANKA_SELF_CRITIQUE
```

- [ ] `TANKA_SELF_CRITIQUE=0` が確認できた

### Health チェック

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

- [ ] `status: ok`

### 評価実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
nohup ./eval.sh sc-off eval_themes.json > sc-off.log 2>&1 &
PID=$!
echo "sc-off PID=$PID"
```

**記録**:
- PID: `__________`
- 開始時刻: `__________`

### 進捗監視

```bash
tail -20 sc-off.log
```

- [ ] ログ出力あり
- [ ] エラーなし

### 完了待機

```bash
ls -lh /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/*sc-off*.json
```

**ファイル名**: `__________`
- [ ] ファイルサイズ > 900 bytes

---

## 📊 Step 6: 比較と判定

### Compare 実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh sc-on sc-off
```

**出力を記録**:

```
テーマファイル: eval_themes.json
sc-on:  n=____, avg_final_score=____.__, pass_rate=__%, ...
sc-off: n=____, avg_final_score=____.__, pass_rate=__%, ...

Difference: +____.__ points
```

- [ ] n が両方とも 50 （一致確認）
- [ ] _themes_file が `eval_themes.json` で一致

### 観測差の記録

- 平均最終スコア差: `+____.__ 点` or `-____.__ 点`
- 初回合格率差: `+____ %` or `-____ %`
- Attempt 平均差: (記録)

---

## 🔍 Step 7: 劣化チェック

### sc-ON の前半/後半スコア

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
RESULT_FILE=$(ls -1tr results/*sc-on*.json | tail -1)
SID=$(python3 -c "import json; print(json.load(open('$RESULT_FILE'))['_session_id'])")
curl -s "http://localhost:8001/api/sessions/$SID" | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
sc = [m['final_score'] for m in data.get('messages', [])
      if m.get('kind') == 'tanka' and m.get('final_score') is not None]
if len(sc) >= 2:
    h = len(sc) // 2
    first = sum(sc[:h]) / len(sc[:h])
    second = sum(sc[h:]) / (len(sc) - h)
    print(f'sc-ON:  前半 {first:.1f}  後半 {second:.1f}  (低下 {first - second:.1f} 点)')
EOF
```

**記録**: `__________________________________________________`

- [ ] 低下幅が 3-5 点 (正常) / 10 点以上 (異常)

### sc-OFF の前半/後半スコア

```bash
RESULT_FILE=$(ls -1tr results/*sc-off*.json | tail -1)
SID=$(python3 -c "import json; print(json.load(open('$RESULT_FILE'))['_session_id'])")
curl -s "http://localhost:8001/api/sessions/$SID" | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
sc = [m['final_score'] for m in data.get('messages', [])
      if m.get('kind') == 'tanka' and m.get('final_score') is not None]
if len(sc) >= 2:
    h = len(sc) // 2
    first = sum(sc[:h]) / len(sc[:h])
    second = sum(sc[h:]) / (len(sc) - h)
    print(f'sc-OFF: 前半 {first:.1f}  後半 {second:.1f}  (低下 {first - second:.1f} 点)')
EOF
```

**記録**: `__________________________________________________`

### 劣化パターン判定

```
sc-ON 低下:   ±____._ 点
sc-OFF 低下:  ±____._ 点

劣化が対称か: [ ] はい / [ ] いいえ
```

- [ ] 対称 (両者の低下幅 ≈ 3-5 点) → OK、この run は有効
- [ ] 非対称 (片側が 10+ 点低下) → 要注意、別の run を検討

---

## ✅ Step 8: 結論判定

**観測差**: `+____.__ 点` (sc-ON 基準)

**ノイズ下限**: `±2.7 点` (n=50)

**判定ロジック**:

```
|観測差| > 2.7?

YES かつ 観測差 > 0:
  ☑️ sc-ON が有意に優位 → 採用推奨
  初回合格率: ◻️ 改善 / ◻️ 不変
  Attempt 平均: ◻️ 削減 / ◻️ 不変
  
YES かつ 観測差 < 0:
  ☑️ sc-OFF が有意に優位 → 棄却推奨
  理由: [ ] 点検指示が悪化 / [ ] 不明
  
NO:
  ☑️ 判定不能 → ノイズ内
  次ステップ: [ ] n=100拡張 / [ ] paired再測定 / [ ] 保留
```

**最終判定**: 
- [ ] 採用推奨（sc-ON 有効）
- [ ] 棄却推奨（sc-ON 有害）
- [ ] 判定不能（ノイズ内）

---

## 📝 Step 9: 記録と報告

### FINDINGS.md に追記

```markdown
## Self-Critique 効果測定 (2026-06-12)

**テーマセット**: eval_themes.json (n=50)
**モデル**: llm-jp-4-8b-thinking
**context**: 32768

| 指標 | sc-ON | sc-OFF | 差 |
|---|---|---|---|
| 平均最終スコア | ____.__ | ____.__ | ±____.__ |
| 初回合格率 | __% | __% | ±__% |

**劣化チェック**:
sc-ON  前半 ____._ 後半 ____._ (低下 __._)
sc-OFF 前半 ____._ 後半 ____._ (低下 __._)
→ 対称的

**判定**: 有意 / 無意 / 判定不能
**結論**: [採用 / 棄却 / 保留]
```

- [ ] FINDINGS.md に記録した
- [ ] 数値が正確に転記されている

### PR 作成（変更が必要な場合）

- [ ] コード変更不要 (保持推奨の場合)
  または
- [ ] config.py / docker-compose.yml を変更
- [ ] PR タイトル: `feat: [disable/keep] self-critique (效果測定)`
- [ ] PR 本文に数値と判定根拠を記載

---

## ✨ 完了確認

すべてのステップが完了したら：

- [ ] RUNBOOK.md の 8 ステップを全実行
- [ ] compare.sh で数値を確認
- [ ] 劣化チェックで異常がない
- [ ] 結論を判定した
- [ ] FINDINGS.md に記録した

**実験終了。お疲れさまでした！**

---

## 🆘 トラブル時の緊急対応

| 問題 | 対応 |
|---|---|
| eval timeout | 続行（nohup で背景実行）。進捗を tail -f で監視 |
| health error | `./app/start.sh` を再実行 |
| context exceeded | `lms load ... --context-length 32768` で再初期化 |
| LM Studio slow (3+分/call) | 別日に再実行 / モデル fresh reload |
| compare.sh で n 不一致 | テーマファイルを確認 (eval_themes.json で揃える) |
| 劣化が異常に大きい | この run は採用不可。再測定 |

詳細は **RUNBOOK.md** のトラブルシューティング参照。

---

**用紙を印刷して、実験当日に手元に置くことをお勧めします。**
