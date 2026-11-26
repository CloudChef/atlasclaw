let clearAuthToken
let getAuthToken
let installAuthFetchInterceptor
let setAuthToken

const AUTH_KEY = 'atlasclaw_auth_token'
const AUTH_HEADER = 'AtlasClaw-Authenticate'
let rawFetch

beforeAll(async () => {
  ({ clearAuthToken, getAuthToken, installAuthFetchInterceptor, setAuthToken } =
    await import('../../app/frontend/scripts/auth.js'))
})

function response(token, ok = true) {
  return {
    ok,
    headers: new Headers(token ? { [AUTH_HEADER]: token } : {})
  }
}

function jwt(exp, sub = 'user-1', iat = exp - 1000) {
  const payload = btoa(JSON.stringify({ exp, sub, iat }))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
  return `header.${payload}.signature`
}

beforeEach(() => {
  sessionStorage.clear()
  delete window.__atlasclawFetchWrapped
  rawFetch = jest.fn()
  global.fetch = rawFetch
  setAuthToken(jwt(2000))
  installAuthFetchInterceptor()
})

afterEach(() => {
  clearAuthToken()
})

describe('sliding JWT browser session', () => {
  test('stores a renewed JWT from a successful same-origin API response without consuming the body', async () => {
    const renewed = jwt(3000)
    const result = response(renewed)
    rawFetch.mockResolvedValue(result)

    const returned = await fetch('/api/sessions')

    expect(returned).toBe(result)
    expect(getAuthToken()).toBe(renewed)
  })

  test('preserves Request headers while allowing init headers to override them', async () => {
    rawFetch.mockResolvedValue(response())
    const request = {
      url: `${window.location.origin}/api/sessions`,
      headers: new Headers({ 'X-From-Request': 'kept', 'X-Override': 'request' })
    }

    await fetch(request, { headers: { 'X-Override': 'init' } })

    const passedHeaders = new Headers(rawFetch.mock.calls[0][1].headers)
    expect(passedHeaders.get('X-From-Request')).toBe('kept')
    expect(passedHeaders.get('X-Override')).toBe('init')
  })

  test('does not send auth or credentials or store renewal from cross-origin requests', async () => {
    rawFetch.mockResolvedValue(response(jwt(4000)))

    await fetch({
      url: 'https://other.example/api/sessions',
      headers: new Headers({ [AUTH_HEADER]: jwt(5000) })
    })

    const [, init] = rawFetch.mock.calls[0]
    expect(new Headers(init.headers).has(AUTH_HEADER)).toBe(false)
    expect(init.credentials).toBe('omit')
    expect(getAuthToken()).toBe(jwt(2000))
  })

  test('does not let an older response overwrite a newer renewal', async () => {
    let finishOld
    let finishNew
    rawFetch
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve }))

    const oldRequest = fetch('/api/first')
    const newRequest = fetch('/api/second')
    finishNew(response(jwt(4000)))
    await newRequest
    finishOld(response(jwt(3000)))
    await oldRequest

    expect(getAuthToken()).toBe(jwt(4000))
  })

  test('uses issued-at ordering when concurrent renewals have the same expiry', async () => {
    const initial = jwt(3000, 'user-1', 1000)
    const later = jwt(3000, 'user-1', 2500)
    const earlier = jwt(3000, 'user-1', 2000)
    setAuthToken(initial)
    let finishEarlier
    let finishLater
    rawFetch
      .mockReturnValueOnce(new Promise(resolve => { finishEarlier = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { finishLater = resolve }))

    const earlierRequest = fetch('/api/first')
    const laterRequest = fetch('/api/second')
    finishLater(response(later))
    await laterRequest
    finishEarlier(response(earlier))
    await earlierRequest

    expect(getAuthToken()).toBe(later)
  })

  test('does not restore auth after the token was cleared while a request was in flight', async () => {
    let finish
    rawFetch.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))

    const pending = fetch('/api/sessions')
    clearAuthToken()
    finish(response(jwt(5000)))
    await pending

    expect(sessionStorage.getItem(AUTH_KEY)).toBeNull()
  })

  test('does not consume renewal from an unsuccessful API response', async () => {
    rawFetch.mockResolvedValue(response(jwt(5000), false))

    await fetch('/api/sessions')

    expect(getAuthToken()).toBe(jwt(2000))
  })

  test('does not consume renewal for a request using another explicit identity token', async () => {
    rawFetch.mockResolvedValue(response(jwt(6000, 'other-user')))

    await fetch('/api/sessions', {
      headers: { [AUTH_HEADER]: jwt(5000, 'other-user') }
    })

    expect(getAuthToken()).toBe(jwt(2000))
  })

  test('preserves explicit bearer API credentials without adding or consuming a JWT', async () => {
    rawFetch.mockResolvedValue(response(jwt(6000)))

    await fetch('/api/sessions', { headers: { Authorization: 'Bearer api-token' } })

    const [, init] = rawFetch.mock.calls[0]
    const headers = new Headers(init.headers)
    expect(headers.get('Authorization')).toBe('Bearer api-token')
    expect(headers.has(AUTH_HEADER)).toBe(false)
    expect(getAuthToken()).toBe(jwt(2000))
  })
})
