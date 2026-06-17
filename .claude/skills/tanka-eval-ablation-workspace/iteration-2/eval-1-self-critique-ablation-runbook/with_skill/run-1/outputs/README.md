# Self-Critique 効果測定 — 実験キット

このディレクトリには、短歌生成パイプラインの **self-critique フェーズが本当に品質に寄与しているか** を測定するための完全な実験手順書が含まれています。

---

## 📋 ドキュメント一覧

| ファイル | 目的 | 読むタイミング |
|---|---|---|
| **README.md** (このファイル) | 実験全体のガイダンス | 最初に読む |
| **EXPERIMENT_DESIGN.md** | 実験の設計・仮説・判定基準を事前登録 | 実行前に一読し、ユーザーに確認を取る |
| **RUNBOOK.md** | コマンドのコピペ手順（Step 0-8） | 実行当日、このドキュメントに従い実行 |
| **JUDGMENT_CRITERIA.md** | 結果の統計的解釈・判定フロー | 実験完了後に読み、結論を判定 |
| **QUICK_REFERENCE.md** (optional) | 短版コマンドリスト・トラブルシューティング | 必要に応じて参照 |

---

## ⚡ クイックスタート

実験をすぐ始めたい場合：

1. **RUNBOOK.md の「Pre-flight チェック」から順に実行**
2. Step 0-8 をコピペで実行（所要 2-3 時間）
3. 完了後、結果を **JUDGMENT_CRITERIA.md** で解釈
4. 判定を `FINDINGS.md` に記録

---

## 🎯 実験の目的

| 質問 | 測定方法 |
|---|---|
| **Self-critique は品質を上げるか？** | 最終スコアの平均差（ノイズ下限 ±2.7 との比較） |
| **初回合格率は改善するか？** | first_pass_rate の差（refine 必要度の低下を示唆） |
| **コストに見合う効果か？** | attempt 平均と time-to-pass の変化 |

---

## 🔍 実験の構造

```
┌─────────────────────────────────────────┐
│ Phase 1: Pre-flight Check               │
│  - health check                         │
│  - configured_model 確認                │
│  - failures リセット                     │
└──────────────┬──────────────────────────┘
               │
               ↓
┌──────────────────────────┐
│ Phase 2: Variant A (ON)  │
│ TANKA_SELF_CRITIQUE=1    │
│ 50 テーマを評価          │
│ 結果: sc-on.json        │
└──────────────┬───────────┘
               │
               ↓
┌──────────────────────────┐
│ Phase 3: Interim         │
│ Fresh reload             │
│ Failures リセット        │
└──────────────┬───────────┘
               │
               ↓
┌──────────────────────────┐
│ Phase 4: Variant B (OFF) │
│ TANKA_SELF_CRITIQUE=0    │
│ 同一 50 テーマを評価     │
│ 結果: sc-off.json       │
└──────────────┬───────────┘
               │
               ↓
┌──────────────────────────┐
│ Phase 5: 比較と判定      │
│ - compare.sh             │
│ - 劣化チェック           │
│ - 結論判定               │
└──────────────────────────┘
```

---

## 📊 判定基準（最重要）

### ノイズ下限

n=50 テーマ の場合、観測された平均スコア差が以下を超えなければ、統計的に有意差があると言えません：

**±2.7 点**

出典: `FINDINGS.md` §2, 実測 stdev ≈ 9.6 → 2SE ≈ 2.7

### 判定フロー

```
観測差 Δ = avg_final_score(sc-ON) - avg_final_score(sc-OFF)

|Δ| > 2.7?
  ├─ YES, Δ > 0  → sc-ON 有効。保持推奨。
  ├─ YES, Δ < 0  → sc-ON 有害。無効化推奨。
  └─ NO          → 判定不能。ノイズ内。再測定か判定保留。

＋ 劣化チェック: 前半/後半スコアに異常な低下がないか確認
                 → 異常があれば: この run は採用不可。再測定。
```

詳細は **JUDGMENT_CRITERIA.md** を参照。

---

## ⏱️ 所要時間

| フェーズ | 時間 |
|---|---|
| Pre-flight | 5 分 |
| Variant A (sc-ON) | 50-90 分 |
| Interim | 10 分 |
| Variant B (sc-OFF) | 50-90 分 |
| 比較・判定 | 10 分 |
| **合計** | **2-3.5 時間** |

LM Studio の状態により変動します。
夜間無人実行を想定した設計になっています。

---

## 🚀 実行手順（高レベル）

```bash
# ① 前提チェック
curl -s http://localhost:8001/api/health | python3 -m json.tool

# ② Fresh reload
lms unload && sleep 2 && lms load llm-jp-4-8b-thinking --context-length 32768

# ③ Failures リセット
curl -X DELETE http://localhost:8001/api/failures

# ④ Variant A (sc-ON)
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend && sleep 3
cd backend/eval && nohup ./eval.sh sc-on eval_themes.json > sc-on.log 2>&1 &

# ⑤ 完了を待つ（1-2 時間）
tail -f sc-on.log
# または results/ を監視: ls -lh results/*sc-on*.json

# ⑥ Interim
lms unload && sleep 2 && lms load llm-jp-4-8b-thinking --context-length 32768
curl -X DELETE http://localhost:8001/api/failures

# ⑦ Variant B (sc-OFF)
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=0 docker compose up -d backend && sleep 3
cd backend/eval && nohup ./eval.sh sc-off eval_themes.json > sc-off.log 2>&1 &

# ⑧ 完了を待つ（1-2 時間）
tail -f sc-off.log

# ⑨ 比較
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh sc-on sc-off

# ⑩ 劣化チェック
# ... (RUNBOOK.md Step 7 参照)
```

