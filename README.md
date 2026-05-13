# Tanka Chat — ローカル LLM を使った短歌生成チャット

LM Studio で動かすローカル LLM (`llm-jp-4-8b-thinking`) に、構造化検証 ・採点 ・自動改稿
・長期失敗記憶を加えた、Web ベースの短歌生成チャットです。
API コストなし、データはすべてローカル、Docker 一発で起動。

```bash
./app/start.sh    # → http://localhost:5178
```

---

## ✨ 主な機能

- **2 モード**: 通常チャットと、短歌生成専用パイプラインの切替（`tanka:<お題>` で開始）
- **5 段階パイプライン**: お題 → 構想 (Plan) → 作歌 (Compose, JSON 出力) → 検証 (Validate) → 改稿 (Refine, 何度でも)
- **9 ルール × 多軸スコアリング**: 拍数・季語の唯一性・季違い・Plan との整合性などを 100 点満点で採点、80 点以上で合格
- **226 語の歳時記辞書** をハンドキュレートで同梱、季違い検出に使用
- **改稿の自動打ち切り (plateau 検知)**: 連続して score が改善しなければ最高得点案を採用
- **長期失敗記憶**: 過去の違反を MongoDB に蓄積、同季のお題で「教訓」として LLM に注入
- **真のストリーミング**: バックエンドは LLM 呼び出しを HTTP リクエスト寿命から切り離し、Redis Streams 経由で SSE 配信。**セッション切替やタブ切替で abort されない**
- **永続セッション**: MongoDB に会話履歴を保存、リロード後も復元、サイドバーで切替
- **思考プロセスの可視化**: モデルの内省 (harmony format) を折りたたみで表示
- **Graceful shutdown**: Ctrl-C 1 回で in-flight タスクの partial save まで待つ、2 回で強制停止

---

## 🏗 アーキテクチャ

```
┌─ Host (developer machine) ──────────────────────────────────┐
│                                                              │
│  LM Studio (:1234)                                           │
│      ▲                                                       │
│      │ host.docker.internal                                  │
│      │                                                       │
│  ┌───┴────────────── Docker network ────────────────────┐    │
│  │                                                      │    │
│  │  frontend (Vite :5178)  ──/api──▶  backend (:8001)   │    │
│  │  React 18 + marked                  FastAPI + uv     │    │
│  │                                       │  │           │    │
│  │                            ┌──────────┘  └────────┐  │    │
│  │                            ▼                      ▼  │    │
│  │                   mongo:8.0 (:27017)     redis:7 (:6379) │
│  │                   sessions / tasks /      task event     │
│  │                   failures                bus            │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

詳細は [`app/docs/architecture.md`](app/docs/architecture.md)。

---

## 🚀 クイックスタート

### 前提

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 必須)
- [LM Studio](https://lmstudio.ai/) でホスト上に `llm-jp-4-8b-thinking` をロード&起動
  ```bash
  lms server start
  ```
  ※ CORS 設定は不要（バックエンドが proxy するため）

### 起動

```bash
./app/start.sh
```

これだけで 4 サービス (frontend / backend / mongo / redis) を build & up します。
初回はイメージ pull と依存インストールで数分。2 回目以降は数秒で起動。

ブラウザで http://localhost:5178 を開く。

### 使い方

| 入力 | 動作 |
|---|---|
| 通常メッセージ | チャットモードで応答 |
| `tanka:夏の夕暮れ` | 短歌パイプライン起動（構想→作歌→検証→改稿） |
| `/end-tanka` | 短歌モードのフォローアップを終了 |

詳しい操作は [`app/README.md`](app/README.md)。

---

## 🛠 技術スタック

| 層 | 採用 |
|---|---|
| LLM | LM Studio + `llm-jp-4-8b-thinking` (ローカル) |
| Backend | Python 3.12, FastAPI, uvicorn, AsyncOpenAI, Pydantic v2, pymongo, redis-py (async), pykakasi |
| Frontend | React 18, Vite 5, marked (Markdown), プレーン CSS |
| Persistence | MongoDB 8.0 (sessions / tasks / failures) |
| Event broker | Redis 7 Streams (replay 可能な per-task event bus) |
| Container | Docker Compose v2 (4 services + 3 named volumes) |
| Dev tooling | uv (Python deps), npm |

依存はすべて Docker イメージに含まれるので、ホストには **Docker と LM Studio だけ** あれば動きます。

---

## 📁 リポジトリ構成

```
LLM/
├── README.md              ← このファイル (プロジェクトの公的な入口)
├── CLAUDE.md              ← Claude Code 用のオンボーディング
├── main.py                ← 初期 CLI 版 (now obsolete, 参考用に残置)
├── chat.html              ← 初期単一 HTML 版 (now obsolete, 参考用に残置)
├── pyproject.toml         ← 初期 CLI 版用 (now obsolete)
└── app/                   ← ★ 現行プロジェクト本体 ★
    ├── README.md          起動 / 操作の詳細
    ├── docker-compose.yml 4 サービスの orchestration
    ├── start.sh           1 コマンド起動 + 2 段階 Ctrl-C
    │
    ├── docs/
    │   └── architecture.md   開発者向け詳細ドキュメント
    │
    ├── backend/
    │   ├── main.py        FastAPI ルート (薄い HTTP 層)
    │   ├── tasks.py       asyncio タスク管理 + cancel + 永続化
    │   ├── tanka.py       LLM パイプライン本体
    │   ├── validator.py   Pydantic + 9 ルール + スコアリング
    │   ├── db.py          MongoDB CRUD
    │   ├── bus.py         Redis Streams ラッパ
    │   └── data/kigo.json 226 季語 (春 50 / 夏 50 / 秋 50 / 冬 50 / 新年 21)
    │
    └── frontend/
        └── src/
            ├── main.jsx
            ├── App.jsx    UI (sessions, streaming, validation, failures)
            ├── api.js     SSE クライアント + REST wrapper
            └── styles.css
