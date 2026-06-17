# repeated_word ルール重み A/B 測定 実行手順

## タスク概要

validator の `repeated_word` ルール (語の重複検出) の重みを現在の **3 → 12** に上げた場合の効果を A/B 測定する。
重みを上げると refine ループの強制力が強まり、語の重複排除が厳格になるが、その代償として合格率や必要試行回数に影響が出る可能性を調査。

---

## A/B 測定パターン

### Baseline (Before)
- `TANKA_W_REPEATED_WORD=3` (現在のデフォルト)
- その他の設定は標準

### Variant (After)
- `TANKA_W_REPEATED_WORD=12` (重み 4 倍)
- その他の設定は標準

---

## 環境変数の仕組み

validator.py の `RULE_WEIGHTS` ディクショナリは `_env_int()` で環境変数をチェックしている:

```python
"repeated_word": _env_int("TANKA_W_REPEATED_WORD", 3),
```

このため、**重みを変えるには 2 つの方法がある**:

### 方法 1: ハードコード変更 (リビジョン管理)
diff を app/backend/validator.py の line 61 に適用:
```diff
-    "repeated_word":         _env_int("TANKA_W_REPEATED_WORD", 3),
+    "repeated_word":         _env_int("TANKA_W_REPEATED_WORD", 12),
```

**手順**:
```bash
cd /Users/nrm176p/GitHub2/LLM
patch -p0 < DIFF-repeated-word-weight-ablation.patch
docker compose restart backend
sleep 5  # backend 再起動待ち
cd app/backend/eval
./eval.sh variant-w12
```

その後、before の eval と比較:
```bash
./compare.sh baseline variant-w12
```

**メリット**: 再現性が高く、git に記録できる
**デメリット**: 評価終了後に revert が必要

### 方法 2: 環境変数オーバーライド (推奨、リビジョン管理不要)

docker-compose コンテナ側で環境変数を指定。

**手順**:
```bash
cd /Users/nrm176p/GitHub2/LLM
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5  # backend 起動待ち
cd app/backend/eval
./eval.sh variant-w12
```

eval 完了後、baseline と比較:
```bash
./compare.sh baseline variant-w12
```

baseline と同じ設定で再度 up:
```bash
cd /Users/nrm176p/GitHub2/LLM
docker compose up -d backend  # env var なし = デフォルト
```

**メリット**: ファイル変更が不要、いつでも revert 可能
**デメリット**: env var 指定方法が複数あり、typo に注意

---

## 詳細な実行手順 (方法 2 推奨)

### Step 1: Baseline (Before) の測定

既存設定で baseline eval を実施 (未実施なら):
```bash
cd /Users/nrm176p/GitHub2/LLM
docker compose up -d  # 標準設定で起動 (TANKA_W_REPEATED_WORD=3)
curl -s http://localhost:8001/api/health  # 起動確認
cd app/backend/eval
./eval.sh baseline
```

結果: `results/20YYMMDDhhmmss-baseline.json`

### Step 2: Variant (After) の測定

重みを 12 に上げたバージョンで eval 実施:
```bash
cd /Users/nrm176p/GitHub2/LLM
# backend コンテナを env var 付きで再起動
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
curl -s http://localhost:8001/api/health  # 起動確認

cd app/backend/eval
./eval.sh variant-w12
```

結果: `results/20YYMMDDhhmmss-variant-w12.json`

### Step 3: 差分を解析

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh baseline variant-w12
```

出力例:
```
BEFORE: baseline      (n=12, results/20260101-100000-baseline.json)
AFTER:  variant-w12   (n=12, results/20260101-110000-variant-w12.json)
========================================================================

[ 品質指標 ]
  初回合格率                  25.0% ->  20.8%  (-0.0417) 劣化
  総合合格率                  91.7% ->  83.3%  (-0.0833) 劣化
  平均 attempt 数             4.25 ->   5.08  (+0.83) 劣化
  平均最終スコア              88.50 ->  86.25  (-2.25) 劣化
  plateau 率                   8.3% ->  25.0%  (+16.7%) 劣化
  max_refines 率               0.0% ->   8.3%  (+8.3%) 劣化

