# CLAUDE.md

このリポジトリは **ローカル LLM (LM Studio + `llm-jp-4-8b-thinking`) を使った
短歌生成チャット**。Claude Code がこのリポジトリで作業する際に、
セッションを跨いでも文脈を失わないための要点メモ。

---

## 1. 重要: ディレクトリの "今" と "昔"

```
LLM/
├── main.py              ← CLI 版プロトタイプ。今は使われていない (touch 禁止)
├── pyproject.toml       ← CLI 版用。今は使われていない
├── chat.html            ← 単一 HTML 版プロトタイプ。今は使われていない
├── CLAUDE.md            ← このファイル
└── app/                 ← **ここが現行のプロジェクト本体**
    ├── README.md
    ├── docker-compose.yml
    ├── start.sh
    ├── docs/architecture.md   ← 開発者向け詳細ドキュメント (必読)
    ├── backend/         FastAPI + asyncio + Pydantic + pymongo + redis-py
    └── frontend/        React 18 + Vite + marked
```

**作業はすべて `app/` 配下で行う。** ルート直下の `main.py` / `pyproject.toml`
は最初期の CLI 版で、現アーキテクチャと別物。ユーザーが明示的に「ルートの main.py を」
と言わない限り触らない。

### 🔑 最初に必ず読むべき設計ノート

LLM とのフィードバックループ設計には **コードからは読み取りにくい意図** が多く含まれている。
`app/docs/feedback-architecture.md` を最初に読むこと。特に：

- System プロンプトは静的、validator 出力は user メッセージに流れる (system は never touched)
- Validator は単なる checker ではなく **critique 生成器・教訓化器を兼ねる中枢モジュール**
- 長期失敗記憶は few-shot 形式ではなく **教訓フレーズ注入** として実装されている
- Plan→Compose の整合性は validator ルール (-50 点) で強制矯正している

これらを誤解した状態で実装すると、的外れな箇所を触ることになる。

---

## 2. アーキテクチャ要約 (30 秒版)

```
Browser ──/api──▶ Vite proxy ──▶ FastAPI ──▶ LM Studio (:1234 on host)
                                    │
                                    ├─▶ MongoDB (sessions / tasks / failures)
                                    └─▶ Redis Streams (per-task event bus)
```

- **LLM 呼び出しは HTTP リクエストから完全に切り離されている**。
  `POST /api/chat` は `task_id` だけ即返し、`asyncio.create_task` で背景実行。
  `GET /api/tasks/{tid}/stream` で SSE 購読。再接続時は Redis Stream から `XREAD 0` で頭から replay。
- **セッション切替で SSE を切ってもバックエンドのタスクは死なない**。完了まで走り切る。

詳細は `app/docs/architecture.md` を参照。

---

## 3. 起動と確認

```bash
./app/start.sh                                # 4 サービス起動 (docker compose)
                                              # Ctrl-C 1 回: graceful shutdown
                                              # Ctrl-C 2 回: 強制停止

curl http://localhost:8001/api/health         # status: ok を確認
open http://localhost:5178                    # フロント
```

事前条件: ホスト側で LM Studio を起動 (`lms server start`)、
`llm-jp-4-8b-thinking` がロード済みであること。
**LM Studio の CORS 有効化はもう不要** (backend が proxy するため)。

### ポート割り当て (他プロジェクトとの競合を避けるため意図的に変えてある)

| サービス | port | なぜこの番号か |
|---|---|---|
| frontend (Vite) | 5178 | ホストの他プロジェクトが 5173 / 5176 を占有していたため |
| backend (FastAPI) | 8001 | ホストの他プロジェクト (EdinetAPI) が 8000 を占有していたため |
| mongo | 27017 | デフォルト |
| redis | 6379 | デフォルト |
| LM Studio | 1234 | ホスト (Docker 外) |

ポート競合に再度遭遇したら `app/start.sh` は起動前に lsof で検出して終了する。

---

## 4. バックエンドのモジュール責務

