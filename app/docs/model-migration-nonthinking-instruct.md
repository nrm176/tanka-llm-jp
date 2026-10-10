# モデル移行検討: llm-jp-4-8b-thinking → 非-thinking / Instruct 系への乗り換え調査

> 区分: 調査記録 (採否は eval A/B で確定させること。本書は候補の順位付けまで)
> 日付: 2026-07-10
> 関連: [design-principle-verifier-in-the-loop.md](./design-principle-verifier-in-the-loop.md) /
> [model-characteristics.md](./model-characteristics.md) /
> [model-compat-qwen3-swallow.md](./model-compat-qwen3-swallow.md) /
> [thinking-runaway-postmortem.md](./thinking-runaway-postmortem.md) / CLAUDE.md §6.14〜6.16, §7

本書は「既定モデル llm-jp-4-8b-thinking の 17% thinking 暴走リスクを、日本語質の天井を落とさずに
消せる乗り換え先はあるか」を、**このプロジェクトが既に確立した選定基準で**調査した記録である。
新しい評価軸を作る必要はなく、既存3ドキュメントの原則をそのまま適用する。

**重要**: 本書のモデル性能に関する記述は **層 D (プローブ) すら未実施**であり、すべて**仮説**である
(model-characteristics.md §0 のエビデンス階層に準拠)。採否は §5 の eval A/B を通してのみ確定する。

---

## 1. TL;DR

- **最有力は "遠くの新モデル" ではなく "同族の兄弟" だった**:
  現行既定 `llm-jp-4-8b-thinking` には非-thinking 版 **`llm-jp-4-8b-instruct`** が存在する
  (2026-04-03 公開、SFT only・DPO なし、Apache 2.0、GGUF あり)。
- これは互換性の構造的法則 (§4 in model-compat) を**非-thinking で構造的に満たし**、かつ
  **同一ファミリー = 日本語特化を維持**するため、「暴走を消す」と「質の天井を保つ」を両立できる
  **唯一の候補**である (仮説)。
- **避けるべき**: Qwen3 Swallow (8B/30B-A3B/32B) は日本語ベンチ最高クラスだが**全て reasoning 型**で、
  既に不適合が実証された `qwen3-swallow-30b-a3b-rl` と同じ構造。「ベンチが高い ≠ このタスクで良い」。
- 採否は必ず統制 eval (n=12) で。主指標は avg ではなく **zero 率 (暴走が消えたか)** と
  **90-100 点産出数 (質の天井を保てたか)**。

---

## 2. 選定基準 (既存ドキュメントからの再掲。詳細はリンク先)

このタスクの評価軸は確立済み。要点のみ:

1. **互換の構造的法則** (model-compat §4): 互換を決めるのは規模でも世代でもなく
   **「最終 JSON が `content` に届くか」の一点**。条件は (a) 非-thinking、または
   (b) 閉じる thinking で最終回答が content 側。
2. **設計原則** (design-principle §1): 外部 validator + refine がある設計では、モデル内 thinking は
   二重の無駄で暴走の元。選定は「素の実力」でなく **「refine 応答性 × 無事故性」**。
3. **検証器の適用範囲** (design-principle §6 / model-characteristics §1 末): validator が見るのは
   **規則化済みの次元 (拍数・季語・JSON) のみ**。**規則化できない次元 (詩情・名歌力) は素の質がそのまま出る**
   → ここでは llm-jp の日本語特化が代替不能 (zero 除き avg ~92.6、90-100 点 6/12)。

→ この3番目が移行の核心。**「非-thinking 化で暴走を消す」だけなら gemma-3 で既に可能だが、
それは質の天井 (名歌力) を犠牲にする**。理想は「非-thinking **かつ** llm-jp 同等の日本語特化」であり、
それが `llm-jp-4-8b-instruct` そのものである。

---

## 3. 候補の順位付け (2026 年央時点、パイプライン基準でフィルタ済み)

