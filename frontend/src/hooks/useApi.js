import { useState, useCallback } from 'react'
import client from '../api/client.js'

export function useApi() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const request = useCallback(async (method, url, body = undefined, config = {}) => {
    setLoading(true)
    setError(null)
    try {
      const fn = client[method.toLowerCase()].bind(client)
      let response
      if (['post', 'put', 'patch'].includes(method.toLowerCase())) {
        response = await fn(url, body, { timeout: 10000, ...config })
      } else {
        response = await fn(url, { timeout: 10000, ...config })
      }
      setData(response.data)
      return response.data
    } catch (err) {
      setData(null) // Clear page data on error
      const message = err.response?.data?.detail ?? err.message ?? 'Unknown error'
      setError(message)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  return { data, loading, error, request, setData, setError }
}
