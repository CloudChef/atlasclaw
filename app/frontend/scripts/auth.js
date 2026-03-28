const AUTH_STORAGE_KEY = 'xuanwu_auth_token'
const LEGACY_AUTH_STORAGE_KEY = 'atlasclaw_auth_token'
const AUTH_HEADER_NAME = 'Xuanwu-Authenticate'

export function redirectToLogin() {
  const current = `${window.location.pathname}${window.location.search}`
  const target = encodeURIComponent(current || '/')
  window.location.href = `/login.html?redirect=${target}`
}

export function getAuthToken() {
  try {
    const token = sessionStorage.getItem(AUTH_STORAGE_KEY)
    if (token) {
      return token
    }

    const legacyToken = sessionStorage.getItem(LEGACY_AUTH_STORAGE_KEY) || ''
    if (legacyToken) {
      sessionStorage.setItem(AUTH_STORAGE_KEY, legacyToken)
      sessionStorage.removeItem(LEGACY_AUTH_STORAGE_KEY)
    }

    return legacyToken
  } catch (_error) {
    return ''
  }
}

export function setAuthToken(token) {
  try {
    if (token) {
      sessionStorage.setItem(AUTH_STORAGE_KEY, token)
    }
  } catch (_error) {
    // ignore
  }
}

export function clearAuthToken() {
  try {
    sessionStorage.removeItem(AUTH_STORAGE_KEY)
    sessionStorage.removeItem(LEGACY_AUTH_STORAGE_KEY)
  } catch (_error) {
    // ignore
  }
}

export function installAuthFetchInterceptor() {
  if (window.__xuanwuFetchWrapped) {
    return
  }

  const rawFetch = window.fetch.bind(window)
  window.fetch = (input, init = {}) => {
    const nextInit = { ...init }
    const headers = new Headers(nextInit.headers || {})
    const token = getAuthToken()

    let url = ''
    if (typeof input === 'string') {
      url = input
    } else if (input && typeof input.url === 'string') {
      url = input.url
    }

    const isApiRequest = url.includes('/api/')
    const isLocalLogin = url.includes('/api/auth/local/login')
    if (isApiRequest && !isLocalLogin && token && !headers.has(AUTH_HEADER_NAME)) {
      headers.set(AUTH_HEADER_NAME, token)
    }

    nextInit.headers = headers
    nextInit.credentials = nextInit.credentials || 'include'
    return rawFetch(input, nextInit)
  }

  window.__xuanwuFetchWrapped = true
}

export async function checkAuth({ redirect = true } = {}) {
  try {
    const token = getAuthToken()
    const headers = token ? { [AUTH_HEADER_NAME]: token } : {}

    const response = await fetch('/api/auth/me', {
      method: 'GET',
      headers,
      credentials: 'include'
    })

    if (!response.ok) {
      if (response.status === 401) {
        clearAuthToken()
      }
      if (redirect) {
        redirectToLogin()
      }
      return null
    }

    return await response.json()
  } catch (_error) {
    if (redirect) {
      redirectToLogin()
    }
    return null
  }
}

export async function login(username, password) {
  const response = await fetch('/api/auth/local/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    credentials: 'include',
    body: JSON.stringify({ username, password })
  })

  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.success === false) {
    throw new Error(data.detail || data.error || 'Login failed, please check your username and password')

  }

  const token = data.token || data.access_token || ''
  if (token) {
    setAuthToken(token)
  }

  return data
}

export async function logout({ redirect = true } = {}) {
  try {
    await fetch('/api/auth/logout?redirect=false', {
      method: 'GET',
      credentials: 'include'
    })
  } finally {
    clearAuthToken()
    if (redirect) {
      window.location.href = '/login.html'
    }
  }
}
