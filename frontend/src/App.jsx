import { useState } from 'react'
import HeroPage from './pages/HeroPage'
import OperationsPage from './pages/OperationsPage'
import Login from './pages/Login'
import { AuthProvider, useAuth } from './context/AuthContext'

function AppInner() {
  const { user, loading } = useAuth()
  const [page, setPage]   = useState('hero')

  // Hero page is always public — no auth required
  if (page === 'hero') {
    return <HeroPage onEnter={() => setPage('operations')} />
  }

  // Everything below here is the protected operations section.
  // Auth check runs only when the user tries to enter.
  if (loading) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#0a0a0a', color:'#444', fontFamily:'monospace' }}>
        loading...
      </div>
    )
  }

  if (!user) return <Login />

  return <OperationsPage onBack={() => setPage('hero')} />
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  )
}
