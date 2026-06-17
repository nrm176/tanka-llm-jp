# 判定基準と統計的解釈ガイド

このドキュメントは、実験完了後に結果を解釈し、「self-critique は効果があるか」を判定するための基準です。

---

## 1. 判定の枠組み

実験は以下の仮説検定の形式を取ります：

```
H0 (帰無仮説): self-critique の有無でスコアに有意差がない
H1 (対立仮説): 有意差がある（方向は未定）

有意水準: α = 0.05 (2SE 基準)
```

### ノイズ下限（重要）

**n=50 テーマ × 1 run の場合、観測された平均スコア差がこれ以下なら、
統計的に区別できない（ノイズ範囲内）。**

| 比較単位 | ノイズ幅（95%信頼区間） |
|---|---|
| 50 テーマ平均 | **±2.7 点** |

出典: `FINDINGS.md` §2, variance 実測 (stdev ≈ 9.6, SEM = 9.6/√50 ≈ 1.36, 2SE ≈ 2.7)

---

## 2. 判定フロー

```
Step 1: 観測差を計算
        avg_final_score(sc-ON) - avg_final_score(sc-OFF) = Δ

Step 2: 両 variant の n を確認
        同一テーマファイル (eval_themes.json) を使用したか
        → _themes_file が一致しているか compare.sh 出力を見る

Step 3: Δ の絶対値をノイズ下限と比較
        |Δ| > 2.7? 
           ├─ YES → Step 4 (有意判定)
           └─ NO  → Step 5 (判定不能)

Step 4: 方向を確認
        Δ > 0 (ON > OFF)   → sc-ON 有効。保持推奨
        Δ < 0 (OFF > ON)   → sc-OFF が良い (ON が有害)。無効化推奨

Step 5: 交絡チェック（差が ±2.7 に近い場合も実施）
        前半スコア と 後半スコア に LM Studio 劣化の兆候がないか
        → 両 arm で対称的な劣化か、片側が異常に沈んでいないか
           └─ 異常なら: この run は採用不可。再測定。
           └─ 対称なら: 観測差は self-critique の実効果

最終判定:
  ├─ 採用 (sc-ON 有効)
  ├─ 棄却 (sc-ON 有害)
  ├─ 判定不能 (ノイズ内)
  └─ 再測定必須 (交絡検出)
```

---

## 3. 各判定の詳細

### ケース 1: Δ > 2.7 (sc-ON が有意に高い)

**判定**: **採用推奨。self-critique は有効。**

```
例: avg_final_score(sc-ON) = 81.5, avg_final_score(sc-OFF) = 77.2
    Δ = 4.3 > 2.7 ✓

→ sc-ON は平均で 4.3 点高い
→ これはノイズでは説明できない差（95% 信頼度）
```

**追加分析**:
- 初回合格率 (first_pass_rate) の差も確認
  - sc-ON が高い → refine 前の品質が良い（直接効果）
  - 同じ → refine ループの length や convergence 速度に差（間接効果）
- attempt 平均数の比較
  - sc-ON が少ない → self-critique が早期に合格状態に到達させている
  - 同じ → コスト（+1 LLM call）が正当化されにくい

**報告文言**:
> Self-critique は +4.3 点の有意な改善をもたらした。
> 初回合格率は XX% → YY% に改善。
> attempt 平均は Z% 削減。
> **推奨: 保持。コスト対効果あり。**

---

### ケース 2: Δ < -2.7 (sc-OFF が有意に高い)

**判定**: **棄却推奨。self-critique は有害。無効化推奨。**

```
例: avg_final_score(sc-ON) = 76.8, avg_final_score(sc-OFF) = 80.5
    Δ = -3.7 < -2.7 ✓

→ sc-OFF が平均で 3.7 点高い
→ self-critique により品質が低下している
```

**分析対象**:
- sc-ON が低下した具体的な理由は何か
  - 例: 「点検」という指示が composition を過度に慎重にした
  - または: self-critique の修正提案が本来の vision を損なった
  - または: LLM の「点検」自体が誤判定を導入した
- どのルール (violation 種別) で特に低下が見られるか
  - validator.py の rule 別に、sc-ON vs sc-OFF の違反率を比較
  - 特定のルール（例: `kigo_matches_plan`) では逆方向か確認

**報告文言**:
> Self-critique は -3.7 点の有意な悪化をもたらした。
> これは「点検」という指示が composition を悪化させていることを示唆する。
> **推奨: 無効化。代替手段を検討（例: プロンプト再設計）。**

