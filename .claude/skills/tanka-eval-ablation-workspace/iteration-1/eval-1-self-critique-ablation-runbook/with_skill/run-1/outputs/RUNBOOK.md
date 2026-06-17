# self-critique アブレーション実験 — 夜間実行ランブック

- **問い**: 自己点検 (self-critique) フェーズは本当に品質に効いているのか。切ると何点差が出るのか
- **作成日**: 2026-06-11 (tanka-eval-ablation スキルの手順に従って設計)
- **実行者**: ユーザー (夜間)。本書のコマンドはすべてコピペで流せる
- **所要**: 健全時 約 3〜5 時間 / 劣化時もタイムボックス (既定 7h) で必ず止まる。中断・再開可能
- **リポジトリへの変更**: 新規ファイル 1 件の追加のみ (§3)。既存ファイルの変更ゼロ

---

## 0. TL;DR — 夜に流すもの

```bash
# (1) 設置 (初回のみ)
cp '/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/with_skill/outputs/eval_paired_sc.py' \
   '/Users/nrm176p/GitHub2/LLM/app/backend/eval/eval_paired_sc.py'

# (2) 事前チェック (§4 を全部) — 特に LM Studio の fresh reload と失敗記憶リセットの判断

# (3) 実行
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
nohup python3 eval_paired_sc.py run > results/eval-paired-sc.log 2>&1 &
echo "started pid=$!"

# (4) 朝: 解析 (判定文まで自動で出る)
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
python3 eval_paired_sc.py analyze | tee results/eval-paired-sc-report.txt
```

---

## 1. 背景 — なぜこの設計か

### 1.1 測りたいもの

self-critique は `tanka.py` の compose 直後に入る **+1 LLM call** で、
「自分の初稿を点検し、問題があれば修正版を出せ」と促す
(`prompts.SELF_CRITIQUE_USER`、出力が非空なら composition を置き換える)。
`TANKA_SELF_CRITIQUE` env (既定 1) で ON/OFF できる。コード変更は不要。

### 1.2 前回実験の教訓 (FINDINGS.md §5.5) — blocked A/B は使えない

前回 (16 お題、ON を全部 → OFF を全部の blocked 設計) は **結論不能** に終わった:

- 観測差 +4.69 (OFF 優位) は n=16 のノイズ下限 ±4.8 未満
- ON run の後半が LM Studio の sustained-load 劣化でクレーター化 (前半 88.1 → 後半 62.0)。
  **前半だけ見ると符号が逆** (ON 優位) で、効果の符号すら不明
- 結論: 「blocked A/B は不安定ハードでは信頼できない。次は paired/interleaved 設計が必須」

### 1.3 今回の設計 — paired interleave (お題ごと交互実行)

```
pair 1: [ON  生成] → [OFF 生成]   ←  散りゆく桜
pair 2: [OFF 生成] → [ON  生成]   ←  春の別れ      (ペア内順序も交互 = ABBA)
pair 3: [ON  生成] → [OFF 生成]   ←  通学路の桜並木
...50 ペア (eval_themes.json の全 50 お題、計 100 生成)

切替のたびに: TANKA_SELF_CRITIQUE=X docker compose up -d --no-deps backend
              → printenv で適用を検証 → /api/health で復帰を確認
ON 生成は session [eval] sc-paired-on に、OFF は [eval] sc-paired-off に蓄積
```

- **LM Studio の劣化は両群に同じだけ当たる** (時間的に隣接して走るため)。劣化しても差分には乗らない
- お題は **同一ファイル** (`eval_themes.json`、50 題)。subset を作らないので `/tmp` 消失の罠もない
- eval.sh は 1 invocation = 1 variant でお題ごとの env 切替ができないため、
  専用ドライバ `eval_paired_sc.py` (run / status / analyze) を使う。
  **analyzer は過去の sc-on / sc-off セッション (Mongo に現存) に対してテスト済みで、
  FINDINGS.md §5.5 の数値 (-4.69、判定不能、後半クレーター) を完全再現することを確認した** (§10)

---

## 2. 事前登録 — 判定基準 (実行後に動かさないこと)

> ここが実験の本体。走らせてから基準を動かすと「有意に見えるまで探す」p-hacking になる。
> analyzer はこの基準をそのまま機械適用して判定文を出す。

