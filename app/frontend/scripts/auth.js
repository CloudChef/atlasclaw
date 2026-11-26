/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { buildApiUrl, buildAppUrl, rewriteManagedAppUrl } from './config.js'
import { clearSession } from './session-manager.js'

const AUTH_STORAGE_KEY = 'atlasclaw_auth_token'
const AUTH_HEADER_NAME = 'AtlasClaw-Authenticate'
let authTokenEpoch = 0

export function redirectToLogin() {
  const current = `${window.location.pathname}${window.location.search}`
  const target = encodeURIComponent(current || '/')
  window.location.href = buildAppUrl(`/login.html?redirect=${target}`)
}

export function getAuthToken() {
  try {
    return sessionStorage.getItem(AUTH_STORAGE_KEY) || ''
  } catch (_error) {
    return ''
  }
}

export function setAuthToken(token) {
  try {
    if (token) {
      if (window.sessionStorage.getItem(AUTH_STORAGE_KEY) !== token) {
        authTokenEpoch += 1
      }
      sessionStorage.setItem(AUTH_STORAGE_KEY, token)
    }
  } catch (_error) {
    // ignore
  }
}

export function clearAuthToken() {
  try {
    if (window.sessionStorage.getItem(AUTH_STORAGE_KEY)) {
      authTokenEpoch += 1
    }
    sessionStorage.removeItem(AUTH_STORAGE_KEY)
  } catch (_error) {
    // ignore
  }
}

function tokenClaims(token) {
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, '=')))
  } catch (_error) {
    return null
  }
}

function isNewerToken(candidate, current) {
  const nextClaims = tokenClaims(candidate)
  const currentClaims = tokenClaims(current)
  if (!nextClaims || !currentClaims) return false
  if (nextClaims.sub && currentClaims.sub && nextClaims.sub !== currentClaims.sub) return false

  const nextExpiry = Number(nextClaims.exp) || 0
  const currentExpiry = Number(currentClaims.exp) || 0
  return nextExpiry > currentExpiry || (
    nextExpiry === currentExpiry && (Number(nextClaims.iat) || 0) > (Number(currentClaims.iat) || 0)
  )
}

export function installAuthFetchInterceptor(targetWindow = window) {
  if (!targetWindow || targetWindow.__atlasclawFetchWrapped) {
    return
  }

  const rawFetch = targetWindow.fetch.bind(targetWindow)
  targetWindow.fetch = async (input, init = {}) => {
    const nextInit = { ...init }
    const headers = new targetWindow.Headers(input?.headers || {})
    new targetWindow.Headers(nextInit.headers || {}).forEach((value, name) => {
      headers.set(name, value)
    })
    let token = ''
    try {
      token = targetWindow.sessionStorage.getItem(AUTH_STORAGE_KEY) || ''
    } catch (_error) {
      // ignore
    }

    let url = ''
    if (typeof input === 'string') {
      url = rewriteManagedAppUrl(input)
      input = url
    } else if (input && typeof input.url === 'string') {
      url = input.url
    }

    let requestUrl
    try {
      requestUrl = new URL(url, targetWindow.location.href)
    } catch (_error) {
      requestUrl = null
    }
    const isSameOrigin = Boolean(requestUrl && requestUrl.origin === targetWindow.location.origin)
    const isApiRequest = Boolean(isSameOrigin && requestUrl.pathname.includes('/api/'))
    const isLocalLogin = Boolean(isApiRequest && requestUrl.pathname.includes('/api/auth/local/login'))
    const suppliedAuthToken = headers.get(AUTH_HEADER_NAME) || ''
    if (
      isSameOrigin && isApiRequest && !isLocalLogin && token
      && !suppliedAuthToken && !headers.has('Authorization')
    ) {
      headers.set(AUTH_HEADER_NAME, token)
    }
    if (!isSameOrigin) {
      headers.delete(AUTH_HEADER_NAME)
    }

    nextInit.headers = headers
    nextInit.credentials = isSameOrigin ? (nextInit.credentials || 'include') : 'omit'
    const requestToken = headers.get(AUTH_HEADER_NAME) || ''
    const requestEpoch = authTokenEpoch
    const usesSessionToken = Boolean(token && requestToken === token)
    const response = await rawFetch(input, nextInit)
    if (
      isApiRequest && !isLocalLogin && usesSessionToken && requestEpoch === authTokenEpoch
      && response?.ok
    ) {
      const renewedToken = response.headers?.get(AUTH_HEADER_NAME) || ''
      const currentToken = (() => {
        try {
          return targetWindow.sessionStorage.getItem(AUTH_STORAGE_KEY) || ''
        } catch (_error) {
          return ''
        }
      })()
      if (renewedToken && isNewerToken(renewedToken, currentToken)) {
        try {
          targetWindow.sessionStorage.setItem(AUTH_STORAGE_KEY, renewedToken)
        } catch (_error) {
          // ignore
        }
      }
    }
    return response
  }

  targetWindow.__atlasclawFetchWrapped = true
}

export async function checkAuth({ redirect = true } = {}) {
  try {
    const token = getAuthToken()
    const headers = token ? { [AUTH_HEADER_NAME]: token } : {}

    const response = await fetch(buildApiUrl('/api/auth/me'), {
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
  const response = await fetch(buildApiUrl('/api/auth/local/login'), {
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
  clearSession()

  return data
}

export async function logout({ redirect = true } = {}) {
  try {
    await fetch(buildApiUrl('/api/auth/logout?redirect=false'), {
      method: 'GET',
      credentials: 'include'
    })
  } finally {
    clearAuthToken()
    clearSession()
    if (redirect) {
      window.location.href = buildAppUrl('/login.html')
    }
  }
}
