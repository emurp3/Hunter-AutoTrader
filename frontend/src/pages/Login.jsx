import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function Login({ onSuccess, onBack }) {
  const { login }                     = useAuth()
  const [username, setUsername]       = useState('')
  const [password, setPassword]       = useState('')
  const [error,    setError]          = useState('')
  const [loading,  setLoading]        = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.detail || 'Login failed')
        return
      }
      login({ username: data.username || username, role: data.role })
      if (onSuccess) onSuccess()
    } catch {
      setError('Network error — is the server running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={wrap}>
      <div style={card}>
        <h2 style={heading}>hunter // login</h2>
        <form onSubmit={handleSubmit}>
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="username"
            autoFocus
            required
            style={{ ...inp, marginBottom: '0.9rem' }}
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="password"
            required
            style={{ ...inp, marginBottom: '1.25rem' }}
          />
          {error && <p style={errStyle}>{error}</p>}
          <button type="submit" disabled={loading} style={btn}>
            {loading ? 'signing in...' : 'sign in'}
          </button>
        </form>
        {onBack && (
          <button onClick={onBack} style={backBtn}>← back</button>
        )}
      </div>
    </div>
  )
}

const wrap    = { display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#0a0a0a' }
const card    = { background:'#111', border:'1px solid #1e1e1e', borderRadius:8, padding:'2.5rem 2rem', width:320 }
const heading = { color:'#e8e8e8', fontFamily:'monospace', margin:'0 0 1.5rem', fontSize:17, fontWeight:500 }
const inp     = { display:'block', width:'100%', padding:'0.6rem 0.75rem', background:'#0d0d0d', border:'1px solid #2a2a2a', borderRadius:4, color:'#e8e8e8', fontFamily:'monospace', fontSize:14, boxSizing:'border-box', outline:'none' }
const btn     = { display:'block', width:'100%', padding:'0.65rem', background:'#1a6b3c', border:'none', borderRadius:4, color:'#e8e8e8', fontFamily:'monospace', fontSize:14, cursor:'pointer' }
const errStyle = { color:'#f87171', fontFamily:'monospace', fontSize:13, margin:'0 0 0.9rem' }
const hint    = { color:'#444', fontFamily:'monospace', fontSize:12, marginTop:'1.5rem', textAlign:'center' }
const backBtn = { display:'block', width:'100%', marginTop:'0.75rem', background:'none', border:'none', color:'#555', fontFamily:'monospace', fontSize:12, cursor:'pointer', textAlign:'center' }
