import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getSession, listEvalRuns } from './api.js'

// 評価モニタ: eval.sh / eval-repeat.sh が走らせている run の状況をライブ表示する。
// バックエンドの /api/eval/runs をポーリングするだけの read-only ダッシュボード。

function scoreClass(score) {
  if (typeof score !== 'number') return 'sc-none'
  if (score >= 90) return 'sc-90'
  if (score >= 80) return 'sc-80'
  if (score >= 60) return 'sc-60'
  return 'sc-low'
}

function Stat({ label, value }) {
  return (
    <div className="ev-stat">
      <div className="ev-stat-val">{value}</div>
      <div className="ev-stat-label">{label}</div>
    </div>
  )
}

function RunCard({ run, expanded, onToggle }) {
  const scores = run.scores || []
  const mean = run.avg_score
  // 展開時に session 詳細を取得して、実際に詠まれた短歌本文を表示する
  // (list_eval_runs は本文を含まないので、既存の /api/sessions/{id} を使う。backend 変更不要)
  const [tankaByTheme, setTankaByTheme] = useState({})
  useEffect(() => {
    if (!expanded) return
    let cancelled = false
    getSession(run.id).then((sess) => {
      if (cancelled || !sess) return
      const map = {}
      ;(sess.messages || [])
        .filter((m) => m.kind === 'tanka')
        .forEach((m, i) => { map[i] = { tanka: m.tanka, image: m.image, emotion: m.emotion } })
      setTankaByTheme(map)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [expanded, run.id, run.done])

  // best-of-N の variance を可視化: scores の min/max/range
  const stats = useMemo(() => {
    if (scores.length === 0) return null
    const min = Math.min(...scores)
    const max = Math.max(...scores)
    const m = scores.reduce((a, b) => a + b, 0) / scores.length
    const variance = scores.reduce((a, b) => a + (b - m) ** 2, 0) / scores.length
    return { min, max, range: max - min, stdev: Math.sqrt(variance).toFixed(1) }
  }, [scores])

  return (
    <div className={'ev-card' + (run.status === 'running' ? ' running' : '')}>
      <div className="ev-card-head" onClick={onToggle}>
        <div className="ev-card-title">
          <span className={'ev-kind ev-kind-' + run.kind}>{run.kind}</span>
          <span className="ev-variant">{run.variant || '(無題)'}</span>
          {run.status === 'running' && <span className="ev-spinner" />}
        </div>
        <div className="ev-card-summary">
          {run.status === 'running' && run.running_theme && (
            <span className="ev-running-theme" title={run.running_theme}>
              ▶ {run.running_theme.slice(0, 18)}
            </span>
          )}
          <span className="ev-done">{run.done} 首</span>
          {typeof mean === 'number' && (
            <span className={'ev-avg ' + scoreClass(mean)}>avg {mean}</span>
          )}
          <span className="ev-caret">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* score スパークライン (best-of-N の振れを一目で) */}
      {scores.length > 0 && (
        <div className="ev-spark">
          {run.themes.map((t, i) => (
            <div
              key={i}
              className={'ev-bar ' + scoreClass(t.score)}
              style={{ height: typeof t.score === 'number' ? `${Math.max(6, t.score)}%` : '6%' }}
              title={`${t.theme}: ${t.score ?? '—'} (${t.attempts} attempts)`}
            />
          ))}
        </div>
      )}

      {expanded && (
        <div className="ev-detail">
          {stats && (
            <div className="ev-stats-row">
              <Stat label="平均" value={mean} />
              <Stat label="最小" value={stats.min} />
              <Stat label="最大" value={stats.max} />
              <Stat label="範囲" value={stats.range} />
              <Stat label="標準偏差" value={stats.stdev} />
            </div>
          )}
          <div className="ev-poems">
            {run.themes.map((t, i) => {
              const poem = tankaByTheme[i]
              return (
                <div key={i} className="ev-poem">
                  <div className="ev-poem-head">
                    <span className="ev-poem-num">{i + 1}</span>
                    <span className="ev-poem-theme">{t.theme}</span>
                    <span className="ev-poem-meta">
                      {t.kigo && <span className="ev-kigo">季語 {t.kigo}/{t.season ?? '?'}</span>}
                      <span className="ev-poem-att">{t.attempts}回{t.plateau ? ' ⚠' : ''}</span>
                      <span className={'ev-score ' + scoreClass(t.score)}>{t.score ?? '—'}</span>
                    </span>
                  </div>
                  {poem?.tanka ? (
                    <div className="ev-poem-body">
                      {String(poem.tanka).split('\n').map((line, j) => (
                        <span key={j} className="ev-poem-line">{line}</span>
                      ))}
                    </div>
                  ) : (
                    <div className="ev-poem-body ev-poem-loading">（本文読み込み中…）</div>
                  )}
                  {poem?.emotion && <div className="ev-poem-emotion">心情: {poem.emotion}</div>}
                </div>
              )
            })}
            {run.status === 'running' && run.running_theme && (
              <div className="ev-poem ev-row-running">
                <div className="ev-poem-head">
                  <span className="ev-poem-num">{run.themes.length + 1}</span>
                  <span className="ev-poem-theme">{run.running_theme}</span>
                  <span className="ev-generating">生成中 <span className="ev-spinner" /></span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function EvalMonitor() {
  const [runs, setRuns] = useState([])
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const timerRef = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const data = await listEvalRuns()
      setRuns(data)
      setError(null)
      setLastUpdate(new Date())
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    timerRef.current = setInterval(refresh, 3000) // 3s ポーリング
    return () => clearInterval(timerRef.current)
  }, [refresh])

  const anyRunning = runs.some((r) => r.status === 'running')

  return (
    <div className="eval-monitor">
      <div className="ev-header">
        <h2>評価モニタ</h2>
        <div className="ev-header-right">
          {anyRunning && <span className="ev-live">● LIVE</span>}
          {lastUpdate && (
            <span className="ev-updated">
              更新 {lastUpdate.toLocaleTimeString('ja-JP')}
            </span>
          )}
          <button className="ev-refresh" onClick={refresh}>更新</button>
        </div>
      </div>

      {error && <div className="error-banner">取得エラー: {error}</div>}

      {runs.length === 0 && !error && (
        <div className="ev-empty">
          <p>実行中・完了済みの評価 run はありません。</p>
          <p className="ev-hint">
            <code>app/backend/eval/eval.sh &lt;variant&gt;</code> や{' '}
            <code>eval-repeat.sh "&lt;お題&gt;" 5</code> を実行すると、ここに状況が表示されます。
          </p>
        </div>
      )}

      <div className="ev-runs">
        {runs.map((run) => (
          <RunCard
            key={run.id}
            run={run}
            expanded={expandedId === run.id}
            onToggle={() => setExpandedId(expandedId === run.id ? null : run.id)}
          />
        ))}
      </div>
    </div>
  )
}
