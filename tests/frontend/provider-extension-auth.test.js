const installAuthFetchInterceptor = jest.fn()
const redirectToLogin = jest.fn()

jest.unstable_mockModule('../../app/frontend/scripts/auth.js', () => ({
  installAuthFetchInterceptor,
  redirectToLogin
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
  translateIfExists: () => ''
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
  installAuthFetchInterceptor.mockClear()
  redirectToLogin.mockClear()
  document.body.innerHTML = '<div id="host"></div>'
})

afterEach(() => unmount())

test('installs the host auth interceptor in the same-origin frame before init message', async () => {
  const container = document.getElementById('host')
  await mount(container, { params: { providerType: 'example', navId: 'home' } })
  const frame = container.querySelector('iframe')
  const postMessage = jest.spyOn(frame.contentWindow, 'postMessage')

  frame.dispatchEvent(new Event('load'))

  expect(installAuthFetchInterceptor).toHaveBeenCalledWith(frame.contentWindow)
  expect(postMessage).toHaveBeenCalledWith(
    expect.objectContaining({ type: 'atlasclaw:init' }),
    window.location.origin
  )
  expect(installAuthFetchInterceptor.mock.invocationCallOrder[0])
    .toBeLessThan(postMessage.mock.invocationCallOrder[0])
})

test('redirects the top-level when the frame loads the same-origin login page', async () => {
  const container = document.getElementById('host')
  await mount(container, { params: { providerType: 'example', navId: 'home' } })
  const frame = container.querySelector('iframe')
  const originalUrl = global.URL
  global.URL = class {
    constructor() {
      this.origin = window.location.origin
      this.pathname = '/login.html'
    }
  }

  try {
    frame.dispatchEvent(new Event('load'))
  } finally {
    global.URL = originalUrl
  }

  expect(redirectToLogin).toHaveBeenCalledTimes(1)
})

test('does not redirect when the frame load is cross-origin or after unmount', async () => {
  const container = document.getElementById('host')
  await mount(container, { params: { providerType: 'example', navId: 'home' } })
  const frame = container.querySelector('iframe')
  const originalUrl = global.URL
  global.URL = class {
    constructor() {
      this.origin = 'https://foreign.example'
      this.pathname = '/login.html'
    }
  }

  try {
    frame.dispatchEvent(new Event('load'))
    expect(redirectToLogin).not.toHaveBeenCalled()
    expect(installAuthFetchInterceptor).not.toHaveBeenCalled()
    unmount()
    frame.dispatchEvent(new Event('load'))
    expect(redirectToLogin).not.toHaveBeenCalled()
  } finally {
    global.URL = originalUrl
  }
})
