# repeated_word ルール重み A/B 測定 — 期待結果・解釈ガイド

## 概要

`repeated_word` ルールの重みを **3 → 12** に上げた場合に、どのような結果が見られるかを予測し、
その結果をどう解釈するかのガイド。

---

## ルール仕様の復習

validator.py の `_rule_repeated_word()`:

```python
def _rule_repeated_word(t: Tanka) -> list[Violation]:
    """目立つ語の重複。kana 読みベースで 3 文字以上の重複を緩く検出。
    (純粋な助詞などは除外したいが、簡易判定で十分)"""
    # 各句を比較し、3 文字以上の共通文字列が複数ある場合のみ報告
    readings = [line.reading for line in t.lines]
    for length in (4, 3):
        substr_counter: Counter[str] = Counter()
        for r in readings:
            seen_in_this = set()
            for i in range(len(r) - length + 1):
                s = r[i:i + length]
                if s not in seen_in_this:
                    substr_counter[s] += 1
                    seen_in_this.add(s)
        for s, c in substr_counter.items():
            if c >= 2 and not _is_trivial_substring(s):
                out.append(_violation(
                    "repeated_word", "minor", _w("repeated_word"),
                    f"「{s}」が複数の句に登場しています。表現の重複を避けると引き締まります。"
                ))
                return out  # 一件報告すれば十分
    return out
```

**検出内容**:
- 読みベースで **3 文字以上の部分文字列が複数の句に登場** する場合に違反
- 例: 「咲く・咲き・咲」の「咲く」部分が複数句にあると重複と判定
- 検出されたら一件報告して return (複数個の重複を指摘しない)

**重み**:
- 現在: `_w("repeated_word")` → デフォルト 3
- 変更後: デフォルト 12 (4 倍)

---

## 期待される効果と根拠

### 1. repeated_word 違反の頻度増加

**予測**: `violation_frequency['repeated_word']` が **増加**

**根拠**:
- ルール自体は不変 (検出アルゴリズムは変わらない)
- ただし重みが増すと、refine ループの早期終了条件が変わる
- 重み 3 で検出されても「低いスコアへの寄与 (3 点)」なため、他の違反が大きければ refine の優先度が低い
- 重み 12 になると、repeated_word 一件で -12 点となり、他の違反と競合するようになる

**メカニズム**:
```
評価スコア: 100 - (違反1の重み) - (違反2の重み) - ...

例: 初回出力で [repeated_word, kigo_present] が検出された場合

重み 3 のとき:
  score = 100 - 3 - 25 = 72
  refine が kigo_present (-25) の方に注力。repeated_word は「修正しなくても大丈夫」

重み 12 のとき:
  score = 100 - 12 - 25 = 63
  repeated_word も refine の焦点になる。compose プロンプトに「語の重複を避けろ」と明示される可能性
```

その結果、refine ループで repeated_word がより多く検出される。

**観測方法**:
```bash
./compare.sh baseline variant-w12
# [ 違反頻度 ] セクションで repeated_word を確認
```

**期待される数字**: baseline で 2-3 件 → variant で 5-10 件 (お題 12 個の eval での集計)

---

### 2. 平均試行回数 (avg_attempts) の増加の可能性

**予測**: `avg_attempts` が **微増～中程度増加** (±1 程度か、+2 以上か)

**根拠**:
- repeated_word は "minor" 重み度 (重要度は低い)
- ただし weight 3 → 12 で「低」→「中」程度に格上げされる感覚
- refine ループの検査条件が厳しくなると、修正が必要な件数が増える

**シナリオ分析**:

| シナリオ | 説明 | avg_attempts の変化 |
|---|---|---|
| A: 語重複は稀 | お題が「語の重複を避けやすい」なら、検出 0→0 で影響なし | ~0 |
| B: 語重複は常態 | LLM が「同じ句型を繰り返す」くせがあれば、修正が必要 | +0.5 ~ +2 |
| C: 過度な強制 | 重み 12 がボトルネック化し、他の違反は解決しても repeated_word で plateau | +3 以上 |

**観測方法**:
```bash
./compare.sh baseline variant-w12
# [ 品質指標 ] セクションで "平均 attempt 数" を確認
```

**期待される数字**: 
- baseline: 3.5 → variant: 3.8 ~ 4.5 (シナリオ B)
- または baseline: 4.0 → variant: 4.0 (シナリオ A、影響なし)

---

### 3. 合格率 (overall_pass_rate) への影響

**予測**: `overall_pass_rate` は **ほぼ不変か微減**

**根拔**:
- repeat ループは HARD_CAP (50) まで回る
- 50 回試行すれば、大抵の生成が何らかの valid output に到達
- repeated_word の重み増加だけでは、合格敷居 (PASS_THRESHOLD=80) を割る可能性は低い

**ただし、以下の場合は低下**:
- repeated_word が「複合違反」に化ける場合
  - 例: 語重複を避けようとして季語が変わる → season_matches_plan (-30) が発火
  - 結果、重み 12 の語重複を直しても、新しい -30 点違反で合格 80 に届かない

