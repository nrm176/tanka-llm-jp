# プリフライト チェックリスト

実験実行直前に以下を確認してください。

---

## 環境確認

### [ ] backend + LM Studio の状態

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

**確認項目**:
- `"status": "ok"` ← backend が応答している
- `"lm_studio_ok": true` ← LM Studio が接続している
- `"model_loaded": true` ← モデルがロード済み
- `"configured_model": "llm-jp-4-8b-thinking"` ← 意図したモデル

❌ 何かが false の場合:
```bash
# backend を再起動
./app/start.sh

# または個別起動
cd app && docker compose up -d backend
```

---

## LM Studio fresh reload

### [ ] 直前の負荷確認

LM Studio が以下のいずれかに該当する場合は fresh reload を実施:

- **直近 20 分以内に eval を実行した** → reload 必須
- **直近 1 時間以内に手動生成を 10 回以上投げた** → reload 推奨
- **20 分以上アイドル** → reload 不要

fresh reload が必要な場合:

```bash
# ホスト側で (container ではなく)
lms stop
sleep 5
lms server start
sleep 10

# 再接続確認
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
# true が返ってくれば OK
```

---

## docker-compose.yml 編集確認

### [ ] 環境変数が追加されたか

```bash
grep "TANKA_W_REPEATED_WORD" /Users/nrm176p/GitHub2/LLM/app/docker-compose.yml
```

**期待出力**:
```
      TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}
```

❌ 見つからない場合:

`docker-compose-diff.patch` の内容を参照して、`app/docker-compose.yml` の
`backend.environment` セクションに以下の行を追加:

```yaml
TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}
```

---

## 実験設計の理解

### [ ] ノイズ下限を理解した

**判定基準 (n=50 の場合)**:

| スコア差 | 判定 |
|---|---|
| > +2.7 | ✅ 改善 (採用の検討) |
| ±2.7 以内 | ⚪ 判定不能 (ノイズ内) |
| < -2.7 | ❌ 劣化 (不採用) |

**重要**: 「+1.5 向上した」のような 2.7 以内の差は、統計的には
ランダムノイズと区別できません。この幅を超えて初めて結論を出します。

参考: SKILL.md §1 の「大原則」セクション

### [ ] 交絡対策を理解した

**failures リセット** (必須):
```bash
curl -X DELETE http://localhost:8001/api/failures
```

**理由**: 重み変更は lesson の優先順位に影響するため、蓄積記憶を
クリアしないと「治療効果」と「記憶の効果」が混ざってしまう

**実施タイミング**: 各 variant (baseline / treatment) の直前

---

## 実行環境確認

### [ ] ディスク容量が十分か

```bash
df -h /Users/nrm176p/GitHub2/LLM
```

**必要**: 最低 2GB (50 お題 × 2 runs で mongo に蓄積)

### [ ] ホストのスリープ対策

**長時間実行のため、以下を推奨**:

```bash
# eval 実行中の自動スリープを無効化
caffeinate -dims bash run-experiment.sh
```

または、System Preferences で一時的にスリープ無効化

---

## スクリプト確認

### [ ] run-experiment.sh が実行可能か

```bash
ls -l /Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-2/eval-2-weight-env-passthrough-trap/with_skill/run-1/outputs/run-experiment.sh
```

**出力例** (最初の部分が `rwx` なら OK):
```
-rwxr-xr-x  1 nrm176p  staff  5.8K Jun 12 19:59 run-experiment.sh
```

❌ `rw-` の場合は実行可能に変更:
```bash
chmod +x run-experiment.sh
```

---

## 最終確認

### [ ] 上記をすべて確認した

以下を実行して最終確認:

```bash
# 1. health check
curl -s http://localhost:8001/api/health | jq '.status, .lm_studio_ok, .model_loaded, .configured_model'

# 期待出力:
# "ok"
# true
# true
# "llm-jp-4-8b-thinking"

# 2. docker-compose.yml 確認
grep "TANKA_W_REPEATED_WORD" /Users/nrm176p/GitHub2/LLM/app/docker-compose.yml

# 期待出力:
# TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}

# 3. スクリプト確認
file /Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-2/eval-2-weight-env-passthrough-trap/with_skill/run-1/outputs/run-experiment.sh

# 期待: executable
```

---

## 実行コマンド

すべての確認が完了したら、以下を実行:

**オプション 1: 自動スクリプト**
```bash
bash /Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-2/eval-2-weight-env-passthrough-trap/with_skill/run-1/outputs/run-experiment.sh | tee experiment.log
```

**オプション 2: 手動実行**

`EVAL_RUNBOOK.md` の Step 2～4 に従い、コマンドを手動実行

---

## 実行中の確認

### [ ] baseline 実行中

```bash
# ログで進捗を監視
docker compose logs -f backend | grep -E 'theme|score|refine'

# または (リアルタイム監視)
watch -n 5 'ls -lrt /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | tail -1'
```

**所要時間**: 25～60 分 (LM Studio の負荷によって変動)

### [ ] treatment 実行中

baseline と同様に進捗を監視

---

## トラブル時

### env が反映されない

```bash
# ✗ これは効かない
export TANKA_W_REPEATED_WORD=12
docker compose up -d backend

# ✓ これが正しい
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# 確認
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD
```

### "Context size has been exceeded" エラー

```bash
lms stop
lms load llm-jp-4-8b-thinking --context-length 32768
lms server start
sleep 10
curl -s http://localhost:8001/api/health | jq .lm_studio_ok
```

### eval.sh が進まない

```bash
# ログを確認
docker compose logs backend | tail -50

# または process を確認
ps aux | grep eval

# もし止めたければ (PID を控える)
kill <PID>
```

---

## 完了後

### [ ] 結果ファイルを確認

```bash
ls -lrt /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/ | tail -2
```

**期待**: w-repeated-3*.json と w-repeated-12*.json が出現

### [ ] 比較実行

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh w-repeated-3 w-repeated-12
```

### [ ] 結果を記録

`app/backend/eval/FINDINGS.md` に以下形式で追記:

```markdown
### Experiment: repeated_word weight (3 vs 12)

**Date**: 2026-06-12
**Model**: llm-jp-4-8b-thinking
**Themes**: 50 (eval_themes.json)
**N**: 50

| Metric | Baseline (w=3) | Treatment (w=12) | Δ |
|---|---|---|---|
| avg_final_score | XX.X | YY.Y | ZZ.Z |
| repeated_word residual rate | AA.A% | BB.B% | CC.C% |

**Judgment**: [改善 / 判定不能 / 劣化]
**Reasoning**: スコア差 ZZ.Z は判定基準 ±2.7 を [超えた/超えない] ため...

**Notes**: ...
```

---

**準備OK。実験を開始してください！** 🚀

