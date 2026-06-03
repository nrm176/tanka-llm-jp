# LLM フィードバック設計 — 引き継ぎ必読

このドキュメントは、コードを読んだだけでは **誤解されやすい設計判断** を明示するための引き継ぎ資料です。
「validator が何をしているのか」「失敗記憶はどう活用されているのか」「LLM への状態伝達はどう設計されているか」
を、実装の根拠とともに整理しています。

> **要約**: System プロンプトは静的な役割定義のみで不変。
> 動的な状態（critique・教訓・Plan 制約）はすべて **user メッセージに埋め込み**。
> Validator は単なる pass/fail checker ではなく **改稿ループを駆動する中央フィードバック生成器**。
> 長期失敗記憶は **few-shot ではなく "教訓フレーズの注入"**。

---

## 1. なぜこのドキュメントが必要か

このプロジェクトを引き継ぐ担当者は、コードを表面的に読むと以下の **3 つの誤解** を抱きやすいです：

| よくある誤解 | 実態 |
|---|---|
| 「validator は出力をパスさせるかどうか判定する checker」 | 実際は **数値・違反詳細・自然言語 critique・教訓フレーズ** を生成する **フィードバック信号発生器** |
| 「失敗記憶は few-shot 例 (悪い出力 + 修正版) として LLM に見せている」 | **見せていない**。違反 → 1 行の "教訓" に圧縮して **user メッセージに箇条書きで埋め込んでいる** |
| 「validator の結果は system プロンプトに反映される」 | **反映されない**。System プロンプトは静的な定数で、validator 出力はすべて **user メッセージ側** に流れる |

この 3 点を最初に押さえないと、デバッグや拡張時に的外れな箇所を触ることになります。

---

## 2. System プロンプトと User メッセージの分担

これがアーキテクチャの根本ルールです。

### 静的 system / 動的 user の原則

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│ [system] TANKA_SYSTEM_PROMPT (定数)                          │
│    - "あなたは熟練した歌人です。短歌の作法を熟知し..."        │
│    - 短歌のルール、思考の順序、出力形式の仕様                 │
│    - ★ 全 attempt で書き換えられない ★                       │
│                                                              │
│ [user]      few-shot 例 1 のお題                ┐             │
│ [assistant] few-shot 例 1 の良い JSON           │ 正例         │
│ [user]      few-shot 例 2 のお題                │ (静的)       │
│ [assistant] few-shot 例 2 の良い JSON           │ TANKA_       │
│ [user]      few-shot 例 3 のお題                │ COMPOSE_     │
│ [assistant] few-shot 例 3 の良い JSON           │ FEW_SHOT     │
│ [user]      few-shot 例 4 のお題                │ から         │
│ [assistant] few-shot 例 4 の良い JSON           ┘             │
│                                                              │
│ [user] ★ 今回のお題: <theme>                                 │
│        構想: <plan>                              ─┐           │
│        【重要】kigo は必ず "蝉" / season "夏"     │           │
│        【長期失敗記憶 — 教訓 1 / 2 / 3】          │ 動的       │
│        【過去の試行で起きた違反 (短期)】          │ (毎 attempt│
│        JSON で出力してください...                ─┘ で組立て) │
│                                                              │
│ [assistant] 初稿 JSON                                        │
│   ↓ ↓ ↓ validator がここで動作 ↓ ↓ ↓                         │
│                                                              │
│ [user] ★ validator の critique を入れる                      │
│        "前回 65/100 (合格 80)                                │
│         **最も重要な違反**:                                  │
│           ✗ [-15 major] 季語「花」が 2 回 ...                │
│           ✗ [-10 critical] 1句目「春霞む」は 6 拍 ..."       │
│        + 失敗履歴更新                                        │
│                                                              │
│ [assistant] 再詠 JSON                                        │
│   ↓ また validator が動作 ↓                                  │
│ [user] ★ critique (前回 78/100, 違反 ...)                    │
│ [assistant] 再々詠 JSON ...                                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### この分担が意図的である理由

