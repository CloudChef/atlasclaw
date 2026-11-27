jest.unstable_mockModule('../../app/frontend/scripts/auth.js', () => ({
  installAuthFetchInterceptor: jest.fn(),
  redirectToLogin: jest.fn()
}))
jest.unstable_mockModule('../../app/frontend/scripts/app.js', () => ({
  getAuthInfo: () => ({ user: { user_id: 'user-1' } })
}))
jest.unstable_mockModule('../../app/frontend/scripts/config.js', () => ({
  buildAppUrl: path => path,
  stripBasePath: path => path
}))
jest.unstable_mockModule('../../app/frontend/scripts/i18n.js', () => ({
  getCurrentLocale: () => 'en',
  translateIfExists: key => key === 'ext.retry' ? 'Retry' : ''
}))
jest.unstable_mockModule('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

let mount
let unmount

beforeAll(async () => {
  ({ mount, unmount } = await import('../../app/frontend/scripts/pages/provider-extension.js'))
})

beforeEach(() => {
  jest.useFakeTimers()
  document.body.innerHTML = '<div id="host"></div>'
})

afterEach(() => {
  unmount()
  jest.useRealTimers()
})

test('a stale mount timeout cannot replace a remounted ready extension', async () => {
  const container = document.getElementById('host')
  await mount(container, { params: { providerType: 'example', navId: 'first' } })
  const firstFrame = container.querySelector('iframe')

  await mount(container, { params: { providerType: 'example', navId: 'second' } })
  const currentFrame = container.querySelector('iframe')
  window.dispatchEvent(new MessageEvent('message', {
    origin: window.location.origin,
    source: currentFrame.contentWindow,
    data: { type: 'atlasclaw:ready' }
  }))

  jest.advanceTimersByTime(5000)

  expect(firstFrame.isConnected).toBe(false)
  expect(container.querySelector('iframe')).toBe(currentFrame)
  expect(container.querySelector('.provider-extension-failed')).toBeNull()
  expect(container.querySelector('.provider-extension-loading')).toBeNull()
})

test('a slow extension stays mounted and can become ready after the slow-load notice', async () => {
  const container = document.getElementById('host')
  await mount(container, { params: { providerType: 'example', navId: 'slow' } })
  const frame = container.querySelector('iframe')

  jest.advanceTimersByTime(5000)

  expect(container.querySelector('iframe')).toBe(frame)
  expect(container.querySelector('.provider-extension-failed')).toBeNull()
  expect(container.querySelector('.provider-extension-retry')).not.toBeNull()

  window.dispatchEvent(new MessageEvent('message', {
    origin: window.location.origin,
    source: frame.contentWindow,
    data: { type: 'atlasclaw:ready' }
  }))

  expect(container.querySelector('iframe')).toBe(frame)
  expect(container.querySelector('.provider-extension-loading')).toBeNull()
})