[ 違反頻度 (上位) ]
  repeated_word                      5 ->       12  (+7) 悪化
  kigo_present                       1 ->        0  (-1) 改善
  ...
```

この例では:
- **repeated_word 検出が 7 件増加** = 語重複がより厳しく検出されている
- **平均 attempt が +0.83 増加** = 修正に試行回数がかかるようになった
- **plateau 率が +16.7% 増加** = 改善が停滞するケースが増えた
- **総合合格率が -8.3% 低下** = 重みが強すぎる可能性

---

## 測定後の状態復帰

variant 測定後は標準設定に戻す:
```bash
cd /Users/nrm176p/GitHub2/LLM
# env var なしで再起動 = TANKA_W_REPEATED_WORD=3 (デフォルト)
docker compose up -d backend
sleep 5
curl -s http://localhost:8001/api/health
```

---

## Variance 確認 (オプション: 結果の信頼性を上げたい場合)

単一 run の avg_final_score は確率変数のため、同一お題でも ±数点ぶれる。
compare.sh の結論に確実性を持たせたい場合、variance を測定:

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval

# baseline の variance (同一お題 5 回反復)
./eval-repeat.sh "夏の終わり" 5
# 出力: results/20YYMMDDhhmmss-repeat-*.json に score の分布

# variant に切り替えて variance 測定
cd /Users/nrm176p/GitHub2/LLM
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
cd app/backend/eval
./eval-repeat.sh "夏の終わり" 5
```

出力例:
```
=== variance: お題「夏の終わり」 (n=5) ===
  scores: [85, 88, 82, 90, 87]
  mean:   86.4
  stdev:  3.16   (母標準偏差)
  min-max: 82 - 90  (range 8)

  → 同一お題で ±3.16 ぶれる。ベースライン比較でこの幅以下のスコア差は sampling noise とみなすべき。
```

**判定の目安**:
- 比較結果の差が `stdev × 2` 以上あれば **有意な差** (偽陽性の可能性 < 5%)
- `stdev × 2` 未満なら **ノイズ内** (確定判定できない)

---

## 留意事項

### 警告: 環境変数を複数指定する場合の落とし穴

`docker compose` での環境変数指定方法が複数あるため注意:

**パターン A: シェル環境変数 (推奨)**
```bash
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
```
→ Linux/macOS では問題ない。docker-compose は親シェルから VAR を継承。

**パターン B: `.env` ファイル (非推奨 — デフォルト無視)**
```bash
# docker-compose.yml と同じディレクトリに .env を作成
cat > app/.env << 'EOF'
TANKA_W_REPEATED_WORD=12
EOF
docker compose up -d backend
```
→ docker-compose.yml で `${TANKA_W_REPEATED_WORD:-}` として参照が必要。
  現在のセットアップでは `docker-compose.yml` が `environment` 節で 
  明示的に env var を指定していないため、この方法は **効かない**。

**パターン C: docker-compose up でのインライン指定 (効かない)**
```bash
docker compose up -d -e TANKA_W_REPEATED_WORD=12 backend
```
→ 誤り。`docker-compose` コマンドに `-e` フラグはない。

**結論**: **パターン A (シェル環境変数) のみ確実**。

---

## メトリクス解釈ガイド

出力される主要メトリクス:

| メトリクス | 説明 | 低いほど良い? |
|---|---|---|
| `first_attempt_pass_rate` | 初回生成で PASS_THRESHOLD (80点) 到達の割合 | 高いほど良い |
| `overall_pass_rate` | refine ループ終了時点での合格率 | 高いほど良い |
| `avg_attempts` | refine ループの平均試行回数 | 低いほど良い (高速) |
| `avg_final_score` | 最終スコアの平均値 | 高いほど良い |
| `plateau_rate` | PLATEAU_WINDOW (3) 試行でスコア改善が止まった割合 | 低いほど良い |
| `max_refines_rate` | HARD_CAP (50 試行) に達した割合 | 低いほど良い |
| `violation_frequency` | ルール別の違反出現回数 | 低いほど良い (ルール依存) |