| 理由 | 説明 |
|---|---|
| **役割定義の純粋性** | "あなたは歌人" と "前回 65 点だった" を混ぜると、LLM の役割認識が不安定になる |
| **トークン管理** | system を毎 attempt で書き換えると、refine 履歴と二重に状態が累積して context を圧迫 |
| **対話構造との整合** | refine は本質的に "前の出力 + フィードバック → 次の出力" の対話。validator critique は user 発話として自然 |
| **モデル挙動の安定性** | OpenAI 系の API では system は高優先度命令扱い。毎回変えると振る舞いが揺れやすい |
| **実装の簡潔さ** | system は constant 参照、user は append のみ |

### 該当コード

`tanka.py` 内で `{"role": "system", ...}` が現れる箇所は **3 箇所すべて** `TANKA_SYSTEM_PROMPT` (もしくは `NORMAL_SYSTEM_PROMPT`) という **module-level 定数の参照のみ**。動的構築されていません。

```python
# Plan
plan_messages = [
    {"role": "system", "content": TANKA_SYSTEM_PROMPT},   # ← 定数
    {"role": "user", "content": "..."},
]

# Compose (build_compose_messages 内)
return [
    {"role": "system", "content": TANKA_SYSTEM_PROMPT},   # ← 同じ定数
    *TANKA_COMPOSE_FEW_SHOT,
    {"role": "user", "content": (
        f"お題: {theme}\n構想:\n{plan}"
        + plan_constraint                                  # 動的だが user 側
        + validator.format_long_term_failures(...)         # 動的だが user 側
        + _format_failure_history_block(...)               # 動的だが user 側
        + "JSON 形式で..."
    )},
]
```

---

## 3. Validator の本当の役割

`validator.py` を「Pydantic で形式検証してスコアを出すだけ」と思って読むと、半分しか理解できません。
実際には **6 つの役割を兼ねるパイプライン中枢** です。

### 6 つの責務

| # | 役割 | 該当 API | LLM への影響 |
|---|---|---|---|
| 1 | **スキーマ検証** | `parse_tanka_json()` + Pydantic `Tanka` | 型不正なら即 refine ループに戻す |
| 2 | **ルール検証** | `RULES` リスト 7 関数 + 2 つの Plan 整合ルール | 違反を構造化データに変換 |
| 3 | **スコアリング** | `evaluate()` → `ValidationResult(score, violations, passed)` | refine 継続判定の根拠 (passed=True で停止) |
| 4 | **critique 生成** | `format_critique(result)` | **これが refine の方向性を決める。最も重要** |
| 5 | **Plan 抽出 helper** | `extract_season_from_plan` / `extract_kigo_from_plan` | Plan-Compose 整合性チェック (`season_matches_plan` ルール) の入力 |
| 6 | **失敗記憶ヘルパ** | `format_long_term_failures()` / `format_failure_summary()` / `LESSONS` | 失敗記録 → 教訓フレーズの変換器 |

### validator が "純粋関数" である意味

`validator.py` には **副作用がありません**。

- DB 接続なし
- Redis アクセスなし
- ログ出力 (log) を除き、ファイル I/O なし

これは意図的で、**単体テストしやすい・呼び出し位置に依存しない・順序不変** という性質を保つためです。
DB への記録は呼び出し側 (`tasks.py`) が行います。validator はあくまで `Tanka` を入力に取って ValidationResult を返す関数の集合体です。

### "ルールテーブル" としての設計

```python
RULES: list[RuleFunc] = [
    _rule_mora_count,
    _rule_kigo_present,
    _rule_kigo_unique,
    _rule_kigo_in_dictionary,
    _rule_season_consistent,
    _rule_no_other_kigo,
    _rule_repeated_word,
]
```

各ルールは `(Tanka) -> list[Violation]` の純関数。`evaluate()` がこれを `for rule in RULES: ...` で回すだけ。

**新ルール追加 = 関数を 1 つ書いてリストに append、それだけ**。`LESSONS` 辞書に 1 行教訓を追加すれば長期記憶でも使われる。

### Plan 整合ルールが RULES に入っていない理由

`season_matches_plan` / `kigo_matches_plan` は **`Tanka` だけでは判定できない** (Plan の値が必要)。
そのため `evaluate(t, *, expected_season, expected_kigo)` の **オプション引数経由で外から注入** する設計になっており、RULES リストには入っていません。