| ファイル | 責務 | 触る頻度 |
|---|---|---|
| `app/backend/main.py` | FastAPI ルートのみ。**ロジックは書かない**。リクエスト検証 → 適切な層に委譲のみ | エンドポイント追加時 |
| `app/backend/tasks.py` | asyncio Task の lifecycle (起動/cancel/cleanup)、SSE への event emit、長期失敗の記録 | 新タスク種別追加時 |
| `app/backend/tanka.py` | LLM パイプライン本体 (Plan → Compose → Validate → Refine ループ) | パイプライン構造変更時 |
| `app/backend/validator.py` | Pydantic `Tanka` + 9 ルール + スコアリング + critique 整形 + 長期記憶用 lesson 化 | ルール追加時 |
| `app/backend/rag.py` | 古典短歌コーパスからの構造的検索 (季語/季節 match)。compose に作例注入 | コーパス/検索変更時 |
| `app/backend/db.py` | MongoDB CRUD のみ (副作用唯一の境界) | スキーマ変更時 |
| `app/backend/bus.py` | Redis Streams 操作のみ (XADD/XREAD/EXPIRE) | ブローカ層変更時 |
| `app/backend/data/kigo.json` | 226 季語 × 5 季のキュレーション | 季語追加時 |
| `app/backend/data/classical_tanka.json` | RAG 用の古典名歌 31 首 (古今集/新古今集等、PD) | 作例追加時 |

**重要な依存方向**:

- `tanka.py` は `db` / `validator` / `rag` を遅延 import する (循環参照回避)
- `validator.py` は `tanka` の helpers (kanji_to_hira, count_moras) を import する
- `db.py` / `bus.py` / `rag.py` はどこにも依存しない (最下層、副作用なし)

**RAG (Phase 3)**: `rag.retrieve(season, kigo)` が季語 exact → 同季 → 雑 の優先で
古典作例を返し、`tanka.py` の compose プロンプトに「参考」として注入する (`TANKA_RAG=1` で
既定 ON、toggle で A/B 可能)。embedding は使わず構造的検索 (LM Studio embedding の不安定さ回避)。
模倣防止はプロンプトで明示。`rag` SSE イベントで UI に retrieval 結果を表示。

---

## 5. フロントエンドのざっくり構造

| ファイル | 役割 |
|---|---|
| `src/main.jsx` | React root |
| `src/App.jsx` | 大きな単一コンポーネント (AppInner) + ErrorBoundary でラップ。サイドバー、メッセージリスト、入力欄、失敗履歴モーダル | 
| `src/api.js` | SSE 手動パース + REST wrapper + harmony format 分離 + DB → UI 形式の normalize |
| `src/styles.css` | スタイル一式 |

**hook の宣言順序に注意**: `useCallback` が他の `useCallback` を deps に含む場合、
被依存側を先に宣言しないと TDZ で `Cannot access X before initialization` エラー。
現在の順: `refreshSessions → updateLastMessage → consumeTaskStream → loadSession → runTanka/runChat`。

---

## 6. 既知の罠 / アンチパターン

過去にハマったポイント。再発防止のために残す。

### 6.1 ファイル名の stdlib 衝突
- `queue.py` を作ると stdlib `queue` を shadow して redis-py が壊れる → `bus.py` にリネーム済み

### 6.2 IME (日本語入力) 関連
- **Enter キーで送信しない** (IME 変換確定 Enter で誤送信する)
- 送信は **送信ボタン or Cmd/Ctrl+Enter** のみ
- `onKeyDown` で `e.metaKey || e.ctrlKey` チェック必須

### 6.3 pykakasi の限界
- 現代漢字辞書ベース → 古典固有読み (「日」を「ひ」と読む) を誤判定
- `mora_count_disputed` ルールで minor (-3) に降格して救済済み
- 完全解決には MeCab + UniDic 移行が必要 (今のところ不要)

### 6.4 8B モデルの指示追従の弱さ
- **Plan で決めた季節/季語を Compose で勝手に変える**現象がよく起きる
- 対策: `season_matches_plan` (-30) / `kigo_matches_plan` (-20) を validator に
- 加えて Compose プロンプトで `【重要・必須】season は "夏" / kigo は "蝉"` と動的注入
- Few-shot は **四季全部** 揃える (春・夏・秋・冬)。一つでも欠けるとそこに regress する

### 6.5 React コンポーネントの null 安全
- `TankaCompleteBlock` で `tanka.split('\n')` が null で crash した過去あり
- `if (!tanka) return null` + `String(tanka).split(...)` で防御
- ErrorBoundary を app root に置いて、万一の crash でも白画面にならないようにしてある

### 6.6 セッション切替とストリームの整合
- セッション切替時は SSE のみ abort (バックエンドのタスクは生かす)
- 旧セッションのストリームが新セッションの messages を汚染しないように、
  ストリーム処理ループ内で `activeIdRef.current === sessionAtStart` をチェック

### 6.7 ファイル中の non-breaking space (U+00A0)
- 過去に App.jsx の JSX に nbsp が混入し、Edit tool の文字列マッチが失敗した
- 日本語編集環境からの貼り付け時に発生する可能性。気になったら `od -c` で確認

