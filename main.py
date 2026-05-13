import logging
import re
import sys

from openai import OpenAI
from pykakasi import kakasi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("tanka")

_kks = kakasi()


def kanji_to_hira(text: str) -> str:
    """漢字を含むテキストを全てひらがなに変換する。モデル提供の読みに依存しない正規化用。"""
    return "".join(item["hira"] for item in _kks.convert(text))

client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio"
)

MODEL = "llm-jp-4-8b-thinking"

messages = [
    {
        "role": "system",
        "content": "あなたは知的で丁寧な日本語アシスタントです。状況に応じて適切な役割を担ってください。"
    }
]

HARMONY_FINAL_MARKER = "<|channel|>final<|message|>"
SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]+\|>")

TANKA_PREFIX_RE = re.compile(r"^tanka\s*[:：]\s*", re.IGNORECASE)

# 拗音などの「前のモーラに付随する小書き仮名」。促音(っ/ッ)はそれ自体が1拍なので含めない。
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")

# 読みは「漢字本体（ひらがな読み）」のパターン。全角・半角の括弧、読みに含まれる仮名(ひらがな・カタカナ・長音記号)を許容。
TANKA_LINE_RE = re.compile(
    r"([^\n()（）]+?)\s*[（(]\s*([぀-ヿー]+)\s*[）)]"
)

TANKA_SYSTEM_PROMPT = """あなたは熟練した歌人です。短歌の作法を熟知し、季語と情景を大切にして詠みます。

【短歌の作法】
- 一首には季語を一つだけ用いる（一句一季語の原則）
- 異なる季節の季語を混在させない（季違い・季重なりは厳禁）
- 一首は一つの情景・一つの心情に焦点を絞る
- 形式は五七五七七の三十一拍

【思考の順序（必ずこの順で思考すること）】
1. お題から喚起される一つの季節を選ぶ
2. その季節を象徴する季語を一つだけ選ぶ
3. 季語を中心とした一つの情景を描く
4. その情景に伴う一つの心情を定める
5. 上記を踏まえて五七五七七で詠む

拍数の最終検証はプログラム側で行う。あなたは拍数の試行錯誤に時間を割かず、季語・情景・心情の選定に集中すること。

【作歌時の出力形式】
短歌を詠む際は、各句の末尾に必ず（ひらがな読み）を付ける。漢字の読みは正確に。
例：
　花の色は（はなのいろは）
　うつりにけりな（うつりにけりな）
　いたづらに（いたづらに）
　わが身世にふる（わがみよにふる）
　ながめせしまに（ながめせしまに）

【質問への応答】
過去に詠んだ短歌について質問されたときは、歌人の視点から構造（拍数・句切れ・季語）、用いた言葉、情景、感情の機微を説明する。
ビジネスや実務への応用といった解釈は一切しない。純粋に詩・文学として論じる。

【思考言語】
思考も日本語で行うこと。"""

# 作歌(Compose)用の few-shot。お題＋構想 → 読み付き短歌、の形式。
TANKA_COMPOSE_FEW_SHOT = [
    {"role": "user", "content": (
        "お題：散る桜\n\n"
        "以下の構想に基づいて短歌を一首詠んでください：\n"
        "季語：花\n"
        "季節：春\n"
        "情景：春のうららかな日に、桜が静かに散ってゆく\n"
        "心情：花の散り急ぐ様への憐れみと、自然の理への諦観\n\n"
        "短歌本体（五句）のみを、各句の末尾に（ひらがな読み）を付けて出力してください。"
    )},
    {"role": "assistant", "content": (
        "ひさかたの（ひさかたの）\n"
        "光のどけき（ひかりのどけき）\n"
        "春の日に（はるのひに）\n"
        "しづ心なく（しづこころなく）\n"
        "花の散るらむ（はなのちるらむ）"
    )},
    {"role": "user", "content": (
        "お題：秋風\n\n"
        "以下の構想に基づいて短歌を一首詠んでください：\n"
        "季語：秋風\n"
        "季節：秋\n"
        "情景：目には見えない秋の到来を、風の音から感じ取る瞬間\n"
        "心情：季節の移ろいへの繊細な驚きと感慨\n\n"
        "短歌本体（五句）のみを、各句の末尾に（ひらがな読み）を付けて出力してください。"
    )},
    {"role": "assistant", "content": (
        "秋来ぬと（あききぬと）\n"
        "目にはさやかに（めにはさやかに）\n"
        "見えねども（みえねども）\n"
        "風の音にぞ（かぜのおとにぞ）\n"
        "おどろかれぬる（おどろかれぬる）"
    )},
    {"role": "user", "content": (
        "お題：冬の寂しさ\n\n"
        "以下の構想に基づいて短歌を一首詠んでください：\n"
        "季語：冬\n"
        "季節：冬\n"
        "情景：山里に冬が深まり、人の気配も草木も消えてゆく\n"
        "心情：寂寥のなかに見出す静かな受容\n\n"
        "短歌本体（五句）のみを、各句の末尾に（ひらがな読み）を付けて出力してください。"
    )},
    {"role": "assistant", "content": (
        "山里は（やまざとは）\n"
        "冬ぞさびしさ（ふゆぞさびしさ）\n"
        "まさりける（まさりける）\n"
        "人目も草も（ひとめもくさも）\n"
        "かれぬと思へば（かれぬとおもへば）"
    )},
]