```python
# evaluate 内
for rule in RULES:
    violations.extend(rule(t))                # ← 純粋ルール

if expected_season and t.season != expected_season:   # ← 外部入力依存
    violations.append(_violation("season_matches_plan", ...))
if expected_kigo and t.kigo != expected_kigo:
    violations.append(_violation("kigo_matches_plan", ...))
```

これは「ルールは Tanka 内で完結すべき」という抽象を壊さないための設計判断です。

---

## 4. 長期失敗記憶の実体

`db.failures` コレクションの中身 (例) を見ると勘違いしやすい構造になっています。

### 保存されているもの

```javascript
// 1 件の failure ドキュメント
{
  _id: ObjectId(...),
  task_id: "...",
  session_id: "...",
  theme: "夏の夕暮れ",
  attempt: 0,
  raw_output: "{\"kigo\":\"蛍\",\"season\":\"夏\",\"lines\":[...]}",  // ← 完全な悪い JSON
  parsed: {kigo: "蛍", season: "夏", ...},                            // ← Pydantic 検証済 dict
  score: 65,
  violations: [
    {rule: "kigo_unique", severity: "major", weight: 15, message: "..."}
  ],
  ts: ISODate("...")
}
```

**raw_output (生の悪い JSON) も保存されている** ので、ぱっと見「これを few-shot として LLM に見せている」と思いがち。
**実際にはそうしていません**。

### LLM に見せているもの

`format_long_term_failures()` の出力を確認すると：

```
【長期失敗記憶 — 過去にあなたがやらかした違反パターン。同じことを繰り返さないこと】
  失敗 1: お題「夏の夕暮れ」 (宣言季語「蛍」) で score=65 → 教訓: 宣言した季語は本文中ちょうど 1 回だけ出現させる
  失敗 2: お題「秋の月」 (宣言季語「月」) で score=70 → 教訓: 宣言した季と異なる季の季語を本文に含めない
```

つまり：

- ❌ `raw_output` の JSON 全文は **LLM に送られない**
- ❌ user/assistant ペアの **few-shot 形式でも送られない**
- ✅ `LESSONS` 辞書による **rule_name → 1 行教訓** に圧縮して、現お題の **user メッセージ末尾にテキストブロックとして埋め込み**

### なぜ "本物の few-shot" にしなかったか

検討した上での意図的な選択です：

| 観点 | 教訓注入 (現実装) | 本物の失敗 few-shot |
|---|---|---|
| トークン消費 | 数行 | 1 件 50〜100 行 (JSON + critique + 修正版) |
| 8B モデルの混乱リスク | 低 (明示的に "やるな" と書ける) | 中〜高 (悪い例を "お手本" と誤認するケースあり) |
| in-context learning 効果 | 弱 (抽象的指示のみ) | 強 (パターンを直接学習) |

8B クラスの thinking モデルでは "悪い例の見せすぎ" が裏目に出る傾向があるので、抑制的な教訓注入方式を採用しています。

### 効果の現実

正直、長期失敗記憶の効果は **限定的** です。理由：

1. **抽象的すぎる** — `"宣言した季語は本文中ちょうど 1 回だけ"` のような汎用フレーズは、システムプロンプトに既に書いてあるルールとほぼ同じ。新情報量が少ない
2. **修正例がない** — 「これは失敗だった」だけで、「ではどう直せばよかったか」の対比がない
3. **季節フィルタはあるが、テーマ類似度ベースではない** — お題の意味的近さでマッチしているわけではない

**実際の品質保証は validator のルール強制 + refine ループが担っており**、長期記憶は補助輪レベルです。
強化する場合は次節「拡張時の選択肢」を参照。

---

## 5. Refine ループの会話構造

### コード上の messages 配列の蓄積

`tanka.py` の `generate_tanka_pipeline` 内で `refine_history` がどう成長するか：