### 6.8 Strict Mode の二重 effect
- 初期化 effect は `initOnceRef` ガード必須 (二重実行で複数セッション作成事故)

### 6.9 docker compose up の port 衝突
- `start.sh` が起動前に lsof で 8001/5178 をチェックして fail-fast する
- 過去に別プロジェクトのプロセスが占有 → docker compose の `frontend` start に失敗

### 6.10 refine ループの context 超過 (Phase 2 で対策済み)
- 旧実装は `refine_history` を ever-growing にしていたため、attempt が増えると
  LM Studio の context を圧迫し `"Context size has been exceeded."` で zero-output 失敗
- 対策: refine/self-critique は **固定ベース (compose_messages) + 直近 1 ラウンド** で組み立て、
  context を attempt 数に依存させない。加えて LLM エラー時は best-so-far にフォールバック
- 詳細は `app/docs/feedback-architecture.md §5`。**会話履歴を全 attempt 蓄積する実装に戻さないこと**

### 6.11 LLM ストリームのサイレントハング (Phase 2 で対策済み)
- `stream_completion` に timeout が無く、LM Studio の stuck connection で
  `async for delta in stream_completion(...)` が **無限ブロック** (実測 14 分ハング)
- context-overflow と違い **例外が出ない** ため try/except では捕捉できない
- 対策: `AsyncOpenAI` に `httpx.Timeout(read=120s)` を設定。チャンク間の無音が
  read timeout を超えたら `APITimeoutError` を送出 → refine ループの fallback が best-so-far を採用
- env: `TANKA_LLM_READ_TIMEOUT` (既定 120s)。thinking モデルは生成中ずっとトークンを出すので、
  120 秒の無音は確実にハング。**timeout を外す実装に戻さないこと**

### 6.12 auto-title がセッションタイトルを上書きする (評価モニタ導入時に発覚)
- `db.append_message` は最初の user メッセージでタイトルを自動設定するが、
  これが **明示的に付けたタイトルも潰していた** (eval.sh の `[eval] <variant>` が `tanka:...` に化ける)
- 結果、評価モニタが eval run を識別できなかった
- 対策: auto-title は **現タイトルがデフォルト "新規セッション" のときだけ** 適用する
- 副次効果: ユーザーが rename したセッションも保護される。**この条件を外さないこと**

### 6.13 LM Studio の sustained-load 劣化 (環境制約、コードではない)
- このホストで LM Studio は連続生成負荷下に **15s/call → 3.5min/call + HTTP 400** で oscillate
- 自動 eval が大半失敗することがある (variance 実験で 5 回中 3 回 null)。コードは fail-clean (ハングなし)
- 対策にはモデルの fresh reload + run 間の間隔。**eval 中に診断 streaming を投げない** (load 上乗せで悪化)
- 詳細は `app/backend/eval/FINDINGS.md §5`

---

## 7. 設計上の重要な決定 (覆さないように)

| 決定 | 理由 |
|---|---|
| LLM 呼び出しを HTTP リクエストの寿命から切り離す | セッション切替で abort されないため。Phase 6 で導入 |
| Redis **Streams** を使う (Pub/Sub ではなく) | replay 可能。SSE 再接続時の頭出しに必要 |
| 短歌出力を **JSON 強制** | Pydantic で型検証でき、ルールが書きやすい |
| validator は **副作用なし** (DB を知らない) | テスタビリティ。`tasks.py` が orchestrator として副作用を持つ |
| `max_refines` を **無制限** + plateau 検知 | 簡単なお題は 1 回、難しいお題は何度でも。固定回数で打ち切らない |
| 全 attempt の **best score を最終結果に採用** | 改善が単調でないケースの保険 |
| 季語辞書は **ハンドキュレート JSON** | 依存追加なし、十分な範囲をカバー |
| Plan/Compose 整合性は **validator ルール** で強制 | プロンプトで願うだけでは 8B には効かない |
| 長期失敗記憶は **季節フィルタ** + LESSONS で教訓化 | 関係ない失敗を混ぜると逆効果。"これをやるな" を明示するほうが効く |
| **graceful shutdown** に lifespan + stop_grace_period 30s | LLM の partial save に余裕を確保 |

---

## 8. テスト / 動作確認の流儀

### 自動テスト (Phase 2 で導入)

```bash
# validator の単体テスト (39 ケース、副作用ゼロなので LLM/DB 不要)
cd app/backend && uv run pytest -q
```