```

ルート直下のファイル (`main.py`, `chat.html`, `pyproject.toml`) は段階的に発展してきた **過去のプロトタイプ** で、現在は使われていません。実装は `app/` 以下に集約されています。

---

## 📚 ドキュメント

| ファイル | 対象 | 内容 |
|---|---|---|
| [README.md](README.md) | 全員 | 概要、入口（このファイル） |
| [app/README.md](app/README.md) | 動かしたい人 | 起動・操作・トラブルシュート |
| [app/docs/architecture.md](app/docs/architecture.md) | 開発者 | システム図、モジュール責務、データフロー、データモデル、SSE プロトコル、設計判断、拡張ガイド |
| **[app/docs/feedback-architecture.md](app/docs/feedback-architecture.md)** | **引き継ぎ担当者必読** | **LLM フィードバック設計の核心。Validator の役割、System/User メッセージの分担、失敗記憶の実体、アンチパターン** |
| [CLAUDE.md](CLAUDE.md) | Claude Code 別セッション | 既知の罠、規約、設計判断のサマリ |

---

## 🎯 設計のハイライト

このプロジェクトには、ローカル LLM (8B クラス) の弱さを **構造化された検証** と **アーキテクチャ的な工夫** で補う仕掛けが入っています。

### 1. LLM 寿命と HTTP リクエストの切り離し

普通の SSE 実装だと、クライアントが切断すると LLM 呼び出しも abort されます。
この実装では、`POST /api/chat` が `task_id` を即返してジョブを background に起動し、
クライアントは `GET /api/tasks/{tid}/stream` で SSE 購読する **2 段階** にしてあります。

イベントは Redis Streams に流れ、再接続時は `XREAD ... 0-0` で **頭から replay**。
セッション切替で SSE を切ってもタスクは生き続け、完了まで走ります。

### 2. JSON 強制 + Pydantic 検証

LLM の自由テキストではなく、明示的な JSON スキーマ（`kigo / season / lines[5] / image / emotion`）で
出力させます。これにより構造的なルール検証が型安全に書け、季語の唯一性・季違い・Plan 整合性などを
プログラム的に検出できます。

### 3. ルール × 重み × 教訓化

```
critical -30  季違い (季語と季節が辞書と不一致)
critical -25  宣言した季語が本文に出てこない
critical -10  拍数 5-7-5-7-7 違反
major    -20  Plan で決めた季語と Compose 出力の不一致
major    -15  季語の重複使用
minor     -5  辞書外の季語 / 同季の他季語混入
minor     -3  古典読みでの拍数ズレ
...
```

各違反は **重みで減点** され、80 点以上で合格。違反は重要度順に並べて critique 化、refine に投入。

### 4. Plateau 検知 + Best-score 採用

固定回数 (`max_refines=3`) で打ち切らず、`改善が続く限り refine 続行`。
直近 3 試行で score 更新が止まったら plateau 認定で打ち切り、**全 attempt 中の最高得点案を最終結果**
として採用します（改善が単調でないケースの保険）。

### 5. 長期失敗記憶

検証で合格しなかった出力を MongoDB に蓄積。次回の compose 前に、Plan で決まった季節と
一致する **直近 3 件** を取り出し、`【長期失敗記憶 — 同じ違反を繰り返さないこと】` として
教訓形式でプロンプトに注入します。同じ穴に何度も落ちないようにする lightweight な学習ループ。

### 6. Plan-Compose 整合の強制

8B モデルは Plan で `蝉/夏` と決めたのに Compose で `花/春` を出すことがよくあります。
これを Validator のルール (`season_matches_plan` / `kigo_matches_plan`) として実装し、
Plan の決定値と異なる出力は -50 点ペナルティで強制的に refine ループに戻します。
さらに Compose プロンプトでは `【重要・必須】season は "夏" / kigo は "蝉" とすること` と明示注入。

### 7. Graceful Shutdown

`Ctrl-C` 1 回で in-flight タスクに `asyncio.CancelledError` を投げ、各タスクは partial save を行ってから終了。
docker compose の `stop_grace_period: 30s` と FastAPI lifespan で最大 25 秒の cleanup 時間を確保。
2 回目の `Ctrl-C` で `docker compose kill` による強制停止。

詳細は [`app/docs/architecture.md`](app/docs/architecture.md) §8 "設計判断の "なぜ"" を参照。

---

## 🧪 試してみる

おすすめのお題（パイプラインの挙動が見やすい）:

```
tanka:夏の夕暮れ
tanka:剣道部の男女の部員が部活帰りに海の見える坂を下る
tanka:秋の月光に照らされた古寺
tanka:雪と桜          ← 季違いを引き起こすお題 (validator が refine を強制)
```

`tanka:` で開始すると、左から右へ:

```
[計画] 季語/季節/情景/心情 が決まる
   ↓
