---
name: tanka-eval-ablation
description: >-
  短歌生成パイプラインに影響する変更 (prompts.py / validator.py / tanka.py / config.py /
  rag.py / few-shot / ルール・重み / TANKA_* 環境変数 / モデル切替) の効果測定・A/B 比較・
  アブレーション実験を、正しい統計的作法で実行する。ユーザーが「効果を測って」「eval して」
  「比較して」「改善したか確認して」「アブレーション」「実験して」と言ったとき、
  パイプライン変更の採否・merge 判断が必要なとき、eval 結果ファイルの解釈を求められたとき、
  品質に影響しうる変更を加えた直後には、明示的な依頼がなくても必ずこのスキルを使う。
  チャット上の 1 回生成や主観で品質を判断してはならない。
---

# tanka-eval-ablation — パイプライン変更の数値検証

## 大原則 (なぜ手順が厳格か)

この repo の品質判断はすべて eval ハーネスの数値で行う。理由: 短歌生成は反復改善ではなく
**validator-guided best-of-N サンプリング**であり、score は確率変数。同一お題・同一設定でも
**stdev ≈ 9.6、レンジ 25 点**ぶれる (実測: `app/backend/eval/FINDINGS.md` §2)。
「2〜3 点上がった」は改善ではなくサイコロの目である。

**ノイズ下限 (2SE 基準)。実行前に判定基準として事前登録する:**

| 比較単位 | 偶然生じうる平均差 |
|---|---|
| 1 お題 × 1 run | ±19 |
| 8 お題平均 | ±6.8 |
| 16 お題平均 | ±4.8 |
| 50 お題平均 | ±2.7 |

この幅以下の差で「改善/劣化」と結論しない。**「差はノイズ内 = 判定不能」も正当な結論**であり、
そう報告する。実例: self-critique 実験 (FINDINGS.md §5.5) は +4.69 (n=16) を有意と認めず保留した。

## Step 0 — 前提チェック (毎回)

```bash
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

- `status: ok` / `lm_studio_ok: true` / `model_loaded: true` を確認。落ちていれば `./app/start.sh` と
  ホスト側 `lms server start` を案内
- **`configured_model` が意図したモデルか確認**。モデルは UI から runtime 切替でき mongo に永続化される
  ため、「知らないうちに別モデルで測っていた」事故が起きうる。結果 JSON にモデルは記録されないので、
  **variant 名にモデルを含める** (デフォルト以外なら。例: `gemma12-rag-off`)
- LM Studio が直前まで連続負荷を受けていたら、ユーザーに**モデルの fresh reload** を依頼してから始める
  (劣化中は 15s/call → 3.5min/call + HTTP 400 に oscillate する。CLAUDE.md §6.13)

## Step 1 — 実験設計 (実行より先に書き出す)

走らせる前に以下を決めて notes.md (または会話) に明記する。後から基準を動かすと
「有意に見えるまで探す」p-hacking になるため、**判定基準の事前登録が本体**である。

1. **問い**: 何の効果を測るか (1 実験 1 変数。複数変えると切り分け不能)
2. **変更の種類**: コード変更か env 切替か (→ Step 2 で適用方法が違う)
3. **お題セット**: full 50 (`eval_themes.json`) か subset か。
   - subset は `app/backend/eval/` 内にファイルとして置く (`/tmp` は消えて再現不能になる)。
     **before/after は必ず同一ファイル**。結果 JSON の `_themes_file` が一致しない比較は無効
   - 所要時間: 1 お題 30 秒〜2 分。50 お題で variant あたり 25〜60 分。試行錯誤段階は 8 お題 subset、
     採否の最終判断は 50 お題が目安。**実験の重さは判断の重さに合わせる** (好奇心の sanity check に
     paired 50 題 ×2 を組まない。逆に merge の採否を 8 題 1 run で断定しない)
4. **判定基準**: 上のノイズ下限表から該当行を引用し「±X を超えたら採用」と宣言する
5. **交絡対策**:
   - **LM Studio 劣化**: variant ごとに fresh reload。blocked A/B (A を全部→B を全部) は
     後半が劣化で沈む交絡が実証済み (FINDINGS.md §5.5)。判定が際どくなりそうなら最初から
     お題ごと交互実行 (paired) か複数 run 平均を設計する
   - **長期失敗記憶の順序効果**: `failures` コレクションは run をまたいで蓄積し、後で走る variant が
     先行 run の教訓の恩恵を受ける。厳密にやるなら各 variant 直前に `curl -X DELETE
     http://localhost:8001/api/failures` でリセットする (蓄積記憶が消えるため、実行前にユーザーに一言確認)
6. **重み・しきい値を変える実験の特例**: `TANKA_W_*` / `TANKA_PASS_THRESHOLD` の変更は
   **スコアの物差し自体を変える**ため、`avg_final_score` や合格率を arm 間で直接比較しても無効
   (同じ歌でも点が変わる)。主要指標は (a) 最終成果物における当該違反の**残存率**、または
   (b) **共通重み (変更前の重み) で再採点**したスコアにする。さらに長期失敗記憶の lesson は
   最大 weight の違反から優先的に選ばれるため、重み変更は記憶の内容にも交絡する —
   この種の実験では上記の failures リセットを必ず実施する

## Step 2 — 変更の適用 (事故多発点)

適用方法を間違えると**「同じものを 2 回測って差を論じる」**最悪の事故になる。

- **コード変更** (prompts.py / validator.py 等): backend はソースを volume mount しているので
  `cd app && docker compose restart backend`