| 順位 | モデル | 型 | 日本語特化 | 互換見込み | LM Studio 入手性 | 一言 |
|---|---|---|---|---|---|---|
| **★1** | **llm-jp-4-8b-instruct** | 非-thinking (SFT only) | ◎ 同族=現行同等 | ◎ 非-thinking で構造的に安全 | GGUF あり (`mmnga/llm-jp-4-8b-instruct-gguf` ~5GB, Apache 2.0) | **本命**。暴走を消しつつ質の天井を保つ唯一の候補 |
| 2 | Qwen3-30B-A3B-Instruct-2507 | **非-thinking 専用** (`<think>` 非生成) | ○ 高いが特化ではない | ◎ 非-thinking・256K context | GGUF あり (unsloth) | 無事故性・知識量で有力。失敗した qwen3-swallow の "非think 兄弟" |
| 3 | Sarashina2-Instruct (SB Intuitions) | 非-thinking | ◎ 日本語 native・MIT | ○ 要プローブ | GGUF あり | 日本語 native な代替。70B は重い |
| 4 | Llama-3.1 Swallow 8B Instruct 等 | 非-thinking | ○ 日本語強化 | ○ 要プローブ | GGUF あり | 実績ある日本語強化系の非-think 変種 |
| — | **Qwen3 Swallow (8B/30B-A3B/32B)** | **reasoning 型** | ◎ (JP ベンチ最高クラス) | **✗ 回避** | — | 失敗実証済み `qwen3-swallow-30b-a3b-rl` と同じ reasoning 構造。§4 の不適合再発リスク |

★=本命候補 / ✗=回避推奨。「互換見込み」列は**未検証の予測** (非-thinking は構造的に content 直行のため
高確率だが、§5-2 のプローブを省略しないこと)。

---

## 4. 本命 `llm-jp-4-8b-instruct` の評価 (正直なトレードオフ)

### 4.1 なぜ本命か

- **同族性が決定的**: llm-jp-4 の 8B は thinking 版と instruct 版が**同じベースモデル**で、
  post-training だけが違う (thinking = SFT+DPO、instruct = SFT only)。つまり
  **拍数感覚・季語語彙・名歌力といった "規則化できない日本語質" を共有する見込み**。
  他の非-thinking 候補 (gemma-3/Qwen) はここで llm-jp に劣る (model-characteristics §3.2 実測)。
- **非-thinking = 構造的に無事故**: thinking 暴走 (`</think>` 不到達 → content 空) が原理的に起きない。
  現行の主要リスク (17% zero) を消せる見込み (gemma-3 が zero 0 だった機序と同じ)。

### 4.2 確認が必要な点 (仮説の検証項目)

- **SFT only (DPO なし) の指示追従**: thinking 版は DPO で追加整列されている。instruct 版は
  指示追従がわずかに弱い可能性。**ただしこれは validator + refine が吸収する規則化済み次元**なので、
  本設計とはむしろ相性が良い (design-principle §5)。
- **質の天井を本当に保てるか**: 同族でも post-training が違えば名歌力が落ちる可能性はある。
  → **90-100 点の産出数**で必ず測る (llm-jp-thinking は 6/12)。
- **暴走が本当に 0 か**: 非-thinking でも制約過多 compose で妙な挙動が出ないか、**zero 率**で確認。

### 4.3 測定すべき指標 (model-characteristics §2 の作法)

`first_attempt_pass_rate` (素の実力) / `avg_attempts` (validator 依存度) /
`mora 系・kigo 規律` 違反 / **`zero 率` (暴走が消えたか)** / **`90-100 点産出数` (質の天井)**。
主指標 avg のノイズ下限は n=12 で **±5.6** (これ以内は判定不能)。

---

## 5. 導入パス (model-characteristics §5 のチェックリスト準拠)

> LM Studio 実機が必要なため、以下はユーザー環境での手順。CI/この作業環境では回せない。
> **【2026-10 更新】具体的な実行手順と判定基準は §8 の runbook に事前登録済み。そちらに従うこと**
> (本節の「zero 率」「±5.6」の枠組みは 2026-06 時点のもので、§8.1 の前提更新で一部古くなっている)。

1. **DL & 明示ロード** (JIT の既定 context 8192 罠を回避、CLAUDE.md §6.14):
   ```
   lms load llm-jp/llm-jp-4-8b-instruct --context-length 32768
   ```
2. **互換プローブ** (本物の compose メッセージで content 着地を確認。手順は model-compat §6):
   `content > 0` なら互換。非-thinking なので通る見込みだが**省略しない**。