def split_harmony(content: str) -> tuple[str | None, str]:
    if HARMONY_FINAL_MARKER not in content:
        return None, content.strip()
    reasoning, _, answer = content.partition(HARMONY_FINAL_MARKER)
    reasoning = SPECIAL_TOKEN_RE.sub("", reasoning).strip()
    answer = SPECIAL_TOKEN_RE.sub("", answer).strip()
    return reasoning or None, answer


def count_moras(kana: str) -> int:
    """ひらがな・カタカナ・長音記号からなる文字列のモーラ数を数える。
    拗音の小書き仮名(ぁゃゅょ等)は前のモーラに吸収されるためカウント外。
    促音(っ)・撥音(ん)・長音(ー)はそれぞれ1拍。"""
    cleaned = re.sub(r"[\s、。「」・]", "", kana)
    return sum(1 for ch in cleaned if ch not in SMALL_KANA)


def parse_tanka(text: str) -> list[tuple[str, str]] | None:
    """出力テキストから (本文句, ひらがな読み) を5組抽出する。失敗時は None。"""
    matches = TANKA_LINE_RE.findall(text)
    if len(matches) != 5:
        return None
    return [(body.strip(), reading.strip()) for body, reading in matches]


def verify_tanka(parsed: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """漢字本体から pykakasi で独立に正規読みを生成し、拍数を検証する。
    モデル提供の読み（カッコ内）はあくまで表示用で、検証には使わない（モデルが
    短い読みを提示してバリデータをごまかす攻撃を防ぐため）。
    返り値: (errors, warnings)
      errors   = 拍数違反。再詠ループのトリガー。
      warnings = 形式は満たすがモデル読みと正規読みが食い違うケース（参考表示用）。"""
    expected = [5, 7, 5, 7, 7]
    errors = []
    warnings = []
    for i, (body, model_reading) in enumerate(parsed):
        canonical_reading = kanji_to_hira(body)
        canonical_count = count_moras(canonical_reading)
        model_count = count_moras(model_reading)

        if canonical_count != expected[i]:
            mismatch_note = ""
            if model_count != canonical_count:
                mismatch_note = f"（モデル提供読み「{model_reading}」は{model_count}拍と主張）"
            errors.append(
                f"{i+1}句目「{body}」は実際は{canonical_count}拍"
                f"（正規読み：{canonical_reading}）。{expected[i]}拍に整えてください。{mismatch_note}"
            )
        elif canonical_count != model_count:
            warnings.append(
                f"{i+1}句目: モデル提供読み「{model_reading}」({model_count}拍) と"
                f"正規読み「{canonical_reading}」({canonical_count}拍) が不一致"
            )
    return errors, warnings


def render_clean_tanka(parsed: list[tuple[str, str]]) -> str:
    return "\n".join(body for body, _ in parsed)


def stream_chat(request_messages: list[dict], temperature: float = 0.3, header: str | None = None) -> tuple[str | None, str]:
    """1回のチャット呼び出しをストリーミングで実行し、(reasoning, answer) を返す。
    構造的なメッセージは logging に流すが、トークンストリーム自体は raw print で出す
    （logging だと token ごとに改行・タイムスタンプが入って読めなくなるため）。"""
    if header:
        log.info("【%s】", header)
    else:
        print()

    stream = client.chat.completions.create(
        model=MODEL,
        messages=request_messages,
        temperature=temperature,
        stream=True,
    )
    chunks = []
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            print(delta, end="", flush=True)
            chunks.append(delta)
    print()  # ストリーム末尾に改行を入れて、後続の log メッセージが行頭から始まるようにする

    raw = "".join(chunks)
    return split_harmony(raw)


def generate_tanka_pipeline(theme: str, max_refines: int = 3) -> tuple[str, list[dict]]:
    """Plan → Compose → Verify → (Refine *) のパイプライン。
    最終的な短歌(整形済テキスト)と、follow-up 用の history(お題と最終短歌のみ)を返す。"""

    # ── Step 1: Plan ──
    plan_messages = [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"お題：{theme}\n\n"
            "このお題で詠む短歌の構想を立ててください。短歌本体はまだ書かないでください。\n"
            "以下の形式で構想だけを出力してください：\n"
            "季語：（一つだけ）\n"
            "季節：（季語に対応する一つの季節）\n"
            "情景：（一つの場面を一文で）\n"
            "心情：（一つの感情を一文で）"
        )},
    ]
    _, plan = stream_chat(plan_messages, header="計画")

    # ── Step 2: Compose ──
    compose_messages = [
        {"role": "system", "content": TANKA_SYSTEM_PROMPT},
        *TANKA_COMPOSE_FEW_SHOT,
        {"role": "user", "content": (
            f"お題：{theme}\n\n"
            f"以下の構想に基づいて短歌を一首詠んでください：\n{plan}\n\n"
            "短歌本体（五句）のみを、各句の末尾に（ひらがな読み）を付けて出力してください。"
        )},
    ]
    _, composition = stream_chat(compose_messages, header="作歌")

    refine_history = compose_messages + [{"role": "assistant", "content": composition}]

    # ── Step 3: Verify (& Refine loop) ──
    final_parsed: list[tuple[str, str]] | None = None
    final_text = composition

    for attempt in range(max_refines + 1):
        parsed = parse_tanka(composition)
        if parsed is None:
            errors = ["五句のうちいずれかで（ひらがな読み）が見つかりませんでした。すべての句に読みを付けて再出力してください。"]
            warnings: list[str] = []
        else:
            errors, warnings = verify_tanka(parsed)

        if warnings:
            log.info("参考: 読みの不一致（拍数は正しい）")
            for w in warnings:
                log.info("  %s", w)

        if not errors:
            final_parsed = parsed
            final_text = composition
            break

        log.warning("【検証 (試行%d)】 拍数違反:", attempt + 1)
        for err in errors:
            log.warning("  %s", err)

        if attempt == max_refines:
            log.warning("規定回数に達したため、最後の出力をそのまま採用します。")
            final_parsed = parsed
            final_text = composition
            break

        critique = "\n".join(f"・{e}" for e in errors)
        refine_history.append({"role": "user", "content": (
            f"以下の問題があります。季語と情景・心情は維持したまま、該当箇所だけを修正してください：\n"
            f"{critique}\n\n"
            "拍数はプログラム側で漢字から独立に計算しており、ごまかせません。"
            "短い読みを書いて辻褄を合わせるのではなく、句そのものを実際に短くしてください。\n\n"
            "再度、五句すべてに（ひらがな読み）を付けて出力してください。"
        )})
        _, composition = stream_chat(refine_history, header=f"再詠 {attempt+1}")
        refine_history.append({"role": "assistant", "content": composition})

    # ── 完成 ──
    log.info("【完成】")
    if final_parsed is not None:
        clean = render_clean_tanka(final_parsed)
        # 短歌本体は print（logging プレフィックスを入れずに詩として表示）
        print(clean)
        moras = [count_moras(kanji_to_hira(body)) for body, _ in final_parsed]
        log.info("拍数: %s", "-".join(str(m) for m in moras))
    else:
        clean = final_text
        print(clean)

    log.info("構想:")
    print(plan)

    return clean, plan


