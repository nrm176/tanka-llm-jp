# A/B 実験手順書: `repeated_word` 重み 3 → 12 (`TANKA_W_REPEATED_WORD=12`)

作成日: 2026-06-11
対象リポジトリ: `/Users/nrm176p/GitHub2/LLM` (作業はすべて `app/` 配下)

---

## 0. TL;DR

- `TANKA_W_REPEATED_WORD` の env 上書きは **コードには既に実装済み** (`app/backend/validator.py:61`)。
  ホスト側で動作確認済み: `TANKA_W_REPEATED_WORD=12 uv run python -c "import validator; ..."` → `12`。
- **しかし、そのまま README の手順に従うと A/B は無言で無効化される。**
  `app/docker-compose.yml` の `backend.environment` は **明示列挙した変数しかコンテナに渡さず**、
  `TANKA_W_REPEATED_WORD` は列挙されていない。シェルで設定しても backend には届かず、
  **両 arm とも重み 3 のまま「noise 同士の比較」を測ってしまう**。
- 対策 = 同梱パッチ `01-docker-compose-weight-passthrough.patch` を適用してから実験する。
  各 arm 開始前に **コンテナ内の実効重みを必ず確認** する (§4 の verify ステップ)。

---

## 1. 背景と変更の意味

### 1.1 現状

- ルール実装: `app/backend/validator.py` の `_rule_repeated_word` (L418-440)。
  かな読みベースで 4→3 文字窓の重複部分文字列を検出。助詞のみの並びは除外。
  **1 attempt につき最大 1 件しか報告しない** (`return out` で早期終了) → 減点は 0 か W のどちらか。
- 現在の重み: `RULE_WEIGHTS["repeated_word"] = _env_int("TANKA_W_REPEATED_WORD", 3)` (minor)。
- 既知の問題意識: `app/backend/eval/FINDINGS.md §4` が既に
  「**`repeated_word` が重複に甘い** — 3文字窓・1件返しのため `光`×3 でも軽微 (-3)」と記録している。
  今回の「3 じゃ甘すぎる」という直感は計測済みの所見と一致する。

### 1.2 重みを 12 にすると何が変わるか (機械的な効果)

スコアは `100 − Σweight`、合格閾値は 80 (`TANKA_PASS_THRESHOLD`、変更しない)。

| 状況 | W=3 | W=12 |
|---|---|---|
| repeated_word 単独 | 97 (合格) | 88 (**依然合格**) |
| 不合格に転落させるのに必要な他違反の合計 | ≥18 点 | **≥9 点** (mora_count 10 / kigo_unique 15 / minor 3×3 などで転落) |
| critique での提示順 (weight 降順 top-2 が「最も重要な違反」枠) | mora_count(10) より下 | **mora_count(10) より上** — refine 時に LLM の注意を最優先で引く |

つまり W=12 は「単独で refine を強制する」のではなく、**他違反と同時に出たときに refine ループへ
押し戻す圧力と、critique 内での優先順位を上げる**変更。単独でも不合格にしたいなら W≥21 が必要
(今回はやらない。まず 12 の効果を測る)。

検出ロジック自体は重みと無関係なので、**「違反が検出されたか」は arm 間で比較可能** — これが §6 の分析の鍵。

### 1.3 副作用として変わるもの (解釈時に頭に入れる)

- **長期失敗記憶の教訓選択**: `_lesson_for_violations` は最大重みの違反を採用する。
  W=12 は mora_count(10) を上回るため、失敗記録の教訓が
  「目立つ語の重複を避け、表現を引き締める」に寄りやすくなる (これは意図した効果の一部)。
- **failures コレクション経由の arm 間汚染**: 不合格 attempt は `db.record_failure` で蓄積され、
  以後の生成の compose プロンプトに教訓として注入される (季節・モデルで絞った直近 3 件)。
  arm A の失敗が arm B のプロンプトに混入する order effect があるため、§4 では各 arm 前に
  failures をクリアする (`DELETE /api/failures` が用意されている)。

---

## 2. ⚠ 罠の正体: env passthrough (この実験設計の核心)

### 2.1 何が起きるか

`app/backend/eval/README.md` (L84) は `TANKA_W_<RULE>` を調整可能 env として案内し、
compose のコメント (docker-compose.yml L60-61) も
`TANKA_DYNAMIC_FEWSHOT=0 docker compose up -d backend && ./eval.sh baseline` 形式を推奨している。
これに従って

