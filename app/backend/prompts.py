"""プロンプト定数とメッセージ組み立て (純関数、副作用なし)。

tanka.py のパイプラインから呼ばれる。動的データ (お題・構想・各種注入ブロック) は
引数で受け取り、LLM に渡す messages 配列を組み立てて返す。
注入ブロック (RAG・長期失敗・短期失敗) は呼び出し側で整形済みの文字列として渡す
(それらは validator/rag/db に依存するため、葉モジュールであるここには持ち込まない)。"""

from __future__ import annotations

NORMAL_SYSTEM_PROMPT = (
    "あなたは知的で丁寧な日本語アシスタントです。状況に応じて適切な役割を担ってください。"
)

TANKA_SYSTEM_PROMPT = """あなたは熟練した歌人です。短歌の作法を熟知し、季語と情景を大切にして詠みます。

【短歌の作法】
- 一首には季語を必ず一つだけ用いる（一句一季語の原則）
- **宣言した季語以外の季語を本文に一切入れてはならない**。季違い（別の季の季語）はもちろん、
  同じ季の他の季語（季重なり）も禁止。例えば季語「蛍」と決めたら、本文に「月」「螢」以外の
  季語（桜・紅葉・雪・夕立 等）を入れないこと
- 一首は一つの情景・一つの心情に焦点を絞る
- 形式は五七五七七の三十一拍
- 一首において、宣言した季語は本文中に **ちょうど一回だけ** 出現する（再利用禁止）
- **お題が時間帯や場面を指定している場合 (例「夕暮れ」「朝」「夜」) は、それに忠実に詠む**。
  夕暮れのお題に朝の情景を詠む等、お題と矛盾する時刻・場面にしないこと

【思考の順序（必ずこの順で思考すること）】
1. お題から喚起される一つの季節を選ぶ
2. その季節を象徴する季語を一つだけ選ぶ
3. 季語を中心とした一つの情景を描く
4. その情景に伴う一つの心情を定める
5. 上記を踏まえて五七五七七で詠む

拍数の最終検証はプログラム側で行う。あなたは拍数の試行錯誤に時間を割かず、季語・情景・心情の選定に集中すること。

【作歌時の出力形式】
短歌を詠む際は **必ず JSON 形式** で出力する。説明文・前置き・コードフェンスは付けず、
{ から } までで完結する 1 オブジェクトのみを出す。スキーマは以下:

{
  "kigo": "用いた季語 (本文中にちょうど 1 回出現する文字列)",
  "season": "春 | 夏 | 秋 | 冬 | 新年 | 雑",
  "lines": [
    {"body": "1 句目 (漢字・かな混じり)", "reading": "1 句目の全ひらがな読み"},
    {"body": "2 句目", "reading": "2 句目の読み"},
    {"body": "3 句目", "reading": "3 句目の読み"},
    {"body": "4 句目", "reading": "4 句目の読み"},
    {"body": "5 句目", "reading": "5 句目の読み"}
  ],
  "image": "一文の情景描写",
  "emotion": "一文の心情描写"
}

キー名は `kigo` / `season` / `lines` / `image` / `emotion` の 5 つを**一字一句そのまま**使う。
各句の "reading" は漢字を全てひらがなに展開した正確な読みにする。
reading に空白・改行・句読点を含めないこと。**文字列の見た目の自己検証は不要** —
読みと拍数の照合はプログラム側で機械的に行うので、あなたは検証を繰り返さないこと。

【検証の仕組み】
出力後、プログラムが構造・拍数・季語の唯一性・季違い等を機械的に採点する。
100 点満点から減点され、80 点以上で合格。違反があった場合はあなたに critique が返るので、
スコアを上げるよう書き直すこと。同じ違反を繰り返さないこと。

【質問への応答】
過去に詠んだ短歌について質問されたときは、歌人の視点から構造（拍数・句切れ・季語）、用いた言葉、情景、感情の機微を説明する。
ビジネスや実務への応用といった解釈は一切しない。純粋に詩・文学として論じる。

【思考言語】
思考も日本語で行うこと。"""

