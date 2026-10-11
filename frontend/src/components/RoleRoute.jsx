import { Navigate, Outlet } from 'react-router-dom'
import { getToken } from '../api/client.js'

function parseJwt(token) {
  try {
    const [, payload] = token.split('.')
    return JSON.parse(atob(payload))
  } catch {
    return null
  }
}

function RoleRoute({ allowedRoles = [] }) {
  const token = getToken()
  const user = token ? parseJwt(token) : null

  if (!token || !user) {
    return <Navigate to="/login" replace />
  }

  if (!allowedRoles.includes(user.role)) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}

export default RoleRoute
