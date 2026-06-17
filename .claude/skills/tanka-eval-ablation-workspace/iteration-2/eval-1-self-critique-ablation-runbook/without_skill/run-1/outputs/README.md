# Self-Critique 効果測定実験 — 完全実行手順書

このディレクトリは、短歌生成パイプラインの **self-critique フェーズが品質に効いているか** を測定するための **完全な無人実行可能な手順書** です。

---

## 📋 ドキュメント構成

| ファイル | 目的 | 読むタイミング |
|---|---|---|
| **README.md** (このファイル) | 実験全体の概要とガイダンス | 最初に読む |
| **EXPERIMENT_DESIGN.md** | 実験設計・仮説・交絡対策を詳細記述 | 実行前に確認 |
| **RUNBOOK.md** | Step 0-8 のコピペ手順書（全コマンド完全記載） | 実行当日に従う |
| **JUDGMENT_CRITERIA.md** | 結果の統計的解釈・判定フロー | 実験完了後に参照 |
| **CHECKLIST.md** | 紙チェックリスト版（タイムスタンプ記録欄付き） | 当日持参用 |
| **SUMMARY.txt** | 実験概要と各ドキュメントの役割の要約 | 必要に応じて参照 |

---

## ⚡ クイックスタート（今すぐ始める場合）

1. **このファイルを最後まで読む** (5 分)
2. **EXPERIMENT_DESIGN.md の「交絡対策」を精読** (10 分) — 最重要
3. **RUNBOOK.md の「Pre-flight チェック」から順に実行** (2-3 時間)
4. 完了後、結果を **JUDGMENT_CRITERIA.md** で判定
5. 判定結果を notes.md または別紙に記録

---

## 🎯 実験の目的

### 主問い
**Self-critique フェーズは本当に短歌の品質を向上させるのか？コストに見合う効果があるのか？**

### 測定対象

| 指標 | 測定方法 | 期待方向 |
|---|---|---|
| **平均最終スコア** | 50 テーマ × 2 arm (ON/OFF) の avg_final_score | ON > OFF ならば効果あり |
| **初回合格率** | first_attempt_pass_rate | ON > OFF なら refine 必要度低下 |
| **平均 attempt 数** | avg_attempts | ON < OFF なら効率的改稿 |
| **Plateau 率** | plateau_rate | ON < OFF なら改善し続ける |

---

## 📊 判定基準（事前登録）

### ノイズ下限（最重要）

n=50 テーマでは、観測された平均スコア差がこの値を**超えない限り**、統計的に有意差があると言えません：

**±2.7 点** (出典: FINDINGS.md §2, 実測値から導出)

### 判定フロー

```
観測差 Δ = avg_final_score(sc-ON) - avg_final_score(sc-OFF) を測定
           ↓
        |Δ| > 2.7?
           ├─ YES, Δ > 0  → sc-ON が有効。保持推奨
           ├─ YES, Δ < 0  → sc-ON が有害。無効化推奨
           └─ NO         → 判定不能。ノイズ内の差
```

### 判定テンプレート

実験完了後、結果を以下のフォーマットで記録してください：

```
【結果】
観測差: <Δ値> 点
ノイズ幅: ±2.7
判定: [有効/有害/判定不能] のいずれか

【詳細】
- sc-ON avg_final_score: <値>
- sc-OFF avg_final_score: <値>
- 前半/後半劣化チェック: [OK/NG]
- その他の指標 (first_pass_rate, avg_attempts など): <記述>
```

---

## ⚙️ 実験の流れ（大まかな構造）

```
【準備フェーズ】
├─ Health チェック
├─ Configured model 確認（UI による切替を検出）
├─ LM Studio Fresh Reload (unload → load 32768)
└─ Failures リセット

【Variant A: self-critique ON】
├─ Backend 再起動 (TANKA_SELF_CRITIQUE=1)
├─ 50 テーマを順序固定で評価 (eval.sh sc-on)
└─ 結果: results/<ts>-sc-on.json

【中間処理】
├─ Failures リセット
└─ LM Studio Fresh Reload

【Variant B: self-critique OFF】
├─ Backend 再起動 (TANKA_SELF_CRITIQUE=0)
├─ 同一 50 テーマを同一順序で評価 (eval.sh sc-off)
└─ 結果: results/<ts>-sc-off.json

【判定フェーズ】
├─ 結果を比較 (compare.sh sc-on sc-off)
├─ 前半/後半劣化チェック
├─ ノイズ下限と照合
└─ 判定結果を記録
```

**所要時間**: 2〜3.5 時間（LM Studio の状態による）

---

## 🔍 この実験が重要な理由

