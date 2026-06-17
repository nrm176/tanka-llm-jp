# Self-Critique 効果測定 — 実験設計書（事前登録）

## 1. 研究背景

### Self-Critique フェーズの実装背景

短歌生成パイプラインは以下の構造を持つ：
```
Plan → Compose(JSON) → [self-critique] → Validate → Refine* → Complete
```

**Self-critique フェーズ** (Phase 1 B4, tanka.py L216-227):
- Compose で生成した JSON 短歌に対し、LLM に以下の 5 観点を自己点検させる：
  1. 季語が本文にちょうど 1 回出現しているか
  2. 宣言外の他季の季語が混在していないか
  3. 各句の拍数が 5-7-5-7-7 になっているか
  4. plan で決めた季語と季節を維持しているか
  5. 切れ字や体言止めで余韻が生まれているか
- 「問題ありきはい修正版を JSON で、問題なければそのまま JSON を出力」と指示
- **コスト**: +1 LLM call / 生成

### 前回の実験 (2026-06-04, FINDINGS.md §5.5)

16 テーマの blocked A/B で sc-ON vs sc-OFF を測定：
- **観測差**: sc-OFF が +4.69 点上回る
- **ノイズ下限**: ±4.8 (n=16)
- **結論**: 観測差がノイズ幅内 → 判定不能
- **交絡検査で判明**:
  - 前半 (テーマ 1-8): sc-ON 88.1 vs sc-OFF 83.3 （ON が優れている）
  - 後半 (テーマ 9-16): sc-ON 62.0 vs sc-OFF 76.3 （OFF が優れている）
  - **符号が逆転** → sc-ON の効果ではなく、後半の LM Studio 劣化が原因

---

## 2. 実験の研究問い（仮説）

### 主仮説

**帰無仮説 (H0)**:
> Self-critique の有無で最終スコアに有意差がない。観測される差はすべて測定ノイズに帰属する。

**対立仮説 (H1)**:
> Self-critique が有効であれば、sc-ON の平均スコアが sc-OFF より統計的に有意に高い。

### 従属変数（測定指標）

| 指標 | 定義 | 期待方向 |
|---|---|---|
| **avg_final_score** | 50 テーマの最終スコア (0-100) の平均 | ON > OFF |
| **first_attempt_pass_rate** | 初回 (attempt 0) で合格した割合 | ON > OFF |
| **overall_pass_rate** | 最終的に合格した割合 | ON > OFF（ただし効果は avg_final_score ほど大きくない可能性） |
| **avg_attempts** | 平均 refine 回数 | ON < OFF |
| **plateau_rate** | 改善が止まって打ち切りになった割合 | ON < OFF |

**主要判定指標**: `avg_final_score` (ノイズ下限 ±2.7)

---

## 3. 実験設計

### 3.1 独立変数（操作）

| Arm | 設定 | 説明 |
|---|---|---|
| **Variant A (sc-ON)** | `TANKA_SELF_CRITIQUE=1` | self-critique フェーズを実行。compose 出力を自己点検させ、修正版があれば採用 |
| **Variant B (sc-OFF)** | `TANKA_SELF_CRITIQUE=0` | self-critique フェーズをスキップ。compose 出力をそのまま validate へ進める |

**適用方法**:
```bash
# sc-ON の場合
cd /Users/nrm176p/GitHub2/LLM/app
TANKA_SELF_CRITIQUE=1 docker compose up -d backend
# restart ではなく up -d を使うこと（env が反映されない）

# sc-OFF の場合
TANKA_SELF_CRITIQUE=0 docker compose up -d backend
```

### 3.2 テーマセット（お題）

**使用ファイル**: 
```
/Users/nrm176p/GitHub2/LLM/app/backend/eval/eval_themes.json (version: 1)
```

**構成**: 50 テーマ
- 四季別: 春 10 + 夏 10 + 秋 10 + 冬 10 + 新年 5 = 50
- 古典/現代: 各季 5+5
- 抽象/具体: 各種類 5+5

**変更履歴**: なし。過去実験との比較可能性を保つため、このセットを固定

