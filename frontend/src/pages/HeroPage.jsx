import { useRef, useState } from 'react'

const HERO_VIDEO_PATH = '/media/hunter-hero-loop.mp4'

export default function HeroPage({ onEnter, onLogin, onStatus }) {
  const videoRef             = useRef(null)
  const [videoFailed, setVideoFailed] = useState(false)
  const [emailOpen,   setEmailOpen]   = useState(false)
  const [email,       setEmail]       = useState('')
  const [emailSent,   setEmailSent]   = useState(false)

  function handleEmailSubmit(e) {
    e.preventDefault()
    setEmailSent(true)
    setTimeout(() => { setEmailOpen(false); setEmailSent(false); setEmail('') }, 2000)
  }

  return (
    <div className="hero-root">
      {!videoFailed ? (
        <video
          ref={videoRef}
          className="hero-video hero-video--desktop"
          src={HERO_VIDEO_PATH}
          autoPlay loop muted playsInline
          onError={() => setVideoFailed(true)}
        />
      ) : (
        <div className="hero-video-fallback" />
      )}

      <div className="hero-overlay" />

      <div className="hero-content">
        <div className="hero-badge">v0.2.0</div>
        <h1 className="hero-title">HUNTER</h1>
        <p className="hero-tagline">Elite Liberation Agent</p>
        <p className="hero-sub">
          Autonomous Revenue Acquisition · Live Execution · Weekly Quota Enforcement
        </p>

        <button className="hero-cta" onClick={onEnter}>
          Enter Hunter Operations
        </button>

        <div style={secondaryRow}>
          <button style={secBtn} onClick={onLogin}>Login</button>
          <button style={secBtn} onClick={() => setEmailOpen(true)}>Email Notifications</button>
          <button style={secBtn} onClick={onStatus}>System Status</button>
        </div>
      </div>

      {emailOpen && (
        <div style={modalOverlay} onClick={() => setEmailOpen(false)}>
          <div style={modalCard} onClick={e => e.stopPropagation()}>
            <p style={modalTitle}>Email Notifications</p>
            {emailSent ? (
              <p style={{ color:'#22c55e', fontFamily:'monospace', fontSize:13 }}>Saved.</p>
            ) : (
              <form onSubmit={handleEmailSubmit}>
                <input
                  type="email" value={email} onChange={e => setEmail(e.target.value)}
                  placeholder="your@email.com" required autoFocus style={emailInput}
                />
                <button type="submit" style={emailBtn}>Notify Me</button>
              </form>
            )}
            <button onClick={() => setEmailOpen(false)} style={modalClose}>✕</button>
          </div>
        </div>
      )}
    </div>
  )
}

const secondaryRow = {
  display:'flex', gap:'0.75rem', marginTop:'1rem',
  justifyContent:'center', flexWrap:'wrap',
}
const secBtn = {
  padding:'0.5rem 1.1rem',
  background:'rgba(255,255,255,0.05)',
  border:'1px solid rgba(255,255,255,0.12)',
  borderRadius:4, color:'#c8c8c8',
  fontFamily:'monospace', fontSize:12,
  cursor:'pointer', letterSpacing:'0.03em',
}
const modalOverlay = {
  position:'fixed', inset:0, background:'rgba(0,0,0,0.7)',
  display:'flex', alignItems:'center', justifyContent:'center', zIndex:100,
}
const modalCard = {
  background:'#111', border:'1px solid #1e1e1e', borderRadius:8,
  padding:'1.75rem 1.5rem', width:300, position:'relative',
}
const modalTitle = { color:'#e8e8e8', fontFamily:'monospace', fontSize:14, fontWeight:500, margin:'0 0 1rem' }
const emailInput = {
  display:'block', width:'100%', padding:'0.55rem 0.75rem',
  background:'#0d0d0d', border:'1px solid #2a2a2a', borderRadius:4,
  color:'#e8e8e8', fontFamily:'monospace', fontSize:13,
  boxSizing:'border-box', marginBottom:'0.75rem', outline:'none',
}
const emailBtn = {
  display:'block', width:'100%', padding:'0.55rem',
  background:'#1a6b3c', border:'none', borderRadius:4,
  color:'#e8e8e8', fontFamily:'monospace', fontSize:13, cursor:'pointer',
}
const modalClose = {
  position:'absolute', top:'0.6rem', right:'0.75rem',
  background:'none', border:'none', color:'#555', cursor:'pointer', fontSize:14,
}