```python
# 初稿
compose_messages = build_compose_messages()    # system + few-shot + user
# stream で composition_raw を取得
refine_history = compose_messages + [{"role": "assistant", "content": composition}]

# refine ループ
while True:
    # validator で評価
    parsed = validator.parse_tanka_json(composition)
    result = validator.evaluate(...)

    if result.passed: break
    if plateau or hard_cap: break

    # critique を user メッセージとして追加
    refine_history.append({"role": "user", "content": (
        f"{critique}\n\n季語の宣言と本文の整合... JSON で再出力してください。"
        + _format_failure_history_block(failure_history)
    )})
    # 次の attempt
    async for delta in stream_completion(refine_history):
        ...
    refine_history.append({"role": "assistant", "content": composition})
```

つまり `refine_history` は **マルチターン会話そのもの** として LLM に渡されます：

```
[system]    TANKA_SYSTEM_PROMPT
[user]      few-shot 例 1...
[assistant] few-shot 例 1 出力
...
[user]      お題 + 構想 + 制約 + 失敗記憶 (validator 由来)
[assistant] 初稿                            ← attempt 0
[user]      critique (validator.format_critique)  ← validator 由来
[assistant] 再詠                            ← attempt 1
[user]      critique
[assistant] 再々詠                          ← attempt 2
...
```

### 重要: context は attempt 数に依存しないよう bound されている

**【更新: Phase 2 で対策済み】** かつては `refine_history` を ever-growing にしていたため、
attempt が増えると LM Studio の context を圧迫し、実際に Phase 2 の最初の eval 実行で
`"Context size has been exceeded."` 失敗 (zero-output) を観測した。

現在は **固定ベース + 直近 1 ラウンド方式** に変更済み:

```
各 refine の messages = compose_messages (system + few-shot + 初回 user)   ← 固定
                      + [{assistant: 直近の出力}, {user: 最新の critique}]  ← 1 ラウンドのみ
```

validator の critique が問題点を毎回伝えるので、過去全 attempt を保持する必要はない。
これで context は attempt 数に依存せずほぼ一定。self-critique フェーズも同じ方式。

**多層の安全網**:

- **bounded context**: 上記。根本対策
- **エラーフォールバック**: refine 中に LLM がコンテキスト超過等で失敗したら、
  `llm_error` イベントを出して **best-so-far を最終結果に採用** (zero-output を防ぐ)。
  self-critique の失敗も非致命的 (初稿で続行)
- **Plateau 検知**: 連続 3 試行で best_score が更新されなければ打ち切り → 通常 3〜6 attempt で収束
- **HARD_CAP = 50**: 暴走防止 (通常踏まない)

`_is_context_error()` がプロバイダ別のコンテキスト超過メッセージを広めに検出する。

---

## 6. 「触る前に知っておくべき」マップ

このプロジェクトのどこかを変更しようとしている人向けの早見表：

| やりたいこと | 触るファイル | 注意点 |
|---|---|---|
| **新しい検証ルールを追加** | `validator.py` の `RULES` リスト + `LESSONS` 辞書 | `Tanka` だけで判定可能なら RULES に。外部入力依存なら `evaluate()` に直書き |
| **季語辞書を拡張** | `app/backend/data/kigo.json` のみ | モジュールロード時に flat 化される。再起動で反映 |
| **critique の言い回しを変える** | `validator.format_critique` | LLM の refine 挙動が変わる可能性。動作確認必須 |
| **教訓のフレーズを変える** | `validator.LESSONS` 辞書 | 長期失敗記憶に流れる文言が変わる |
| **System プロンプトを書き換える** | `tanka.py` の `TANKA_SYSTEM_PROMPT` 定数 | **影響範囲が広い** (Plan / Compose / Refine 全てに影響)。慎重に |
| **Few-shot 例を増やす / 入れ替える** | `tanka.py` の `TANKA_COMPOSE_FEW_SHOT` リスト | 四季のバランスを保つ (一つ欠けるとそこに regress する傾向) |
| **失敗記憶の活用方法を変える** | `validator.format_long_term_failures` + 必要なら `db.recent_failures` | テキスト注入 → 本物の few-shot に変える等の拡張時 |
| **Plan-Compose の整合ルールを強化** | `validator._rule_*` 追加 + `evaluate()` の引数追加 | 8B モデルの slippage 対策の中核なので妥協しない |
| **refine ループの停止条件を変える** | `tanka.py` の `PLATEAU_WINDOW` / `HARD_CAP` + `evaluate` 後の break ロジック | Plateau 検知のテストケースが `validator.py` 単体テスト相当の重要性を持つ |

