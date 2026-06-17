# self-critique アブレーション 夜間実験 ランブック

**問い: 自己点検 (self-critique) フェーズは本当に品質に効いているのか。切るとどのくらい差が出るのか。**

- 作成日: 2026-06-11
- 対象コミット: main @ c84fba6 (実行時の rev は runner が manifest に自動記録)
- 付属ツール (このファイルと同じディレクトリ):
  - `run_ab.sh` — paired-interleaved A/B ランナー (夜間無人実行)
  - `analyze_ab.py` — 朝の解析 + 整合性ゲート + 機械判定
  - `themes_ab16.json` — 16 テーマセット (前回実験と同一、`eval_themes.json` の四季×4 部分集合)

---

## 0. TL;DR

1. **寝る前 (5 分)**: §2 の事前チェック → §3 のブロックを 1 回コピペ → 寝る
2. ランナーが 16 テーマ × 2 ラウンド = **32 ペア (64 生成)** を、各ペア内で sc-ON / sc-OFF を**隣接実行**しながら回す (想定 1.5〜3 時間、上限 8 時間)。終了時は backend を本番既定 (sc ON) に自動復元
3. **朝 (2 分)**: §5 の解析コマンドをコピペ → §6 の判定基準 (事前登録) に当てはめて結論

---

## 1. 実験設計と根拠

### 1.1 何を測るか

`tanka.py` のパイプラインは Plan → Compose → **(self-critique)** → Validate → Refine*。
self-critique (`config.SELF_CRITIQUE_ENABLED`, env `TANKA_SELF_CRITIQUE`, 既定 ON) は
compose 直後に **+1 LLM call** を費やして初稿を自己修正させるフェーズ
(`prompts.SELF_CRITIQUE_USER`: 季語 1 回出現 / 他季語混入 / 拍数 / plan 維持 / 切れ字の 5 観点)。

- **品質側**: 最終採用スコア `final_score` (= 全 attempt の best、production がユーザーに見せる値)
- **コスト側**: 1 首あたりの LLM call 数 (`len(phases)`) と所要秒。sc は +1 call だが、
  初回合格率を上げて refine 回数を減らすなら**元が取れる**可能性がある

### 1.2 前回 (FINDINGS.md §5.5) はなぜ結論不能だったか

2026-06-04/06 の blocked A/B (16 テーマを ON で全部 → 別日に OFF で全部) は:

- 観測差 +4.69 点 (OFF 優位) は n=16 のノイズ下限 ±4.8 とほぼ同じで判別不能
- 前半/後半分割で **ON run の後半が LM Studio 持続負荷劣化でクレーター** (88.1→62.0) と判明。
  前半だけなら ON 優位 (88.1 vs 83.3) で**符号すら逆**
- 教訓: *blocked A/B は不安定ハードでは信頼できない → paired/interleaved 設計が必須*

今回の設計はこの教訓の実装そのもの。

### 1.3 今回の設計 (paired-interleaved)

