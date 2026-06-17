# Self-Critique 効果測定実験 — ドキュメント完全インデックス

このディレクトリに含まれるすべてのドキュメントの目的、内容、読む順序を示します。

---

## 📚 ドキュメント一覧

### 1️⃣ **README.md** (最初に読む)
- **所要時間**: 5-10 分
- **目的**: 実験全体の概要と実行フロー
- **内容**:
  - 実験の目的（self-critique が効くか？）
  - 判定基準の要約（ノイズ下限 ±2.7）
  - 実験の流れ（5 つのフェーズ）
  - 注意点（環境変数の適用方法、LM Studio reload など）
  - チェックリスト
- **読み終わったら**: EXPERIMENT_DESIGN.md へ

### 2️⃣ **EXPERIMENT_DESIGN.md** (実行前に精読)
- **所要時間**: 15-20 分
- **目的**: 実験設計を詳細に理解し、交絡対策を確認
- **内容**:
  - 背景（self-critique とは何か、前回の失敗から学ぶこと）
  - 実験の仮説（帰無仮説、対立仮説）
  - 独立変数・従属変数の定義
  - テーマセット（n=50）
  - **【最重要】交絡対策 5 つ**:
    1. LM Studio sustained-load 劣化
    2. 長期失敗記憶の順序効果
    3. Configured model の UI による切替
    4. イベントドリブンな自動チューニング（ない）
    5. 劣化検査（前半/後半スコア比較）
  - 判定基準（事前登録）
  - 実験スケジュール
- **最重要セクション**: § 5 交絡対策
- **読み終わったら**: RUNBOOK.md へ

### 3️⃣ **RUNBOOK.md** (実行当日に従う)
- **所要時間**: 実験全体で 2-3.5 時間
- **目的**: コピペで実行できる完全なステップバイステップ手順
- **内容**:
  - **Pre-flight チェック** (5 分)
    - Health check
    - Configured model 確認
  - **Step 0**: Health Check (確認のみ)
  - **Step 1**: LM Studio Fresh Reload (unload → load 32768)
  - **Step 2**: Failures リセット
  - **Step 3**: Variant A (sc-ON) 実行
    - Backend 再起動 (env 変数確認)
    - eval.sh sc-on 実行 (45-90 分)
  - **Step 4**: 中間処理 (fresh reload + failures reset)
  - **Step 5**: Variant B (sc-OFF) 実行
    - Backend 再起動 (env 変数確認)
    - eval.sh sc-off 実行 (45-90 分)
  - **Step 6**: 比較と判定
    - compare.sh 実行
    - 観測差抽出
    - ノイズ下限との比較
    - 劣化チェック
  - **Step 7**: 判定結果の記録
  - **Step 8**: 追加詳細分析（オプション）
  - トラブルシューティング（eval timeout, LM Studio error など）
  - チェックリスト（実行確認用）
- **使い方**: 
  - このドキュメントを片手に、コマンドをコピペで実行
  - エラーが起きたらトラブルシューティングセクション参照
- **読み終わったら**: 実験完了後に JUDGMENT_CRITERIA.md へ

### 4️⃣ **JUDGMENT_CRITERIA.md** (実験完了後に参照)
- **所要時間**: 10-15 分（判定フロー確認）
- **目的**: 結果を統計的に正確に解釈し、判定を下す
- **内容**:
  - 判定フロー図（全体の流れ）
  - Step 1: 観測差の算出（Δ = sc-ON - sc-OFF）
  - Step 2: ノイズ下限との比較（|Δ| > 2.7?）
  - Step 3: 前半/後半の劣化チェック（|差| > 10?）
  - Step 4: 最終判定テンプレート
  - ケース 1-4 の詳細解釈
  - 判定の困難ケース FAQ
  - チェックリスト（判定完了まで）
- **4 つの判定フロー**:
  - **有効**: SC-ON が有意に優位（保持推奨）
  - **有害**: SC-OFF が有意に優位（無効化推奨）
  - **判定不能**: ノイズ内の差（追加実験検討）
  - **LM Studio 劣化**: 前半/後半で大逆転（再実験必須）
- **読み終わったら**: EXECUTION_NOTES.md へ結果を記入

### 5️⃣ **EXECUTION_NOTES.md** (実験記録用テンプレート)
- **所要時間**: 20-30 分（実験後の記録）
- **目的**: 実験の事前宣言と事後記録をテンプレート形式で管理
- **内容**:
  - **事前登録セクション** (実験前に記入):
    - 実験日時・実施者
    - 仮説の明記
    - 交絡対策の事前確認
  - **実験実施記録** (実験後に記入):
    - 実際の日時・所要時間
    - 結果ファイルのパス
    - 主要指標の数値（スコア、合格率、attempt、violation など）
    - 劣化チェック結果（前半/後半）
    - 統計判定（|Δ| > 2.7?）
    - 最終判定（有効/有害/判定不能）
  - **異常記録**: トラブルが発生した場合の記録
  - **推奨アクション**: コード修正・ドキュメント更新・追加実験
  - **振り返り**: 予想と実際の乖離、学んだこと、環境的懸念