```bash
TANKA_W_REPEATED_WORD=12 docker compose up -d backend && ./eval.sh rw12   # ← 罠
```

とすると、**エラーも警告も出ずに重み 3 のまま** eval が走る。理由:

- `docker-compose.yml` の `backend.environment` は **明示列挙** (allowlist) 方式。
  現在列挙されている TANKA_* は `DYNAMIC_FEWSHOT` / `SELF_CRITIQUE` / `RAG` / `HARD_CAP` /
  `PLATEAU_WINDOW` の 5 つだけ。`TANKA_W_*` と `TANKA_PASS_THRESHOLD` は **1 つも無い**。
- compose ファイルに `${TANKA_W_REPEATED_WORD...}` という参照が無い以上、シェル env は
  どこにも展開されず、コンテナの環境に入らない。`app/.env` も存在しない。
- `validator.py` は **import 時** に env を読むため、コンテナに届かなければ既定の 3 で確定する。

結果: 両 arm が同条件になり、FINDINGS.md §2 の noise (8 題で ±6.8、50 題でも ±2.7) が
そのまま「効果」に見えてしまう。**偽の改善/劣化を信じる最悪パターン**。

### 2.2 第二の罠: `docker compose restart` では env が変わらない

CLAUDE.md §8 の「`docker compose restart backend` で reload」は **コード変更用** (volume mount +
uvicorn --reload)。`restart` は既存コンテナを再起動するだけで **環境変数は再評価されない**。
env を変えるときは必ず **`docker compose up -d backend`** (設定差分を検知してコンテナ再作成) を使う。

### 2.3 対策

1. パッチ適用 (§3) で `TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}` を compose に追加。
   compose 側の既定 3 = コード既定 3 なので、**未設定時の挙動は一切変わらない** (適用したまま
   コミットしてよい安全な変更)。
2. 各 arm の eval 開始前に、**コンテナ内の実効値を必ず verify** する (§4 手順内)。
   「設定したつもり」を二度と踏まないための儀式として固定化する。

---

## 3. 適用する差分

### 3.1 必須: docker-compose.yml への passthrough 追加

ファイル: `01-docker-compose-weight-passthrough.patch` (本ディレクトリ同梱、`git apply --check` 検証済み)

```diff
diff --git a/app/docker-compose.yml b/app/docker-compose.yml
index b79265b..185be48 100644
--- a/app/docker-compose.yml
+++ b/app/docker-compose.yml
@@ -64,6 +64,10 @@ services:
       TANKA_RAG: ${TANKA_RAG:-1}
       TANKA_HARD_CAP: ${TANKA_HARD_CAP:-50}
       TANKA_PLATEAU_WINDOW: ${TANKA_PLATEAU_WINDOW:-3}
+      # validator ルール重み (A/B 実験用)。compose は明示列挙した変数しかコンテナに渡さないため、
+      # TANKA_W_* をシェルで設定しても、ここに列挙が無ければ無言で既定値のままになる (罠)。
+      # 既定値 3 は validator.py RULE_WEIGHTS の既定と一致させ、未設定時の挙動を変えない。
+      TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}
     extra_hosts:
       # Linux でも host.docker.internal が解決されるように
       - "host.docker.internal:host-gateway"
```

### 3.2 推奨 (任意): eval README に罠の警告を追記

ファイル: `02-eval-readme-passthrough-warning.patch` (同梱)。
README が `TANKA_W_<RULE>` を「設定すれば効く」ように読める記述になっているため、
allowlist の存在と verify 手順を注記する。実験自体には不要だが、再発防止になる。

### 3.3 適用コマンド

```bash
cd /Users/nrm176p/GitHub2/LLM
OUT=.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-2-weight-env-passthrough-trap/without_skill/outputs
git apply --check "$OUT/01-docker-compose-weight-passthrough.patch"   # 事前検証
git apply "$OUT/01-docker-compose-weight-passthrough.patch"
git apply "$OUT/02-eval-readme-passthrough-warning.patch"             # 任意
```

(リポジトリ運用に合わせるなら issue → feature ブランチ → PR → squash で。compose の 1 行は
挙動中立なので baseline 計測前に適用して問題ない。)

---

## 4. 実行手順

### 4.0 前提条件

