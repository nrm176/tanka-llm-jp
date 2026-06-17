# repeated_word 重み A/B 実験 — 準備パッケージ (差分 + 実行手順書)

- 実験: `TANKA_W_REPEATED_WORD` 3 (現行既定) vs 12
- 準拠: `.claude/skills/tanka-eval-ablation/SKILL.md` (Step 0–5)
- 準備日: 2026-06-11 (本書の事実確認はすべてこの日の repo / 稼働中 backend に対して実施)
- 状態: **準備のみ完了。repo は未変更、docker は未操作、eval は未実行。**

## 成果物一覧 (このディレクトリ)

| ファイル | 役割 |
|---|---|
| `RUNBOOK-repeated-word-weight-ab.md` | 本書。事前登録 + 差分 + 実行手順 + 判定フロー |
| `repeated-word-weight-ab.patch` | 適用すべき差分 (`git apply` 可能なことを `--check` で検証済み) |
| `analyze_final_repeats.py` | 専用事後分析 (GET のみ)。過去の実データでスモークテスト済み (付録 B) |

---

## 0. なぜ「設定変更の差分」が必要か — 本実験は passthrough の罠に該当する

SKILL.md Step 2 の警告どおり、`docker-compose.yml` の `backend.environment` に列挙されていない
`TANKA_*` は **ホストで export しても backend コンテナに届かない**。

2026-06-11 時点の列挙 (`app/docker-compose.yml` L62–66):
`TANKA_DYNAMIC_FEWSHOT` / `TANKA_SELF_CRITIQUE` / `TANKA_RAG` / `TANKA_HARD_CAP` / `TANKA_PLATEAU_WINDOW`
— **`TANKA_W_REPEATED_WORD` は無い**。

つまり差分なしで `TANKA_W_REPEATED_WORD=12 docker compose up -d backend` としても backend は
重み 3 のまま動き、**「同じものを 2 回測って差を論じる」最悪の事故**になる。本パッケージの差分 1 は
この passthrough 追記である (既定値 3 なので、適用しただけでは挙動は一切変わらない)。

さらに二重の罠: env は `docker compose restart` では効かない (up 時に固定)。**必ず `up -d` で
コンテナ再作成**し、適用確認 (手順 §4) を必ず行う。重みは `validator.py` の import 時に
`RULE_WEIGHTS` へ焼き込まれる (L48–70) ため、プロセス再起動が必須という意味でも `up -d` が正しい。

## 1. 問いと作用機序 (コード上の事実)

**問い (1 実験 1 変数)**: `repeated_word` ルールの重みを 3 → 12 に上げると、最終成果物から
語の重複が実際に減るか。そのコスト (attempt 増・合格率低下) は見合うか。

ユーザー仮説の傍証は既に記録されている: `app/backend/eval/FINDINGS.md` §4
「`repeated_word` が重複に甘い — 3文字窓・1件返しのため `光`×3 でも軽微 (-3)」。

コード上の事実 (`app/backend/validator.py`):

- L61: `"repeated_word": _env_int("TANKA_W_REPEATED_WORD", 3)` — severity は minor
- `_rule_repeated_word` (L418–440) は **1 評価あたり最大 1 件しか報告しない** (1 件目で即 return)
  → 重み変更の score 影響は「発火した評価につき、ちょうど −3 → −12 (差 9 点)」の正確な算術
- `PASS_THRESHOLD = 80` (L45)。**w=12 でも repeated_word 単独では 88 点で合格のまま**。
  効果は単独不合格化ではなく、以下の 4 経路で効く:
  1. **合算不合格化**: 不合格 (<80) に必要な「他の違反」が 18 点分 → 9 点分に半減。
     例: repeated(12) + theme_motif_uncovered(5) + kireji_absent(3) + mora_disputed(3) = 77 で不合格
  2. **critique での浮上**: `format_critique` は重み降順 top-2 を「最も重要な違反 (これを必ず直す)」
     に出す。12 は全 minor (3/3/3/5/5/5) を上回り、refine 指示の先頭に立つ
  3. **長期失敗記憶の教訓選定**: `_lesson_for_violations` は最大 weight の違反の LESSON を採る。
     w=12 では「目立つ語の重複を避け…」が記録されやすくなる
  4. **best-attempt 選定のシフト**: 最終結果は全 attempt の best score (`tanka.py` L241、strict 比較・
     タイは先勝ち)。重複なし草稿が +9 相対的に優遇される

