/**
 * Axios HTTP client pre-configured for the Margin-DB backend API.
 *
 * - Base URL: Vite's VITE_API_URL env var, defaulting to http://localhost:8000
 * - Attaches the JWT stored in localStorage as a Bearer token on every request.
 * - On 401 responses the token is cleared and the user is redirected to /login.
 */
import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const client = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

// Attach JWT to every outgoing request
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// Handle 401 — token expired or missing
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default client