**詳細は RUNBOOK.md で確認してください。**

---

## ⚠️ 重要な注意

### 実行環境の前提

- ホスト側で LM Studio が起動済み (`lms server start`)
- モデル `llm-jp-4-8b-thinking` がロード可能
- context を 32768 に設定可能（デフォルト 8192 では context 超過で失敗）
- backend / mongo / redis / frontend が `docker compose up -d` で起動可能

### 実行中の禁則

⛔ 以下はしないこと：

- **backend の設定・コードを変更** (測定対象が変わる)
- **LM Studio へ診断 streaming や手動生成を投げる** (負荷が増し、劣化が加速)
- **複数の eval を並行実行** (LM Studio を奪い合い、両方の測定が壊れる)
- **モデルをセッション中に切り替える** (variant を汚す)

### 万が一の失敗時

- **eval.sh が timeout**: nohup で背景実行しているので大丈夫。進捗は `tail -f` で監視。
- **health check で error**: `./app/start.sh` を再実行。
- **context exceeded**: `lms load ... --context-length 32768` で再初期化。
- **LM Studio slow**: モデルを fresh reload。別日に再実行もあり。

詳細は **RUNBOOK.md** のトラブルシューティングを参照。

---

## 📈 期待される結果

### ケース 1: sc-ON が有意に高い (|Δ| > 2.7, Δ > 0)

→ **Self-critique は効果あり。保持推奨。**

詳細な分析:
- 初回合格率の改善度
- attempt 平均の削減度

### ケース 2: sc-OFF が有意に高い (|Δ| > 2.7, Δ < 0)

→ **Self-critique は有害。無効化推奨。**

深掘り:
- どのルールで特に悪化するか
- プロンプト再設計か、フェーズ削除か

### ケース 3: 判定不能 (|Δ| ≤ 2.7)

→ **ノイズ内。判定保留。さらなる測定が必要。**

次のステップ:
- n=100 への拡張
- paired design での再測定
- 判定保留のまま保持 (無害と仮定)

---

## 📝 実験後のアクション

実験完了後、以下を実施：

1. **結論を `FINDINGS.md` に記録**
   - 観測差
   - 判定根拠
   - 採用/棄却/判定不能
   - 追加実験の提案

2. **コード変更が必要な場合**
   - config.py / docker-compose.yml を修正
   - PR を作成して merge

3. **次の実験を設計**
   - 判定不能の場合: paired design などで再測定
   - 発見があった場合: 他フェーズへの影響を調査

---

## 📚 参考資料

| ドキュメント | 読むべき理由 |
|---|---|
| `CLAUDE.md` §2 | アーキテクチャ概観、パイプラインの流れ |
| `CLAUDE.md` §6.13 | LM Studio 劣化の環境制約 |
| `FINDINGS.md` §2 | variance 実測、ノイズ下限の導出 |
| `FINDINGS.md` §5.5 | 前回の sc-on/sc-off 実験（教訓） |
| `tanka-eval-ablation` SKILL.md | eval 規律のマスターガイド |
| `app/docs/architecture.md` | パイプラインの詳細 |
| `app/backend/config.py` | 設定可能な環境変数一覧 |

---

## 🎓 このドキュメントセットの由来

このキットは `tanka-eval-ablation` スキルに従って設計されました。
目的は「短歌生成パイプラインの変更効果を、統計的に正しく測定する」こと。

**大原則**:
- 「2-3 点上がった」は改善ではなく、サイコロの目である
- ノイズ下限を事前登録し、それ以下の差は判定不能とする
- 交絡（LM Studio 劣化など）を検出し、run の信頼性を査定する
- 判定不能も知見として報告する（無責任な断定は避ける）

---

## ✅ 実行チェックリスト

実験完了時に、以下をチェック：

- [ ] EXPERIMENT_DESIGN.md で実験設計を確認した
- [ ] RUNBOOK.md Step 0 (health check) を実行した
- [ ] Step 1 (fresh reload) を実行した
- [ ] Step 2 (failures リセット) を実行した
- [ ] Step 3 (sc-ON eval) を完了した → `results/*sc-on*.json` が存在
- [ ] Step 4 (interim) を実行した
- [ ] Step 5 (sc-OFF eval) を完了した → `results/*sc-off*.json` が存在
- [ ] Step 6 (compare.sh) を実行した → 数値を記録した
- [ ] Step 7 (劣化チェック) を実行した → 対称性を確認した
- [ ] Step 8 (結論記録) を完了した
- [ ] JUDGMENT_CRITERIA.md で結論を判定した
- [ ] 結果を `app/backend/eval/FINDINGS.md` に追記した

---

## 📞 問題が生じた場合

1. RUNBOOK.md のトラブルシューティングセクションを確認
2. `app/backend/eval/` の既存ドキュメント (README.md, FINDINGS.md) を確認
3. 環境的な問題 (LM Studio 劣化など) の可能性を検討
4. 判断に迷う場合は、結論保留で記録 (判定不能も価値のある結論)

---

**実験の成功を願っています。データドリブンな開発を！**

実行中に質問や問題があれば、RUNBOOK.md のセクション参照か、
ここのドキュメントの該当箇所を確認してください。
