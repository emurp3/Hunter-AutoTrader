import { useState } from 'react'
import HeroPage from './pages/HeroPage'
import OperationsPage from './pages/OperationsPage'

export default function App() {
  const [page, setPage] = useState('hero')

  return (
    <>
      {page === 'hero' && <HeroPage onEnter={() => setPage('operations')} />}
      {page === 'operations' && <OperationsPage onBack={() => setPage('hero')} />}
    </>
  )
}