| 項目 | 内容 |
|---|---|
| 問い | self-critique フェーズは最終 score を上げるか。差は何点か |
| 変更の種類 | env 切替のみ (`TANKA_SELF_CRITIQUE` 1↔0)。**他の変数はすべて既定に固定** (DYNAMIC_FEWSHOT=1, RAG=1, HARD_CAP=50, PLATEAU_WINDOW=3, モデル=llm-jp-4-8b-thinking)。ドライバはホスト shell の他の TANKA_* を意図的に遮断する |
| お題セット | `app/backend/eval/eval_themes.json` 全 50 題。両群とも同一ファイル |
| 主要指標 | **完了ペアの final_score 差 (ON−OFF) の平均** |
| ノイズ下限 | **±2×9.6/√n** (FINDINGS.md §2 の規約)。n=50 で **±2.7**。部分完了時は実 n で計算 |
| 交絡対策 | paired interleave + ABBA 順 + 開始前 fresh reload + arm 整合性の事後検証 (phases) + 失敗記憶は開始時リセットで両群同条件 (§4.4) |
| 失敗の扱い | 失敗した生成は**再試行しない**。両腕が揃わないペアは解析から除外し、除外数を報告 |

**判定マトリクス** (上から順に適用):

| # | 条件 | 判定 |
|---|---|---|
| 1 | arm 整合性違反 (ON 群に self_critique phase が無い等) > 0 | **run 無効** (env 適用ミス)。数値を使わず再実行 |
| 2 | 完了ペア < 80% (40 ペア未満) | 結果は**参考値**。再測定を推奨 (判定自体は出す) |
| 3 | 前半/後半の平均差が逆符号で**双方**ノイズ下限超え | **時間交絡 → 判定不能**。再測定 |
| 4 | \|平均差\| > ノイズ下限 | **有意**。＋なら ON 維持 (採用) / −なら OFF 既定化を提案 |
| 5 | \|平均差\| ≤ ノイズ下限 | **「効果はノイズ以下 (±2.7 点未満)」**。これも正当な結論として記録し、採否はコスト (生成時間・+1 call) と副次指標を添えてユーザー判断 |

**副次指標** (方向の参考のみ。単独で採否を決めない):
初回合格率 (前回 ON 37.5% vs OFF 6.2% と大差がついた指標。self-critique は検証前に初稿を直すので、効くならここに最も出るはず)、総合合格率、平均 attempt、plateau 率、平均生成時間 (= self-critique のコスト実測)。

---

## 3. リポジトリへの変更 (1 件のみ、追加)

既存ファイルの変更は**不要**。根拠:

- `TANKA_SELF_CRITIQUE` は `app/docker-compose.yml` の backend.environment に
  passthrough 済み (`TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-1}`) — compose 修正不要
- 切替は env のみで、`config.SELF_CRITIQUE_ENABLED` が読む — コード修正不要

追加する新規ファイル (内容は outputs に同梱の `eval_paired_sc.py` がそのまま diff に相当):

```bash
cp '/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/with_skill/outputs/eval_paired_sc.py' \
   '/Users/nrm176p/GitHub2/LLM/app/backend/eval/eval_paired_sc.py'
```

- `git status` に untracked として現れる。実験採用時の PR に含めるか、不要なら消してよい
- state / log / レポートはすべて `results/` 配下 (`.gitignore` 済み) に書かれ、git を汚さない

---

## 4. 実行前チェックリスト (寝る前に上から順に)

### 4.0 working tree の状態確認 (並行セッション対策)

backend はソースを volume mount しているため、**ドライバのコンテナ再作成は
「その時点の working tree のコード」をロードする**。並行セッションの未コミット変更が
backend 配下に残っていると、それ込みで測ることになる。

```bash
cd /Users/nrm176p/GitHub2/LLM && git status --short -- app/backend/
```

未コミット変更があれば: その状態で測ると意図的に決めるか、`git stash` してから実行する。
どちらにせよ **run 中に backend 配下を編集しない** (途中で測定対象が変わる)。
※本ランブック作成時点 (2026-06-11) の未コミット変更は self-critique 経路に触れていないことを
diff で確認済みだが、夜の時点で再確認すること。

### 4.1 スタックとモデルの確認

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

**期待値**: `status: "ok"` / `lm_studio_ok: true` / `model_loaded: true` /
`configured_model: "llm-jp-4-8b-thinking"`。

