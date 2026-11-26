let clearAuthToken
let getAuthToken
let setAuthToken

jest.unstable_mockModule('../../app/frontend/scripts/app.js', () => ({
  getAuthInfo: () => ({ user: { user_id: 'user-1' } })
}))
jest.unstable_mockModule('../../app/frontend/scripts/i18n.js', () => ({
  getCurrentLocale: () => 'en',
  translateIfExists: () => ''
}))
jest.unstable_mockModule('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

let mount
let unmount

beforeAll(async () => {
  ({ clearAuthToken, getAuthToken, setAuthToken } = await import('../../app/frontend/scripts/auth.js'))
  ;({ mount, unmount } = await import('../../app/frontend/scripts/pages/provider-extension.js'))
})

function jwt(exp) {
  const payload = btoa(JSON.stringify({ exp, sub: 'user-1' }))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
  return `header.${payload}.signature`
}

beforeEach(() => {
  sessionStorage.clear()
  delete window.__atlasclawFetchWrapped
  document.body.innerHTML = '<div id="host"></div>'
  setAuthToken(jwt(100))
})

afterEach(() => {
  unmount()
  clearAuthToken()
})

test('the iframe interceptor sends and stores renewed JWTs in shared sessionStorage', async () => {
  const container = document.getElementById('host')
  const mounted = mount(container, { params: { providerType: 'example', navId: 'home' } })
  const frame = container.querySelector('iframe')
  const responseToken = jwt(200)
  const rawFetch = jest.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ 'AtlasClaw-Authenticate': responseToken })
  })
  Object.defineProperty(frame.contentWindow, 'fetch', {
    configurable: true,
    writable: true,
    value: rawFetch
  })
  if (!frame.contentWindow.Headers) {
    Object.defineProperty(frame.contentWindow, 'Headers', { configurable: true, value: Headers })
  }

  await mounted
  frame.dispatchEvent(new Event('load'))
  expect(frame.contentWindow.__atlasclawFetchWrapped).toBe(true)
  await frame.contentWindow.fetch('/api/sessions')

  expect(new Headers(rawFetch.mock.calls[0][1].headers).get('AtlasClaw-Authenticate')).toBe(jwt(100))
  expect(getAuthToken()).toBe(responseToken)
  expect(frame.contentWindow.sessionStorage.getItem('atlasclaw_auth_token')).toBe(responseToken)
})
