import { useEffect, useState } from 'react'

export default function StatusPage({ onBack }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/system/readiness', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d  => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  const indicators = data ? [
    { label: 'backend',          value: 'ok',                      ok: true  },
    { label: 'sandbox brokerage',value: data.sandbox_brokerage?.status ?? '—', ok: data.sandbox_brokerage?.connected },
    { label: 'live brokerage',   value: data.live_brokerage?.status   ?? '—', ok: data.live_brokerage?.connected   },
    { label: 'autotrader',       value: data.autotrader?.source_type  ?? '—', ok: data.autotrader?.online           },
    { label: 'execution mode',   value: data.execution_mode           ?? '—', ok: true                              },
  ] : []

  return (
    <div style={root}>
      <button onClick={onBack} style={back}>← back</button>
      <h2 style={title}>system status</h2>

      {loading && <p style={dim}>checking...</p>}

      {!loading && !data && <p style={err}>status unavailable — backend may be starting</p>}

      {!loading && data && (
        <div style={grid}>
          {indicators.map(row => (
            <div key={row.label} style={row_}>
              <span style={label_}>{row.label}</span>
              <span style={{ ...value_, color: row.ok ? '#22c55e' : '#f59e0b' }}>
                {String(row.value).toLowerCase()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const root   = { background:'#0a0a0a', minHeight:'100vh', padding:'2.5rem 2rem', fontFamily:'monospace', color:'#e8e8e8' }
const back   = { background:'none', border:'none', color:'#555', fontFamily:'monospace', fontSize:12, cursor:'pointer', padding:0, marginBottom:'2rem', display:'block' }
const title  = { color:'#e8e8e8', fontSize:16, fontWeight:500, margin:'0 0 1.5rem', letterSpacing:'0.04em' }
const dim    = { color:'#444', fontSize:13 }
const err    = { color:'#f87171', fontSize:13 }
const grid   = { maxWidth:420 }
const row_   = { display:'flex', justifyContent:'space-between', alignItems:'center', padding:'0.6rem 0', borderBottom:'1px solid #1a1a1a' }
const label_ = { color:'#666', fontSize:12, textTransform:'uppercase', letterSpacing:'0.06em' }
const value_ = { fontSize:12 }