| 設計要素 | 内容 | 何を統制するか |
|---|---|---|
| **隣接ペア実行** | 各 (ラウンド, テーマ) で ON と OFF を連続実行 | LM Studio の状態 (劣化度) を両アームでほぼ共有 → ペア差分から環境項が相殺 |
| **ペア内順序の交互反転** | 偶数ペア ON→OFF、奇数ペア OFF→ON | ペア内の先行/後行の系統差 (直前生成の影響・失敗記憶の先取り) を平均で相殺 |
| **n = 32 ペア** | 16 テーマ × 2 ラウンド | 検出力 (§1.4) |
| **アーム切替** | `TANKA_SELF_CRITIQUE=<0\|1> docker compose up -d backend` (タスク非実行区間でのみ) | env は `restart` では効かない。`up -d` による再作成が必須 |
| **切替の per-generation 検証** | 保存メッセージの `phases[].phase == "self_critique"` の有無 (G1 ゲート) | 「切替したつもり」事故の検出。env を信じず実挙動で確認 |
| **モデル固定** | セッション作成時に `model: llm-jp-4-8b-thinking` をピン (#20) + 全生成の `model` フィールド検証 (G2) | 夜間に UI でモデルが切替わっても汚染されない |
| **長期失敗記憶** | 開始時にバックアップ → クリア。以後は両アーム共有のまま進行 | 初期状態の対称化。進行中の蓄積は隣接ペア + 順序反転で対称 |
| **その他の TANKA_*** | flip 時に本番既定を明示固定 (DYNAMIC_FEWSHOT=1 / RAG=1 / HARD_CAP=50 / PLATEAU_WINDOW=3) | シェルに紛れた export の混入防止 |
| **生成間 20 秒休止** | `SLEEP_BETWEEN=20` | 持続負荷劣化の緩和 (FINDINGS §5「eval 中に probe しない」も遵守 — 診断呼び出しは一切しない) |
| **watchdog** | 1 生成 30 分で cancel + 全体 8 時間で新規ペア停止 | 夜間ハング対策。コード側 timeout (§6.11) の上にもう 1 枚 |
| **終了時復元** | 正常/異常問わず trap で `TANKA_SELF_CRITIQUE=1` に戻し、メトリクスを収集 | 朝起きたら本番状態 + 部分データでも解析可能 |

テーマ順は前回と同一の固定順 (春→夏→秋→冬ブロック)。両アームが同一順序を経験するので
ペア比較には影響しない。

### 1.4 検出力 (何点差まで見えるか)

- 同一テーマ・同一条件の score の標準偏差は実測 **σ ≈ 9.6** (FINDINGS §2, eval-repeat n=6)
- 独立 2 標本のペア差分なら sd_diff ≈ √2·σ ≈ **13.6**。隣接実行で環境ノイズが相関する分、実際はこれ以下になる見込み
- n=32 ペア → SE = 13.6/√32 ≈ 2.4 → **2SE ≈ 4.8 点 = 検出限界の見込み**
  (参考: 前回の「別日ペア」差分は sd=26.1 — 環境ドリフト混入の実例。今回これが ~13 台に落ちていること自体が設計の検証になる)
- つまり **±5 点級の効果なら判定でき、それ未満なら「効果は ±2SE 未満」と上限を確定できる**。
  実測の 2SE は `analyze_ab.py` が出力する (仮定値で判定しない)

時間見積: 1 生成 30 秒〜2 分 (README 実績) × 64 + 休止 21 分 + 切替 ~33 回×10 秒 ≈ **1.5〜3 時間**。
劣化が起きても DEADLINE=8h で必ず止まる。

---

## 2. 寝る前: 事前チェック (コピペ、約 5 分)

### 2.1 状態確認

```bash
# (1) docker スタックと backend の健康確認 — status:ok / model_loaded:true /
#     configured_model: llm-jp-4-8b-thinking であること
curl -s http://localhost:8001/api/health | python3 -c "
import sys, json; d = json.load(sys.stdin)
print('status           :', d.get('status'))
print('configured_model :', d.get('configured_model'))
print('model_loaded     :', d.get('model_loaded'))"

# (2) 実行中タスクが無いこと (0 であること)
curl -s http://localhost:8001/api/sessions | python3 -c "
import sys, json
print('active tasks:', sum(1 for s in json.load(sys.stdin) if s.get('active_task')))"
```

どれかが NG なら §7 のトラブルシュートへ。なお runner も同じチェックを起動時に再実行する
(寝た後に状態が変わっていたら fail-fast する)。

### 2.2 LM Studio を fresh reload (推奨)

FINDINGS §5 の推奨運用。**現行設定 (2026-06-11 実測: context 32768 / parallel 4) を明示して** 戻すこと
— 既定値ロードだと context が小さくなり挙動が変わる罠がある。

```bash
lms unload llm-jp-4-8b-thinking
lms load llm-jp-4-8b-thinking --context-length 32768 --parallel 4
lms ps   # CONTEXT 32768 / PARALLEL 4 で IDLE になっていることを確認
```

### 2.3 注意事項 (重要)

- **Mac を電源に接続したままにする** (ランナーは `caffeinate` でスリープを防ぐが、AC 前提)
- **実行中に UI からモデル切替・短歌生成・チャットをしない**。評価モニタ (http://localhost:5178) の閲覧は GET ポーリングのみなので OK
- 既定で**長期失敗記憶を開始時にクリアする** (バックアップは WORK に保存されるが、復元 API は無い —
  運用上は自然に再蓄積される)。クリアしたくなければ §3 で `CLEAR_FAILURES=0` を付ける
  (その場合、直前の使い方の偏りが初期状態に残る点は解釈時に注意)

---

## 3. 実行 (コピペ 1 ブロック)

```bash
TOOLS=/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/without_skill/outputs
export WORK=~/tanka-eval-night/sc-ab-$(date +%Y%m%d-%H%M%S)   # export 必須 (runner が読む)
mkdir -p "$WORK"

nohup bash "$TOOLS/run_ab.sh" > "$WORK/run.log" 2>&1 &
echo $! > "$WORK/run.pid"
caffeinate -is -w "$(cat "$WORK/run.pid")" &   # ランナー存命中だけスリープ抑止

echo "WORK=$WORK"; sleep 8; tail -5 "$WORK/run.log"
```

最後の `tail` で `themes: 16 件 × 2 ラウンド = 32 ペア` と最初のペアが流れ始めたのを見届けたら寝てよい。

> 環境変数で調整可能: `ROUNDS=3` (48 ペア・検出力増、+50% 時間) / `SLEEP_BETWEEN=40`
> (劣化が心配なら) / `CLEAR_FAILURES=0`。例: `nohup env ROUNDS=3 bash "$TOOLS/run_ab.sh" ...`

### 実行中に見たくなったら (任意)

```bash
tail -f "$WORK/run.log"                          # 進捗ログ
grep -c '"type": "cell"' "$WORK/manifest.jsonl"  # 完了セル数 (64 で完走)
# ブラウザ: http://localhost:5178 → 評価モニタ → [eval] sc-on-paired / sc-off-paired
```

### 途中で止めたくなったら

```bash
kill "$(cat "$WORK/run.pid")"   # trap が backend 復元 + 部分メトリクス収集まで実行する
# 念のため復元確認 (configured ok / TANKA_SELF_CRITIQUE=1):
docker inspect app-backend-1 --format '{{json .Config.Env}}' | grep -o 'TANKA_SELF_CRITIQUE=[01]'
# もし復元されていなければ手動で:
( cd /Users/nrm176p/GitHub2/LLM/app && TANKA_SELF_CRITIQUE=1 docker compose up -d backend )
```

---

## 4. ランナーが行うこと / 残すもの (参照用)

1. preflight: health / モデル一致 / 実行中タスク 0 / git rev・backend env・`lms ps` スナップショット
2. 長期失敗記憶バックアップ → クリア (既定)
3. セッション 2 本作成 (`[eval] sc-on-paired <ts>` / `[eval] sc-off-paired <ts>`、モデルをセッションにピン)
4. 32 ペアを隣接交互実行 (上記 §1.3)。セルごとに manifest.jsonl へ 1 行追記
5. 終了時 (異常時も trap で): `TANKA_SELF_CRITIQUE=1` へ復元 → 両セッションのメトリクスを
   `app/backend/eval/results/<ts>-sc-{on,off}-paired.json` に保存 (compare.sh 互換、results/ は gitignore 済み)

| 成果物 | 場所 |
|---|---|
| 実行ログ | `$WORK/run.log` |
| manifest (run_meta + 64 cell + run_end) | `$WORK/manifest.jsonl` |
| 健康/環境スナップショット (前後) | `$WORK/health_*.json`, `$WORK/backend_env_*.json`, `$WORK/lms_ps_before.txt` |
| 失敗記憶バックアップ | `$WORK/failures_backup.json` |
| メトリクス JSON ×2 | `app/backend/eval/results/` |

---

## 5. 朝: 解析 (コピペ)

```bash
TOOLS=/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/without_skill/outputs
# WORK を export していなければ: WORK=$(ls -dt ~/tanka-eval-night/sc-ab-* | head -1)

tail -3 "$WORK/run.log"          # status=completed (または deadline) を確認
python3 "$TOOLS/analyze_ab.py" --work "$WORK" --json "$WORK/summary.json"
```

参考 (従来ツールでの突き合わせ。集計単位が違うので数字は §6 では使わない):

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval && ./compare.sh sc-on-paired sc-off-paired
```

---

## 6. 判定基準 (事前登録 — 結果を見てから変えないこと)

**主要評価指標は 1 つだけ**: ペア差分 `D = mean(final_score_ON − final_score_OFF)`。
有意性しきい値は **実測 2SE** (`analyze_ab.py` が出力)。設計上の見込みは ±4.8 点 (§1.4)。

### 6.1 整合性ゲート (1 つでも FAIL なら「判定不能」— 効果の議論をしない)

| ゲート | 条件 | FAIL の意味 |
|---|---|---|
| **G1 用量** | OFF アームの全生成に `self_critique` phase が **0 件** / ON アームの sc 実行率 **≥90%** | env 切替が効いていない (実験自体が無効) / sc 呼び出しの失敗多発 (underdose) |
| **G2 モデル** | 全 64 生成の `model == llm-jp-4-8b-thinking` | 夜間にモデルが切替わった (汚染) |
| **G3 充足率** | 成立ペア ≥ 28/32 (87.5%) | timeout/失敗が多すぎて検出力不足 (LM Studio 劣化が支配的だった夜) |
| **G4 環境** | `configured_model` が実験前後で不変 | 警告扱い (G1/G2 が通っていれば致命ではない) |

### 6.2 判定表

| # | 結果 | 結論 | アクション |
|---|---|---|---|
| 1 | **D > +2SE** | self-critique は品質に効いている | ON 維持。FINDINGS §5.5 を「存続確定 (+D 点)」で更新。コスト (calls 差) も記録 |
| 2 | **D < −2SE** | self-critique はむしろ品質を下げている | 既定 OFF 化 (§6.3 の diff を issue → PR)。フラグは残す |
| 3 | **\|D\| ≤ 2SE** かつ calls_ON ≤ calls_OFF | 品質差は検出限界未満・コスト中立以下 (sc が refine を減らして +1 call を回収) | 現状維持 (ON)。「効果があっても ±2SE 点未満」と FINDINGS に上限を記録 |
| 4 | **\|D\| ≤ 2SE** かつ calls_ON > calls_OFF | 品質便益が測れないのにコスト増 | 既定 OFF 化を推奨 (§6.3)。「±2SE 未満の効果に +Δcalls/首 を払わない」 |
| 5 | ドリフトガード発火 (前半/後半で D の符号逆転・両半とも \|D_half\| > 2SE_half) | 環境交絡の疑い | 判定保留。fresh reload + `SLEEP_BETWEEN=40` で後日再実行 |
| 6 | ゲート FAIL | 測定不成立 | §7 の該当行の対処をして再実行 (結果数値は引用しない) |

補助指標 (結論を覆さない・解釈の肉付けのみ):

- **初回合格率の McNemar** (ON のみ合格 vs OFF のみ合格): sc の機械的効果の確認。
  前回データでは 37.5% vs 6.2% (ON のみ合格 5/discordant 5) と強く ON 寄りだった。
  「初回合格は上がるが最終スコアは変わらない」なら『sc は早く着くだけで高くは登らない』が結論になる
- 符号検定 p、平均 attempt 数、平均所要秒/首、score=0 件数

### 6.3 判定 #2 / #4 になった場合に適用する diff (準備済み)

```diff
--- a/app/docker-compose.yml
+++ b/app/docker-compose.yml
@@ -60,7 +60,7 @@
       # eval/ablation 用の TANKA_* 上書き口 (未指定なら各モジュールの既定値が効く)。
       # 例: TANKA_DYNAMIC_FEWSHOT=0 docker compose up -d backend && ./eval.sh baseline
       TANKA_DYNAMIC_FEWSHOT: ${TANKA_DYNAMIC_FEWSHOT:-1}
-      TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-1}
+      TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-0}
       TANKA_RAG: ${TANKA_RAG:-1}