# 四季全ペアを並べたフォールバック用 few-shot (季節不明・雑、または動的選択 OFF 時)。
# 動的選択 (select_few_shot) は通常ここから「お題の季の 1 ペア」だけを抜き出して使う。
# 新年は専用ペア (TANKA_COMPOSE_FEW_SHOT_NEW_YEAR) を別定数で持ち、このリストには含めない
# (dynamic=OFF の A/B 統制群と季不明/雑フォールバックを従来の「四季全部」のまま保つため)。
# 横断分析で「四季を全部見せても非春 plan の 37% が春へ regress、写し元は #1=散る桜 のみ」と
# 判明したため、季が分かる場合は他季 (特に春) の磁石を隠すのが既定挙動。
# 並び順 (春→夏→秋→冬) は FEW_SHOT_BY_SEASON のスライスが依存するので変えないこと。
# 構想セクションは実際の Plan 出力と同じインデントなしフォーマットに揃える。
TANKA_COMPOSE_FEW_SHOT: list[dict] = [
    {"role": "user", "content": (
        "お題: 散る桜\n"
        "構想:\n"
        "季語: 花\n"
        "季節: 春\n"
        "情景: 春のうららかな日に、桜が静かに散ってゆく\n"
        "心情: 花の散り急ぐ様への憐れみと、自然の理への諦観\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "花", "season": "春", '
        '"lines": ['
        '{"body": "ひさかたの", "reading": "ひさかたの"}, '
        '{"body": "光のどけき", "reading": "ひかりのどけき"}, '
        '{"body": "春の日に", "reading": "はるのひに"}, '
        '{"body": "しづ心なく", "reading": "しづこころなく"}, '
        '{"body": "花の散るらむ", "reading": "はなのちるらむ"}'
        '], '
        '"image": "春のうららかな日に、桜が静かに散ってゆく", '
        '"emotion": "花の散り急ぐ様への憐れみと、自然の理への諦観"}'
    )},
    {"role": "user", "content": (
        "お題: 夏の夜\n"
        "構想:\n"
        "季語: 夏の夜\n"
        "季節: 夏\n"
        "情景: 短い夏の夜、まだ宵のうちに明けてしまい、月はどこに宿るのか\n"
        "心情: 過ぎゆく夏の夜への惜別の念\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "夏の夜", "season": "夏", '
        '"lines": ['
        '{"body": "夏の夜は", "reading": "なつのよは"}, '
        '{"body": "まだ宵ながら", "reading": "まだよひながら"}, '
        '{"body": "明けぬるを", "reading": "あけぬるを"}, '
        '{"body": "雲のいづこに", "reading": "くものいづこに"}, '
        '{"body": "月宿るらむ", "reading": "つきやどるらむ"}'
        '], '
        '"image": "短い夏の夜、まだ宵のうちに夜が明けてしまう", '
        '"emotion": "過ぎゆく夏の夜への惜別の念"}'
    )},
    {"role": "user", "content": (
        "お題: 秋風\n"
        "構想:\n"
        "季語: 秋風\n"
        "季節: 秋\n"
        "情景: 目には見えない秋の到来を、風の音から感じ取る瞬間\n"
        "心情: 季節の移ろいへの繊細な驚きと感慨\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "秋風", "season": "秋", '
        '"lines": ['
        '{"body": "秋来ぬと", "reading": "あききぬと"}, '
        '{"body": "目にはさやかに", "reading": "めにはさやかに"}, '
        '{"body": "見えねども", "reading": "みえねども"}, '
        '{"body": "風の音にぞ", "reading": "かぜのおとにぞ"}, '
        '{"body": "おどろかれぬる", "reading": "おどろかれぬる"}'
        '], '
        '"image": "目には見えない秋の到来を、風の音から感じ取る瞬間", '
        '"emotion": "季節の移ろいへの繊細な驚きと感慨"}'
    )},
    {"role": "user", "content": (
        "お題: 冬の寂しさ\n"
        "構想:\n"
        "季語: 冬\n"
        "季節: 冬\n"
        "情景: 山里に冬が深まり、人の気配も草木も消えてゆく\n"
        "心情: 寂寥のなかに見出す静かな受容\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "冬", "season": "冬", '
        '"lines": ['
        '{"body": "山里は", "reading": "やまざとは"}, '
        '{"body": "冬ぞさびしさ", "reading": "ふゆぞさびしさ"}, '
        '{"body": "まさりける", "reading": "まさりける"}, '
        '{"body": "人目も草も", "reading": "ひとめもくさも"}, '
        '{"body": "かれぬと思へば", "reading": "かれぬとおもへば"}'
        '], '
        '"image": "山里に冬が深まり、人の気配も草木も消えてゆく", '
        '"emotion": "寂寥のなかに見出す静かな受容"}'
    )},
]