---

## 7. やってはいけないこと (アンチパターン)

過去にやって失敗した、あるいは設計上明確に避けるべきもの。

### 7.1 Validator に DB アクセスを追加する

副作用なしの原則を破ると、テストしにくく、呼び出し順序に依存するバグが入る。
DB アクセスは `tasks.py` か `db.py` 経由のみ。

### 7.2 System プロンプトに critique を流す

`TANKA_SYSTEM_PROMPT` を attempt ごとに書き換えるような実装はしない。
状態は messages 配列 (user/assistant の交互) で表現する。

### 7.3 raw_output (悪い JSON) をそのまま few-shot に注入する

8B モデルは "悪い例" を "お手本" と誤認するケースがある。
教訓フレーズに圧縮するか、もし生例を見せるなら critique と修正版をセットで提示する（=本物の few-shot にする）。

### 7.4 ルールごとの重み (weight) を独立に変えて済ます

スコアは重みの単純和。1 ルールだけ重くすると、他ルールが軽視される。
**`PASS_THRESHOLD = 80` との関係で全体バランスを取る** こと。

### 7.5 Plan 整合ルールを RULES リストに入れる

`(Tanka) -> list[Violation]` の単純シグネチャを壊すと、`evaluate` の責務が増えて分離が壊れる。
外部入力依存ルールは `evaluate` の引数経由で。

### 7.6 Few-shot を全部消して system プロンプトに集約する

system プロンプトでルールを言葉で説明しても、8B モデルは JSON 出力形式を頻繁に間違える。
**positive few-shot は四季 4 例を維持** すること（春・夏・秋・冬）。一つ欠けるとそこに regress する。

---

## 8. 拡張時の選択肢 (長期失敗記憶を強化するなら)

現在の "教訓注入" 方式の効果を上げたい場合の典型パターン：

### Option A: 失敗→修正ペアを本物の few-shot 化

失敗を記録する時に、最終的に合格した同じ task の出力もペアで保存。

```
[user]      過去のお題 / 構想
[assistant] 悪い試行 (JSON)
[user]      上記は kigo_unique 違反 (critique)
[assistant] 修正版 (合格 JSON)
[user]      ★ 今回のお題 / 構想
```

最も学習効果が高いが、実装は重い (DB スキーマ拡張 + few-shot ビルダ書き換え)。

### Option B: 失敗の生 JSON を短く見せる

`format_long_term_failures` を改修し、教訓 1 行に加えて `failed_excerpt: "1句目: 春霞む / 2句目: 桜散りてや"` のような **悪例の断片** を併記。具体性が上がる。

### Option C: ルール別の "再発防止" 集計

`failures` を rule ごとに count して、`"kigo_unique は過去 N 回違反した。今回特に注意"` のような頻度ベース強調を入れる。同じ穴に何度も落ちる場合のセーフティ。

### Option D: 季語 NG リスト (出力空間を絞る)

過去に複数回失敗した季語を **今回避ける** リストに変換し、system prompt ではなく user メッセージで強い指示として注入。

どのオプションでも、validator のルール群と LESSONS 辞書を起点に拡張するのが自然な実装パスです。

---

## 9. 一行サマリ (Tweet 用)

> Static system prompt (役割定義) × Dynamic user messages (状態) で対話を表現。
> Validator は pass/fail だけでなく **critique と教訓を生成する LLM フィードバック中枢**。
> 失敗記憶は few-shot ではなく **教訓フレーズの user メッセージ注入** として実装。
> Plan-Compose 整合は validator ルールで強制矯正、固定回数ではなく plateau 検知で停止。

---

## 10. 関連ドキュメント

- [`README.md`](../../README.md) — プロジェクト全体の入口
- [`CLAUDE.md`](../../CLAUDE.md) — Claude Code 用の引き継ぎノート（既知の罠など）
- [`app/docs/architecture.md`](architecture.md) — システム構造・データフロー・SSE プロトコル詳細
- [`app/README.md`](../README.md) — 起動・操作手順