log.info("Chat with %s. Ctrl-D to send. 'exit' or empty Ctrl-D to quit.", MODEL)
log.info("短歌モード: 'tanka:<お題>' で開始 / '/end-tanka' で通常モードに戻る。")

tanka_active = False
tanka_history: list[dict] = []

while True:
    lines = []
    try:
        lines.append(input("You: " if not tanka_active else "You [短歌]: "))
        while True:
            lines.append(input())
    except EOFError:
        print()

    user_input = "\n".join(lines).strip()

    if not user_input:
        break
    if user_input.lower() in ("exit", "quit"):
        break
    if user_input.lower() in ("/end-tanka", "/normal"):
        if tanka_active:
            tanka_active = False
            tanka_history = []
            log.info("通常モードに戻りました。")
        else:
            log.info("既に通常モードです。")
        continue

    tanka_match = TANKA_PREFIX_RE.match(user_input)

    if tanka_match:
        # 新しい短歌セッション開始 → パイプライン実行
        tanka_active = True
        tanka_history = []
        theme = user_input[tanka_match.end():].strip()

        final_tanka, plan = generate_tanka_pipeline(theme)

        # 後続のフォローアップ質問用に最低限の文脈を保持
        tanka_history.append({"role": "user", "content": f"お題：{theme}"})
        tanka_history.append({"role": "assistant", "content": (
            f"（構想）\n{plan}\n\n（短歌）\n{final_tanka}"
        )})
        continue

    if tanka_active:
        # 短歌モード中のフォローアップ質問（解説・修正依頼など）
        tanka_history.append({"role": "user", "content": user_input})
        request_messages = [
            {"role": "system", "content": TANKA_SYSTEM_PROMPT},
            *tanka_history,
        ]
    else:
        # 通常会話
        messages.append({"role": "user", "content": user_input})
        request_messages = messages

    reasoning, answer = stream_chat(request_messages)

    if tanka_active:
        tanka_history.append({"role": "assistant", "content": answer})
    else:
        messages.append({"role": "assistant", "content": answer})

    if reasoning is not None:
        log.info("[整形後]")
        print(answer)
        print()