- `configured_model` が違う → UI からの切替が残っている。UI (http://localhost:5178) で
  `llm-jp-4-8b-thinking` に戻す (mongo に永続化されるので戻せば以後も維持)
- スタックが落ちている → `./app/start.sh`、ホストで `lms server start`

### 4.2 LM Studio の fresh reload + ウォームアップ

連続負荷で劣化した状態から始めない (SKILL.md Step 0 / CLAUDE.md §6.13)。

1. LM Studio で `llm-jp-4-8b-thinking` を一度 eject → **普段と同じ設定 (context length 等を変えない)** でロードし直す
2. ウォームアップ 1 発 (LM Studio 直叩きなので DB/失敗記憶を汚さない):

```bash
time curl -s http://localhost:1234/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"llm-jp-4-8b-thinking","messages":[{"role":"user","content":"準備運動です。「はい」とだけ答えてください。"}],"max_tokens":200}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'][:100])"
```

**期待値**: 30 秒以内に返る。1 分超なら劣化が残っている → LM Studio を再起動してやり直す。

### 4.3 ほかに何も走っていないこと

```bash
curl -s http://localhost:8001/api/sessions | python3 -c "
import sys, json
r = [s for s in json.load(sys.stdin) if s.get('active_task')]
print('実行中タスク:', len(r), [s['id'] for s in r])"
```

**期待値**: `実行中タスク: 0 []`。残っていれば終了を待つ (ドライバの preflight でも拒否される)。

### 4.4 失敗記憶のリセット (推奨 — ただし要判断)

長期失敗記憶 (`failures`、現在 349 件) は生成プロンプトに教訓として注入される。
リセットすると **両群がクリーンな記憶から同条件で開始**でき、再現性が上がる。
リセットしなくても interleave により両群対称なので実験は成立する (初期条件が
「既存の蓄積あり」になるだけ)。**消した記憶は戻せない前提** (バックアップは監査用。
走らせればまた蓄積される) で、嫌ならこの節をスキップしてよい。

```bash
# バックアップ (results/ は gitignore 済み)
TS=$(date +%Y%m%d-%H%M%S)
curl -s "http://localhost:8001/api/failures?limit=500" \
  > "/Users/nrm176p/GitHub2/LLM/app/backend/eval/results/failures-backup-$TS.json"
python3 -c "import json; d=json.load(open('/Users/nrm176p/GitHub2/LLM/app/backend/eval/results/failures-backup-$TS.json')); print('backup:', d['count'], '件中', len(d['items']), '件保存')"

# リセット
curl -s -X DELETE http://localhost:8001/api/failures
```

注: API は最新 500 件までしか返さない。実行時に count が 500 超なら全量バックアップは
`docker compose exec -T mongo mongosh tanka_chat --quiet --eval 'print(EJSON.stringify(db.failures.find().toArray()))' > backup-full.json` で。

### 4.5 ドライバ設置と疎通

```bash
cp '/Users/nrm176p/GitHub2/LLM/.claude/skills/tanka-eval-ablation-workspace/iteration-1/eval-1-self-critique-ablation-runbook/with_skill/outputs/eval_paired_sc.py' \
   '/Users/nrm176p/GitHub2/LLM/app/backend/eval/eval_paired_sc.py'
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
python3 -m py_compile eval_paired_sc.py && echo OK
python3 eval_paired_sc.py status   # 「state がありません」と出れば正常 (未開始)
env | grep -E '^TANKA_|^LM_STUDIO_MODEL' || echo "shell に TANKA_* なし: OK"
```

最後の行で何か表示された場合も、ドライバが compose へ渡す環境から TANKA_* /
LM_STUDIO_MODEL を遮断するので実験は守られる (が、`unset` しておくのが行儀が良い)。

---

## 5. 実行

### 5.1 起動

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
nohup python3 eval_paired_sc.py run > results/eval-paired-sc.log 2>&1 &
echo "started pid=$!  (PID ファイル: results/eval-paired-sc.pid)"
sleep 20 && tail -5 results/eval-paired-sc.log   # preflight 通過と 1 件目の開始を確認して寝る
```

タイムボックスや watchdog を変えたいときだけ (通常は既定でよい):

```bash
EVAL_STOP_AFTER_MIN=480 EVAL_TASK_TIMEOUT_MIN=30 nohup python3 eval_paired_sc.py run > results/eval-paired-sc.log 2>&1 &
```

### 5.2 ドライバが自動でやること (把握用)

- preflight (health / モデル / 走行中タスクなし / docker 疎通)。失敗時は何も変えずに終了
- 開始時にまず backend をサニタイズ済み env で再作成 (走っていたコンテナの env 混入を排除)。
  以後もホスト shell の他の `TANKA_*` / `LM_STUDIO_MODEL` は compose へ渡さない (1 実験 1 変数の保証)
- お題ごとに `TANKA_SELF_CRITIQUE=1|0 docker compose up -d --no-deps backend` で切替え、
  **`printenv` で適用を検証してから**生成 (検証失敗 = 即中断。「同じものを 2 回測る」事故を構造的に排除)
- 1 生成 45 分の watchdog (前回実験では劣化で 1 生成平均 80 分という実測があるため) → 超過は cancel して failed 扱い、**再試行しない**
- 4 連続失敗で 10 分クールダウン (LM Studio は idle 後に回復する実測あり)、12 連続で安全中断
- タイムボックス (既定 420 分) 超過後は新規ペアを始めない (進行中ペアは完遂し、ペアを壊さない)
- 進捗は `results/eval-paired-sc-state.json` に逐次保存 → **いつ死んでも resume 可能**
- 終了時 (中断時も) に backend を必ず既定 `TANKA_SELF_CRITIQUE=1` に復元

### 5.3 進捗確認 (任意)

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
python3 eval_paired_sc.py status        # ペアごとの o/x/・ 一覧
tail -3 results/eval-paired-sc.log
```

ブラウザなら http://localhost:5178 の「評価モニタ」タブに
`[eval] sc-paired-on` / `[eval] sc-paired-off` の 2 run が出る。

### 5.4 実行中の禁則 (SKILL.md Step 3)

- backend の設定・コード・モデルを変えない (測定対象が途中で変わる)
- LM Studio に手動チャットや診断 streaming を投げない (負荷上乗せで劣化悪化の実績)
- 別の eval を並行で走らせない (ドライバ自身も PID lock で二重起動を拒否する)

### 5.5 止めたいとき

```bash
kill $(cat /Users/nrm176p/GitHub2/LLM/app/backend/eval/results/eval-paired-sc.pid)
```

**pkill は使わない** (パターン外れで生き残る既知の罠)。kill 後も走行中の backend タスクは
完走して保存され、次回 `run` が resume で回収する。

---

## 6. 朝の手順

### 6.1 終了確認と既定復帰の検証

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
tail -15 results/eval-paired-sc.log     # 「run 終了: done=… 」と「既定 (TANKA_SELF_CRITIQUE=1) に復元」を確認
python3 eval_paired_sc.py status | head -5

# backend が既定に戻っているか (必ず確認)
docker inspect app-backend-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep TANKA_SELF_CRITIQUE
# → TANKA_SELF_CRITIQUE=1 であること。0 のままなら:
#    cd /Users/nrm176p/GitHub2/LLM/app && TANKA_SELF_CRITIQUE=1 docker compose up -d --no-deps backend
```

まだ走っていたら: そのまま完走を待つか、§5.5 で止めて完了分だけ解析してよい
(paired 設計は部分データでも成立する。判定マトリクス #2 が n を見て扱いを決める)。

### 6.2 解析 (判定文まで自動)

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
python3 eval_paired_sc.py analyze | tee results/eval-paired-sc-report.txt
```

analyze は何度でも実行可 (GET のみ)。eval.sh 互換の結果 JSON
(`results/<ts>-sc-paired-on.json` / `-off.json`) も保存するので、いつもの比較ツールも使える:

```bash
./compare.sh sc-paired-on sc-paired-off
```

注意: compare.sh 側の集計は「全生成」(未成ペアの片割れも含む) なので、ペアが欠けた場合は
n 不一致の警告が出うる。**判定は analyze のペア解析が正**。compare.sh は違反頻度の
内訳などを眺める補助ビューとして使う。

### 6.3 レポートの読み方

セクション構成と見るポイント:

- **[1] arm 整合性** — `ok (違反 0 件)` であること。違反があれば判定マトリクス #1 (run 無効)。
  これは「ON 群の全タスクの生成過程 (phases) に self_critique フェーズがあり、OFF 群には無い」
  ことの機械検証で、env 切替が全 100 生成で正しく効いた証明になる
- **[2] ペア完成度** — 80% 以上か。`llm_error` の件数は LM Studio 劣化の体温計
- **[3] 主要指標** — `平均差 (ON−OFF): ±X [ノイズ下限 ±Y]`。この 1 行が結論
- **[4] 前半/後半** — 符号一致なら時間交絡なし。「注意」表示が出たら per-arm の
  半区間平均と llm_error を見て、劣化が走っていなかったか確認
- **[5] 副次指標** — 初回合格率の差と平均生成時間の差 (= self-critique の実コスト) をメモ
- **[判定]** — 事前登録基準の機械適用結果。この文をそのまま FINDINGS に書ける

参考: 旧 blocked 実験データ (§5.5 の 2 セッション) に対して本 analyzer を流した実出力
(抜粋) — 読み方のサンプルであり、本 analyzer が過去の結論を正しく再現する証明でもある:

```
[3] 主要指標: final_score (完了ペアのみ, n=16)
    ON 平均  :  75.06
    OFF 平均 :  79.75
    平均差 (ON−OFF): -4.69   [ノイズ下限 ±4.80]
[4] 前半/後半 (実行順) の交絡チェック
    前半 n=8: ON 88.12 / OFF 83.25 / 差 4.88 (下限 ±6.79)
    後半 n=8: ON 62.00 / OFF 76.25 / 差 -14.25 (下限 ±6.79)
    → 注意: 符号不一致 (片側のみ有意)。FINDINGS §5.5 型の時間不安定の弱いシグナル。…
[判定] 判定不能 — 差 -4.69 はノイズ下限 ±4.80 以内。…
```

---

## 7. 判定 → 次のアクション

| 判定結果 | アクション |
|---|---|
| **ON 優位 (差 > +2.7)** | self-critique 維持。FINDINGS に §5.6 として追記 (§8)。終わり |
| **OFF 優位 (差 < −2.7)** | OFF 既定化を提案: issue → ブランチ → PR。変更は `config.py` の `SELF_CRITIQUE_ENABLED` 既定を False に + `docker-compose.yml` の既定 `:-1` を `:-0` に + README/SKILL の既定値表記を更新。PR 本文に before/after 数値と本レポートを貼る |
| **判定不能 (\|差\| ≤ 2.7)** | **「self-critique の score への効果は ±2.7 点未満」を確定知見として FINDINGS に記録**。採否は score 以外で判断する材料を併記: ①平均生成時間の差 (コスト)、②初回合格率の差 (前回は ON +31pp。1 発目の品質が要る用途では ON の価値)。コストが大きく副次にも利点が無ければ OFF 化の提案を検討、利点があれば「score 同等・初回品質で ON 維持」と記録 |
| **run 無効 / 時間交絡** | 数値を結論に使わない。§9 を見て原因 (env 適用 / 劣化) を潰し、**別の晩に再実行** (state を退避して新規 run) |

部分完了 (40 ペア未満) の場合: 判定は出るが参考値。ノイズ下限は実 n で広がる
(例 n=25 → ±3.8)。差が下限を大きく超えていれば方向の確度は高い。際どければ再測定。

---

## 8. 記録 — FINDINGS.md への追記テンプレ

`app/backend/eval/FINDINGS.md` の §5.5 の後ろに追記 (空欄をレポートから埋める):

```markdown
## 5.6 self-critique paired A/B (再測定) — <結論を一行で>

§5.5 の教訓に従い paired/interleaved 設計で再測定した。
50 お題 (eval_themes.json) × {ON, OFF} をお題ごと交互実行 (ABBA、計 100 生成)。
ドライバ: eval/eval_paired_sc.py (env 切替+printenv 検証、watchdog 45min、timebox 7h)。
失敗記憶は開始時リセット (バックアップ: results/failures-backup-<ts>.json)。

| 指標 | sc-ON | sc-OFF | 差 (ON−OFF) |
|---|---|---|---|
| 平均スコア (完了ペア n=__) | __ | __ | __ (ノイズ下限 ±__) |
| 初回合格率 | __% | __% | |
| 平均 attempt | __ | __ | |
| 平均生成時間 | __s | __s | ← self-critique のコスト |

- arm 整合性 (phases 検証): 違反 0 / ペア完成度: __% / llm_error: on __ off __
- 前半/後半の差: __ / __ (符号__致)
- **判定**: <analyze の [判定] をそのまま>
- 結果ファイル: results/<ts>-sc-paired-{on,off}.json、レポート: results/eval-paired-sc-report.txt
```

採否の変更 (OFF 化) をする場合は issue → feature ブランチ → PR → squash の通常フローで。
PR 本文に上記の表と判定根拠を必ず書く。

---

## 9. トラブルシュート

| 症状 | 対処 |
|---|---|
| ドライバが途中で死んだ / Mac がスリープした | そのまま再実行 (`nohup python3 eval_paired_sc.py run …`)。state から未完了分だけ resume する。走り残しのタスクも回収される。※スリープ防止に `caffeinate -i` を nohup の前に付けてもよい: `nohup caffeinate -i python3 eval_paired_sc.py run …` |
| 朝になってもまだ走っている | 待てるなら待つ。待てないなら §5.5 で kill → 完了分で analyze (部分データでも判定は出る) |
| backend が OFF (=0) のまま | `cd /Users/nrm176p/GitHub2/LLM/app && TANKA_SELF_CRITIQUE=1 docker compose up -d --no-deps backend` |
| log に「12 連続失敗 — 系統的障害」 | LM Studio が死んでいる可能性。`lms server status` / LM Studio 再起動 → 再実行で resume |
| failed が大量 (ペア完成度 < 80%) | LM Studio 劣化が深い。判定は参考値扱い (#2)。再測定するときは: `mv results/eval-paired-sc-state.json results/eval-paired-sc-state.failed-<ts>.json` で退避してから新規 run (resume ではなく最初から) |
| `既に実行中です (pid=…)` | 前回の run が生きている。止めるなら表示された pid に kill |
| 失敗記憶を戻したい | 原則戻さない (走らせれば再蓄積する)。どうしてもならバックアップ JSON の items を mongoimport で戻すことになるが、型 (ts) が崩れるため非推奨 |
| analyze が「state がありません」 | run を一度も開始していない。state パスを override したなら `EVAL_STATE=… python3 eval_paired_sc.py analyze` |

---

## 10. 本ランブック作成時に検証済みの事実 (2026-06-11 時点)

実走なし (GET と読み取りのみ) で確認した内容:

1. **現状の backend**: 稼働中 (`status: ok`)、`configured_model=llm-jp-4-8b-thinking`、
   コンテナ env は `TANKA_SELF_CRITIQUE=1` ほか全て既定 (docker inspect で確認)
2. **compose passthrough**: `TANKA_SELF_CRITIQUE: ${TANKA_SELF_CRITIQUE:-1}` が
   `app/docker-compose.yml` に列挙済み → compose 修正不要
3. **phases の永続化**: 直近の実セッションで tanka メッセージに
   `phases: [plan, compose, self_critique, refine]` が保存されていることを確認
   → arm 整合性の事後検証 (レポート [1]) が機能する。
   ※2026-06-04/06 以前の旧データには phases/model が無い (analyzer は unknown 扱いで耐える)
4. **analyzer のスモークテスト**: 旧 sc-on (`6a2182…`) / sc-off (`6a2346…`) セッションに対して
   実行し、FINDINGS.md §5.5 の記録を完全再現:
   ON 75.06 / OFF 79.75 / 差 -4.69 vs 下限 ±4.80 → 判定不能、
   前半 88.12/83.25・後半 62.00/76.25 (後半クレーター)、初回合格率 37.5% vs 6.2%
5. **旧実験の生成時間実測**: 旧 ON run は 1 生成平均 約 80 分まで劣化していた
   (メッセージタイムスタンプから算出) → watchdog 45 分の根拠
6. **失敗記憶**: 現在 349 件蓄積 (§4.4 のリセット判断の前提)
7. **eval_themes.json**: 50 題、theme 文字列はすべて一意 (ペア照合キーとして安全)
8. **評価モニタ**: `[eval] <variant>` タイトルの正規表現マッチで表示される
   → `[eval] sc-paired-on/off` は自動で出る

---

## 付録: 今回測らないもの (scope out)

- self-critique の **プロンプト内容の良し悪し** (ON/OFF の存在効果のみを測る。1 実験 1 変数)
- 他モデル (gemma 等) での効果 — 必要なら同じドライバの定数 `EXPECTED_MODEL` を変えて別実験に
- best-of-N の N や plateau 窓の調整 — FINDINGS §6 の別実験候補