### 背景: Phase 1 B4 で導入された self-critique

短歌生成パイプラインの Compose フェーズ直後に、LLM に生成した短歌を自己点検させるステップが追加されました。
- コスト: +1 LLM call / 生成
- 期待効果: 季語・拍数・季節一貫性などの検査により、Compose の質を上げる

### 前回の実験 (2026-06-04, FINDINGS.md §5.5)

n=16 テーマの blocked A/B 実験を実施しましたが、**結果は判定不能**：
- 観測差: +4.69 点（sc-OFF が上）
- ノイズ下限: ±4.8
- 交絡検査で「前半は ON が優れていたが、後半の LM Studio 劣化で逆転」が判明
- 符号すら不安定 → 結論を出せず

### 今回の改善（n=50, 交絡対策強化）

- **テーマ数を 3 倍に** (16 → 50) して ノイズ幅を 33% に縮小 (±4.8 → ±2.7)
- **LM Studio 劣化を前提に対策** (fresh reload, failures リセット, 前半/後半チェック)
- **実験設計を事前登録** (p-hacking 防止)

---

## 📌 注意点（実行前に必ず読む）

### 1. 環境変数の適用には `docker compose up -d` が必須

```bash
# ❌ 間違い: restart では env が反映されない
docker compose restart backend

# ✅ 正しい: up -d で env を再読み込みしてコンテナ再起動
TANKA_SELF_CRITIQUE=0 docker compose up -d backend
```

### 2. LM Studio Fresh Reload は毎 variant 前に実施

```bash
lms unload  # KV キャッシュをクリア
lms load llm-jp-4-8b-thinking --context-length 32768  # 初期化状態で再起動
```

**理由**: sustained load 下で LM Studio の性能が劣化する (実測: 15s/call → 3.5min/call)

### 3. Failures リセットを忘れずに

```bash
curl -X DELETE http://localhost:8001/api/failures
```

**理由**: 長期失敗記憶が run をまたいで蓄積し、後の variant が恩恵を受けるため、順序効果が生じる

### 4. Configured model を確認する

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

UI から手動でモデルを切り替えると、eval の結果を汚す。
必要に応じて `llm-jp-4-8b-thinking` に戻す。

### 5. Eval.sh は最大 10 分超かかる可能性がある

```bash
# ターミナル分割で background で実行し、ログを監視することを推奨
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./eval.sh sc-on &  # background 実行
tail -f results/<最新タイムスタンプ>.log  # 進捗監視（ない場合は curl で確認）
```

---

## 📖 詳細ドキュメント

- **EXPERIMENT_DESIGN.md**: 実験設計・仮説・交絡対策の詳細
- **RUNBOOK.md**: Step 0-8 のコピペ手順書（全コマンド完全掲載）
- **JUDGMENT_CRITERIA.md**: 結果の統計的解釈フロー（ケース別テンプレート）
- **CHECKLIST.md**: 紙版チェックリスト（当日持参用）

---

## 🎓 参考資料

| 資料 | 理由 |
|---|---|
| `CLAUDE.md` §6.13 | LM Studio sustained-load 劣化の環境制約 |
| `CLAUDE.md` §6.14 | Context 8192 での JIT ロード失敗（32768 を指定する理由） |
| `FINDINGS.md` §2 | スコアは確率変数（ノイズ下限表、平均の標準誤差） |
| `FINDINGS.md` §5.5 | 前回の sc-on/sc-off 実験（交絡の実例教訓） |
| `app/backend/config.py` | TANKA_* 環境変数の定義 |

---

## ✅ 実行前のチェックリスト

実験を開始する前に、以下を確認してください：

- [ ] Docker が起動している: `docker ps`
- [ ] Backend コンテナが起動している: `docker compose ps`
- [ ] LM Studio が起動している: `lms ps`（host 側）
- [ ] Configured model が `llm-jp-4-8b-thinking` である: `curl http://localhost:8001/api/health`
- [ ] このディレクトリ内のすべてのドキュメントを読んだ（特に EXPERIMENT_DESIGN.md）
- [ ] notes.md を準備した（実行当日にログを記録）

すべて確認できたら、**RUNBOOK.md の「Pre-flight チェック」から開始** してください。

---

## 🔗 関連リンク

- 短歌生成パイプライン: `/Users/nrm176p/GitHub2/LLM/app/backend/tanka.py`
- Self-critique 実装: `tanka.py` L216-227
- 評価スコアリング: `/Users/nrm176p/GitHub2/LLM/app/backend/validator.py`
- 評価ハーネス: `/Users/nrm176p/GitHub2/LLM/app/backend/eval/`