**観測方法**:
```bash
./compare.sh baseline variant-w12
# [ 品質指標 ] セクション + [ 違反頻度 ] を両方見る
```

**期待される数字**:
- baseline: 91.7% → variant: 88% ~ 92% (ほぼ変化なし)
- または baseline: 91.7% → variant: 83% (他の違反が増えた = over-tuning)

---

### 4. スコア分布 (score_buckets) の変化

**予測**: 分布が **わずかに左シフト** (低スコア側へ)

**根拠**:
- repeated_word を検出する確率が上がる
- → 違反数が増える
- → 最終スコアが若干下がる傾向

**観測方法**:
```bash
./compare.sh baseline variant-w12
# [ スコア分布 ] セクション
```

**期待される結果**:
```
[ スコア分布 ]
  0-39                          0 ->        0
  40-59                         0 ->        0
  60-79                         1 ->        2  (+1 左シフト)
  80-89                         7 ->        6  (-1)
  90-100                        4 ->        4
```

---

### 5. Plateau 率 (plateau_rate) の変化

**予測**: `plateau_rate` は **大きく変わらない** (small increase 最大)

**根拠**:
- repeated_word は軽い違反 (minor)
- refine ループが停滞するほどの「ハードブロック」ではない
- max_refines に達する前に改善が見つかる可能性が高い

**観測方法**:
```bash
./compare.sh baseline variant-w12
# [ 品質指標 ] セクションで "plateau 率" を確認
```

**期待される数字**:
- baseline: 8.3% → variant: 8.3% ~ 16.7%
- +15% 以上なら「重すぎ」の兆候

---

## 解釈ガイド: 結果から何を読むか

### ✓ 想定通りのポジティブ結果 (重みアップの効果あり)

```
BEFORE: baseline      (n=12)
AFTER:  variant-w12   (n=12)
========================================================================

[ 品質指標 ]
  初回合格率                  25.0% ->  20.8%  (-4.2%) 微劣化
  総合合格率                  91.7% ->  91.7%  (0.0%) 不変
  平均 attempt 数             4.25 ->   4.50  (+0.25) 微増
  平均最終スコア              88.50 ->  87.92  (-0.58) 微低下
  plateau 率                   8.3% ->   8.3%  (0.0%) 不変
  max_refines 率               0.0% ->   0.0%  (0.0%) 不変

[ 違反頻度 ]
  repeated_word                      2 ->        6  (+4) ⭐
  kigo_present                       0 ->        0
  season_matches_plan                0 ->        0
```

**解釈**:
- ✓ repeated_word 違反が 2 → 6 に増加 (4 倍!) = 検出が機能している
- ✓ 他の違反は不変 = 語重複修正が他の品質を損なわない
- ✓ 試行回数が +0.25 で微増 = 修正に手間が増えた程度
- ✓ 合格率・plateau・max_refines は不変 = 実行可能性に影響なし
- → **重み 12 は適切。語重複を適度に強制しながら、他の品質は維持できている**

**結論**: 重み 12 への変更を**採用可能**。ただし他の優先度を下げる検討も

---

### ⚠ 注意が必要な結果 (over-tuning 兆候)

```
BEFORE: baseline      (n=12)
AFTER:  variant-w12   (n=12)
========================================================================

[ 品質指標 ]
  初回合格率                  25.0% ->  16.7%  (-8.3%) 劣化
  総合合格率                  91.7% ->  75.0%  (-16.7%) ⚠ 大幅劣化
  平均 attempt 数             4.25 ->   6.58  (+2.33) ⚠ 大幅増加
  平均最終スコア              88.50 ->  79.25  (-9.25) ⚠ 大幅低下
  plateau 率                   8.3% ->  33.3%  (+25.0%) ⚠ 激増
  max_refines 率               0.0% ->   8.3%  (+8.3%) ⚠

[ 違反頻度 ]
  repeated_word                      2 ->        8  (+6)
  season_matches_plan                2 ->        5  (+3) ⚠
  no_other_kigo_cross                1 ->        3  (+2) ⚠
  kigo_present                       0 ->        1  (+1)
```

**解釈**:
- ⚠ 合格率が 91.7% → 75% (16.7% 低下!) = 著しい品質劣化
- ⚠試行回数が +2.33 = refine が「最後まで走っても合格できない」ケースが増えた
- ⚠ plateau 率 25% (大幅増加) = 改善が停滞するお題が増えた
- ⚠ season_matches_plan や no_other_kigo_cross も増加 = 複合効果で誤検出が増えている可能性

**原因の推測**:
- 重み 12 が「語重複回避」にモデルを集中させすぎている
- 季語を変える、季節を逸脱する等の「副作用」が起きている

**結論**: 重み 12 は**過度。元の 3 に戻すか、中間値 (6-8) を試す検討が必要**

