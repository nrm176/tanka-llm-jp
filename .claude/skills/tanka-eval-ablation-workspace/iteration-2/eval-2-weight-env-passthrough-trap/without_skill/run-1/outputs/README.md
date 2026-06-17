# repeated_word ルール重み A/B 測定セット

## 概要

`validator.repeated_word` ルールの重みを **3 → 12** に上げた場合の効果を測定するための **準備資料一式**。

実際の eval 実行と docker 操作は**含まない** (制約のため)。
このセットで完全な測定ができるように設計。

---

## ファイル一覧

### 1. DIFF-repeated-word-weight-ablation.patch
- **形式**: Unified diff (.patch)
- **対象**: app/backend/validator.py
- **変更内容**: line 61 の デフォルト値 3 → 12
- **用途**:
  - git で変更を追跡したい場合
  - または `patch` コマンドで直接適用
- **使い方**:
  ```bash
  cd /Users/nrm176p/GitHub2/LLM
  patch -p0 < DIFF-repeated-word-weight-ablation.patch
  docker compose restart backend
  ```

### 2. RUNBOOK.md
- **形式**: Markdown (日本語)
- **内容**: A/B 測定の手順書 (5000 文字)
- **含まれるセクション**:
  - タスク概要
  - 環境変数の仕組み (方法 1, 2 の説明)
  - Step-by-step 実行手順 (推奨フロー)
  - メトリクス解釈ガイド
  - トラブルシューティング
  - 参考: Config 設定値
- **推奨**: 最初に全文読んで全体像を把握

### 3. ENV-VAR-PASSTHROUGH-GUIDE.md
- **形式**: Markdown (日本語)
- **内容**: 環境変数を docker-compose に通す仕組みの詳細ガイド (4500 文字)
- **含まれるセクション**:
  - 問題背景
  - 3 つのメカニズム (シェル env, .env ファイル, YAML 編集)
  - 現プロジェクトでの推奨手順
  - トラブルシューティング (変数が通らない場合)
  - チートシート (最小限コマンド)
  - 一体化スクリプト例
- **推奨**: 環境変数通過に確実性がないなら精読

### 4. EXPECTED-RESULTS.md
- **形式**: Markdown (日本語)
- **内容**: 期待される測定結果と解釈ガイド (4000 文字)
- **含まれるセクション**:
  - ルール仕様の復習
  - 期待される効果 (5 パターン)
  - 解釈ガイド (ポジティブ / 注意 / 判定困難なケース)
  - A/B 測定の実行例
  - 判定チェックリスト
  - 異なる重みでの多段階テスト案
- **推奨**: eval 実行前と実行後の両方で参照

### 5. README.md (このファイル)
- **形式**: Markdown (日本語)
- **内容**: ファイル一覧とクイックスタート

---

## クイックスタート

### 最小限の手順

```bash
# 1. リポジトリに移動
cd /Users/nrm176p/GitHub2/LLM

# 2. Baseline 測定 (現行: weight=3)
docker compose down
docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh baseline

# 3. Variant 測定 (weight=12)
cd /Users/nrm176p/GitHub2/LLM
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh variant-w12

# 4. 比較
./compare.sh baseline variant-w12

# 5. 復帰
cd /Users/nrm176p/GitHub2/LLM
docker compose down
docker compose up -d backend
```

### 詳細な手順

RUNBOOK.md の「Step 1-3」を順に実行。

---

## ファイル読む順序

### シナリオ A: 急いでいる (5 分)
1. **README.md** (このファイル) — 全体像把握
2. **RUNBOOK.md** 「Step 1-3」 — 実行手順だけ取得
3. eval を回す

### シナリオ B: 標準的 (20 分)
1. **README.md** — 全体像
2. **RUNBOOK.md** 全文 — 仕組みと手順
3. **EXPECTED-RESULTS.md** 「期待結果」セクション — 何が見えるか予習
4. eval を回す
5. **EXPECTED-RESULTS.md** 「解釈ガイド」セクション — 結果を読む

### シナリオ C: 完璧主義 (40 分)
1. **README.md** — 全体像
2. **ENV-VAR-PASSTHROUGH-GUIDE.md** 全文 — 環境変数の仕組み完全理解
3. **RUNBOOK.md** 全文 — A/B 測定の全手順
4. **EXPECTED-RESULTS.md** 全文 — 期待結果と解釈
5. eval を回す

---

## よくある質問

### Q: どのファイルから読み始めればいい?
A: このファイル (README.md) を 2-3 分で読む → RUNBOOK.md の「Step 1-3」へ

### Q: 環境変数がどうしても通らない場合は?
A: ENV-VAR-PASSTHROUGH-GUIDE.md の「トラブルシューティング」セクションを参照

### Q: eval の結果を解釈するには?
A: EXPECTED-RESULTS.md の「解釈ガイド」セクション

### Q: 単純にコマンドだけ知りたい
A: RUNBOOK.md の「メリット」「デメリット」の折りたたみセクション、または
   ENV-VAR-PASSTHROUGH-GUIDE.md の「環境変数指定のチートシート」