[作歌] JSON で初稿
   ↓
[検証 試行 0] 評点 72 / ✗ 違反あり
   ↓
[再詠 1] 違反を解消するよう書き直し
   ↓
[検証 試行 1] 評点 88 / ✓ 合格
   ↓
[完成] 短歌本体 + 拍数 + 季語 + 季節 + 評点
```

ヘッダー右の **「失敗履歴」** ボタンで、長期記憶に溜まった違反例を確認できます。

---

## ⚠️ 既知の限界

- **シングルユーザー前提**: localhost バインド、認証なし、CORS 全許可
- **pykakasi の限界**: 古典固有読みを誤判定することがある（minor 違反に降格して救済）
- **季語辞書のサイズ**: 226 語、マイナーな季題は `kigo_in_dictionary` minor warning が出る
- **LM Studio 必須**: 商用 API を使わないため、ホストに LLM ランタイムが必要
- **8B モデルの揺らぎ**: Plan-Compose の slippage、JSON 形式違反は時々起きる（validator が refine で矯正）

---

## 📝 ライセンス

個人プロジェクト。コード自体は MIT 相当の自由利用想定。
ただし依存ライブラリ・モデル (`llm-jp-4-8b-thinking`) は各自のライセンスに従ってください。

歳時記データ ([`app/backend/data/kigo.json`](app/backend/data/kigo.json)) はパブリックドメインの
代表的な季語をハンドキュレートしたものです。

---

## 🗺 開発の進化

このプロジェクトは段階的に発展してきました。経緯は [CLAUDE.md](CLAUDE.md) §7 "設計上の重要な決定" や
[`app/docs/architecture.md`](app/docs/architecture.md) §8 に詳しいですが、主な節目は:

1. **Phase 0** — CLI プロトタイプ (`main.py`)
2. **Phase 1** — 単一 HTML フロント (`chat.html`)
3. **Phase 2** — FastAPI + React + Vite に分離
4. **Phase 3** — MongoDB 永続化、サイドバー UI、Docker 化
5. **Phase 4** — Redis Streams 追加、LLM ライフサイクル分離、再接続対応
6. **Phase 5** — 構造化 JSON 出力 + Pydantic 検証 + スコアリング + plateau 検知 + Plan 整合性ルール
7. **Phase 6** — 長期失敗記憶 (MongoDB failures コレクション + 教訓注入)

各フェーズで踏んだ罠と学びは [CLAUDE.md §6 "既知の罠 / アンチパターン"](CLAUDE.md) に集約してあります。
