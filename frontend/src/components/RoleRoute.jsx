/**
 * RoleRoute — restricts access to routes based on the JWT role claim.
 *
 * Props:
 *   - allowedRoles: string[]  e.g. ['business_analyst'] or ['db_engineer']
 *
 * Renders <Outlet /> when the user's role is in allowedRoles.
 * Returns HTTP-403-style redirect to /unauthorized otherwise.
 */
import { Navigate, Outlet } from 'react-router-dom'

function parseJwt(token) {
  try {
    const [, payload] = token.split('.')
    return JSON.parse(atob(payload))
  } catch {
    return null
  }
}

function RoleRoute({ allowedRoles = [] }) {
  const token = localStorage.getItem('token')
  const user = token ? parseJwt(token) : null

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (!allowedRoles.includes(user.role)) {
    return <Navigate to="/unauthorized" replace />
  }

  return <Outlet />
}

export default RoleRoute
