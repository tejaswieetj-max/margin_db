import { Routes, Route, Navigate } from 'react-router-dom'
import ProtectedRoute from './components/ProtectedRoute.jsx'

function App() {
  return (
    <Routes>
      {/* Public routes */}
      <Route path="/login" element={<div>Login Page</div>} />

      {/* Protected routes — require a valid JWT */}
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<div>Dashboard</div>} />
      </Route>

      {/* Catch-all redirect */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