---

### ケース 3: -2.7 ≤ Δ ≤ 2.7 (判定不能)

**判定**: **「ノイズ内。有意差なし」と報告。判定保留。**

```
例: avg_final_score(sc-ON) = 79.5, avg_final_score(sc-OFF) = 77.8
    Δ = 1.7, |Δ| = 1.7 < 2.7

→ 観測差は 2 点弱だが、これは ±2.7 のノイズ幅以下
→ 「sc-ON が良い」と言えず、「sc-OFF が良い」とも言えない
→ 現データでは判定不能
```

**なぜ判定できないのか**:
- 50 テーマでも n が不足している可能性
- または LM Studio の劣化が両 arm に不均等に作用した
- または単なる確率変動（best-of-N の「引きの差」）

**次のステップ**:

選択肢 A: **n をさらに増やす**（100 テーマまで拡張）
- 計算: ノイズ幅は 1/√n に比例 → n=100 なら ±1.9 に縮小
- 時間: 2-4 時間
- ただし現ハードウェア (LM Studio) の不安定性を考えると、
  n=100 でも劣化交絡が入る可能性

選択肢 B: **paired/repeated design**
- 各テーマで ON/OFF を複数 run 実施
- 例: テーマ 1-50 で ON を 1 run, その後 OFF を 1 run, 平均を取る
  または各テーマで ON/OFF/ON/OFF と交互に 2 run ずつ
- 効果: per-theme の分散を削減 → より小さな差を検出可能
- 時間: 4-8 時間（n がかさむ）

選択肢 C: **モデル依存性の確認**
- デフォルトモデル (llm-jp-4-8b-thinking) では 判定不能だが、
  別のモデル (例: より大きい instruction-tuned model) では
  self-critique の効果が顕著かもしれない
- ただし評価系を複雑化させるため、current model での
  再測定を先にやるのが筋

**報告文言**:
> Self-critique は +1.7 点だが、ノイズ下限 ±2.7 以下であり、
> **有意差を判定できない**。
> 
> **追加実験候補**:
> 1. n を 100 テーマに拡張 (ノイズ幅 ±1.9)
> 2. paired design で per-theme 分散削減
> 3. 判定不能は knowledge gap である。
>    self-critique を「保持するか削除するか」の判断には
>    さらなる測定が必須。

---

## 4. 交絡チェック（LM Studio 劣化の検出）

観測差が ±2.7 に「近い」場合（1.5 ≤ |Δ| ≤ 3.5）や、判断に迷う場合は、
**必ず前半/後半の劣化パターンを確認**。

### 4.1 正常パターン（交絡なし）

```
sc-ON:   前半 82.0  後半 79.5  (低下 2.5 点)
sc-OFF:  前半 80.0  後半 77.8  (低下 2.2 点)

→ 両者の低下幅がほぼ同じ (2.5 vs 2.2)
→ LM Studio 劣化は両 arm に等しく作用
→ 観測差 +2.2 は self-critique の実効果と解釈できる

判定: OK。この run は有効。
```

### 4.2 異常パターン（交絡あり）

```
sc-ON:   前半 85.0  後半 68.0  (低下 17.0 点) ⚠️ 異常
sc-OFF:  前半 81.0  後半 78.5  (低下 2.5 点)

→ sc-ON が後半で崩壊
→ 理由: sc-ON が LM Studio 劣化に脆弱か、
  または sc-ON の env 設定が refresh されておらず stale state か
  
判定: 採用不可。この run は信頼できない。

対応: 再測定。
   - fresh reload を再度実施
   - 可能なら sc-OFF → sc-ON の順序を逆転（blocked → interleaved）
```

```
sc-ON:   前半 82.0  後半 79.5  (低下 2.5 点)
sc-OFF:  前半 78.0  後半 71.0  (低下 7.0 点)

→ sc-OFF の後半が悪化
→ sc-OFF の環境が汚れているか、または単なる unlucky variance

判定: 慎重に。劣化が sc-OFF 側なので、
  観測差 (ON が高い) は実効果の可能性が高い。
  ただし sc-OFF 後半の異常をデバッグして、
  環境セットアップに問題がなかったか確認。
```

### 4.3 劣化チェックの実行

RUNBOOK.md Step 7 で以下を実行：