3. **統制 eval A/B** (tanka-eval-ablation スキルの作法):
   セッション固定 (#20) で `eval_themes_x12.json` を回し `./compare.sh thinking instruct`。
   avg のノイズ下限 ±5.6、first-pass・violation 内訳・**zero 率**・**90-100 点数**を併読。
4. **記録**: `app/backend/eval/FINDINGS.md` と `model-characteristics.md` の表を更新、本書に結果を追記。

---

## 6. 参考: 32B-A3B と "使い分け" の可能性

- **llm-jp-4-32b-a3b**: MoE (32B total / 3B active)。thinking 版・base 版は確認できたが、
  **instruct 版 (特に GGUF) の有無は未確認 → 要調査**。存在すれば「質の天井をさらに上げる非-think」候補。
- design-principle §6 / model-characteristics §6 の結論どおり、**単一の万能モデルは無い**。
  最終形は「llm-jp-4-8b-instruct を既定 (無事故 × 日本語質)」に据えつつ、
  #20 のセッション固定で用途別に使い分ける形になる可能性が高い。本書はその既定候補の差し替えを扱う。

---

## 7. エビデンス状況と未解決事項

| 項目 | 状況 |
|---|---|
| `llm-jp-4-8b-instruct` の存在・型・入手性 | **確認済み** (公開情報・GGUF 実在) |
| 同モデルのパイプライン性能 (zero率/名歌力/first-pass) | **未測定** (層 D プローブも未実施)。§5 の eval で確定必須 |
| Qwen3-30B-A3B-Instruct-2507 の非-thinking 性 | **確認済み** (`<think>` 非生成が公式仕様) |
| Qwen3 Swallow の reasoning 型 (回避理由) | **確認済み** (公式が reasoning-type と明記) |
| llm-jp-4-32b-a3b の instruct/GGUF 有無 | **未確認** (要調査) |

---

## 出典

- NII: LLM-jp-4 8B/32B-A3B 公開プレスリリース (2026-04-03) —
  https://www.nii.ac.jp/news/release/2026/0403.html
- llm-jp/llm-jp-4-8b-instruct (Hugging Face) — https://huggingface.co/llm-jp/llm-jp-4-8b-instruct
- mmnga llm-jp-4-8b-instruct-gguf (LM Studio 用) — HF `mmnga` org
- Qwen3-30B-A3B-Instruct-2507 (non-thinking 専用) —
  https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507
- Qwen3 Swallow (全て reasoning 型・回避対象) — https://swallow-llm.github.io/qwen3-swallow.ja.html
- Sarashina (SB Intuitions) — awesome-japanese-llm 掲載
- awesome-japanese-llm — https://github.com/llm-jp/awesome-japanese-llm

---

## 8. 実行 runbook (2026-10 追記) — 前提の更新と事前登録済み A/B

> tanka-eval-ablation スキルの作法に準拠。**判定基準をここに事前登録する** (走らせてから基準を
> 動かさないため)。実行はユーザーの LM Studio 環境。

### 8.1 前提の更新 — 「17% の 0 点」はもう主動機ではない

2026-06 の調査 (§1-5) 以降、状況が 2 つ変わった:

1. **0 点は本番から消えた**: #65 (末尾切れ救済) + #68 (emotion alias) により、observed 0 点は
   0 件になった (FINDINGS §5.7: +14.3 点、所要 −31%、2SE 12.75→2.44)。
   「zero 率 17% の撲滅」を移行の主目的に据える前提は**失効**
2. **score の尺度が変わった**: 拍数重み 3/3/10 → 12/12/25 (#81, FINDINGS §5.9)。
   旧 campaign の avg (thinking 77.2 等) とは**直接比較不能** → **thinking の baseline も新尺度で
   取り直す** (本 A/B は両腕を同時期に測る)

それでも移行を検討する価値 (再定義):

- **暴走の浪費除去**: json_repaired 23.9% (§5.7) は「rescue という松葉杖で立っている」ことの証左。
  thinking は失敗の前に数千 token を焼く — latency・発熱 (§6.13/§6.15 の劣化・Thermal Emergency の
  原因側) に直結
- **配管リスクの除去**: §6.17 で実証されたとおり、thinking の「流れ方」は LM Studio 更新で
  静かに変わり、1 ヶ月気づかず壊れた。non-thinking は reasoning ストリーム自体が存在しない
- **chat 側の暴走 (§6.17 #54) も消える**

**主要リスクは不変**: 質の天井 (90-100 産出・名歌力) を instruct が保てるか。thinking 版は
SFT+DPO、instruct 版は SFT only であり、指示追従がわずかに弱い可能性 (validator+refine が吸収する
見込みだが未測定)。

### 8.2 事前登録 (実行前にここを読み、基準を動かさない)

- **問い**: 既定モデルを `llm-jp-4-8b-instruct` に替えると、質 (avg / 90-100 産出) を落とさずに
  暴走系の浪費 (所要時間・schema_invalid・avg_attempts) を減らせるか
- **1 変数**: モデルのみ。`TANKA_*` は全て既定のまま、コード変更なし
- **お題セット**: `eval_themes_x12.json` (n=12、四季×3。旧 campaign と同セット)。
  所要の目安: thinking ~50 分 / instruct はそれ以下の見込み (1 arm ずつ、並行禁止)
- **判定基準** (n=12 の参考ノイズ下限 ±5.6 — 旧尺度実測の参考値、スキルの表参照):
  | 結果 | 判定 |
  |---|---|
  | instruct の avg が −5.6 を超えて低い | 質で劣後 → **不採用** (セッション固定の選択肢には残す) |
  | avg 非劣性 (±5.6 内) **かつ** 浪費指標 (所要 or schema_invalid or attempts) が明確改善 | **既定昇格候補** → 確認 run (もう 1 回 or 50 題) へ |
  | avg 非劣性だが浪費も同等 | **判定不能 = 既定据え置き** (それも正当な結論として FINDINGS に記録) |
  | 90-100 の産出が半減以下 (thinking 6/12 → 3 以下) | avg が同等でも「質の天井」喪失として要再考 |
- **交絡対策**: arm ごとに fresh reload (§6.13) / `caffeinate -dims` でラップ (§6.15) /
  失敗記憶はモデル別分離済み (#19) のため arm 間漏洩はないが、incumbent (thinking) だけが
  過去の蓄積教訓を持つ非対称がある。公平な cold-start で測るなら両 arm 直前に
  `curl -X DELETE http://localhost:8001/api/failures` (**蓄積記憶が消えるので実行前に要判断**)。
  結果が際どければ順序を反転して再測 (blocked A/B の後半劣化交絡、FINDINGS §5.5)

### 8.3 手順 (コマンド列)

```bash
# 0. スタック起動 + health (configured_model も見る)
./app/start.sh
curl -s http://localhost:8001/api/health | python3 -m json.tool

# 1. モデル取得 (LM Studio の検索 UI か lms get で mmnga/llm-jp-4-8b-instruct-gguf)
#    ロード後、LM Studio 上の正確なモデル ID を確認 (TANKA_EVAL_MODEL はこの ID と一致させる):
curl -s http://localhost:8001/api/models | python3 -m json.tool

# 2. 互換プローブ (model-compat-qwen3-swallow.md §6 のスクリプトを instruct の ID で実行)
#    判定: content > 0 なら互換。非-thinking なので通る見込みだが省略しない
lms load <instruct-model-id> --context-length 32768   # JIT 8192 罠の回避 (§6.14)

# 3. (任意・8.2 で判断) 失敗記憶のリセット
# curl -X DELETE http://localhost:8001/api/failures

# 4. arm A: thinking baseline を新尺度で取り直す (fresh reload してから)
cd app/backend/eval
caffeinate -dims ./eval.sh thinking-x12-$(date +%m%d) eval_themes_x12.json   # 既定モデルのまま
#   (明示するなら TANKA_EVAL_MODEL=llm-jp-4-8b-thinking を付ける。結果 JSON の _model で検証可)

# 5. arm B: instruct (fresh reload してから)
TANKA_EVAL_MODEL=<instruct-model-id> caffeinate -dims \
  ./eval.sh instruct-x12-$(date +%m%d) eval_themes_x12.json

# 6. 比較 + 前半/後半の劣化チェック (スキル Step 4-3 のスニペット)
./compare.sh thinking-x12-<date> instruct-x12-<date>
```

- eval.sh は `TANKA_EVAL_MODEL` をセッション固定 (#20) として渡す。モデル ID 誤り・固定不発は
  **開始前に fail-loud** で止まり、使用モデルは結果 JSON `_model` に記録される
- 読む指標: `avg_final_score` / `first_attempt_pass_rate` / `avg_attempts` /
  `score_buckets` (90-100 と 0) / `violation_frequency` (mora 系・schema_invalid) + 壁時計所要

### 8.4 判定後にやること

- **どの結論でも**: FINDINGS.md に追記 (判定不能も知見)、`model-characteristics.md` §1 表と §3.5 を更新、
  本書 §7 の表を更新
- **採用 (既定昇格) の場合**: `config.py` の `MODEL` 既定値 + `docker-compose.yml` の env 既定 +
  CLAUDE.md 冒頭/関連節 (§6.16 等) を書き換え。mongo の `settings.model` 永続値が旧モデルを
  指したままにならないか確認 (POST /api/model で上書き)。確認 run (50 題 or 再 run) を経てから
- **不採用/判定不能の場合**: instruct をセッション固定の選択肢として README に記載して終了