## 2. 計測上の最重要注意 — 重み変更は「物差し」自体を変える

w=3 と w=12 では **score の定義が違う**。最終稿に重複が残った歌は w=12 側で機械的に 9 点低く出るため、
`compare.sh` が出す `avg_final_score` の生の差は品質差として解釈できない (同一品質でも「劣化」に見える)。

→ 本実験の評価軸は次の 3 つに分離する (`analyze_final_repeats.py` が全部計算する):

| 軸 | 指標 | 出所 |
|---|---|---|
| 効果 (主要) | **最終成果物の repeated_word 残存率** (final_score に最初に到達した attempt の violations を見る) | 分析スクリプト §1 |
| 品質ガード | **w=3 共通スケール換算の avg_final_score** (残存 final に +9 を足し戻し) | 分析スクリプト §2 |
| コスト | avg_attempts / first_attempt_pass_rate / plateau_rate | compare.sh |

`violation_frequency` は全 attempt 横断 (探索過程の分布) であり最終成果ではない (SKILL.md Step 4-5)。
参考値としてのみ見る。

なお、永続化された violations には weight がそのまま入る (`tanka.py` L132、実データで確認済み) ので、
**「env が届いていたか」を測定後に実データから検証できる**。分析スクリプトの有効性ガードが
before=[3] / after=[12] 以外を検出したら、その測定は無効 (passthrough 事故) として捨てる。

## 3. 実験設計の事前登録 (実行開始前に notes.md へ転記すること)

> 実行担当への注意: CLAUDE.md §12 に従い、実行開始時にこのブロックを repo ルートの `notes.md` に
> 転記し、開始時刻を記録すること (本準備セッションは repo ファイル変更禁止の制約下のため未作成)。

1. **問い**: TANKA_W_REPEATED_WORD のみ 3 vs 12 (他は一切変えない)
2. **変更の種類**: env 切替 (コード変更なし)。compose passthrough 追記が前提 (差分 1)
3. **固定条件** (2026-06-11 に `docker inspect app-backend-1` で実測した現行値):
   `TANKA_DYNAMIC_FEWSHOT=1` / `TANKA_SELF_CRITIQUE=1` / `TANKA_RAG=1` / `TANKA_HARD_CAP=50` /
   `TANKA_PLATEAU_WINDOW=3` / モデル `llm-jp-4-8b-thinking` (デフォルト → variant 名にモデル名不要)
4. **variant 名**: `w-repeated-3` / `w-repeated-12` (パイロットは `-pilot` を後置。
   compare.sh の名前解決は末尾一致 glob なので衝突しない)
5. **お題セット**:
   - パイロット: `eval_themes_quick8.json` (差分 2 で新設、8 件、四季×2・register/concreteness 各 4)。
     目的は **env 適用検証・所要時間見積もり・分析スクリプトの動作確認のみ**。品質判定には使わない
   - 本判定: `eval_themes.json` (full 50)。before/after は必ず同一ファイル
     (結果 JSON の `_themes_file` 不一致の比較は無効)
6. **判定基準 (ノイズ下限 2SE、FINDINGS.md §2 由来。後から動かさない)**:
   - 品質ガード (w=3 換算平均): n=50 で **±2.7**、n=8 で ±6.8、n=16 で ±4.8。
     換算後の差がこの幅以内なら「品質への影響は判定不能」と報告する (それも正当な結論)
   - 効果 (残存率の差、比率): 過去 run の実測残存率は 12.5〜25% (付録 B; 別実験の n=16 なので参考値)。
     p≈0.2 と仮定すると n=50 同士の差の 2SE ≈ **±0.16**。残存率の低下がこれ以下なら「効果は判定不能」
   - コスト許容値 (採用の必要条件): avg_attempts の増分 +50% 以内、かつ overall_pass_rate /
     plateau_rate がノイズを超えて悪化しないこと。**この許容値は実行前にユーザーと合意してから走らせる**
7. **採用条件 (AND)**: 残存率が ±0.16 を超えて低下 / w=3 換算品質が ±2.7 を超えて悪化していない /
   コスト許容内。1 つでも欠ければ「採用見送り (劣化 or 判定不能)」として FINDINGS.md に記録
