# モデル互換性インシデント: qwen3-swallow 切替で「JSON オブジェクトが見つかりません」が頻発

> 日付: 2026-06-11 / 関連: issue [#28](https://github.com/nrm176/tanka-llm-jp/issues/28)
> 対象モデル: `qwen3-swallow-30b-a3b-rl-v0.2-mlx` (tocchitocchi 氏の MLX 4bit 変換、Qwen3-30B-A3B thinking-RL 系)
> 背景ドキュメント: [thinking-runaway-postmortem.md](./thinking-runaway-postmortem.md) / CLAUDE.md §6.14〜6.16

---

## TL;DR

UI からモデルを qwen3-swallow (thinking RL 変種) に切替えたところ、全生成が
`schema_invalid`「JSON オブジェクトが見つかりません」で失敗した。

確定原因は **3 つの要因の複合**で、主因は「**短歌 compose プロンプトが thinking モデルに
自己検証の暴走を誘発し、モデルが `</think>` を閉じる前に予算を使い切る**」こと。
llm-jp 8B で観測した病理 (postmortem 参照) は **モデル非依存のタスク特性**であり、
30B でも同様に再現した。**この変種は本パイプラインに構造的に不適合**であり、
推奨は non-thinking (Instruct) 変種への乗り換えである (詳細は §5)。

---

## 1. 症状

- qwen 切替後の短歌生成が全 attempt `schema_invalid` (score 0) で失敗、短歌が出ない
- エラーやタイムアウトは出ない (タスクは "completed" で終わる)
- LM Studio のチャットモードでは同モデルが正常に応答する

## 2. 調査の足跡 (証拠チェーン)

| # | 観測 | わかったこと |
|---|---|---|
| 1 | failures コレクションの qwen 失敗の **raw_output が全件 0 バイト** | JSON が「壊れている」のではなく**何も届いていない**。think タグや JSON 抽出以前の問題 |
| 2 | `lms ps` → **CONTEXT 8192** | JIT ロード罠 (§6.14) を踏んでいた |
| 3 | 小プロンプトの直接プローブ → `content` に正常な JSON、思考は **`reasoning_content`** に分離 | LM Studio は Qwen3 の `<think>` を reasoning_content へ分離する。パイプラインと qwen は基本互換 |
| 4 | `max_tokens=80` プローブ → content 0 / reasoning 295 / **finish_reason=length** | 「予算を think 内で使い切ると content が空のまま正常終了する」機構を再現 |
| 5 | context を 32768 で明示ロードして本番再実行 → **それでも raw 0 バイト** | context は十分条件ではなかった (当初診断の上方修正点) |
| 6 | **本物の compose メッセージ**で直接プローブ → **reasoning 17,004 文字 / content 0 / finish=length**。reasoning 末尾は拍数の数え直しループ | 主因確定: タスク誘発の **thinking 暴走** (llm-jp と同一の病理)。`</think>` に到達しない |
| 7 | `/no_think` ソフトスイッチ付きで再プローブ → **無視される** (reasoning 8,624 / content 0) | この RL 変種 (Thinking-2507 系統) は thinking を無効化できない |

## 3. 確定診断

```
短歌 compose プロンプト (5-7-5-7-7 + 季語制約 + JSON 指定)
  → thinking モデルが拍数の自己検証ループに陥る (モデル非依存の病理)
  → どんな実用的予算でも </think> に到達しない
  → LM Studio は思考を reasoning_content に分離 (アプリは content のみ読む — 正しい挙動)
  → content が一度も始まらないまま finish_reason=length で正常終了
  → 空文字列 → parse_tanka_json 失敗 →「JSON オブジェクトが見つかりません」
  → refine も同条件で空 → 全 attempt 失敗 (エラーは一切出ない)
```

- **context 8192 (JIT 罠) は「失敗を早める」副因**。32k 化は必要だが十分ではない
- **チャットモードで動く理由**: 雑談はこのスパイラルを誘発しない。複雑な制約検証タスク固有の現象
- **tokenizer 警告 (`fix_mistral_regex`) は本件と無関係**: Mistral-Small 3.1 の設定から
  コミュニティ変換に伝播した既知の pre-tokenizer 正規表現バグ。LM Studio (mlx-lm) からは
  修正フラグを設定できない。影響は「トークン分割の不正確さ = 生成品質の劣化リスク」であり、
  空出力の原因ではない (プローブで正常応答を確認済み)

### 「どのモデルでも出力の扱いは同じ」という前提の破れ目 (一般化)

パイプラインの処理コードは全モデル同一だが、以下の 3 点はモデル依存:

1. **サーバ側ロード設定** — context はモデル毎。JIT ロードは既定 8192 + TTL 1h (§6.14)
2. **思考の流れ方** — llm-jp: harmony マーカー込みで `content` に流れる (アプリが分離) /
   Qwen3 系: LM Studio が `reasoning_content` に分離 (アプリには最終回答のみ届く)。
   副作用として **qwen では UI の思考表示が空になる** (機能上は無害)
3. **タスク × モデルの相互作用** — このタスクが各モデルの thinking をどれだけ暴走させるかは
   モデルの規模では解決しない (8B でも 30B でも再現)

## 4. 各要因の判定まとめ

| 要因 | 判定 | 対処 |
|---|---|---|
| thinking 暴走 (タスク誘発・`</think>` 不到達) | **主因** | §5-1, §5-2 |
| LM Studio の reasoning_content 分離 | 機構の一部 (それ自体は正常) | §5-3 (表示改善) |
| JIT ロード context 8192 | 副因 (失敗を早める) | §6.14 の明示ロード手順 |
| tokenizer regex バグ (fix_mistral_regex 警告) | 無関係 (品質懸念のみ) | §5-4 |
| `/no_think` ソフトスイッチ | この変種では無効 | — |

## 5. 推奨される選択肢 (優先順)

1. **non-thinking (Instruct) 変種への乗り換え【推奨】**
   本パイプラインは検証器 (validator) を外部に持つ設計であり、「早く出させて機械に直させる」が
   正しい分業。モデル内 thinking の自己検証は丸ごと無駄になる (postmortem の教訓 2)。
   Swallow 系を使うなら Instruct ベースの変種を選ぶこと。
   採用判断は必ず eval ハーネスの A/B で行う (チャット 1 回の主観で判断しない)。

2. **completion 上限 (#22/#23, `TANKA_MAX_COMPLETION_TOKENS=8192`) を有効に保つ**
   本件のような暴走を 8192 tokens で fail-clean に打ち切る安全弁。生成自体は通らないが、
   「数分 × refine 回数の静かな浪費」が止まり、規定未達として可視化される。
   ※ このインシデントは #23 の価値の追加実証になった (発生時は未マージで上限なし)

3. **`reasoning_content` 対応 (改善案・未実装)**
   llm.py で `delta.reasoning_content` も読み、(a) UI の思考表示へ流す (qwen 系で思考が
   見えない問題の解消)、(b) content が空のとき reasoning 末尾から JSON を救済パースする。
   ※ (b) は本件のような「think が閉じない」ケースには効かない (JSON 自体が存在しない) が、
   think を閉じた後に content を出し損ねる系のモデルでは効く

4. **tokenizer 警告への対処 (このモデルを本採用する場合のみ)**
   修正済み tokenizer の変換 か GGUF 版へ乗り換える。`fix_mistral_regex=True` は
   transformers のフラグであり LM Studio からは設定不可。日本語の拍数感覚が品質に直結する
   本タスクでは無視しないこと

5. **運用ルールの徹底 (再発防止)**
   モデル切替後の初回生成前に `lms ps` で CONTEXT を確認し、必要なら
   `lms load <model> --context-length 32768` で明示ロード (§6.14)。
   将来的には切替 API 側での自動プローブ/明示ロード連携 (issue #15 のスコープ外項目) が根治策

## 6. 検証に使ったプローブ (再現手順)

```bash
# 本物の compose メッセージで content / reasoning_content / finish_reason を観測する
cd app/backend && uv run python - <<'PY'
import prompts
from openai import OpenAI
c = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio", timeout=380)
plan = "季語: 夕立\n季節: 夏\n情景: ...\n心情: ..."
msgs = prompts.build_compose_messages("お題", plan, plan_constraint=prompts.build_plan_constraint("夏", "夕立"),
                                      season_hint="夏", dynamic_fewshot=True)
stream = c.chat.completions.create(model="<model>", messages=msgs, stream=True, max_tokens=8192)
content, reasoning, finish = "", "", None
for ch in stream:
    d = ch.choices[0].delta
    content += (d.content or "")
    reasoning += (d.model_extra or {}).get("reasoning_content") or ""
    finish = ch.choices[0].finish_reason or finish
print(len(content), len(reasoning), finish)  # 健全なら content > 0
PY
```

判定基準: `content == 0 かつ finish == "length"` なら think 不到達型の不適合。
`content > 0` なら互換 (UI の思考表示だけが空になる)。
