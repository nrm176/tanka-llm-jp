# モデル特性カタログ — 短歌生成パイプラインにおける各 LLM の強み・弱み

> 最終更新: 2026-06-14 / 測定: tanka-eval-ablation スキルの作法に準拠
> 関連: **[design-principle-verifier-in-the-loop.md](./design-principle-verifier-in-the-loop.md) (本書が実証する設計原則)** /
> [model-compat-qwen3-swallow.md](./model-compat-qwen3-swallow.md) /
> [thinking-runaway-postmortem.md](./thinking-runaway-postmortem.md) /
> `app/backend/eval/FINDINGS.md` §6 / CLAUDE.md §6.13〜6.16, §7

このパイプラインは LM Studio 上のローカル LLM を差し替え可能 (#15 グローバル切替 / #20 セッション固定)。
本書は手元で試した全モデルの特性を**指標に基づいて**集約し、用途別の選択指針を与える
プロジェクトの知財。**主観評価は載せない** — 数値とその出典のみ。

---

## 0. エビデンスの階層 (各数値の信頼度)

捏造と過剰一般化を避けるため、データを 4 層に分けて明示する。**層をまたいで横並び比較しない**。

| 層 | 内容 | n | 用途 |
|---|---|---|---|
| **A. 統制 eval** | `eval_themes_x12.json` (四季×3)、fresh load・切替検証・caffeinate 下 | 12 | **採否判断の根拠** |
| **B. 観測集計** | mongo 全履歴をモデル別集計 (混在お題・混在条件) | 可変 | 故障率 (zero 率) の頑健な signal |
| **C. スポット** | 単発 e2e 生成 | 1〜3 | 方向性のみ。**品質順位には使わない** (n=1 ノイズ ±19) |
| **D. プローブ** | content/reasoning/finish の直接観測 (生成成功なし) | — | **構造互換性の判定** |

ノイズ下限 (tanka-eval-ablation スキル): n=12 で平均差 **±5.6** 以内は「判定不能」。

---

## 1. 一覧表 (TL;DR)

| モデル | 互換 | 統制eval avg (n=12) | first-pass | zero率 (観測) | 速度(12題) | 一言 |
|---|---|---|---|---|---|---|
| **llm-jp-4-8b-thinking** ★既定 | ✓ | 77.2 | **75%** | 13/75 (**17%**) | ~50分 | 一発精度・名歌力は最高。だが thinking 暴走で稀に壊滅 |
| **google/gemma-3-27b** | ✓ | 80.0 | 42% | 0/13 | **~14分** | 高速・無事故だが拍数/季語が雑、validator 依存 |
| **openai/gpt-oss-20b** | ✓ | **84.5** | 8% | 0/3 | ~22分 | **最高avg・無事故だが素の実力は最低**。refine 巧者 |
| **google/gemma-4-e4b** | ✓? | — | — | 0/2 | — | 小型 (7.5B)。少数だが出力した。未測定 |
| **qwen3-swallow-30b-a3b-rl** | **✗** | — | 全失敗 | — | 構造的不適合 (§4)。`</think>` 不到達→content 空 |
| **google/gemma-4-26b-a4b-qat** | **✗** | — | 全失敗 | — | 同上。小タスクは可、compose で発散 |
| google/gemma-4-12b | ? | — | — | — | 未テスト |
| nvidia/nemotron-3-nano-omni | ? | — | — | — | 未テスト |

★ = 現行既定。✓互換 / ✗不適合 / ? 未確認。

### 統制 eval 3モデル横並び (n=12, `eval_themes_x12.json`、確定値)

| model | avg | first-pass | passN | attempts | 違反総数 | mora系 | kigo規律 | schema | 90-100 |
|---|---|---|---|---|---|---|---|---|---|
| llm-jp-4-8b-thinking | 77.2 | **75%** | 83% | **1.58** | **31** | **6** | **1** | 9 | 6 |
| gemma-3-27b | 80.0 | 42% | 83% | 3.00 | 179 | 88 | 57 | **0** | 3 |
| gpt-oss-20b | **84.5** | 8% | 83% | 3.42 | 207 | 135 | 12 | 1 | 6 |

### ★ 本campaign最大の発見: 「素の実力」と「最終品質」の順位が反転する

**avg_final_score の順位 (gpt-oss 84.5 > gemma 80.0 > llm-jp 77.2) は、
素の実力の順位 (llm-jp ≫ gemma > gpt-oss) を完全に反転させる。**

- 素の実力 (first-pass / mora 精度): **llm-jp が圧勝** (75%・mora違反6) ≫ gemma (42%・88) > gpt-oss (8%・135)
- 最終 avg: **gpt-oss が最高** (84.5) > gemma (80.0) > llm-jp (77.2)

**なぜ反転するか**: validator-guided best-of-N では最終品質を決めるのは「一発の上手さ」ではなく
**(a) refine critique への応答性** と **(b) カタストロフィック失敗を出さないこと** の 2 つ。
gpt-oss は最悪のドラフト (8% first-pass) を出すが**最高のリライター**で、3.4 試行かけて最高 avg に磨き上げる。
llm-jp は 75% を一発で決める最高のドラフターだが、**17% の確率で thinking 暴走の 0 点**を出し、
その壊滅尾が avg を押し下げる (zero を除く 10 首は avg ~92.6 で全モデル最高 = **質の天井は llm-jp**)。

**設計含意**: 外部検証器 + refine ループを持つ本パイプラインは、**素の実力が低いモデルでも
「応答性 × 無事故性」が高ければ高品質を出せる**。モデル選定をベンチマークの素の実力で行うと誤る。
逆に、validator が薄い領域 (拍数は規則化済みだが「詩情」は未規則化) ではこの補正は効かない —
**llm-jp の日本語特化の真価 (mora 1/20・名歌力) は、規則化されていない質の部分で効いている。**

---

## 2. 指標の読み方 (なぜ avg だけ見ると誤るか)

このパイプラインは **validator-guided best-of-N**。最終スコアは「何回試行したか」に依存して
底上げされるため、**avg_final_score の同点は『同じ品質』を意味しない**。必ず以下を併読する:

- **first_attempt_pass_rate**: モデルの「素の実力」。一発で規定を満たす率
- **avg_attempts**: validator への依存度。多いほど「自力で書けず叩き直されている」
- **violation 内訳** (全 attempt 横断): どのスキルが弱いか。特に
  - `mora_count` / `mora_count_off_by_one` = 拍数 (五七五七七) の精度
  - `no_other_kigo_*` / `kigo_in_dictionary` = 一句一季語の規律と季語語彙
  - `schema_invalid` = JSON を出せたか (= thinking 暴走の有無の代理指標)
- **zero 率** (観測層): カタストロフィック失敗 (schema_invalid で 0 点) の頻度。**1 つの 0 は曖昧さがない**ため
  観測データからでも頑健な signal

---

## 3. モデル別詳細

### 3.1 llm-jp-4-8b-thinking ★ 現行既定

- **型**: thinking、harmony マーカー込みで思考が `content` に流れる (アプリが split_harmony で分離)
- **統制 eval (n=12)**: avg **77.2** (別測定 cap-off でも 77.9 = **再現性あり**)、first-pass **75%**、
  passN 83.3%、attempts **1.58**、違反 31 (crit9/min22)、**mora 系 6 / kigo 規律 1**、90-100 点 **6/12**
- **観測 (n=75)**: avg 73.6、**zero 13 件 (17%)**。スコア分布は**双峰的** — 13 個の 0 と、90-100 の厚い山
- **強み**: 日本語特化ゆえ**拍数・季語が圧倒的に正確** (mora 違反は gemma の 1/15)。一発で決め、
  名歌 (90-100) の産出力が最高。refine への依存が最小
- **弱み**: **thinking 暴走** (拍数の自己検証ループ→`</think>` 不到達→空出力)。
  6 回に 1 回の頻度でカタストロフィック 0 点。これがパイプラインの主要リスクであり、
  #22/#23 の completion 上限 (8192) はこの暴走を fail-clean に bound する安全弁
- **総評**: 「精密だが時々壊れる職人」。質の天井が高く、既定にふさわしい。暴走は安全弁で緩和済み

### 3.2 google/gemma-3-27b

- **型**: **non-thinking** (gemma-**3** 世代は思考機構なし)。content 直行
- **統制 eval (n=12)**: avg 80.0、first-pass **41.7%**、passN 83.3%、attempts **3.0**、
  違反 **179** (crit56/maj6/min117)、**mora 系 88 / kigo 規律 57**、90-100 点 3/12、所要 **~14分**
- **観測 (n=13)**: avg 79.0、**zero 0**、スコアは 82-91 に密集 (天井 91、100 なし)
- **強み**: **構造的に無事故** (thinking 暴走が原理的に起きない)・**最速 (~3.5倍)**・JSON 安定
- **弱み**: **拍数が決定的に弱い** (mora 違反 88 = llm-jp の約15倍)、季重なり/辞書外季語が多い、
  本文にルビ括弧を書き込む癖。同じ合格率に**2倍の refine** を要し、validator に救われて
  80 点台に滑り込む。名歌は出にくい
- **総評**: 「速いが雑な書き手、検証器頼み」。avg の +2.83 はノイズ内で**品質的勝利ではない**

### 3.3 openai/gpt-oss-20b

- **型**: thinking (harmony in content)、**思考を確実に閉じる**ため content に最終 JSON が届く
- **統制 eval (n=12)**: avg **84.5 (3モデル最高)**、first-pass **8.3% (最低、1/12)**、passN 83.3%、
  attempts **3.42 (最多)**、違反 **207** (crit113/maj14/min80)、**mora 系 135 (最悪) / kigo 規律 12**、
  schema_invalid 1、90-100 点 **6/12 (llm-jp と同率トップ)**、所要 ~22分。
  前半89.3→後半79.7 (軽度低下 — 負荷劣化かテーマ難度差。結論は不変)
- **観測 (n=3)**: scores [67, 87, 94]、avg 82.7、**zero 0**。qwen で全滅した同一お題で 94 点を産出
- **強み**: **avg 最高かつカタストロフィック失敗ゼロ**・名歌 (90-100) を llm-jp と同数産出・構造互換が確実
- **弱み**: **素の実力は3モデル最低** (first-pass 8%、mora 違反 135)。**refine ループに全面依存**して
  高得点に到達する「最低のドラフト・最高のリライター」。1 首あたりの試行が最多 = latency 高
- **総評**: 高 avg は**素の実力ではなく無事故性 (信頼性) 由来**。llm-jp の質の天井 (zero 除く 10 首は
  avg ~92.6) には届かないが、**壊滅失敗を絶対に出さない**点で最も安定。avg +7.3 (vs llm-jp) は
  ノイズ下限 ±5.6 を超えるが単一 run なので、既定昇格には確認 run 推奨

### 3.4 不適合モデル (qwen3-swallow / gemma-4-26b)

- **qwen3-swallow-30b-a3b-rl-v0.2-mlx**: thinking RL 変種。compose プローブで
  **reasoning 17,004 / content 0 / finish=length**。`/no_think` も無効。全 attempt schema_invalid。
  詳細は [model-compat-qwen3-swallow.md](./model-compat-qwen3-swallow.md)
- **google/gemma-4-26b-a4b-qat**: gemma-4 は hybrid thinking。小タスクは完走するが、
  compose プローブで **reasoning 17,267 / content 0 / finish=length** と発散
- **共通機序**: LM Studio が思考を `reasoning_content` に分離 + 制約過多の compose が
  自己検証暴走を誘発 + `</think>` 不到達 → アプリが読む content が空のまま
- **教訓**: thinking 暴走の病理は**モデル非依存** (8B でも 30B でも再現)。規模では解決しない

### 3.5 未測定 (gemma-4-12b / gemma-4-e4b / nemotron-3-nano-omni)

- **gemma-4-e4b** (7.5B): モデル切替検証で n=2 [79, 87] を産出 (互換の可能性)。要正式測定
- **gemma-4-12b / nemotron-3-nano-omni**: 未テスト。gemma-4 系は 26b が不適合だったため
  12b も同型リスクあり。導入時は必ず §5 のプローブを先行させること

---

## 4. 互換性の構造的法則 (本プロジェクト最大の知財)

**互換性を決めるのはモデルの規模でも世代でもなく、「最終 JSON が `content` に届くか」の一点。**

```
互換の条件 = 以下のいずれか
  (a) non-thinking である (gemma-3) → content 直行
  (b) thinking だが、制約過多の compose で </think> を予算内に閉じる
      かつ 最終回答が content 側に出る (llm-jp=harmony in content / gpt-oss=think を閉じる)

不適合 = thinking が reasoning_content に分離され、かつ compose で </think> 不到達
         (qwen3-swallow / gemma-4-26b) → content 空 → 全 schema_invalid
```

理由: 短歌 compose プロンプト (5-7-5-7-7 + 一句一季語 + JSON 強制) は **thinking モデルに
拍数の自己検証ループを誘発する** (モデル非依存)。検証器を外部に持つ本設計では
「モデル内の思考検証」は丸ごと無駄であり、暴走するほど不利になる。

→ **新モデル導入の鉄則**: 規模やベンチマークで選ばず、必ず §5 のプローブで content 着地を確認する。

---

## 5. 新モデル導入チェックリスト

1. **明示ロード** (JIT の既定 context 8192 は thinking を即溢れさせる、CLAUDE.md §6.14):
   `lms load <model> --context-length 32768`
2. **互換プローブ** (本物の compose メッセージで判定。手順は model-compat-qwen3-swallow.md §6):
   `content > 0` なら互換、`content == 0 かつ finish == length` なら不適合 → 導入中止
3. **統制 eval** (採否は必ず A/B。スポット 1 回の主観で決めない):
   セッション固定 (#20) で `eval_themes_x12.json` を回し、llm-jp と比較。
   主指標 avg のノイズ下限 ±5.6、first-pass・violation 内訳・zero 率を併読
4. **記録**: FINDINGS.md に追記、本カタログの表を更新

---

## 6. 用途別 選択ガイド

| 状況 | 推奨 | 理由 |
|---|---|---|
| **既定 (質の天井・正確さ重視)** | llm-jp-4-8b-thinking | 名歌力最高・拍数/季語が正確 (mora 違反 1/20)。暴走 17% は #23 の弁で緩和 |
| **信頼性重視 (壊滅失敗を許さない)** | gpt-oss-20b | avg 最高 (84.5)・zero 0%・名歌も同数。要確認 run の上で既定昇格も検討可 |
| **速度優先・大量ドラフト** | gemma-3-27b | ~3.5倍速・無事故。質は 80 点台で妥協 |
| **新モデルを試す** | — | §5 のプローブ→統制 eval を必ず通す |

(非既定モデルは #20 のセッション固定で利用)

**結論**: 単一の万能モデルは無い。3 つの互換モデルは**故障モードが相補的**:
- **llm-jp** = 質の天井 (zero 除き ~92.6) と日本語精度は最高、代償は 17% の暴走
- **gpt-oss-20b** = 無事故・最高 avg、代償は素の実力の低さ (refine 全依存) と latency
- **gemma-3-27b** = 速度、代償は雑さ

避けるべき単純化: 「avg が高い = 良いモデル」(gpt-oss は素の実力最低)、
「ベンチマークが高い = このタスクで良い」(検証器の有無で順位が変わる)、
「大きい/新しい = 良い」(gemma-4-26b は不適合、§4)。**#20/#21 のセッション固定で使い分ける**のが最適解。
