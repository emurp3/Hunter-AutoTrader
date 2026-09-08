import { useState, useRef, useEffect, useCallback } from 'react'
import { useAuth } from '../context/AuthContext'

export default function HunterAssistant() {
  const { user } = useAuth()
  const [open, setOpen]       = useState(false)
  const [messages, setMsgs]   = useState([
    { role: 'assistant', content: 'Hunter AI online. I have full visibility into your opportunities, account, and signals. Ask me anything.' }
  ])
  const [input, setInput]     = useState('')
  const [loading, setLoading] = useState(false)
  const [snapshot, setSnap]   = useState(null)
  const [decisionsLoaded, setDecisionsLoaded] = useState(false)
  const bottomRef             = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Hunter asks first: pull open Commander decisions and inject them as
  // Hunter-initiated chat messages the first time the panel opens. This
  // reads directly from the execution ledger — it does not depend on the
  // LLM chat call succeeding.
  useEffect(() => {
    if (!open || decisionsLoaded) return
    setDecisionsLoaded(true)
    fetch('/api/hunter-ops/commander-decisions', { credentials: 'include' })
      .then(res => (res.ok ? res.json() : []))
      .then(decisions => {
        if (!Array.isArray(decisions) || decisions.length === 0) return
        setMsgs(prev => [
          ...prev,
          {
            role: 'assistant',
            content: decisions.length === 1
              ? "I've got one thing I need from you before I can move further:"
              : `I've got ${decisions.length} things I need from you before I can move further:`,
          },
          ...decisions.map(d => ({ role: 'decision', decision: d })),
        ])
      })
      .catch(() => { /* chat still works without this — fail quiet */ })
  }, [open, decisionsLoaded])

  const answerDecision = useCallback((decisionId, answerText, decisionType) => {
    setMsgs(prev => prev.map(m => (
      m.role === 'decision' && m.decision.id === decisionId
        ? { ...m, submitting: true }
        : m
    )))
    const params = new URLSearchParams({ answer: answerText })
    if (decisionType) params.set('decision', decisionType)
    fetch(`/api/hunter-ops/candidates/${encodeURIComponent(decisionId)}/commander-answer?${params}`, {
      method: 'POST',
      credentials: 'include',
    })
      .then(res => {
        if (!res.ok) throw new Error('request failed')
        return res.json()
      })
      .then(() => {
        setMsgs(prev => [
          ...prev.filter(m => !(m.role === 'decision' && m.decision.id === decisionId)),
          { role: 'user', content: answerText },
          { role: 'assistant', content: 'Got it — recorded. I’ll pick this back up from there.' },
        ])
      })
      .catch(() => {
        setMsgs(prev => prev.map(m => (
          m.role === 'decision' && m.decision.id === decisionId
            ? { ...m, submitting: false, error: 'Could not save that — try again.' }
            : m
        )))
      })
  }, [])

  if (!user) return null

  async function send() {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMsgs(prev => [...prev, { role: 'user', content: text }])
    setLoading(true)
    try {
      const res  = await fetch('/api/assistant/chat', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      const data = await res.json()
      setMsgs(prev => [...prev, { role: 'assistant', content: data.response }])
      if (data.context_snapshot) setSnap(data.context_snapshot)
    } catch {
      setMsgs(prev => [...prev, { role: 'assistant', content: 'Hunter AI is temporarily offline. Check your connection.' }])
    } finally {
      setLoading(false)
    }
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const btnDisabled = loading || !input.trim()

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          title="Ask Hunter"
          style={{
            position:'fixed', bottom:28, right:28, zIndex:9999,
            width:56, height:56, borderRadius:'50%', border:'none', cursor:'pointer',
            background:'linear-gradient(135deg, #c9a84c, #a8893d)',
            boxShadow:'0 4px 20px rgba(201,168,76,0.55)',
            fontSize:24, display:'flex', alignItems:'center', justifyContent:'center',
            transition:'transform 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.transform='scale(1.08)'}
          onMouseLeave={e => e.currentTarget.style.transform='scale(1)'}
        >
          ⚡
        </button>
      )}

      {open && (
        <div style={{
          position:'fixed', bottom:28, right:28, zIndex:9999,
          width:380, height:520, borderRadius:16,
          background:'#0a0e1a', border:'1px solid rgba(201,168,76,0.3)',
          boxShadow:'0 8px 40px rgba(0,0,0,0.65)',
          display:'flex', flexDirection:'column', overflow:'hidden',
          fontFamily:'system-ui, sans-serif',
        }}>

          <div style={{
            padding:'12px 16px', borderBottom:'1px solid rgba(201,168,76,0.3)',
            display:'flex', alignItems:'center', justifyContent:'space-between',
            background:'rgba(201,168,76,0.07)',
          }}>
            <div>
              <span style={{ color:'#c9a84c', fontWeight:700, fontSize:15 }}>⚡ Hunter AI</span>
              {snapshot && (
                <span style={{ color:'#888', fontSize:11, marginLeft:10 }}>
                  ${snapshot.account_cash ?? '—'} cash &middot; {snapshot.top_opp_count ?? '—'} opps
                </span>
              )}
            </div>
            <button
              onClick={() => setOpen(false)}
              style={{ background:'none', border:'none', color:'#666', fontSize:20, cursor:'pointer', lineHeight:1 }}
            >&times;</button>
          </div>

          <div style={{ flex:1, overflowY:'auto', padding:'12px 14px', display:'flex', flexDirection:'column', gap:10 }}>
            {messages.map((m, i) => (
              m.role === 'decision' ? (
                <DecisionCard key={i} decision={m.decision} submitting={m.submitting} error={m.error} onAnswer={answerDecision} />
              ) : (
                <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth:'85%' }}>
                  <div style={{
                    padding:'9px 13px',
                    borderRadius: m.role === 'user' ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
                    background: m.role === 'user' ? 'rgba(0,212,255,0.15)' : 'rgba(255,255,255,0.05)',
                    border: m.role === 'user' ? '1px solid rgba(0,212,255,0.3)' : '1px solid rgba(201,168,76,0.3)',
                    color:'#e8e8e8', fontSize:13.5, lineHeight:1.5, whiteSpace:'pre-wrap',
                  }}>
                    {m.content}
                  </div>
                </div>
              )
            ))}
            {loading && (
              <div style={{ alignSelf:'flex-start' }}>
                <div style={{
                  padding:'9px 16px', borderRadius:'14px 14px 14px 4px',
                  border:'1px solid rgba(201,168,76,0.3)', background:'rgba(255,255,255,0.05)',
                  color:'#c9a84c', fontSize:18, letterSpacing:4,
                }}>···</div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div style={{
            padding:'10px 12px', borderTop:'1px solid rgba(201,168,76,0.3)',
            display:'flex', gap:8, background:'rgba(0,0,0,0.3)',
          }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask Hunter anything…"
              disabled={loading}
              style={{
                flex:1, background:'rgba(255,255,255,0.07)',
                border:'1px solid rgba(201,168,76,0.3)', borderRadius:10,
                padding:'8px 12px', color:'#e8e8e8', fontSize:13.5, outline:'none',
              }}
            />
            <button
              onClick={send}
              disabled={btnDisabled}
              style={{
                background: btnDisabled ? '#333' : 'linear-gradient(135deg,#c9a84c,#a8893d)',
                border:'none', borderRadius:10, padding:'8px 14px',
                color: btnDisabled ? '#666' : '#000',
                cursor: btnDisabled ? 'default' : 'pointer',
                fontWeight:700, fontSize:14,
              }}
            >&#8593;</button>
          </div>
        </div>
      )}
    </>
  )
}

function DecisionCard({ decision, submitting, error, onAnswer }) {
  const [text, setText] = useState('')

  return (
    <div style={{ alignSelf:'flex-start', maxWidth:'92%', width:'92%' }}>
      <div style={{
        padding:'10px 13px', borderRadius:'14px 14px 14px 4px',
        background:'rgba(255,183,0,0.06)', border:'1px solid rgba(255,183,0,0.35)',
        color:'#e8e8e8', fontSize:13, lineHeight:1.5,
      }}>
        <div style={{ fontWeight:700, color:'#ffb700', marginBottom:4 }}>
          Needs you — {decision.id}
        </div>
        <div style={{ marginBottom:6 }}>{decision.mechanism}</div>
        <div style={{ color:'#c9a84c', marginBottom:8 }}>{decision.checkpoint}</div>

        {error && <div style={{ color:'#ff6b6b', fontSize:12, marginBottom:6 }}>{error}</div>}

        <div style={{ display:'flex', gap:6, marginBottom:6 }}>
          <input
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder="Type an answer (e.g. company name + FEIN)…"
            disabled={submitting}
            style={{
              flex:1, background:'rgba(255,255,255,0.07)',
              border:'1px solid rgba(201,168,76,0.3)', borderRadius:8,
              padding:'6px 10px', color:'#e8e8e8', fontSize:12.5, outline:'none',
            }}
          />
          <button
            onClick={() => text.trim() && onAnswer(decision.id, text.trim(), null)}
            disabled={submitting || !text.trim()}
            style={{
              background: submitting || !text.trim() ? '#333' : 'rgba(0,212,255,0.2)',
              border:'1px solid rgba(0,212,255,0.4)', borderRadius:8, padding:'6px 12px',
              color:'#e8e8e8', fontSize:12, cursor: submitting ? 'default' : 'pointer',
            }}
          >Send</button>
        </div>

        <div style={{ display:'flex', gap:6 }}>
          <button
            onClick={() => onAnswer(decision.id, 'Approved — proceed.', 'approve')}
            disabled={submitting}
            style={{
              flex:1, background:'rgba(80,200,120,0.15)', border:'1px solid rgba(80,200,120,0.4)',
              borderRadius:8, padding:'6px 10px', color:'#8fe0a8', fontSize:12,
              cursor: submitting ? 'default' : 'pointer', fontWeight:600,
            }}
          >Approve</button>
          <button
            onClick={() => onAnswer(decision.id, 'Declined.', 'decline')}
            disabled={submitting}
            style={{
              flex:1, background:'rgba(255,107,107,0.12)', border:'1px solid rgba(255,107,107,0.35)',
              borderRadius:8, padding:'6px 10px', color:'#ff9d9d', fontSize:12,
              cursor: submitting ? 'default' : 'pointer', fontWeight:600,
            }}
          >Decline</button>
        </div>
      </div>
    </div>
  )
}