---

### △ 判定が難しい結果 (ノイズ内か有意差か)

```
BEFORE: baseline      (n=12)
AFTER:  variant-w12   (n=12)
========================================================================

[ 品質指標 ]
  初回合格率                  25.0% ->  20.8%  (-4.2%) 微劣化
  総合合格率                  91.7% ->  83.3%  (-8.4%) △ 微劣化
  平均 attempt 数             4.25 ->   4.92  (+0.67) △ 微増
  平均最終スコア              88.50 ->  85.75  (-2.75) △ 微低下
  plateau 率                   8.3% ->  16.7%  (+8.4%) △ 微増
  max_refines 率               0.0% ->   0.0%  (0.0%)

[ 違反頻度 ]
  repeated_word                      2 ->        5  (+3)
```

**解釈**:
- 数値は「悪い方向」だが、幅が小さい (8% 以内)
- 短歌生成は best-of-N サンプリング (確率変数) なので、同じお題でも ±3 ~ 5% ぶれる可能性
- 単一 run 同士の比較では、この程度の差は **sampling noise の可能性が高い**

**確実な判定を下すには**:
1. variance を測定 (eval-repeat.sh で同一お題 5 回反復)
2. その noise 幅 (stdev) と比較
3. 差が stdev × 2 以上あれば「有意」、以下なら「ノイズ」と判定

例:
```bash
./eval-repeat.sh "夏の終わり" 5  # baseline
# → stdev = 3.2 (同一お題で ±3.2 ぶれる)

./eval-repeat.sh "夏の終わり" 5  # variant-w12
# → stdev = 3.5

# 比較結果が avg_attempts +0.67 なら、
# noise 幅 (3.2 × 2 = 6.4) 内なので「有意差なし」と判定可能
```

---

## A/B 測定の実行例

### コマンド列 (推奨フロー)

```bash
# 1. Baseline 測定
cd /Users/nrm176p/GitHub2/LLM
docker compose down && docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh baseline

# 2. Variant 測定
cd /Users/nrm176p/GitHub2/LLM
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh variant-w12

# 3. 比較
./compare.sh baseline variant-w12

# 4. 結果の解釈 (上記の期待結果パターンと照合)

# 5. 必要に応じて variance 測定
./eval-repeat.sh "散りゆく桜" 5  # baseline の variance
```

### 結果ファイルの保存

```bash
# eval 結果は自動保存される
ls -lh app/backend/eval/results/*.json

# 例:
# 20260612-140000-baseline.json (平均 attempt 4.25 等の集計)
# 20260612-150000-variant-w12.json (平均 attempt 4.92 等の集計)
```

---

## 判定の最終チェックリスト

| 項目 | 確認内容 | 成功基準 |
|---|---|---|
| 環境変数通過 | repeated_word の weight が実際に 12 か | コンテナログで TANKA_W_REPEATED_WORD=12 を確認 |
| 違反検出 | repeated_word が baseline より増加か | violation_frequency で 2-3 件以上の増加 |
| 合格率への影響 | overall_pass_rate の低下が ±5% 以内か | 5% 以内ならOK、10% 以上なら要検討 |
| 試行回数 | avg_attempts の増加が +1.5 以内か | 大幅増加なら過度な強制の兆候 |
| Plateau 率 | plateau_rate の増加が +10% 以内か | 15% 以上なら改善停滞の兆候 |
| 複合効果 | 他の違反 (季語・季節系) が増加していないか | 新規の違反増加がなければOK |

**最終判定**:
- ✓ 全項目が「成功基準」を満たす → **重み 12 を採用推奨**
- △ 数項目が「微超過」 → **variance 測定で確実化**
- ✗ 複数項目が大幅超過 → **重み 12 は過度。元に戻すか中間値を検討**

---

## おまけ: 異なる重みでの A/B も検討

repeated_word は「micro-optimization」なので、単純な二値では なく段階的なテストも有効:

```bash
# 複数段階での測定パターン
TANKA_W_REPEATED_WORD=3 ./eval.sh baseline     # 現行
TANKA_W_REPEATED_WORD=6 ./eval.sh variant-w6  # 2 倍
TANKA_W_REPEATED_WORD=9 ./eval.sh variant-w9  # 3 倍
TANKA_W_REPEATED_WORD=12 ./eval.sh variant-w12 # 4 倍
```

その後、結果をグラフ化 (avg_attempts / plateau_rate / repeated_word freq の
重み別推移) して「最適重み」を見つける。

---

## 参考: CLAUDE.md からの注意書き (§6.4)

> 8B モデルの指示追従の弱さ
> - **Plan で決めた季節/季語を Compose で勝手に変える** 現象がよく起きる
> - 対策: `season_matches_plan` (-30) / `kigo_matches_plan` (-20) を validator に

重み調整の副作用として「季語変更」が起きないか、特に season_matches_plan と
kigo_matches_plan の違反頻度に注目すること。