### 3.3 サンプルサイズと統計力

| 指標 | 前回 (blocked) | 今回 (n=50) | 改善 |
|---|---|---|---|
| サンプル数 | 16 | 50 | 3.125× |
| Stdev (推定) | 9.6 | 9.6 | 同じ |
| SE (標準誤差) | ±3.4 | ±1.4 | 2.4× 小さい |
| ノイズ幅 (2SE) | ±6.8 | **±2.7** | 2.5× 小さい |

**判定基準**: |観測差| > **2.7** で有意と判定

### 3.4 実験デザイン（被験者デザイン）

```
           事前           介入            事後
           ↓             ↓              ↓
Variant A: [Setup] → [sc-ON] → 50テーマ → [metrics]
           ↓
           Fresh reload
           Failures reset
           ↓
Variant B: [Setup] → [sc-OFF] → 同一50テーマ → [metrics]

比較: compare.sh sc-on sc-off
```

**デザインの特徴**:
- 同一被験者（同一テーマセット）で両 variant を測定
- Variant 間での順序効果を最小化するため、両方の間に fresh reload と failures リセットを挿入
- **前半/後半スコアの分割チェック** で環境劣化を検出可能にしている

---

## 4. 交絡変数と対策（最重要）

### 4.1 LM Studio Sustained-Load 劣化

**交絡の実例** (FINDINGS.md §5, §6.13):
- LM Studio は sustained load 下で性能が劣化する
- 実測: 15s/call (fresh) → 3.5min/call (degraded) + HTTP 400
- 前回の実験で、前半と後半でスコアが逆転（LM Studio 劣化が原因と判定）

**対策**:
1. **各 variant 実行前に fresh reload**:
   ```bash
   lms unload  # KV キャッシュをクリア
   lms load llm-jp-4-8b-thinking --context-length 32768  # 初期化状態で再起動
   ```
   **なぜ 32768 か?** (CLAUDE.md §6.14)：llm-jp-4-8b-thinking は thinking + output で最大 32k context を使う。デフォルト 8192 では context 超過で全テーマが失敗する。

2. **前半/後半の劣化チェック**:
   結果ファイルで各テーマのスコアを確認し、「前半 OK、後半 crashing」なパターンが見られたら、その run は不採用とする
   ```python
   # 疑似コード
   first_half = scores[:25]
   second_half = scores[25:]
   if mean(first_half) - mean(second_half) > 10:  # 大幅な劣化
       print("警告: LM Studio 劣化の証拠あり")
   ```

### 4.2 長期失敗記憶による順序効果

**交絡の機序**:
- MongoDB の `failures` コレクションは run をまたいで蓄積
- 後で実行する variant が「前の variant で失敗したお題」の教訓を受け取る
- 例: sc-ON で「季語重複」という失敗を登録 → sc-OFF で同じお題を実行するとき、lesson から「季語の唯一性を確認しろ」と指示される
- 結果として sc-OFF が（unfairly）有利になる可能性

**対策**:
```bash
# 各 variant 実行直前に failures をリセット
curl -X DELETE http://localhost:8001/api/failures
```

**確認**:
```bash
# リセット前後で削除件数を確認
echo "リセット前:"
curl -s http://localhost:8001/api/failures | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"

curl -X DELETE http://localhost:8001/api/failures

echo "リセット後:"
curl -s http://localhost:8001/api/failures | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"
```

### 4.3 UI による Configured Model 切替

**交絡の機序**:
- UI (localhost:5178) の「モデル切替」機能が MongoDB に `settings` を記録
- `/api/health` の `configured_model` がこの値を返す
- 実験中に誤ってモデルを切り替えると、eval の途中からモデルが変わる
- スコアの差が「sc-ON vs sc-OFF」ではなく「モデル A vs モデル B」になる

**対策**:
```bash
# 実験開始前に確認
curl -s http://localhost:8001/api/health | python3 -m json.tool | grep configured_model

# 期待値: "configured_model": "llm-jp-4-8b-thinking"
# 異なる場合は UI から手動で選択し直す（または variant 名にモデル名を含める）
```

### 4.4 イベントドリブンな自動チューニング