**repeated_word ルール重み +9 (3→12) の期待効果**:
- `repeated_word` 違反出現数: **増加** (検出がより厳しくなるため)
- `avg_attempts`: **増加の可能性** (refine ループが強化されるため)
- `avg_final_score`: **影響は不定** (強すぎると却って plateau、適切なら改善)
- `overall_pass_rate`: **低下の可能性** (重みが重いと合格条件が厳しくなるため)

重みが「最適化」されているなら、許容範囲内での trade-off が見られる:
- 語重複をより排除する (repeated_word 違反増加) ✓
- 合格率や試行回数への悪影響が限定的 ✓

重みが「過大」なら:
- 語重複は排除されているが、plateau/max_refines 率が跳ね上がる ✗
- 短歌生成の LM が「語重複回避」だけに集中し、他の品質を見失う ✗

---

## 参考: Config 設定値

validator.py 以外で関連する設定:

```python
# app/backend/config.py
PASS_THRESHOLD = 80  # 合格スコア (env: TANKA_PASS_THRESHOLD)
PLATEAU_WINDOW = 3   # 改善停滞と判定する窓幅 (env: TANKA_PLATEAU_WINDOW)
HARD_CAP = 50        # refine ループの試行上限 (env: TANKA_HARD_CAP)
```

これらも環境変数で変更可能。ただし repeated_word 測定では **これらは変えない** (他要因の混入を避けるため)。

---

## トラブルシューティング

### Q: eval.sh が "backend not reachable" で落ちた

**A**: docker-compose の起動を確認:
```bash
cd /Users/nrm176p/GitHub2/LLM
docker compose ps
# STATUS が Up になっているか確認
curl -v http://localhost:8001/api/health
```

再起動:
```bash
docker compose down
docker compose up -d
sleep 5
```

### Q: 環境変数が反映されていない (eval 後の結果に repeated_word がまだ少ないなど)

**A**: コンテナが古い設定で起動している可能性:
```bash
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 10  # 十分に待つ
curl http://localhost:8001/api/health
```

コンテナログで変数設定確認:
```bash
docker compose logs -f backend | head -20
```

### Q: eval.sh が "no task_id" で複数のテーマが落ちる

**A**: LM Studio がハング/エラー状態の可能性。ホスト側で確認:
```bash
# LM Studio が起動しているか (ホストのターミナル)
ps aux | grep -i studio
# ブラウザで http://localhost:1234/ にアクセスして UI を確認
```

再起動:
```bash
# LM Studio 再起動 (ホスト側)
lms server stop
lms server start
# モデルをロード (初回 or context 超過後)
lms load llm-jp-4-8b-thinking --context-length 32768
```

その後、eval を再開。

### Q: 比較結果が自分の予想と反対 (重み上げたのに repeated_word 違反が増えていない)

**A**: validator.py の重みが実際に変わっていない可能性:

```bash
# コンテナ内で確認
docker compose exec backend python3 -c "
import sys, os
sys.path.insert(0, '/app')
os.environ['TANKA_W_REPEATED_WORD'] = '99'  # テスト値
import validator
print('RULE_WEIGHTS:', validator.RULE_WEIGHTS.get('repeated_word'))
"
```

または docker-compose up 時に `-e` を明示:
```bash
docker compose down
docker compose up -d -e "TANKA_W_REPEATED_WORD=12" backend  # 非標準だが試す
```

（ただし docker-compose に `-e` オプションは無いため、シェル環境変数のみ頼り）

---

## まとめ

1. **Baseline** 測定: 現在の設定 (W=3) で `./eval.sh baseline`
2. **Variant** 測定: `TANKA_W_REPEATED_WORD=12 docker compose up -d backend` で起動、`./eval.sh variant-w12`
3. **比較**: `./compare.sh baseline variant-w12` で差分を確認
4. **復帰**: `docker compose up -d backend` で標準設定に戻す

判定基準:
- repeated_word 違反が明確に増加 → 検出は機能している ✓
- 合格率が著しく低下 (>10%) → 重すぎる可能性
- avg_attempts が +1 以内 → 許容範囲
- plateau/max_refines 率が +15% 以上 → 過度な強制