### Q: diff を適用するなら?
A: DIFF-repeated-word-weight-ablation.patch を使用:
   ```bash
   patch -p0 < DIFF-repeated-word-weight-ablation.patch
   docker compose restart backend
   ```

---

## 測定結果の保存先

eval は自動的に結果を保存:
```bash
app/backend/eval/results/20YYMMDDhhmmss-<variant>.json
```

例:
- `20260612-140000-baseline.json` (baseline 測定結果)
- `20260612-150000-variant-w12.json` (variant 測定結果)

これらは JSON 形式で以下の情報を含む:
- `total`: テーマ総数
- `first_attempt_pass_rate`: 初回合格率
- `overall_pass_rate`: 総合合格率
- `avg_attempts`: 平均試行回数
- `avg_final_score`: 平均最終スコア
- `plateau_rate`: 改善停滞率
- `max_refines_rate`: hard-cap 到達率
- `violation_frequency`: ルール別違反出現数
- `score_buckets`: スコア分布

---

## 意思決定フロー

```
eval 実行 (baseline + variant-w12)
    ↓
compare.sh で結果を確認
    ↓
┌─ repeated_word 違反が 2x 以上に増加?
│  ├─ YES → 検出が機能している ✓
│  └─ NO → 重みが通っていない可能性 → ENV-VAR-PASSTHROUGH-GUIDE 参照
│
├─ 合格率が -5% 以内で収まっている?
│  ├─ YES → ✓ 実行可能性に支障なし
│  └─ NO → ⚠ 重すぎる可能性 → 中間値 (6-9) を検討
│
├─ Plateau 率が +15% 以内?
│  ├─ YES → ✓ 改善停滞が悪化していない
│  └─ NO → ⚠ 改善が困難になっている → 重み戻すか検討
│
└─ 判定
   ├─ 全て YES → **重み 12 を採用可能**
   ├─ 一部 NO → **variance 測定で確実化** (eval-repeat.sh)
   └─ 多数 NO → **重み 12 は過度。3 に戻すか中間値検討**
```

---

## 関連コマンド (リファレンス)

### eval 関連
```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

./eval.sh <variant-name> [themes.json] [backend-url]
# 固定お題セットを生成。結果は results/ に JSON で保存

./compare.sh <before-variant> <after-variant>
# 2 つの eval 結果を比較。差分を表示

./eval-repeat.sh "<theme>" [N] [backend-url]
# 同一お題を N 回生成。score の分布を測定
```

### docker 関連
```bash
cd /Users/nrm176p/GitHub2/LLM

# backend を標準設定で起動
docker compose up -d backend

# backend を環境変数付きで起動
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# ログ確認
docker compose logs -f backend

# 停止・削除
docker compose down

# コンテナ内で確認
docker compose exec backend python3 -c "import os; print(os.environ.get('TANKA_W_REPEATED_WORD'))"
```

### git 関連 (重み変更を記録する場合)
```bash
cd /Users/nrm176p/GitHub2/LLM

# diff を表示
git diff app/backend/validator.py

# diff をファイルに保存
git diff app/backend/validator.py > my-changes.patch

# diff を適用
patch -p1 < my-changes.patch

# 変更を戻す
git checkout app/backend/validator.py
```

---

## トラブルシューティング (クイック)

| 問題 | 確認方法 | 対策 |
|---|---|---|
| backend が起動しない | `curl http://localhost:8001/api/health` | docker-compose logs backend で原因確認 |
| 環境変数が通らない | `docker compose exec backend env \| grep TANKA` | ENV-VAR-PASSTHROUGH-GUIDE 参照 |
| eval が「no task_id」で落ちる | LM Studio が起動しているか | ホスト側で `lms server start` |
| 結果が期待と異なる | compare.sh 出力を確認 | EXPECTED-RESULTS.md で解釈 |
| コンテナが重い / スローダウン | `docker ps` で他プロセス確認 | `docker compose down` で完全クリア |

---

## ファイル統計

| ファイル | 行数 | 概要 |
|---|---|---|
| DIFF-repeated-word-weight-ablation.patch | 13 | 重み変更のみ |
| RUNBOOK.md | 350 | A/B 測定の完全手順 |
| ENV-VAR-PASSTHROUGH-GUIDE.md | 280 | 環境変数通過の詳細 |
| EXPECTED-RESULTS.md | 300 | 期待結果と解釈 |
| README.md (本ファイル) | 200 | ガイド総合 |
| **合計** | **~1140** | **完全なセット** |

---

## 作成日時と対象バージョン

- **作成**: 2026-06-12
- **対象リポジトリ**: `/Users/nrm176p/GitHub2/LLM`
- **対象ブランチ**: `feat/27-generation-duration` (current)
- **backend 実装**: FastAPI + pymongo + redis-py
- **validator**: Python 3.10+

---

## 著作権・ライセンス

このセット全体は、元のリポジトリと同じライセンスに従います。
コマンド例は自由に改変して使用可能。

---

## 最後に

このセットが完全な測定を支援することを目指しています。
質問や不明な点があれば、各ドキュメントの該当セクションを参照してください。

**Happy A/B testing!**
