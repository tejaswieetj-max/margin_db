/**
 * useAuth — authentication state and helpers.
 *
 * Reads the JWT from localStorage, parses the payload, and exposes:
 *   - token: raw JWT string (or null)
 *   - user: decoded payload { sub, role } (or null)
 *   - login(token): persist token and refresh state
 *   - logout(): clear token and redirect to /login
 */
import { useState, useCallback } from 'react'

function parseJwt(token) {
  try {
    const [, payload] = token.split('.')
    return JSON.parse(atob(payload))
  } catch {
    return null
  }
}

export function useAuth() {
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const user = token ? parseJwt(token) : null

  const login = useCallback((newToken) => {
    localStorage.setItem('token', newToken)
    setToken(newToken)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('token')
    setToken(null)
    window.location.href = '/login'
  }, [])

  return { token, user, login, logout }
}