- **使い方**:
  - JUDGMENT_CRITERIA.md で判定を終えたら、ここにすべての数値と判定を記入
  - 完了後、内容を notes.md に転記
- **読み終わったら**: 実験完了

### 6️⃣ **CHECKLIST.md** (当日に持参)
- **所要時間**: 実行当日、随時記入
- **目的**: 紙版チェックリスト（タイムスタンプ記録欄付き）
- **内容**:
  - 【準備フェーズ】
    - Pre-flight チェック
    - 実験開始宣言
  - 【フェーズ 0】LM Studio Fresh Reload
    - アンロード → 再ロード → 確認
  - 【フェーズ 0】Failures リセット
  - 【フェーズ A】Variant A (sc-ON) 実行
    - Backend 再起動 → eval.sh → 結果確認
  - 【フェーズ B】中間処理
    - Fresh reload + failures reset
  - 【フェーズ C】Variant B (sc-OFF) 実行
    - Backend 再起動 → eval.sh → 結果確認
  - 【フェーズ D】判定フェーズ
    - Compare → 観測差抽出 → ノイズ比較 → 劣化チェック
  - 【トラブルシューティング記録】
  - 【実験完了】
  - タイムシート（全体所要時間サマリー）
- **使い方**:
  - 実験前に印刷
  - 実行中にペンでチェック、タイムスタンプ記入
  - 後で notes.md に転記
- **読み終わったら**: 実験完了

### 7️⃣ **SUMMARY.txt** (全体概要)
- **所要時間**: 5 分
- **目的**: ドキュメント構成、実験パラメータ、フローチャートの要約
- **内容**:
  - 【概要】実験の意義
  - 【ドキュメント構成】6 つの主要ドキュメント
  - 【実行フロー】5 つのステップ
  - 【核となる判定基準】ノイズ幅と判定ルール
  - 【主要な交絡対策】4 つ
  - 【実験のパラメータ】テーマセット、モデル、所要時間
  - 【期待される結果パターン】4 つ（有効/有害/判定不能/劣化）
  - 【コード上の実装位置】self-critique の該当行
  - 【参考資料リスト】CLAUDE.md, FINDINGS.md へのリンク
  - 【よくある質問 (FAQ)】6 つ
  - 【トラブルシューティング概要】
  - 【実験後のアクション】
  - 【フローチャート】全体の実行図
  - 【最終チェックリスト】実行直前の 8 項目
- **読み終わったら**: README.md へ（詳細は各ドキュメント参照）

### 8️⃣ **INDEX.md** (このファイル)
- **目的**: すべてのドキュメントの関係図と読む順序
- **内容**: このインデックス

---

## 📖 読むべき順序

### パターン A: 今すぐ実験を始めたい

```
1. README.md (5 分)
   ↓
2. EXPERIMENT_DESIGN.md の「交絡対策」セクション (5 分)
   ↓
3. RUNBOOK.md を手元に置いて実行開始 (2-3.5 時間)
   ↓
4. 実験完了後、JUDGMENT_CRITERIA.md で判定 (15 分)
   ↓
5. EXECUTION_NOTES.md に結果を記入 (30 分)
```

### パターン B: 詳細に理解してから実験したい

```
1. SUMMARY.txt (5 分)
   ↓
2. README.md (10 分)
   ↓
3. EXPERIMENT_DESIGN.md (20 分)
   ↓
4. CHECKLIST.md を印刷
   ↓
5. RUNBOOK.md を手元に置いて実行開始 (2-3.5 時間)
   ↓
6. JUDGMENT_CRITERIA.md で判定 (15 分)
   ↓
7. EXECUTION_NOTES.md に結果を記入 (30 分)
```

### パターン C: セッション継続（前回の実験を振り返る場合）

```
1. SUMMARY.txt で全体を思い出す (5 分)
   ↓
2. EXECUTION_NOTES.md で前回の記録を見直す
   ↓
3. 「現在地」から RUNBOOK.md を再開
   または JUDGMENT_CRITERIA.md で判定を完了
```

---

## 🎯 ドキュメント間の関連図

```
                    README.md
                      ↓
                      ├─→ 「最初に読む」
                      │
            EXPERIMENT_DESIGN.md
                      ↓
                      ├─→ 「交絡対策を確認」
                      │
                   CHECKLIST.md
                  (印刷する)
                      ↓
                   RUNBOOK.md
                      ↓
              (2-3.5 時間かけて実行)
                      ↓
             JUDGMENT_CRITERIA.md
                      ↓
               (結果を判定する)
                      ↓
            EXECUTION_NOTES.md
                      ↓
            (判定と数値を記録)
                      ↓
            完了 → notes.md に転記
```

---

## 🔑 最重要セクション

実験成功のカギになるセクションを **最初に** 読んでください：

