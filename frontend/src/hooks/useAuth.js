import { useState, useCallback } from 'react'
import client, { setToken, clearToken, getToken } from '../api/client.js'

function parseJwt(token) {
  try {
    const [, payload] = token.split('.')
    return JSON.parse(atob(payload))
  } catch {
    return null
  }
}

export function useAuth() {
  const currentToken = getToken()
  const [tokenState, setTokenState] = useState(currentToken)
  const decoded = tokenState ? parseJwt(tokenState) : null
  const role = decoded?.role ?? null
  const isAuthenticated = Boolean(tokenState)

  const login = useCallback(async (username, password) => {
    const response = await client.post('/auth/login', { username, password })
    const { token, role: userRole } = response.data
    setToken(token)
    setTokenState(token)
    return { token, role: userRole }
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setTokenState(null)
    window.location.replace('/login')
  }, [])

  return {
    token: tokenState,
    role,
    isAuthenticated,
    user: decoded,
    login,
    logout,
  }
}
