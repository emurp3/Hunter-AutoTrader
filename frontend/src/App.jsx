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

  if (page === 'hero') {
    return (
      <HeroPage
        onEnter={() => setPage(user ? 'operations' : 'login')}
        onLogin={() => setPage('login')}
        onStatus={() => setPage('status')}
      />
    )
  }

  if (page === 'status') {
    return <StatusPage onBack={() => setPage('hero')} />
  }

  if (page === 'login') {
    if (user) return <OperationsPage onBack={() => setPage('hero')} onAuthFail={() => setPage('login')} />
    return <Login onSuccess={() => setPage('operations')} onBack={() => setPage('hero')} />
  }

  if (loading) return <div style={loadStyle}>loading...</div>
  if (!user)   return <Login onSuccess={() => setPage('operations')} />
  return <OperationsPage onBack={() => setPage('hero')} onAuthFail={() => setPage('login')} />
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  )
}