```bash
# sc-on の結果ファイルから session ID を抽出
RESULT_FILE=results/20260612-140530-sc-on.json
SID=$(python3 -c "import json; print(json.load(open('$RESULT_FILE'))['_session_id'])")

# 前半/後半スコアを計算
curl -s "http://localhost:8001/api/sessions/$SID" | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
sc = [m['final_score'] for m in data.get('messages', [])
      if m.get('kind') == 'tanka' and m.get('final_score') is not None]
if len(sc) >= 2:
    h = len(sc) // 2
    first = sum(sc[:h]) / len(sc[:h])
    second = sum(sc[h:]) / (len(sc) - h)
    print(f'sc-ON:  前半 {first:.1f}  後半 {second:.1f}  (低下 {first - second:.1f})')
EOF
```

**解釈基準**:
- 低下幅が 3-5 点: 正常（LM Studio の natural degradation）
- 低下幅が 10 点以上: 異常。再測定が必須

---

## 5. 結論テンプレート

### パターン 1: 有意 (sc-ON > OFF)

```markdown
## 判定: 有意。Self-critique は有効。

**観測差**: +X.X 点 (n=50)
**ノイズ下限**: ±2.7 点
**統計**: |+X.X| > 2.7 → 有意 (p < 0.05)

**初回合格率**: sc-ON A% vs sc-OFF B% (改善 C%)
**Attempt 平均**: sc-ON 1.X vs sc-OFF 1.Y
**前半/後半**: 対称的な劣化。交絡なし。

**結論**: Self-critique により平均で +X.X 点の改善。
コスト (+1 LLM call) に対して価値がある。

**推奨**: 保持。
```

### パターン 2: 有意 (sc-OFF > ON)

```markdown
## 判定: 有意。Self-critique は有害。

**観測差**: -X.X 点 (sc-OFF が上)
**ノイズ下限**: ±2.7 点
**統計**: |-X.X| > 2.7 → 有意 (p < 0.05)

**違反別分析**:
- sc-ON で増加した違反: [rule names]
- sc-OFF で増加した違反: [rule names]

**前半/後半**: 対称的。交絡なし。

**結論**: Self-critique により -X.X 点の悪化。
「点検」という指示が composition を悪化させている。

**推奨**: 無効化 (TANKA_SELF_CRITIQUE=False がデフォルト)。
```

### パターン 3: 判定不能

```markdown
## 判定: 判定不能。

**観測差**: ±X.X 点 (ノイズ下限 ±2.7 以下)
**統計**: |±X.X| ≤ 2.7 → 区別不能

**可能性 1**: 効果なし（帰無仮説が正しい）
**可能性 2**: 効果があるが n=50 では検出力不足
**可能性 3**: 環境劣化が両 arm に不均等に作用

**次ステップ候補**:
1. n=100 への拡張 (±1.9 に縮小)
2. Paired design での per-theme 分散削減
3. Interleaved/random 順序での再測定

**現時点での推奨**: 判定保留。
ただし +X.X の小幅改善であれば、コスト考慮で無効化する選択肢もあり。
```

---

## 6. 追加指標の解釈

### 初回合格率 (first_pass_rate)

- **定義**: compose 段階で pass_threshold に達する確率
- **高い**: self-critique が early-pass を促進（確実な効果）
- **低い**: self-critique 後も refine が必要（間接効果）

### Plateau 率

- **定義**: score が改善せず、refine が打ち切られた割合
- **sc-ON が高い**: self-critique が無意味な refine を誘発している可能性

### Violation Frequency

- **注意**: これは「全 attempt の分布」であり、**最終成果ではない**
- `violation_history` の集約であるため、難テーマの多くの attempt が
  全体の割合を歪める
- **主指標ではなく参考値**

---

## 7. 判定後のアクション

### 採用する場合

1. FINDINGS.md に結果を追記
2. 変更なし（TANKA_SELF_CRITIQUE=True が既定）
3. 次の実験テーマへ

### 棄却する場合

1. FINDINGS.md に結果を追記
2. config.py で `SELF_CRITIQUE_ENABLED = False` に変更
3. docker-compose.yml で `TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-0}` に更新
4. PR: "feat: disable self-critique (ineffective, +1 call cost)"

### 判定不能の場合

1. FINDINGS.md に「ノイズ内。判定保留」と記録
2. 次の実験設計（n 拡張 or paired design）を検討
3. 設計が固まったら改めて実験を組む

---

## 参考資料

- `FINDINGS.md` §2: variance 実測とノイズ下限の導出
- `FINDINGS.md` §5.5: 前回 self-critique 実験の交絡例
- `CLAUDE.md` §6.13: LM Studio 劣化の環境的実例