**交絡の機序**:
- パイプラインに「自動 threshold 調整」「重み学習」などの機構がないことを確認（現行は hand-tuned）
- 万が一あれば、実験中は无効化する必要あり

**対策**: 
- `config.py` / `validator.py` を grep して自動チューニンがないか確認
- 現行コード (2026-06-13) では無し ✓

---

## 5. 測定指標と判定基準（事前登録）

### 5.1 判定基準（最重要）

#### ノイズ下限の根拠

FINDINGS.md §2 の variance 実測：
```
同一テーマ × 6 run の結果:
mean   = 89.0
stdev  = 9.6  (標本)
range  = 75 - 100

→ stdev ≈ 9.6 は短歌生成の確率的変動 (best-of-N sampling のため)

A/B 比較時の統計的ノイズ下限:
- n=50 の場合: SE = 9.6 / √50 ≈ 1.36
- 2SE (95% CI) ≈ 2.7
```

**判定ルール**:
```
Δ = avg_final_score(sc-ON) - avg_final_score(sc-OFF)

|Δ| > 2.7?
  ├─ YES, Δ > 0  → sc-ON が有意に有効。保持推奨
  ├─ YES, Δ < 0  → sc-ON が有意に有害。無効化推奨
  └─ NO         → 判定不能。観測差はノイズ内。追加実験が必要
```

#### 補助判定指標

| 指標 | 判定方法 |
|---|---|
| **first_attempt_pass_rate** | 改善幅が 5% 以上なら「refine 必要度の低下」と解釈（補助的） |
| **avg_attempts** | 削減が 0.5 以上なら「改稿効率改善」と解釈（補助的） |
| **前半/後半スコア差** | |前半 - 後半| < 5 なら「環境劣化なし OK」、>10 なら「NG」 |

### 5.2 交絡検査フロー

```
実験完了
  ↓
compare.sh で avg_final_score を確認
  ↓
|Δ| > 2.7?
  ├─ NO  → 判定不能。終了
  └─ YES → 続行
     ↓
前半/後半スコアを算出
  前半25テーマ の平均
  後半25テーマ の平均
  ↓
|前半 - 後半| > 10?
  ├─ YES → LM Studio 劣化の証拠あり。この run は不採用。再実験推奨
  └─ NO  → OK。差の符号を確認
     ↓
差の符号（Δ の正負）が一貫しているか？
  ├─ YES → sc-ON が一貫して優位 (or 劣位) → 採用
  └─ NO  → 前半と後半で逆転 → LM Studio 劣化の確実な証拠。不採用
```

---

## 6. 実験実行スケジュール

### 予想所要時間

| フェーズ | 所要時間 | 備考 |
|---|---|---|
| Pre-flight チェック | 5 分 | health / configured_model / LM Studio ps |
| LM Studio Fresh Reload | 3 分 | unload → load 32768 |
| Failures リセット | 1 分 | curl DELETE |
| Variant A (sc-ON) 実行 | 45-90 分 | 1 テーマ = 30s-2min（LLM の気分次第） |
| 中間フェーズ (reload + reset) | 5 分 | |
| Variant B (sc-OFF) 実行 | 45-90 分 | |
| 比較と判定 | 10 分 | compare.sh + 結果の解釈 |
| **合計** | **2-3.5 時間** | 夜間無人実行を想定 |

### 推奨実行時刻

- **開始時刻**: 22:00 - 23:00（翌朝までに完了）
- **完了時刻**: 02:00 - 04:00 AM
- **理由**: 夜間なら LLM の sustained load 劣化が少ない（他の並行処理がない）

---

## 7. 記録と報告（実験後）

### 成果物

実験完了後、以下を記録する：