8. **交絡対策**:
   - **LM Studio sustained-load 劣化** (CLAUDE.md §6.13): 各 variant 直前にモデルを fresh reload。
     blocked A/B (A 全部→B 全部) は後半が劣化で沈む交絡が実証済み (FINDINGS §5.5) なので、
     前半/後半チェックで符号が変わったら run を捨てて再測定。際どい場合は ABBA (50×4 run) に拡張
   - **長期失敗記憶の順序効果 — 本実験では特に危険**: w=12 は失敗記録の lesson 選定 (最大 weight) で
     repeated_word の教訓を選びやすくし、それが後続 run のプロンプトに注入される。variant 間で
     持ち越すと**測定対象ルールに直接交絡**する。→ 各 variant 直前に `DELETE /api/failures` で
     リセットする (蓄積記憶が消える操作なので、実行前にユーザーへ一言確認すること)
   - 実行順: A (w=3) → B (w=12)。eval.sh は per-theme interleave 非対応 (1 セッション = 1 variant 集計)
     のため、paired 設計は不可。fresh reload + 失敗記憶リセット + 前半/後半チェックで代替する
9. **所要時間見積もり**: 1 お題 30 秒〜2 分 → 50 お題 × 2 variant で **50 分〜2 時間** + reload 2 回。
   パイロット 8 お題 ×1 で 4〜16 分

## 4. 差分 (patch ファイルと同一内容)

適用コマンド (検証済み: `git apply --check` 通過、2026-06-11 の main 相当 working tree):

```bash
cd /Users/nrm176p/GitHub2/LLM
git apply .claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-2-weight-env-passthrough-trap/with_skill/outputs/repeated-word-weight-ab.patch
git diff --stat   # docker-compose.yml +3 行 / eval_themes_quick8.json 新規 のみであること
```

> 運用上の推奨: GitHub issue を立てて feature ブランチ上で適用する
> (このプロジェクトは issue → feature ブランチ → PR → squash マージ運用)。

差分 1 — `app/docker-compose.yml` (passthrough 追記。既定値 3 = `validator.py` と一致、適用だけでは無挙動変化):

```diff
--- a/app/docker-compose.yml
+++ b/app/docker-compose.yml
@@ -64,6 +64,9 @@
       TANKA_RAG: ${TANKA_RAG:-1}
       TANKA_HARD_CAP: ${TANKA_HARD_CAP:-50}
       TANKA_PLATEAU_WINDOW: ${TANKA_PLATEAU_WINDOW:-3}
+      # validator ルール重みの passthrough (repeated_word 重み A/B 実験用)。
+      # 既定値は validator.py の _env_int("TANKA_W_REPEATED_WORD", 3) と必ず一致させること
+      TANKA_W_REPEATED_WORD: ${TANKA_W_REPEATED_WORD:-3}
     extra_hosts:
       # Linux でも host.docker.internal が解決されるように
       - "host.docker.internal:host-gateway"
```

差分 2 — `app/backend/eval/eval_themes_quick8.json` (新規、パイロット用 8 お題。SKILL.md Step 1-3 の
「subset は eval/ 内にファイルとして置く (/tmp は再現不能)」に従う。本文は patch ファイル参照。
お題: spr-01 散りゆく桜 / spr-04 新生活への不安と希望 / sum-03 海辺の街を見下ろす坂道 /
sum-07 涼風に覚える秋の予感 / aut-06 落ち葉を踏みしめる帰り道 / aut-07 虫の音に聞く命の儚さ /
win-01 降りしきる雪 / win-08 終電を待つホームの寒さと孤独。spr-01 は variance 実測 (stdev 9.6) の
アンカーお題、sum-03 は score 0 経験のある難お題)。

## 5. 実行手順 (コピペ用)

### Step 0 — 前提チェック (SKILL.md Step 0)

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

- `status: ok` / `lm_studio_ok: true` / `model_loaded: true` を確認
- **`configured_model: llm-jp-4-8b-thinking` を確認** (UI から runtime 切替され mongo に永続化されて
  いることがある。違っていたら UI で戻す)。2026-06-11 時点は上記すべて OK だった
- LM Studio が直前まで連続負荷を受けていたら、ユーザーにモデルの fresh reload を依頼してから開始

### Step 1 — 差分適用 + 事前登録の転記

