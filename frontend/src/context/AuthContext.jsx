import { createContext, useContext, useEffect, useState } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)   // { username, role } | null
  const [loading, setLoading] = useState(true)   // true while probing /auth/me

  useEffect(() => {
    // Re-hydrate session from HTTP-only cookie on every page load/refresh.
    fetch('/api/auth/me', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (data?.username) setUser(data) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function login(userData) {
    setUser(userData)
  }

  function logout() {
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
      .catch(() => {})
      .finally(() => setUser(null))
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be called inside <AuthProvider>')
  return ctx
}
