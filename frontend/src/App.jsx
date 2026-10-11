import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import ProtectedRoute from './components/ProtectedRoute.jsx'
import RoleRoute from './components/RoleRoute.jsx'
import Login from './pages/Login.jsx'
import { useAuth } from './hooks/useAuth.js'

function RoleRedirect() {
  const { isAuthenticated, role } = useAuth()
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }
  if (role === 'business_analyst') {
    return <Navigate to="/analyst/events" replace />
  }
  if (role === 'db_engineer') {
    return <Navigate to="/engineer/workload" replace />
  }
  return <Navigate to="/login" replace />
}

function App() {
  return (
    <Routes>
      {/* Public routes */}
      <Route path="/login" element={<Login />} />

      {/* Analyst routes */}
      <Route element={<ProtectedRoute />}>
        <Route element={<RoleRoute allowedRoles={['business_analyst']} />}>
          <Route path="/analyst/events" element={<div>Analyst Events Page</div>} />
          <Route path="/analyst/margin" element={<div>Analyst Margin Page</div>} />
          <Route path="/analyst/forecast" element={<div>Analyst Forecast Page</div>} />
          <Route path="/analyst/sql" element={<div>Analyst SQL Console Page</div>} />
          <Route path="/analyst/my-queries" element={<div>Analyst My Queries Page</div>} />
        </Route>
      </Route>

      {/* Engineer routes */}
      <Route element={<ProtectedRoute />}>
        <Route element={<RoleRoute allowedRoles={['db_engineer']} />}>
          <Route path="/engineer/workload" element={<div>Engineer Workload Page</div>} />
          <Route path="/engineer/slow-queries" element={<div>Engineer Slow Queries Page</div>} />
          <Route path="/engineer/index-recs" element={<div>Engineer Index Recs Page</div>} />
          <Route path="/engineer/partitions" element={<div>Engineer Partitions Page</div>} />
          <Route path="/engineer/capacity" element={<div>Engineer Capacity Page</div>} />
          <Route path="/engineer/conflicts" element={<div>Engineer Conflicts Page</div>} />
          <Route path="/engineer/events" element={<div>Engineer Events Page</div>} />
        </Route>
      </Route>

      {/* Root & Catch-all redirects */}
      <Route path="/" element={<RoleRedirect />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