```bash
cd /Users/nrm176p/GitHub2/LLM
git apply .claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-2-weight-env-passthrough-trap/with_skill/outputs/repeated-word-weight-ab.patch
# 本書 §3 を notes.md に転記し、`date` で開始時刻を記録
```

### Step 2 — variant A (w=3 を明示して測る)

```bash
# (ユーザー確認の上で) 長期失敗記憶をリセット。現在の蓄積件数を先に控える:
curl -s "http://localhost:8001/api/failures?limit=1" | python3 -c "import sys,json;print('failures:',json.load(sys.stdin)['count'])"
curl -s -X DELETE http://localhost:8001/api/failures

# LM Studio fresh reload (ホスト側。例: lms unload --all && lms load llm-jp-4-8b-thinking)

cd /Users/nrm176p/GitHub2/LLM/app
TANKA_W_REPEATED_WORD=3 docker compose up -d backend     # ★restart 禁止。up -d で再作成

# 適用確認 — 信じずに見る (SKILL.md Step 2):
docker compose exec backend printenv | grep TANKA
#   → TANKA_W_REPEATED_WORD=3 が並ぶこと
docker compose exec backend uv run python -c "import validator; print(validator.RULE_WEIGHTS['repeated_word'])"
#   → 3
```

(任意・初回のみ) パイロットで配管確認:

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./eval.sh w-repeated-3-pilot eval_themes_quick8.json     # 4〜16 分
```

本走 (50 お題、25〜60 分):

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./eval.sh w-repeated-3
```

