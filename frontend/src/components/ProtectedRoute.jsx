/**
 * ProtectedRoute — redirects unauthenticated users to /login.
 *
 * Wraps child routes; renders <Outlet /> when a valid token exists,
 * otherwise navigates to /login preserving the attempted location.
 */
import { Navigate, Outlet, useLocation } from 'react-router-dom'

function ProtectedRoute() {
  const token = localStorage.getItem('token')
  const location = useLocation()

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <Outlet />
}

export default ProtectedRoute
