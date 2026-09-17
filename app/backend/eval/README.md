# 評価ハーネス (Phase 2)

短歌生成パイプラインの **品質を客観測定** し、変更前後を A/B 比較するための仕組み。
「触ったら良くなった気がする」という主観評価を排除し、数字で改善/劣化を判断する。

---

## 構成

| ファイル | 役割 |
|---|---|
| `eval_themes.json` | 固定お題セット (50 件、四季 × 古典/現代 × 抽象/具体)。**安易に変更しない** (変えると過去結果と比較不能) |
| `eval.sh` | 1 バリアントを 1 セッションで順次生成し、メトリクスを `results/` に保存 |
| `eval-repeat.sh` | 同一お題を N 回生成し variance (mean/std/range) を測る。best-of-N の振れ幅を定量化 |
| `eval-paired.sh` | **paired A/B**: 2 arm をお題ごとに ABBA で交互実行 (LM Studio 劣化が対称に作用)。per-request 上書き (例 `{"self_critique": false}`) で arm を定義。paired difference 統計 + 事後検証を出力、中断再開可 |
| `eval_themes_smoke2.json` | ハーネス自体の dry-run 用 2 題。品質判断には使わない |
| `experiments/` | 実験ごとの versioned runbook (事前登録・手順・判定基準)。例: `self-critique-paired/` |
| `compare.sh` | 2 つの結果を比較し、差分を 改善/劣化 + sampling-noise 警告付きで表示 |
| `results/` | 各実行の結果 JSON (`<timestamp>-<variant>.json`) |
| `FINDINGS.md` | Phase 2 の知見ログ (ベースライン・variance・バグ・環境制約) |

### 評価モニタ UI

ブラウザ (http://localhost:5178) のヘッダー「評価モニタ」タブで、走行中・完了済みの
eval run の状況をライブ表示できる (`GET /api/eval/runs` を 3 秒ポーリング)。
run ごとに variant・進捗・実行中テーマ・score スパークライン・per-theme テーブル + 統計を表示。

メトリクスは backend の `GET /api/metrics?session_id=<sid>` が算出する (`db.compute_metrics`)。

---

## 測定指標

| 指標 | 意味 | 良い方向 |
|---|---|---|
| `first_attempt_pass_rate` | 初回 (attempt 0) で合格した割合 | 高い |
| `overall_pass_rate` | 最終的に合格した割合 | 高い |
| `avg_attempts` | 平均何回 validate したか | 低い (少ない改稿で合格) |
| `avg_final_score` | 平均最終スコア (0-100) | 高い |
| `plateau_rate` | plateau 打ち切りの割合 | 低い |
| `max_refines_rate` | HARD_CAP 到達の割合 | 低い |
| `score_buckets` | スコア分布 (0-39 / 40-59 / ...) | 右寄り |
| `violation_frequency` | ルール別違反の総出現数 | 各ルール低い |
| `violation_by_severity` | critical/major/minor 別件数 | critical 低い |

---

## 基本ワークフロー (A/B 比較)

パイプラインに変更を加えたとき、それが本当に改善かを測る手順：

```bash
cd app/backend/eval

# 1. ベースライン (現状) を測定
#    backend がデフォルト設定で起動している状態で:
./eval.sh baseline

# 2. 変更を加える
#    例: 自己点検フェーズを切る場合は env var を変えて backend 再起動
cd ../..  # app/ へ
TANKA_SELF_CRITIQUE=0 docker compose up -d backend
#    (環境変数は docker-compose.yml の backend.environment でも指定可)

# 3. 変更版を測定
cd backend/eval
./eval.sh no-self-critique

# 4. 比較
./compare.sh baseline no-self-critique
```

`compare.sh` は variant 名を渡すと `results/` 内の最新一致ファイルを自動選択する。
ファイルパスを直接渡すことも可能。

---

## チューニング可能な設定 (環境変数)

backend コンテナの環境変数でパイプライン挙動を変えられる (再起動が必要)。
アブレーション実験はこれらを切り替えて `eval.sh` を回す。

| 環境変数 | デフォルト | 効果 |
|---|---|---|
| `TANKA_SELF_CRITIQUE` | `0` | 自己点検フェーズの ON/OFF (Phase 1 B4)。2026-09 に既定 OFF (#66、FINDINGS §5.7) |
| `TANKA_PASS_THRESHOLD` | `80` | 合格スコアのしきい値 |
| `TANKA_W_<RULE>` | 各ルール既定 | ルール別の減点重み (例: `TANKA_W_KIGO_UNIQUE=20`) |

重みの環境変数名は `validator.py` の `RULE_WEIGHTS` を参照。

### アブレーション例

各コンポーネントの寄与を測る:

```bash
# 自己点検なし (既定 = baseline)
./eval.sh baseline

# 自己点検あり
cd ../.. && TANKA_SELF_CRITIQUE=1 docker compose up -d backend && cd backend/eval
./eval.sh with-self-critique

./compare.sh baseline with-self-critique
# → 自己点検が avg_final_score を何点上げているかが分かる
# (※ この blocked A/B は LM Studio 劣化に弱い。厳密な判定は eval-paired.sh を使う — FINDINGS §5.5/§5.7)
```

---

## 所要時間の目安

1 お題あたり 30秒〜2分 (LLM のお題難易度・改稿回数・自己点検の有無による)。
50 お題で **25〜60 分**。短時間で回したいときは小さな subset を作る:

```bash
# 8 お題の subset 例
cat > /tmp/themes_subset.json << 'EOF'
{"_version": 1, "themes": [
  {"id":"spr-01","theme":"散りゆく桜"},
  {"id":"sum-05","theme":"蛍の舞う川辺"},
  ...
]}
EOF
./eval.sh quick-test /tmp/themes_subset.json
```

ただし **正式な A/B 比較は同じお題セットで** 行うこと (subset 同士、full 同士)。

---

## 注意点

- **LM Studio が起動している必要がある** (eval は実際に生成を走らせる)。
- **eval 中は backend の設定を変えない** (途中で変わると測定がブレる)。
- 結果ファイルには `_variant` / `_session_id` / `_timestamp` / `_themes_file` がメタとして埋まる。
  セッション ID から MongoDB で個別の短歌を追える。
- `eval.sh` は専用セッションを作るので、通常のチャット履歴は汚さない。
  ただし `/api/metrics` (session_id 省略) は全セッション集計なので、eval セッションも含まれる点に注意。
  バリアント単位の集計は必ず `session_id` 付きで取る (eval.sh は自動でそうする)。

---

## メトリクスだけ単独で見る

```bash
# 全セッション横断
curl -s http://localhost:8001/api/metrics | python3 -m json.tool

# 特定セッションのみ
curl -s "http://localhost:8001/api/metrics?session_id=<sid>" | python3 -m json.tool
```
