import { useCallback, useEffect, useMemo, useState } from 'react'
import { listTankaRecords } from './api.js'

// 短歌一覧 (Tanka Gallery): 全セッション横断で過去に詠んだ短歌を閲覧する read-only ビュー。
// 表示時に /api/tanka/records を取得し、絞り込み・並べ替えはクライアント側で行う
// (個人利用スケールなので全件持ってきて問題ない)。

// バックエンドの validator.PASS_THRESHOLD (既定 80) に対応。App.jsx と同じ値。
const PASS_THRESHOLD = 80
// validator.Season の取りうる値 (validator.py の Literal と対応)
const SEASONS = ['春', '夏', '秋', '冬', '新年', '雑']

function scoreClass(score) {
  if (typeof score !== 'number') return 'sc-none'
  if (score >= 90) return 'sc-90'
  if (score >= 80) return 'sc-80'
  if (score >= 60) return 'sc-60'
  return 'sc-low'
}

// "openai/gpt-oss-20b" → "gpt-oss-20b" (チップは短縮名、完全 ID は title で)
function shortModel(model) {
  return String(model).split('/').pop()
}

function TankaCard({ rec, onOpenSession }) {
  const lines = String(rec.tanka).split('\n')
  const unmet = typeof rec.score === 'number' && rec.score < PASS_THRESHOLD
  const date = rec.created_at ? new Date(rec.created_at) : null
  return (
    <div className={'tg-card' + (unmet ? ' unmet' : '')}>
      <div className="tg-poem">
        {lines.map((line, i) => <span key={i} className="tg-line">{line}</span>)}
      </div>
      <div className="tg-info">
        <div className="tg-theme" title={`お題: ${rec.theme ?? ''}`}>「{rec.theme || '—'}」</div>
        <div className="tg-meta">
          {rec.kigo && <span className="tg-kigo">{rec.kigo}</span>}
          {rec.season && <span className="tg-season">{rec.season}</span>}
          {rec.model && (
            <span className="tg-model" title={`生成モデル: ${rec.model}`}>{shortModel(rec.model)}</span>
          )}
          <span className={'tg-score ' + scoreClass(rec.score)}>{rec.score ?? '—'}</span>
          {unmet && (
            <span
              className="tg-unmet"
              title={`合格は ${PASS_THRESHOLD} 点以上です。これは規定を満たさない暫定案 (best-of-N の最高得点案) です。`}
            >
              規定未達
            </span>
          )}
        </div>
        {(rec.image || rec.emotion) && (
          <details className="tg-detail" open>
            <summary>情景・心情</summary>
            {rec.image && <div><b>情景:</b> {rec.image}</div>}
            {rec.emotion && <div><b>心情:</b> {rec.emotion}</div>}
          </details>
        )}
        <div className="tg-foot">
          <span className="tg-date">
            {date ? date.toLocaleString('ja-JP', { dateStyle: 'medium', timeStyle: 'short' }) : ''}
          </span>
          <span className="tg-attempts" title="検証にかけた試行回数 (refine 含む)">試行 {rec.attempts} 回</span>
          <button
            className="tg-open"
            onClick={() => onOpenSession(rec.session_id, rec.message_index)}
            title={`セッション「${rec.session_title || '無題'}」のこの歌の位置を開く`}
          >
            会話を開く →
          </button>
        </div>
      </div>
    </div>
  )
}

export default function TankaGallery({ onOpenSession }) {
  const [data, setData] = useState(null) // {count, items} | null
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [season, setSeason] = useState('all')
  const [model, setModel] = useState('all') // 'all' | 'none' (記録なし) | モデル ID
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('newest') // newest | oldest | score-desc | score-asc

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setData(await listTankaRecords())
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  // 読み込んだレコードに実在するモデルだけを絞り込み候補にする
  const models = useMemo(() => {
    const set = new Set((data?.items || []).map((r) => r.model).filter(Boolean))
    return [...set].sort()
  }, [data])
  const hasUnknownModel = useMemo(
    () => (data?.items || []).some((r) => !r.model),
    [data],
  )

  const records = useMemo(() => {
    let items = data?.items || []
    if (season !== 'all') items = items.filter((r) => r.season === season)
    if (model === 'none') items = items.filter((r) => !r.model)
    else if (model !== 'all') items = items.filter((r) => r.model === model)
    const q = query.trim()
    if (q) {
      items = items.filter((r) =>
        [r.tanka, r.theme, r.kigo, r.image, r.emotion].some((f) => f && String(f).includes(q)),
      )
    }
    // created_at は UTC isoformat 文字列なので文字列比較 = 時系列比較
    const newestFirst = (a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))
    const sorted = [...items]
    if (sort === 'newest') sorted.sort(newestFirst)
    else if (sort === 'oldest') sorted.sort((a, b) => newestFirst(b, a))
    else if (sort === 'score-desc') sorted.sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
    else if (sort === 'score-asc') sorted.sort((a, b) => (a.score ?? 101) - (b.score ?? 101))
    return sorted
  }, [data, season, model, query, sort])

  const avg = useMemo(() => {
    const scores = records.map((r) => r.score).filter((s) => typeof s === 'number')
    if (scores.length === 0) return null
    return (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1)
  }, [records])

  const filtered = data && records.length !== data.count

  return (
    <div className="tanka-gallery">
      <div className="tg-header">
        <h2>短歌一覧</h2>
        <div className="tg-header-right">
          <span className="tg-count">
            {records.length} 首{filtered ? ` / 全 ${data.count} 首` : ''}
            {avg !== null && ` ・ 平均 ${avg} 点`}
          </span>
          <button className="ev-refresh" onClick={reload} disabled={loading}>更新</button>
        </div>
      </div>

      <div className="tg-filters">
        <div className="tg-seasons">
          <button
            className={'tg-chip' + (season === 'all' ? ' active' : '')}
            onClick={() => setSeason('all')}
          >すべて</button>
          {SEASONS.map((s) => (
            <button
              key={s}
              className={'tg-chip' + (season === s ? ' active' : '')}
              onClick={() => setSeason(season === s ? 'all' : s)}
            >{s}</button>
          ))}
        </div>
        <input
          className="tg-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="本文・お題・季語で検索"
        />
        {models.length > 0 && (
          <select
            className="tg-sort"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            title="生成モデルで絞り込み"
          >
            <option value="all">すべてのモデル</option>
            {models.map((m) => (
              <option key={m} value={m}>{shortModel(m)}</option>
            ))}
            {hasUnknownModel && <option value="none">記録なし (旧データ)</option>}
          </select>
        )}
        <select className="tg-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="newest">新しい順</option>
          <option value="oldest">古い順</option>
          <option value="score-desc">評点が高い順</option>
          <option value="score-asc">評点が低い順</option>
        </select>
      </div>

      {error && <div className="error-banner">取得エラー: {error}</div>}
      {loading && !data && <div className="tg-empty">読み込み中…</div>}

      {!loading && !error && records.length === 0 && (
        <div className="tg-empty">
          {data?.count > 0
            ? '条件に一致する短歌がありません。'
            : <>まだ短歌がありません。チャットで <code>tanka:&lt;お題&gt;</code> と送ると最初の一首が詠まれます。</>}
        </div>
      )}

      <div className="tg-grid">
        {records.map((r, i) => (
          <TankaCard
            key={`${r.session_id}-${r.created_at || i}`}
            rec={r}
            onOpenSession={onOpenSession}
          />
        ))}
      </div>
    </div>
  )
}