- **env 切替**: `restart` では効かない (env は up 時に固定される)。コンテナ再作成が必要:

  ```bash
  cd app && TANKA_SELF_CRITIQUE=0 docker compose up -d backend
  ```

- **passthrough の罠**: `docker-compose.yml` の `backend.environment` に列挙されていない `TANKA_*` は
  **ホストで export しても backend に届かない**。現在列挙済み: `TANKA_DYNAMIC_FEWSHOT` /
  `TANKA_SELF_CRITIQUE` / `TANKA_RAG` / `TANKA_HARD_CAP` / `TANKA_PLATEAU_WINDOW`。
  それ以外 (例: `TANKA_W_<RULE>`, `TANKA_PASS_THRESHOLD`, `TANKA_MAX_COMPLETION_TOKENS`) を
  切り替えるときは、先に compose に `TANKA_FOO: ${TANKA_FOO:-既定値}` を追記してから `up -d` する。
  既定値は `config.py` / `validator.py` の定義に合わせる
- **適用確認を必ず行う** (信じずに見る):

  ```bash
  docker compose exec backend printenv | grep TANKA
  ```

  生成挙動を変える switch (例: self-critique) は、保存メッセージの `phases` に当該フェーズが
  現れるかで **per-generation の事後検証**もできる (per-phase 永続化 #18)。「env が効いていなかった」
  事故を測定後からでも検出できる保険になる

## Step 3 — 実行

```bash
cd app/backend/eval
./eval.sh <variant-name> [themes.json]   # 例: ./eval.sh sc-off eval_themes_quick8.json
```

- **variant 命名**: 変更内容が分かる名前 (`sc-on` / `sc-off`, `w-repeated-12` 等)。
  デフォルト以外のモデルならモデル名も入れる。結果は `results/<ts>-<variant>.json` に残る
- **Bash ツールの timeout (最大 10 分) を超える**ので、eval.sh は `run_in_background` で起動する。
  完了判定は results/ にファイルが出現したか、または出力ログで行う。進捗はフロントの
  「評価モニタ」タブ (http://localhost:5178) でも見られる。停止させるときは pkill のパターン外しに
  注意 (PID を控えておく)
- **実行中の禁則**:
  - backend の設定・コードを変えない (測定対象が途中で変わる)
  - LM Studio へ診断 streaming や手動チャットを投げない (負荷上乗せで劣化が悪化した実績あり)
  - 複数 eval を並行実行しない (同一 LM Studio を奪い合い、両方の測定が壊れる)

## Step 4 — 比較と判定

```bash
./compare.sh <before-variant> <after-variant>
```

判定の規律:

1. **`_themes_file` と n の一致を確認**してから差を読む (compare.sh は n 不一致しか警告しない)
2. 平均スコア差を Step 1 で事前登録したノイズ下限と照合。**下限以下なら「判定不能」と報告**する。
   全項目が同方向でも n が小さければノイズでそう見える (8 お題 +2.86 の「全項目改善」が
   ノイズだった実例あり)
3. **前半/後半の交絡チェック**: 後半が大きく沈んでいたら LM Studio 劣化を疑う。

   ```bash
   SID=$(python3 -c "import json;print(json.load(open('results/<file>.json'))['_session_id'])")
   curl -s "http://localhost:8001/api/sessions/$SID" | python3 -c "
   import sys, json
   sc = [m['final_score'] for m in json.load(sys.stdin).get('messages', [])
         if m.get('kind') == 'tanka' and m.get('final_score') is not None]
   h = len(sc) // 2
   print(f'前半 {sum(sc[:h])/h:.1f}  後半 {sum(sc[h:])/(len(sc)-h):.1f}')
   "
   ```

   前半と後半で結論の符号が変わるなら、その run は採否判断に使わない (再測定)
4. ぶれの実態を知りたいときは variance を直接測る: `./eval-repeat.sh "<お題>" 5`
5. `violation_frequency` は最終成果ではなく**全 attempt 横断** (探索過程の分布) である点に注意

## Step 5 — 記録

- 結果 (採用・棄却・判定不能のいずれでも) を `app/backend/eval/FINDINGS.md` に追記する。
  **判定不能も知見**として残す (何が交絡したか、次はどう測るべきか)
- 変更を採用する PR の本文に before/after の数値と判定根拠を書く
- 大きな発見 (季節ドリフトの類) は `app/docs/` に実験ノートを起こす

## クイックレシピ

```bash
# env アブレーション (例: self-critique)
cd app && TANKA_SELF_CRITIQUE=1 docker compose up -d backend
cd backend/eval && ./eval.sh sc-on eval_themes_quick8.json        # run_in_background で
cd ../.. && TANKA_SELF_CRITIQUE=0 docker compose up -d backend
cd backend/eval && ./eval.sh sc-off eval_themes_quick8.json
./compare.sh sc-on sc-off

# コード変更の before/after
cd app/backend/eval && ./eval.sh baseline       # 変更前に測る
# (コードを変更)
cd ../.. && docker compose restart backend
cd backend/eval && ./eval.sh my-change && ./compare.sh baseline my-change

# variance の実測 (ノイズ下限の根拠を自分で更新したいとき)
./eval-repeat.sh "散りゆく桜" 5
```

## 参照 (深掘りが必要なとき)

- `app/backend/eval/README.md` — ハーネスの仕様・メトリクス定義・評価モニタ UI
- `app/backend/eval/FINDINGS.md` — variance 実測 (§2)、LM Studio 劣化 (§5)、交絡実例 (§5.5)。
  ノイズ下限の数値の出典はここ
- `CLAUDE.md` §6.13 — LM Studio sustained-load 劣化の環境制約