`validator.py` は純関数集合なので pytest で網羅テスト済み。新ルール追加時は
`tests/test_validator.py` にケースを足す。`test_every_rule_has_a_lesson` が
「全ルールに LESSONS エントリがある」ことを保証しているので、ルール追加時は
LESSONS への追加を忘れると test が落ちる (意図的な安全網)。

### 品質測定 (Phase 2: 評価ハーネス)

```bash
cd app/backend/eval
./eval.sh <variant>                      # 固定お題セットを生成 → results/ にメトリクス保存
./compare.sh <before> <after>            # 2 バリアントの差分を 改善/劣化 で表示
```

パイプライン変更の効果は **必ず eval で数値確認** してから採用する (主観評価しない)。
詳細は `app/backend/eval/README.md`。設定は `TANKA_*` 環境変数で切替 (アブレーション用)。

### 手動確認

```bash
# 1. backend の syntax + import チェック
cd app/backend && uv run python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"

# 2. frontend のバンドルチェック
cd app/frontend && npx --yes esbuild src/App.jsx --bundle --loader:.jsx=jsx --jsx=automatic \
  --platform=browser --external:react --external:react-dom --external:marked --outfile=/dev/null

# 3. validator の単体動作 (アドホック)
cd app/backend && uv run python <<'PY'
import validator
t = validator.Tanka(kigo="花", season="春", lines=[...], image="", emotion="")
print(validator.evaluate(t))
PY

# 4. e2e: SSE 経由でタスク作成 → 結果確認
SID=$(curl -s -X POST http://localhost:8001/api/sessions -H 'Content-Type: application/json' -d '{}' | jq -r .id)
TID=$(curl -s -X POST http://localhost:8001/api/tanka -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"theme\":\"夏\"}" | jq -r .task_id)
curl -sN "http://localhost:8001/api/tasks/$TID/stream" | head -20
```

破壊的変更は **必ず docker compose 上で動作確認** (`docker compose restart backend` で reload)。

---

## 9. 拡張ガイド (頻出パターン)

詳細は `app/docs/architecture.md §9` を参照。要点だけ:

- **新しい validator ルール**: `_rule_X(t) -> list[Violation]` を validator.py に追加 → `RULES` に append → `LESSONS` に教訓を追加
- **新しい季語**: `app/backend/data/kigo.json` の該当季配列に追記するだけ
- **新しい SSE イベント種別**: tanka.py で yield → tasks.py で必要なら state 集約 → frontend の `consumeTaskStream` に分岐追加 → architecture.md §5 を更新
- **新しい DB フィールド**: schemaless なので追加は互換。削除や型変更は migration か `normalizeMessage` (api.js) で吸収

---

## 10. 何をしない (scope out)

- 認証 / マルチユーザー (localhost バインド前提)
- 自動テスト framework (まだ入れていない)
- 本番デプロイ用の設定 (CORS 全許可、port バインドが localhost のまま)
- 季語辞書の大規模化 (226 語で十分稼働中)
- LLM のファインチューニング (validator ルール + 長期記憶でカバー)

---

## 11. ユーザーとのやり取りのスタイル

- ユーザーは **日本語** で会話することが多いが、コードやログは英語/日本語混在 OK
- **長文の Markdown による回答** を好む傾向 (アーキ設計や trade-off の説明)
- 段階的に build up する開発スタイル: 「Phase 1 から始めて、合意したら次へ」
- ユーザーが「一気にやって」と言ったら scope を確認せず実装してよい
- 変更後は **docker compose 上で動作確認** + 結果のスクリーンショット相当のテキストを返す習慣

---

## 12. このファイルの更新

- 新しい罠を踏んで再発防止策を入れたら §6 に追記
- 新サービス追加 (例: PostgreSQL) や大きな設計変更があれば §2, §4 を更新
- 詳細な変更は `app/docs/architecture.md` (こちらは開発者向け詳細)、
  CLAUDE.md は **Claude が次セッションで作業開始するための onboarding 用** に
  簡潔さを優先する

---

## 13. クイックリファレンス

```bash
# 起動
./app/start.sh

# 個別サービスのログ
docker compose logs -f backend    # FastAPI ログ
docker compose logs -f mongo

# DB inspect
docker compose exec mongo mongosh tanka_chat
> db.sessions.find({}, {messages: 0})
> db.failures.find().sort({ts: -1}).limit(5)

# Redis inspect
docker compose exec redis redis-cli
> KEYS task:*
> XLEN task:{id}:events

# 完全リセット (DB 含む)
docker compose down -v

# 依存追加
cd app/backend && uv add <package>
cd app/frontend && npm install <package>
```
