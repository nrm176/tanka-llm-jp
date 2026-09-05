# self-critique 存続判断 — paired A/B 実行手順書 (2026-09 版)

> 目的: self-critique フェーズ (+1 LLM call/生成) が最終品質に効いているかを、**paired (交互実行) 設計**で判定する。
> 6 月の blocked A/B (FINDINGS §5.5) は LM Studio 劣化で後半が沈み、効果の符号すら決まらなかった。
> 本手順は `eval-paired.sh` (ABBA 交互実行 + paired 統計 + 事後検証) を使う。
> 6 月版 runbook (skill workspace) は blocked 設計だったため **使わない**。

## 0. 事前登録 (実行前にここを埋めてから走らせる — 後から動かさない)

| 項目 | 値 |
|---|---|
| 問い | self-critique ON は OFF に対して avg_final_score を有意に変えるか (方向は未定) |
| 変える変数 | `self_critique` の per-request 上書きのみ (他の設定・モデル・お題は同一) |
| お題 | `eval_themes.json` (50 題、両 arm 同一・同順) |
| 主指標 | **paired difference** Δ_i = score_ON,i − score_OFF,i の平均 (n=50) |
| 判定基準 | \|mean Δ\| > 2SE(Δ) なら有意 (方向で採用/棄却)。以下なら**判定不能 = 正当な結論**。参考: 独立平均の差なら ±2.7 (FINDINGS §2) |
| 副指標 | 初回合格率 / 平均 attempt / **平均所要秒 (コスト側、#27 で永続化)** / wins-losses |
| 交絡対策 | ABBA 交互実行 (両 arm が同じ LM Studio 状態を経験) / 実行前 fresh reload / 前半・後半チェック |
| 所要時間 | 実測 ~3 分/生成 (ON) ・~2 分 (OFF) → 50 題 × 2 ≈ **4〜4.5 時間**。日中・`caffeinate` 必須 (§6.15) |

**failures (長期失敗記憶) はリセットしない**: paired 設計では両 arm が同じ記憶状態を経験するため
順序効果は対称に作用する。6 月版の「DELETE /api/failures」は 451 件の記憶を消す割に利益が無い。

## 1. Pre-flight (5 分)

```bash
cd /Users/nrm176p/GitHub2/LLM/app
docker compose ps                      # 4 サービス Up (mongo は 8.2.12、§6.18)
curl -s http://localhost:8001/api/health | python3 -m json.tool   # status ok / configured_model = llm-jp-4-8b-thinking
lms ps                                 # ← health.model_loaded は未ロードでも true になる。必ず lms ps で見る
```

`lms ps` に `llm-jp-4-8b-thinking` が **CONTEXT 32768** で無ければ fresh reload:

```bash
lms unload llm-jp-4-8b-thinking 2>/dev/null; lms load llm-jp-4-8b-thinking --context-length 32768
lms ps
```

直前に長時間の生成負荷を掛けていたら、上の reload を必ず行う (§6.13)。

## 2. 実行 (無人 4〜4.5 時間)

```bash
cd /Users/nrm176p/GitHub2/LLM/app/backend/eval
caffeinate -dims ./eval-paired.sh sc-on '{"self_critique": true}' sc-off '{"self_critique": false}' \
  eval_themes.json > results/paired-sc.log 2>&1 &
echo "started; log: results/paired-sc.log"
```

- 進捗: `tail -f results/paired-sc.log` / ブラウザの「評価モニタ」タブ (2 セッション `[eval] sc-on … (paired)` / `sc-off …`)
- **停止**: `kill $(cat results/eval-paired.pid)` (pkill は不一致で生き残る: §6.15)
- **再開**: ログ冒頭の `RESUME_A=… RESUME_B=…` 行をそのまま前に付けて再実行 (完了済みお題はスキップ)
- **禁則** (skill Step 3): 実行中に backend の設定・コードを変えない / LM Studio へ別の負荷 (手動チャット・診断 streaming) を掛けない / 他の eval を並行させない
- **hot-reload の罠**: backend は `uvicorn --reload` で `app/backend/` 配下の **`.py` 全て (tests/ 含む)** を監視している。
  実行中に `.py` を 1 つでも保存すると backend が再起動し、生成中のタスクは `cancelled` になる (dry-run で実証: テストファイル
  保存で 1 生成を失った)。並行セッションでの開発作業も同じ。走らせている間は backend ディレクトリに触らない

## 3. 結果の読み方 (10 分)

スクリプト末尾が主結果を出力し、`results/<ts>-paired-sc-on-vs-sc-off.json` に保存する:

```
=== Paired result: sc-on (A) vs sc-off (B), n=50 pairs ===
  mean diff (A-B) X.XX   sd Y.YY   2SE Z.ZZ
  wins            A a / B b / ties t
  mean duration   A …s  vs  B …s   (cost side)
  verification    {'arm': 'sc-on', 'expected_self_critique': True, 'checked': 50, 'mismatch': 0}
```

判定 (0 で事前登録した基準どおり):

1. **verification の mismatch が 0 であること**を先に確認 (上書きが効いていなかったら測定無効 — skill Step 2 の事故)
2. `|mean diff| > 2SE` → 有意。`mean diff > 0` なら ON 採用、`< 0` なら OFF 推奨 (ON は有害)
3. `|mean diff| <= 2SE` → **判定不能**。「n=50 の paired でもノイズ内 = 効果は小さい」自体が知見。
   コスト側 (mean duration の差、通常 +60〜120 s/生成) を踏まえて OFF 既定化を検討する
4. 前半/後半チェック (劣化の対称性): 各 arm の 1〜25 題と 26〜50 題の平均を比較

```bash
./compare.sh sc-on sc-off            # 従来指標 (合格率・attempt・違反頻度) の並列表示
python3 - <<'PY'
import json, glob, os
f = sorted(glob.glob('results/*-paired-sc-on-vs-sc-off.json'))[-1]
pairs = json.load(open(f))['pairs']; h = len(pairs)//2
for k in ('score_a', 'score_b'):
    xs = [p[k] for p in pairs if isinstance(p[k], (int, float))]
    print(k, '前半', round(sum(xs[:h])/h, 1), '後半', round(sum(xs[h:])/(len(xs)-h), 1))
PY
```

前半→後半で両 arm が**同程度**に沈むなら劣化は対称 (paired 差は有効)。片方だけ大きく沈むなら run を棄却して再測定。

## 4. 記録

- `FINDINGS.md` に §5.6 として結果を追記 (採用 / 棄却 / 判定不能 のいずれでも)。数値は
  `results/<ts>-paired-*.json` の summary をそのまま貼る
- 判定が「OFF 推奨」または「判定不能 + コスト大」なら `config.SELF_CRITIQUE_ENABLED` の既定変更を issue 化して PR

## 付録: dry-run

ハーネス自体の動作確認 (2 題、約 10 分)。品質判断には使わない:

```bash
./eval-paired.sh sc-on '{"self_critique": true}' sc-off '{"self_critique": false}' eval_themes_smoke2.json
```