```
【タイトル】Self-Critique 効果測定実験 — 結果報告 (2026-06-13)

【実験条件】
- 実行日時: <日付・時刻>
- テーマセット: eval_themes.json (50 テーマ)
- LM Studio モデル: llm-jp-4-8b-thinking
- Context length: 32768
- Failures リセット: 実施

【主要結果】
観測差 Δ = <値> 点
  sc-ON avg_final_score: <値>
  sc-OFF avg_final_score: <値>

【判定】
|Δ| = <値> > 2.7?
  判定: [有効 / 有害 / 判定不能] のいずれか

【補助指標】
  初回合格率: ON <値>% vs OFF <値>%  (差: <値>%)
  平均 attempt: ON <値> vs OFF <値>  (差: <値>)
  Plateau 率: ON <値>% vs OFF <値>%
  
【劣化検査】
  前半 25 テーマ平均: ON <値> vs OFF <値>
  後半 25 テーマ平均: ON <値> vs OFF <値>
  結論: [OK / LM Studio 劣化あり] のいずれか

【所見】
<必要に応じて記述>

【推奨アクション】
- [判定が「有効」の場合] self-critique を保持。コストの正当性が確認された
- [判定が「有害」の場合] self-critique を無効化。むしろ品質を悪化させている
- [判定が「判定不能」の場合] 
  - オプション 1: テーマあたり複数 run で variance を削減して再測定
  - オプション 2: 異なるモデル (gemma など) で再測定（モデル依存性の確認）
  - オプション 3: より詳細な per-rule analysis で効果の局所性を調査
```

---

## 8. 付録: 実装上の詳細

### Self-Critique コードの場所

```python
# tanka.py L216-227
if config.SELF_CRITIQUE_ENABLED:
    sc_messages = compose_messages + [
        {"role": "assistant", "content": composition},
        {"role": "user", "content": prompts.SELF_CRITIQUE_USER},
    ]
    try:
        async for ev in _run_llm_phase("self_critique", sc_messages, model=model):
            if ev["type"] == "phase_end" and ev["text"].strip():
                composition = ev["text"]  # ← 修正版があれば採用
            yield ev
    except Exception as e:
        log.warning("self_critique skipped due to error: %s", e)
```

### 自己点検の観点

```python
# prompts.py L220-228
SELF_CRITIQUE_USER = (
    "上記の短歌について自己点検してください。次の観点を確認し、..."
    "1. kigo フィールドで宣言した語が、本文 (lines.body) のどこかにちょうど 1 回だけ出現しているか\n"
    "2. 宣言外の他の季の季語が混在していないか\n"
    "3. 各句の拍数が 5-7-5-7-7 になっているか\n"
    "4. 構想で決めた kigo と season を維持しているか\n"
    "5. 切れ字や体言止めで余韻が生まれているか"
)
```

### パイプライン全体図

```
User Input (theme)
     ↓
[Plan] → 季語・季節・情景・心情を構想
     ↓
[Compose] → JSON 短歌を生成
     ↓
[Self-Critique] ← ← ← ← ← TANKA_SELF_CRITIQUE により制御
     ↓                    (ON: 実行)
[Validate] → スコアリング、rules チェック    (OFF: スキップ)
     ↓
score >= PASS_THRESHOLD?
  ├─ YES → [Complete] → 終了
  └─ NO  → [Refine] → loop
```

---

## 参考資料

| 資料 | URL / パス | 用途 |
|---|---|---|
| CLAUDE.md §6.13 | `/Users/nrm176p/GitHub2/LLM/CLAUDE.md` | LM Studio 劣化の環境制約 |
| CLAUDE.md §6.14 | (同上) | Context 8192 JIT ロード失敗 |
| FINDINGS.md §2 | `/Users/nrm176p/GitHub2/LLM/app/backend/eval/FINDINGS.md` | Variance 実測・ノイズ下限 |
| FINDINGS.md §5.5 | (同上) | 前回 sc-on/sc-off 実験（失敗ケース） |
| tanka.py | `/Users/nrm176p/GitHub2/LLM/app/backend/tanka.py` | パイプライン実装 |
| config.py | `/Users/nrm176p/GitHub2/LLM/app/backend/config.py` | TANKA_* env 定義 |
| docker-compose.yml | `/Users/nrm176p/GitHub2/LLM/app/docker-compose.yml` | env passthrough 定義 |
| validator.py | `/Users/nrm176p/GitHub2/LLM/app/backend/validator.py` | スコアリングロジック |