1. **EXPERIMENT_DESIGN.md § 5「交絡対策」**
   - LM Studio 劣化への対策（fresh reload）
   - 失敗記憶の順序効果への対策（failures delete）
   - 前半/後半の劣化チェック方法

2. **RUNBOOK.md の Step 1（LM Studio Fresh Reload）と Step 4（中間処理）**
   - 具体的なコマンド列
   - エラーハンドリング

3. **JUDGMENT_CRITERIA.md の「判定フロー」**
   - |Δ| > 2.7? で有意性判定
   - 前半/後半チェック
   - 4 つの判定ケース

---

## 📊 ドキュメントサイズと読了時間

| ドキュメント | ファイルサイズ | 推定読了時間 |
|---|---|---|
| README.md | 8.2 KB | 5-10 分 |
| EXPERIMENT_DESIGN.md | 14 KB | 15-20 分 |
| RUNBOOK.md | 16 KB | 2-3.5 時間（実行時間含む） |
| JUDGMENT_CRITERIA.md | 15 KB | 10-15 分 |
| EXECUTION_NOTES.md | 8.0 KB | 20-30 分（記録時間含む） |
| CHECKLIST.md | 10 KB | 実行中に随時記入 |
| SUMMARY.txt | 11 KB | 5 分 |
| **合計** | **82 KB** | **3-4 時間** |

---

## 🚀 クイックスタート（初心者向け）

1. **この INDEX.md を読む** (5 分)
2. **README.md を読む** (5 分)
3. **EXPERIMENT_DESIGN.md の「交絡対策」を読む** (5 分)
4. **CHECKLIST.md を印刷する**
5. **RUNBOOK.md を手元に、Step 0 から開始** (2-3.5 時間)
6. 完了後 → **JUDGMENT_CRITERIA.md で判定** (15 分)

**合計所要時間**: 3-4 時間

---

## ❓ ドキュメント選択フローチャート

```
「何をしたいのか？」

├─ 実験全体を理解したい
│  └─→ README.md → EXPERIMENT_DESIGN.md
│
├─ すぐに実験を始めたい
│  └─→ RUNBOOK.md を開く（各ステップの詳細はそこに）
│
├─ 実験を実行している
│  └─→ RUNBOOK.md を見ながら進める + CHECKLIST.md でチェック
│
├─ 実験が完了して、結果を判定したい
│  └─→ JUDGMENT_CRITERIA.md を読みながら判定
│
├─ 実験の記録をテンプレートに入れたい
│  └─→ EXECUTION_NOTES.md に記入
│
├─ 前回の実験を振り返りたい
│  └─→ EXECUTION_NOTES.md + notes.md を見直す
│
└─ 何か問題が起きた
   ├─ RUNBOOK.md の「トラブルシューティング」
   └─ EXPERIMENT_DESIGN.md の「交絡対策」を再確認
```

---

## 📌 各ドキュメントの文章形式

| ドキュメント | 形式 | 対象読者 |
|---|---|---|
| README.md | 散文 + リスト | すべての人 |
| EXPERIMENT_DESIGN.md | 散文 + テーブル | 実験設計に関心ある人 |
| RUNBOOK.md | 手順書 + コマンド例 | 実験実施者 |
| JUDGMENT_CRITERIA.md | フロー図 + テンプレート | 実験完了後の判定者 |
| EXECUTION_NOTES.md | テンプレート | 実験実施者と記録者 |
| CHECKLIST.md | チェックリスト | 当日実行者（紙版） |
| SUMMARY.txt | 要約 + フローチャート | クイックリファレンス |

---

## 💾 保存場所

すべてのドキュメントは以下に保存されています：

```
/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/
  └─ iteration-2/eval-1-self-critique-ablation-runbook/
     └─ without_skill/run-1/outputs/
        ├─ INDEX.md (このファイル)
        ├─ README.md
        ├─ EXPERIMENT_DESIGN.md
        ├─ RUNBOOK.md
        ├─ JUDGMENT_CRITERIA.md
        ├─ EXECUTION_NOTES.md
        ├─ CHECKLIST.md
        └─ SUMMARY.txt
```

---

## ✅ 実験前のチェック

```
☐ このディレクトリ内のすべてのファイルが揃っている
☐ README.md を読んだ
☐ EXPERIMENT_DESIGN.md § 5 を精読した
☐ CHECKLIST.md を印刷した
☐ RUNBOOK.md を手元に用意した
☐ JUDGMENT_CRITERIA.md の判定フローを理解した
☐ ホスト側で LM Studio が起動している
☐ Docker が起動している
☐ notes.md を準備した（実験ログ記録用）

すべてチェック完了したら，RUNBOOK.md の「Pre-flight チェック」から開始！
```

---

**最終更新**: 2026-06-13  
**ドキュメント版**: 1.0 (without_skill edition)  
**ファイル数**: 8  
**総ワード数**: 約 7,400  
**総推定読了時間**: 3-4 時間（実験を含む）

このインデックスを使い、スムーズに実験を進めてください！
