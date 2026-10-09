/**
 * useApi — thin wrapper around the axios client that manages loading / error state.
 *
 * Usage:
 *   const { data, loading, error, request } = useApi()
 *   await request('get', '/events')
 *   await request('post', '/sql', { sql: 'SELECT ...' })
 */
import { useState, useCallback } from 'react'
import client from '../api/client.js'

export function useApi() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const request = useCallback(async (method, url, body = undefined) => {
    setLoading(true)
    setError(null)
    try {
      const response = await client[method](url, body)
      setData(response.data)
      return response.data
    } catch (err) {
      const message =
        err.response?.data?.detail ?? err.message ?? 'Unknown error'
      setError(message)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  return { data, loading, error, request }
}