# 新年専用ペア (動的選択でのみ使用、フォールバックには含めない — 上のコメント参照)。
# 新年 plan が四季フォールバックに落ちると、ドリフト対策前と同じ「春磁石 #1 露出」プロンプトに
# 戻ってしまう (新年は新春・初春と春に隣接するため引力はむしろ強い) のを塞ぐ (issue #11)。
# 例は万葉集 20-4493 大伴家持 (PD)。kigo=初春 は辞書 new_year 登録語で、本文に他季の辞書季語なし
# (validator score 94。より有名な 20-4516「新しき年の…」は 雪=冬 を含み no_other_kigo に
# 当たるため不採用)。
TANKA_COMPOSE_FEW_SHOT_NEW_YEAR: list[dict] = [
    {"role": "user", "content": (
        "お題: 新年の祝い\n"
        "構想:\n"
        "季語: 初春\n"
        "季節: 新年\n"
        "情景: 初子の日の宴で玉箒を手に取ると、飾りの玉が揺れて鳴る\n"
        "心情: 新しい年を寿ぐ晴れやかな祝意\n\n"
        "JSON 形式で短歌を出力してください。構想で決めた kigo と season を絶対に変えないこと。"
    )},
    {"role": "assistant", "content": (
        '{"kigo": "初春", "season": "新年", '
        '"lines": ['
        '{"body": "初春の", "reading": "はつはるの"}, '
        '{"body": "初子の今日の", "reading": "はつねのけふの"}, '
        '{"body": "玉箒", "reading": "たまばはき"}, '
        '{"body": "手に取るからに", "reading": "てにとるからに"}, '
        '{"body": "揺らく玉の緒", "reading": "ゆらくたまのを"}'
        '], '
        '"image": "初子の日の宴で玉箒を手に取ると、飾りの玉が揺れて鳴る", '
        '"emotion": "新しい年を寿ぐ晴れやかな祝意"}'
    )},
]

# 季節 → その季の few-shot ペア (user/assistant)。動的選択で「お題の季の例だけ」を見せ、
# 他季の磁石 (特に #1 散る桜=春) を隠して Plan→Compose の季ドリフトを抑えるための索引。
# スライスは TANKA_COMPOSE_FEW_SHOT の並び順 (春→夏→秋→冬) に依存する。
FEW_SHOT_BY_SEASON: dict[str, list[dict]] = {
    "春": TANKA_COMPOSE_FEW_SHOT[0:2],
    "夏": TANKA_COMPOSE_FEW_SHOT[2:4],
    "秋": TANKA_COMPOSE_FEW_SHOT[4:6],
    "冬": TANKA_COMPOSE_FEW_SHOT[6:8],
    "新年": TANKA_COMPOSE_FEW_SHOT_NEW_YEAR,
}


def select_few_shot(season_hint: str | None, *, dynamic: bool) -> list[dict]:
    """compose に注入する few-shot を選ぶ (純関数)。

    dynamic=True かつ season_hint が五季 (春夏秋冬・新年) のいずれかなら、その季の 1 ペアのみを
    返す (他季 — 特に春の散る桜 — の磁石を隠す)。それ以外 (dynamic=False / 季不明 / 雑) は
    四季全ペアを返す (従来挙動)。雑は専用例を validator が現状サポートしない (kigo 必須) ため
    意図的に未対応 (issue #11 スコープ外)。"""
    if dynamic and season_hint in FEW_SHOT_BY_SEASON:
        return FEW_SHOT_BY_SEASON[season_hint]
    return TANKA_COMPOSE_FEW_SHOT


# 自己点検フェーズで投げる固定の指示文 (Phase 1 B4)
SELF_CRITIQUE_USER = (
    "上記の短歌について自己点検してください。次の観点を確認し、問題があれば修正版を JSON で、"
    "問題なければ同じ JSON を JSON 形式でそのまま出力してください (前後の説明は付けない)。\n\n"
    "1. kigo フィールドで宣言した語が、本文 (lines.body) のどこかにちょうど 1 回だけ出現しているか\n"
    "2. 宣言外の他の季の季語が混在していないか\n"
    "3. 各句の拍数が 5-7-5-7-7 になっているか (拗音は 1 拍、促音/撥音/長音は各 1 拍)\n"
    "4. 構想で決めた kigo と season を維持しているか\n"
    "5. 切れ字や体言止めで余韻が生まれているか"
)


def format_failure_history_block(history: list[str]) -> str:
    """同一生成内の短期失敗履歴を refine prompt 用ブロックに整形。"""
    if not history:
        return ""
    lines = ["", "【過去の試行で起きた違反 (繰り返さないこと)】"]
    for i, summary in enumerate(history, 1):
        lines.append(f"  試行 {i}: {summary}")
    lines.append("")
    return "\n".join(lines)


def build_plan_messages(theme: str) -> list[dict]:
    return [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"お題: {theme}\n\n"
            "このお題で詠む短歌の構想を立ててください。短歌本体はまだ書かないでください。\n"
            "以下の形式で構想だけを出力してください:\n"
            "季語: (一つだけ、一首中にちょうど一回出る語にすること)\n"
            "季節: (季語に対応する一つの季節)\n"
            "情景: (一つの場面を一文で)\n"
            "心情: (一つの感情を一文で)"
        )},
    ]