- Claude Code から回す場合は **`run_in_background` で起動** (Bash の 10 分 timeout を超える)。
  完了判定は `results/<ts>-w-repeated-3.json` の出現。進捗はフロント「評価モニタ」タブ
  (http://localhost:5178)。停止する場合に備え PID を控える (pkill のパターン外しに注意)
- **実行中の禁則**: backend の設定・コードを変えない / LM Studio に手動チャット・診断 streaming を
  投げない / 複数 eval を並行しない

### Step 3 — variant B (w=12)

```bash
# (ユーザー確認の上で) 失敗記憶をリセット — w12 は repeated_word 教訓を蓄積しやすく、持ち越しは直接交絡
curl -s -X DELETE http://localhost:8001/api/failures

# LM Studio fresh reload (variant 間で必ず行う)

cd /Users/nrm176p/GitHub2/LLM/app
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# 適用確認 (毎回):
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD    # =12
docker compose exec backend uv run python -c "import validator; print(validator.RULE_WEIGHTS['repeated_word'])"  # 12

cd backend/eval
./eval.sh w-repeated-12        # run_in_background 推奨
```

> 罠: B の実行中・実行前に、変数を export していないシェルで `docker compose up -d` を打つと
> `${TANKA_W_REPEATED_WORD:-3}` が 3 に解決し直されてコンテナが w=3 で再作成される。
> eval 中は compose コマンドを一切打たないこと。

### Step 4 — 比較と判定

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
./compare.sh w-repeated-3 w-repeated-12
# まず _themes_file と n の一致を目視確認。avg_final_score の生差は本実験では解釈禁止 (§2)

python3 /Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-2-weight-env-passthrough-trap/with_skill/outputs/analyze_final_repeats.py \
  results/<ts>-w-repeated-3.json results/<ts>-w-repeated-12.json
```

分析スクリプトの読み方 (§3 の事前登録と照合):

1. **有効性ガード**が ✗ なら測定無効 (passthrough 事故 / お題セット不一致)。原因を直して再測定
2. **残存率** の低下が ±0.16 超 → 効果あり。以内 → 「効果は判定不能」
3. **w=3 換算平均** の悪化が ±2.7 (n=50) 以内に収まっているか → 品質ガード
4. **前半/後半** で結論の符号が変わるなら、その run は採否判断に使わず再測定
   (fresh reload + 必要なら ABBA 4 run に拡張)
5. コスト (compare.sh の avg_attempts / pass rate / plateau_rate) を許容値と照合

### Step 5 — 後片付けと記録

```bash
# 既定 (w=3) へ戻す — 変数なしの up -d で ${VAR:-3} が既定解決され再作成される
cd /Users/nrm176p/GitHub2/LLM/app
docker compose up -d backend
docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD    # =3 を確認
```

- 結果が採用・棄却・判定不能のいずれでも `app/backend/eval/FINDINGS.md` に追記 (テンプレ: 付録 A)
- **採用する場合の本実装**は env 運用ではなく既定値の変更:
  `validator.py` L61 を `_env_int("TANKA_W_REPEATED_WORD", 12)` に変更し、compose の既定も
  `${TANKA_W_REPEATED_WORD:-12}` に揃える (両者の一致が不変条件)。passthrough 行自体は今後の
  アブレーション用に残す。`cd app/backend && uv run pytest -q` を必ず通す
  (テストは `RULE_WEIGHTS` を動的参照しており重み 3 のハードコードは無いことを確認済み)
- PR 本文に before/after の数値と判定根拠 (残存率・w=3 換算差・コスト・ノイズ下限) を書く
- compose に passthrough を足したら、SKILL.md Step 2 の「現在列挙済み」リストにも
  `TANKA_W_REPEATED_WORD` を追記する (リストが古いままだと次の実験者が再び罠を踏む)

---

## 付録 A — FINDINGS.md 追記テンプレ

```markdown
## N. repeated_word 重み A/B (TANKA_W_REPEATED_WORD: 3 vs 12)

- 日付 / variant: <ts>-w-repeated-3 / <ts>-w-repeated-12 (eval_themes.json, n=50)
- 事前登録: 残存率差 ±0.16、w=3 換算平均 ±2.7、avg_attempts +50% 以内 (準備パッケージ §3)
- 有効性: 実測 weight before=[3] after=[12] / _themes_file 一致 / 前半後半の符号反転なし・あり
- 結果:
  | 指標 | w=3 | w=12 | 差 | 判定 |
  |---|---|---|---|---|
  | 最終成果物の repeated_word 残存率 | | | | |
  | avg_final_score (w=3 換算) | | | | |
  | avg_attempts | | | | |
  | first_attempt_pass_rate | | | | |
  | plateau_rate | | | | |
- 結論: 採用 / 見送り / 判定不能 (理由)
- 次に測るなら: (交絡・改善点)
```

## 付録 B — 分析スクリプトのスモークテスト (2026-06-11、過去実データ・読み取りのみ)

`analyze_final_repeats.py` を既存の sc-on / sc-off 結果 (n=16、両方 w=3 時代) に当てた出力で
ロジックを検証済み:

- 有効性ガード: 実測 weight before=[3] after=[3] を正しく検出 (w=3 同士なので通過)
- raw 平均 75.06 → 79.75、前半/後半 88.12/62.0 vs 83.25/76.25 — **FINDINGS.md §5.5 の記録値と
  完全一致** (集計ロジックの正しさの裏取り)
- 最終成果物の repeated_word 残存率: 2/16 (12.5%) / 4/16 (25%) — §3 の検出力見積もりの根拠
- final_score タイ 2 件を検出し注記 (採用 attempt の特定は先勝ち推定であることを明示)

## 付録 C — 本準備で確認した事実の出典

| 事実 | 出典 (2026-06-11 確認) |
|---|---|
| repeated_word 重み = env `TANKA_W_REPEATED_WORD`, 既定 3, minor | `app/backend/validator.py` L61 |
| ルールは 1 評価最大 1 件報告 | 同 L418–440 |
| PASS_THRESHOLD = 80 | 同 L45 |
| critique は重み降順 top-2 を強調 | 同 L603–622 |
| 失敗記憶の lesson は最大 weight 違反 | 同 L668–675 |
| best はタイ先勝ち (`score > best_score`) | `app/backend/tanka.py` L230–242 |
| violations は weight 込みで永続化 | `app/backend/tanka.py` L132 + 実セッション GET で確認 |
| compose の TANKA_* 列挙 5 種のみ (W 系なし) | `app/docker-compose.yml` L53–66 |
| 現コンテナ env に W 系なし = 既定 3 で稼働中 | `docker inspect app-backend-1` (読み取り) |
| backend health OK / model = llm-jp-4-8b-thinking | `GET /api/health` |
| `DELETE /api/failures` 存在 | `app/backend/main.py` L415–417 |
| ノイズ下限 (±19 / ±6.8 / ±4.8 / ±2.7) | `app/backend/eval/FINDINGS.md` §2 (SKILL.md にも引用) |
| quick8 が存在しない (新設が必要) | `ls app/backend/eval/` |
| テストは RULE_WEIGHTS を動的参照 | `app/backend/tests/test_validator.py` L361–373 |