```

```diff
--- a/app/backend/config.py
+++ b/app/backend/config.py
@@ -45,8 +45,10 @@
 # ── 自己点検フェーズ (Phase 1 B4) ──
-SELF_CRITIQUE_ENABLED = env_bool("TANKA_SELF_CRITIQUE", True)
+# 2026-06 paired A/B (n=32 ペア) で品質効果がノイズ下限未満かつコスト増と判定し既定 OFF 化。
+# 根拠データ: eval/results/<ts>-sc-{on,off}-paired.json / FINDINGS.md §5.5
+SELF_CRITIQUE_ENABLED = env_bool("TANKA_SELF_CRITIQUE", False)
```

合わせて `FINDINGS.md §5.5` の「存続判断は保留」を実測値で確定させ、CLAUDE.md には変更不要
(§7 の決定表に新規行を足すなら「self-critique 既定 OFF — paired A/B で効果 < ノイズ下限」)。
**どの判定でも FINDINGS §5.5 の追記は行う** (保留のまま放置しない)。

---

## 7. トラブルシュート

| 症状 | 原因/対処 |
|---|---|
| preflight で `configured_model が ... ではありません` | UI でモデルを切替えたまま。UI で戻すか `curl -X POST localhost:8001/api/model -H 'Content-Type: application/json' -d '{"model":"llm-jp-4-8b-thinking"}'` |
| preflight で `実行中タスクのあるセッションが N 件` | 進行中の生成がある。完了を待つか UI から cancel |
| 朝見たら `status=deadline` / timeout セル多数 | LM Studio 劣化が深刻だった夜。成立ペアが G3 を満たせば解析は有効。満たさなければ fresh reload + `SLEEP_BETWEEN=40` で再実行 |
| 朝見たら `FATAL: backend が ... healthy になりません` | 夜間に LM Studio が落ちた。trap が復元まで実行済みのはず。`lms ps` → §2.2 → 再実行 |
| G1 FAIL (OFF に sc 混入) | flip が効いていない = compose の env passthrough か docker の問題。`$WORK/compose.log` を確認。結果は破棄 |
| analyze が `セッション取得に失敗` | backend 停止中。`./app/start.sh` 等で起動してから再実行 (解析は GET のみ) |
| 復元確認 | `docker inspect app-backend-1 --format '{{json .Config.Env}}' \| grep -o 'TANKA_SELF_CRITIQUE=[01]'` が `=1` |

ランナー異常終了時も trap で復元 + 部分データ収集まで走る設計 (検証済み)。部分データでも
`analyze_ab.py --work` は成立ペアだけで解析し、G3 で検出力不足を判定する。

---

## 8. リポジトリへの変更

**今回の実験のためのリポジトリ変更は不要** (意図的にゼロ変更設計)。ツールは全てこのディレクトリに
外置きし、結果 JSON の保存先 `app/backend/eval/results/` は gitignore 済み。
実験後に採用が決まった場合のみ §6.3 の diff を適用する。
ツールを恒久化したくなったら `run_ab.sh` / `analyze_ab.py` / `themes_ab16.json` を
`app/backend/eval/` にコピーして README に 1 節足すだけでよい (どちらも repo 内パスに依存しない)。

---

## Appendix A: Plan B — 既存 eval.sh だけで回す代替 (ランナーが動かない場合)

paired 設計は諦め、ABAB の 4 ブロックで劣化を部分的に均す (前回の AB よりはまし、Plan A よりは弱い):

```bash
TOOLS=/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/without_skill/outputs
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
for blk in on-b1 off-b1 on-b2 off-b2; do
  v=$([ "${blk%%-*}" = on ] && echo 1 || echo 0)
  ( cd ../.. && TANKA_SELF_CRITIQUE=$v docker compose up -d backend ) && sleep 15
  ./eval.sh "sc-$blk" "$TOOLS/themes_ab16.json"
  sleep 60
