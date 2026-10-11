import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

let _token = null

export function setToken(token) {
  _token = token
}

export function clearToken() {
  _token = null
}

export function getToken() {
  return _token
}

const client = axios.create({
  baseURL: BASE_URL,
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
})

// Request interceptor attaches module-level _token
client.interceptors.request.use((config) => {
  if (_token) {
    config.headers['Authorization'] = `Bearer ${_token}`
  }
  return config
})

// Response interceptor handles 401 redirection with /login exception
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const isLoginRequest = error.config?.url?.includes('/login')
      if (!isLoginRequest) {
        clearToken()
        window.location.replace('/login')
      }
    }
    return Promise.reject(error)
  },
)

export default client