- LM Studio がホストで起動し `llm-jp-4-8b-thinking` がロード済み (`lms server start`)
- スタック起動済み: `./app/start.sh`、`curl http://localhost:8001/api/health` → ok
- §3 のパッチ適用済み
- **LM Studio をモデル fresh reload しておく** (FINDINGS §5: sustained load で 15s/call →
  3.5min/call に劣化するため、各 arm を fresh 状態から始める)
- eval 中に UI でモデルを切り替えない / 設定を変えない / 診断 streaming を投げない

### 4.1 Arm A: baseline (W=3)

```bash
cd /Users/nrm176p/GitHub2/LLM/app

# env 未設定で backend を (再) 作成 — シェルに残骸が無いことを確認
unset TANKA_W_REPEATED_WORD
docker compose up -d backend

# ★ verify (これを省略しない): コンテナ内の実効重みが 3 であること
docker compose exec backend uv run python -c \
  "import validator; print('repeated_word =', validator.RULE_WEIGHTS['repeated_word'])"
# 期待出力: repeated_word = 3

# 長期失敗記憶をクリア (arm 間汚染防止; §1.3)
curl -s -X DELETE http://localhost:8001/api/failures   # → {"cleared": N}

# eval 実行 (50 題フルセット、所要 25〜60 分)
cd backend/eval
./eval.sh rw3-baseline
```

### 4.2 Arm B: treatment (W=12)

```bash
cd /Users/nrm176p/GitHub2/LLM/app

# LM Studio のモデルを fresh reload してから (arm 間の劣化差を防ぐ)

TANKA_W_REPEATED_WORD=12 docker compose up -d backend   # 再作成される (restart は不可)

# ★ verify: 12 になっていること。3 のままなら passthrough が効いていない → §2 を再確認
docker compose exec backend uv run python -c \
  "import validator; print('repeated_word =', validator.RULE_WEIGHTS['repeated_word'])"
# 期待出力: repeated_word = 12

curl -s -X DELETE http://localhost:8001/api/failures

cd backend/eval
./eval.sh rw12
```

補足: `docker compose exec backend env | grep TANKA_W` でも env 自体の到達は確認できるが、
import 済みプロセスの実効値を見る上記 python ワンライナーの方が確実
(exec は新プロセスなので env 確認用、実効値は uvicorn プロセスと同じ env から再現される)。

### 4.3 終了後のロールバック

```bash
cd /Users/nrm176p/GitHub2/LLM/app
unset TANKA_W_REPEATED_WORD
docker compose up -d backend        # 既定 (3) に戻る
docker compose exec backend uv run python -c \
  "import validator; print(validator.RULE_WEIGHTS['repeated_word'])"   # → 3
```

compose のパッチ自体は既定 3 で挙動中立なので残してよい (今後の重み実験の土台になる)。
採用が決まった場合の恒久化は、env ではなく `validator.py` の既定値を 12 に変更 +
`tests/test_validator.py` への反映 + `docs/architecture.md` L361 の `-3` 表記更新で行う。

---

## 5. 比較

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh rw3-baseline rw12          # 既存ツール (まず全体像)

# 本実験専用の公平分析 (GET のみ・副作用なし)
OUT=/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-2-weight-env-passthrough-trap/without_skill/outputs
python3 "$OUT/analyze_repeated_word.py" \
  "$(ls -t results/*-rw3-baseline.json | head -1)" \
  "$(ls -t results/*-rw12.json | head -1)"
