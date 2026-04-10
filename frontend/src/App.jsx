import { useState } from 'react'
import HeroPage      from './pages/HeroPage'
import OperationsPage from './pages/OperationsPage'
import Login          from './pages/Login'
import StatusPage     from './pages/StatusPage'
import { AuthProvider, useAuth } from './context/AuthContext'

const loadStyle = {
  display:'flex', alignItems:'center', justifyContent:'center',
  height:'100vh', background:'#0a0a0a', color:'#444', fontFamily:'monospace',
}

function AppInner() {
  const { user, loading } = useAuth()
  const [page, setPage]   = useState('hero')

  // ── Public pages ──────────────────────────────────────────────────────────
  if (page === 'hero') {
    return (
      <HeroPage
        onEnter={() => setPage('operations')}
        onLogin={() => setPage('login')}
        onStatus={() => setPage('status')}
      />
    )
  }

  if (page === 'status') {
    return <StatusPage onBack={() => setPage('hero')} />
  }

  // Direct login (from "Login" button on hero)
  if (page === 'login') {
    if (user) return <OperationsPage onBack={() => setPage('hero')} />
    return <Login onSuccess={() => setPage('operations')} onBack={() => setPage('hero')} />
  }

  // ── Protected: operations ─────────────────────────────────────────────────
  if (loading) return <div style={loadStyle}>loading...</div>
  if (!user)   return <Login onSuccess={() => setPage('operations')} />
  return <OperationsPage onBack={() => setPage('hero')} />
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  )
}