def build_plan_draft_messages(theme: str, kigo: str | None = None, season: str | None = None,
                              n_candidates: int = 3) -> list[dict]:
    """構想の下書き (#63): 人がレビュー・加筆するための情景候補 + 心情 + 背景を JSON で出させる。

    既存の build_plan_messages (自動経路、自由文 1 案) とは独立した変種で、自動経路のプロンプトは触らない
    (eval の比較可能性を守る)。季語を渡すと固定し、季節だけならその季の季語を選ばせ、どちらも無ければ
    お題から選ばせる。背景 (background) は人が読む素材で、compose には渡さない前提 (長文注入は context を
    圧迫し、8B は一首に全部詰め込もうとして「一情景一心情」が崩れる)。system は静的定数のまま。"""
    if kigo:
        constraint = (
            f"季語: {kigo} (季節: {season or '季語に対応する季節'})\n"
            f"この季語を必ず用い、別の季語に変更しないこと。\n\n"
        )
        kigo_label = f"「{kigo}」"
    elif season:
        constraint = f"季節: {season}\nこの季節の季語を一つだけ選ぶこと。\n\n"
        kigo_label = "選んだ季語"
    else:
        constraint = ""
        kigo_label = "選んだ季語"
    return [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"お題: {theme}\n"
            + constraint +
            "このお題で詠む短歌の構想を下書きしてください。短歌本体はまだ書かないでください。\n"
            f"- 情景の候補を {n_candidates} 案。各案は一つの場面を一文で描き、互いに視点や景物を変えること\n"
            f"- 情景候補の文には、{kigo_label}以外の季語 (別の季の景物はもちろん、同じ季の他の季語も) を入れないこと\n"
            "- 心情は一つの感情を一文で\n"
            "- 背景 (background) は 2〜3 文。お題と季語からどんな場面・時刻・人の気配を想定したかを書く "
            "(人が読むための素材で、短歌本文には直接入れない)\n\n"
            "JSON 形式のみで出力してください (前後に説明を付けない):\n"
            '{"kigo": "用いる季語", "season": "春|夏|秋|冬|新年", '
            '"image_candidates": ["情景 1", "情景 2"], "emotion": "一文の心情", "background": "背景 2〜3 文"}'
        )},
    ]


def format_manual_plan(mp: dict) -> str:
    """人間が指定した構想を、LLM Plan フェーズと同一のテキスト形式に整形する (純関数)。
    build_compose_messages / extract_season_from_plan がそのまま流用できる形にする。"""
    return (
        f"季語: {mp['kigo']}\n"
        f"季節: {mp['season']}\n"
        f"情景: {mp['image']}\n"
        f"心情: {mp['emotion']}"
    )


def build_plan_constraint(season_hint: str | None, kigo_hint: str | None) -> str:
    """Plan→Compose の slippage を防ぐ強調文。"""
    if not (season_hint or kigo_hint):
        return ""
    parts = []
    if kigo_hint:
        parts.append(f'kigo は必ず "{kigo_hint}"')
    if season_hint:
        parts.append(f'season は必ず "{season_hint}"')
    return (
        f"\n【重要・必須】出力 JSON では {' / '.join(parts)} とすること。"
        "構想で決めた季節や季語を勝手に別の季のものに変更してはならない。\n"
    )


def build_compose_messages(
    theme: str,
    plan: str,
    *,
    plan_constraint: str = "",
    rag_block: str = "",
    long_term_block: str = "",
    failure_block: str = "",
    season_hint: str | None = None,
    dynamic_fewshot: bool = False,
) -> list[dict]:
    """初稿生成用メッセージ。各注入ブロックは整形済み文字列を受け取る。
    self-critique / refine はこの戻り値をベース (固定文脈) に直近 1 ラウンドだけ足す。
    dynamic_fewshot=True なら season_hint の季の few-shot のみを見せる (春磁石対策)。"""
    few_shot = select_few_shot(season_hint, dynamic=dynamic_fewshot)
    return [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        *few_shot,
        {"role": "user", "content": (
            f"お題: {theme}\n"
            f"構想:\n{plan}"
            + plan_constraint
            + rag_block
            + long_term_block
            + failure_block
            + "\nJSON 形式で短歌を出力してください (前後に説明は付けない)。"
            "構想で決めた kigo と season を絶対に変えないこと。"
        )},
    ]


def build_refine_user(critique: str, failure_block: str = "") -> str:
    return (
        f"{critique}\n\n"
        f"季語の宣言と本文の整合 (kigo フィールドの語が本文中にちょうど 1 回登場すること)、"
        f"季違いの回避、拍数 (5-7-5-7-7) を守ったうえで、JSON 形式で再出力してください。"
        f"{failure_block}"
    )