done
( cd ../.. && TANKA_SELF_CRITIQUE=1 docker compose up -d backend )   # 復元
```

判定: ブロック平均 (on-b1+on-b2)/2 vs (off-b1+off-b2)/2 を比較し、ブロック間ばらつきを
ノイズ下限として使う。**ブロック内で前半/後半の傾きが大きい場合は判定しない** (前回の轍)。

## Appendix B: 参照データ (前回実験の再解析)

`analyze_ab.py --on 6a2182aa59e4931ddf9b3485 --off 6a23461fbe2019232939c04a` で再現可能
(本ランブック作成時に実行検証済み):

- 別日ペア差分: D = −4.69, sd = 26.1, 2SE = 13.1 → 有意差なし。符号検定 +9/−7 (p=0.80)
- 前半/後半の D: **+4.88 / −14.25** — 後半クレーター (LM Studio 劣化) の definitive な再確認
- 初回合格 McNemar: ON のみ合格 5 / OFF のみ合格 0 (p=0.062) — sc の機械的効果は示唆あり
- ゲート判定: G1/G2 FAIL (当時のデータは phases / model 未記録) → 「判定不能」 — FINDINGS §5.5 の結論と一致
- sd 26.1 → 今回の隣接ペアで ~13 台に落ちることが設計どおりの目印 (analyze の sd 出力で確認)