```

---

## 6. 解釈ガイド — このA/B特有の落とし穴

**重みを変える A/B は「スコアという物差し自体を変える」実験** なので、compare.sh の
標準指標を素直に読むと誤読する。以下を順守する。

### 6.1 `avg_final_score` / 合格率 / score_buckets は物差し不一致

W=12 arm では、最終出力に repeated_word が残るたびスコアが機械的に 9 点余計に下がる。
**実体が全く同じ出力でも** treatment の平均は `9 × (最終 repeated_word 残存率)` だけ低く出る
(例: 残存率 20% なら −1.8 点は「劣化」ではなくただの換算差)。
→ 公平な比較は `analyze_repeated_word.py` の **共通重み再採点** (両 arm を W=3 物差しで再計算) で行う。
保存された violations は rule 名ベースで重み非依存なので、この再採点は正当。

### 6.2 `violation_frequency` は全 attempt 横断 (FINDINGS §4)

重み増 → refine 圧増 → attempt 数増 → 違反の **のべ件数** は改善していても増えうる。
compare.sh の違反テーブルで repeated_word が「悪化 (+N)」と出ても誤読しない。
見るべきは **最終出力 (採用 attempt) における違反** = analyze スクリプトの主要指標。

### 6.3 主要エンドポイントと判定基準

| 指標 | 期待 | 判定 |
|---|---|---|
| ◆ 最終出力の repeated_word 残存率 | 大幅減 (→0 に近づく) | 効果の本体 |
| ◆ 共通重み再採点の平均スコア | 低下しない | noise 下限: 50 題で ±2.7 (FINDINGS §2) |
| 最終出力の他ルール違反数 | 増えない | 増加 = 重複回避のために別制約を壊すモグラ叩き (特に mora_count / kigo 系) |
| avg_attempts / plateau_rate | 微増まで許容 | 急増 = コスト過大、W=12 は過剰 → 中間値 (6〜8) を検討 |

- **採用**: 残存率が明確に下がり、共通スコア・他違反・コストが noise 範囲内。
- **棄却**: 残存率が下がらない (= そもそも単独 88 で合格するため圧が足りない可能性。
  その場合は W≥21 か PASS_THRESHOLD との組合せを次実験に)。
- **判定不能**: 差が noise 下限未満 → eval-repeat.sh で分散を測ってから再判断。

### 6.4 環境交絡のチェック (FINDINGS §5.5 の教訓)

blocked A/B (arm を順に流す) は LM Studio の sustained-load 劣化と交絡する。
解析時に **前半 25 題 / 後半 25 題で分割** し、arm 内の前後半差が arm 間差と同程度なら
環境劣化を疑う (self-critique A/B はこれで結論不能と判明した)。
理想は arm 交互実行だが eval.sh は 1 セッション = 1 variant 設計なので、
最低限「fresh reload + 同一テーマセット + 前後半チェック」を守る。

---

## 7. チェックリスト (印刷用)

- [ ] パッチ 01 適用 + `git apply --check` 通過
- [ ] LM Studio fresh reload (arm A 前)
- [ ] arm A: `unset` → `up -d` → **verify = 3** → failures クリア → `./eval.sh rw3-baseline`
- [ ] LM Studio fresh reload (arm B 前)
- [ ] arm B: `TANKA_W_REPEATED_WORD=12` + `up -d` → **verify = 12** → failures クリア → `./eval.sh rw12`
- [ ] `compare.sh` + `analyze_repeated_word.py` で分析 (§6 の読み方で)
- [ ] 前半/後半分割で環境交絡チェック
- [ ] ロールバック (`unset` → `up -d` → verify = 3)
- [ ] 結果と判断を `FINDINGS.md` に追記 (採用なら validator.py 既定値変更の別 PR)

---

## 8. 同梱ファイル

| ファイル | 内容 |
|---|---|
| `RUNBOOK.md` | 本書 |
| `01-docker-compose-weight-passthrough.patch` | **必須**: compose に `TANKA_W_REPEATED_WORD` passthrough を追加 (`git apply --check` 検証済み) |
| `02-eval-readme-passthrough-warning.patch` | 任意: eval README に allowlist 罠の警告を追記 (`git apply --check` 検証済み) |
| `analyze_repeated_word.py` | 最終出力ベース + 共通重み再採点の公平分析 (GET のみ、オフライン smoke test 済み) |

## 9. 根拠 (検証済み事実)

- `app/backend/validator.py:61` — `"repeated_word": _env_int("TANKA_W_REPEATED_WORD", 3)` (import 時読み込み)
- ホスト実測: `TANKA_W_REPEATED_WORD=12 uv run python -c "import validator; ..."` → `12` (機構は動作する)
- `app/docker-compose.yml:53-66` — backend environment は allowlist、`TANKA_W_*` は不在 / `app/.env` も不在
- `app/backend/eval/README.md:84` — `TANKA_W_<RULE>` を案内 (passthrough 注記なし = 罠の入口)
- `app/backend/db.py:435` — violation_frequency は「全 attempt の全 violation」を数える
- `app/backend/tasks.py:233-245` — 未解決 validation は毎回 `record_failure` → 長期記憶へ
- `app/backend/main.py:415` — `DELETE /api/failures` で失敗記憶をクリア可能
- `app/backend/eval/FINDINGS.md` §2 (noise 下限) / §4 (repeated_word の甘さ) / §5-5.5 (LM Studio 劣化と blocked A/B の教訓)
