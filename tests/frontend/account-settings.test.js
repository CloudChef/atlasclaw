/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

const checkAuthMock = jest.fn(() => Promise.resolve({
  username: 'atlas-admin',
  is_admin: true,
  permissions: {
    provider_configs: { view: true }
  }
}))

let userProviderPayloads = []

jest.mock('../../app/frontend/scripts/auth.js', () => ({
  checkAuth: checkAuthMock,
  installAuthFetchInterceptor: jest.fn(),
  logout: jest.fn()
}))

jest.mock('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

describe('account settings page', () => {
  beforeEach(() => {
    jest.resetModules()
    document.head.innerHTML = ''
    document.body.innerHTML = '<div id="page-root"></div>'
    userProviderPayloads = []

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      const method = String(options.method || 'GET').toUpperCase()

      if (target === '/api/users/me/profile' && (!options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'user-1',
            username: 'atlas-admin',
            display_name: 'Atlas Admin',
            email: 'admin@example.com',
            avatar_url: '',
            roles: { admin: true },
            auth_type: 'local',
            is_active: true,
            is_admin: true,
            created_at: '2026-01-01T09:00:00Z',
            last_login_at: '2026-03-30T15:30:00Z'
          })
        })
      }

      if (target === '/api/users/me/profile' && options.method === 'PUT') {
        const body = JSON.parse(options.body)
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'user-1',
            username: 'atlas-admin',
            display_name: body.display_name,
            email: body.email,
            avatar_url: body.avatar_url,
            roles: { admin: true },
            auth_type: 'local',
            is_active: true,
            is_admin: true,
            created_at: '2026-01-01T09:00:00Z',
            last_login_at: '2026-03-30T15:30:00Z'
          })
        })
      }

      if (target === '/api/users/me/password') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: true })
        })
      }

      if (target === '/api/users/me/avatar' && options.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'user-1',
            username: 'atlas-admin',
            display_name: 'Atlas Admin',
            email: 'admin@example.com',
            avatar_url: '/user-content/avatars/atlas-admin-20260401010101.png',
            roles: { admin: true },
            auth_type: 'local',
            is_active: true,
            is_admin: true,
            created_at: '2026-01-01T09:00:00Z',
            last_login_at: '2026-03-30T15:30:00Z'
          })
        })
      }

      if (target === '/api/service-providers/available-instances') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            providers: [
              {
                provider_type: 'smartcmp',
                instance_name: 'default',
                auth_type: ['cookie', 'provider_token', 'user_token', 'credential'],
                base_url: 'https://console.smartcmp.cloud'
              },
              {
                provider_type: 'smartcmp',
                instance_name: 'backup',
                auth_type: 'cookie',
                base_url: 'https://backup.smartcmp.cloud'
              },
              {
                provider_type: 'dingtalk',
                instance_name: 'ops',
                auth_type: 'app_credentials',
                base_url: 'https://oapi.dingtalk.com'
              }
            ]
          })
        })
      }

      if (target === '/api/service-providers/definitions') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            providers: [
              {
                provider_type: 'smartcmp',
                display_name: 'SmartCMP',
                schema: {
                  fields: [
                    {
                      name: 'base_url',
                      label: 'Base URL',
                      type: 'text',
                      required: true
                    },
                    {
                      name: 'auth_type',
                      type: 'hidden',
                      default: ['cookie', 'provider_token', 'user_token', 'credential']
                    },
                    {
                      name: 'provider_token',
                      label: 'Provider Token',
                      type: 'password',
                      sensitive: true,
                      auth_types: ['provider_token']
                    },
                    {
                      name: 'user_token',
                      label: 'User Token',
                      placeholder: 'Enter user token',
                      type: 'password',
                      required: true,
                      sensitive: true,
                      auth_types: ['user_token']
                    },
                    {
                      name: 'cookie',
                      label: 'Cookie',
                      type: 'password',
                      sensitive: true,
                      auth_types: ['cookie']
                    }
                  ]
                }
              }
            ]
          })
        })
      }

      if (target === '/api/users/me/provider-settings' && method === 'GET') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            providers: {
              smartcmp: {
                default: {
                  configured: true,
                  config: {},
                  updated_at: '2026-04-13T10:00:00Z'
                }
              }
            }
          })
        })
      }

      if (target === '/api/users/me/provider-settings' && method === 'PUT') {
        const payload = JSON.parse(options.body)
        userProviderPayloads.push(payload)
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            providers: {
              [payload.provider_type]: {
                [payload.instance_name]: {
                  configured: true,
                  config: payload.config,
                  updated_at: '2026-04-13T10:30:00Z'
                }
              }
            }
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })
  })

  test('mount loads and renders account profile details', async () => {
    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(container.querySelector('[data-i18n="account.title"]')).not.toBeNull()
    expect(document.getElementById('accountUsername').value).toBe('atlas-admin')
    expect(document.getElementById('accountDisplayName').value).toBe('Atlas Admin')
    expect(document.getElementById('accountDisplayName').readOnly).toBe(true)
    expect(document.getElementById('accountIdentityAdvanced').classList.contains('hidden')).toBe(false)
    expect(document.getElementById('accountSummaryRole').textContent).not.toBe('')
    expect(document.getElementById('accountMainActions').classList.contains('is-hidden')).toBe(true)
    expect(document.getElementById('accountPasswordForm').classList.contains('hidden')).toBe(false)
    expect(container.querySelector('.account-security-card')).toBeNull()
    expect(container.querySelector('.account-preferences-card')).toBeNull()
    expect(container.querySelector('.account-identity-card #accountOpenPasswordBtn')).not.toBeNull()
  })

  test('mount formats timestamps with the active app locale', async () => {
    const localeSpy = jest.spyOn(Date.prototype, 'toLocaleString').mockImplementation((locale) => {
      if (locale === 'en-US') {
        return 'Jan 1, 2026, 09:00'
      }

      return '2026年1月1日 09:00'
    })

    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(document.getElementById('accountCreatedValue').textContent).toBe('Jan 1, 2026, 09:00')
    expect(document.getElementById('accountLastLoginValue').textContent).toBe('Jan 1, 2026, 09:00')
    expect(localeSpy).toHaveBeenCalledWith('en-US', expect.objectContaining({
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23'
    }))
  })

  test('edit button reveals profile actions and unlocks fields', async () => {
    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    document.getElementById('accountEditPublicBtn').click()

    expect(document.getElementById('accountMainActions').classList.contains('is-hidden')).toBe(false)
    expect(document.getElementById('accountDisplayName').readOnly).toBe(false)
    expect(document.getElementById('accountEmail').readOnly).toBe(false)
  })

  test('avatar upload sends form data and updates preview image', async () => {
    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    global.fetch.mockClear()

    const fileInput = document.getElementById('accountAvatarFileInput')
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    Object.defineProperty(fileInput, 'files', {
      configurable: true,
      value: [file]
    })

    fileInput.dispatchEvent(new Event('change'))
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(global.fetch).toHaveBeenCalledWith('/api/users/me/avatar', expect.objectContaining({
      method: 'POST',
      body: expect.any(FormData)
    }))
    expect(document.querySelector('#accountAvatarShell img')).not.toBeNull()
  })

  test('federated profiles render in read-only mode', async () => {
    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      if (target === '/api/users/me/profile' && (!options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'shadow-user-1',
            username: 'sso-user@example.com',
            display_name: 'SSO User',
            email: null,
            avatar_url: '',
            roles: { viewer: true },
            auth_type: 'oidc:test',
            is_active: true,
            is_admin: false,
            created_at: '2026-01-01T09:00:00Z',
            last_login_at: '2026-03-30T15:30:00Z'
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(document.getElementById('accountSummaryRole').textContent).toBe('Viewer')
    expect(document.getElementById('accountRoleValue').textContent).toBe('Viewer')
    expect(document.getElementById('accountEditPublicBtn').disabled).toBe(true)
    expect(document.getElementById('accountAvatarEditBtn').disabled).toBe(true)
    expect(document.getElementById('accountOpenPasswordBtn').disabled).toBe(true)
    expect(document.getElementById('accountMainActions').classList.contains('is-hidden')).toBe(true)
  })

  test('profiles without explicit roles render the empty role summary', async () => {
    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      if (target === '/api/users/me/profile' && (!options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'user-2',
            username: 'plain-user',
            display_name: 'Plain User',
            email: 'plain@example.com',
            avatar_url: '',
            roles: {},
            auth_type: 'local',
            is_active: true,
            is_admin: false,
            created_at: '2026-01-01T09:00:00Z',
            last_login_at: '2026-03-30T15:30:00Z'
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(document.getElementById('accountSummaryRole').textContent).toBe('No explicit roles')
    expect(document.getElementById('accountRoleValue').textContent).toBe('No explicit roles')
  })

  test('account settings embeds personal provider token management without a separate authentication menu', async () => {
    checkAuthMock.mockResolvedValue({
      username: 'ops-admin',
      is_admin: false,
      permissions: {
        provider_configs: { view: true }
      }
    })

    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(document.getElementById('accountAuthConfigCard')).toBeNull()
    expect(document.getElementById('accountOpenAuthConfigBtn')).toBeNull()
    expect(container.textContent).not.toContain('Authentication Configuration')
    expect(document.getElementById('accountProviderTokenCard')).not.toBeNull()
    expect(container.textContent).toContain('Provider Tokens')
    expect(container.textContent).toContain('SmartCMP')
    expect(container.textContent).toContain('default')
    expect(container.textContent).not.toContain('backup')
    expect(container.textContent).not.toContain('Base URL')
    expect(container.textContent).not.toContain('Cookie')
  })

  test('account provider token modal only saves the current user token', async () => {
    const page = await import('../../app/frontend/scripts/pages/account-settings.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    document.querySelector('[data-account-provider-token-configure]').click()

    expect(document.getElementById('accountProviderTokenModal')).not.toBeNull()
    expect(document.querySelector('#accountProviderTokenForm input[name="user_token"]')).not.toBeNull()
    expect(document.querySelector('#accountProviderTokenForm input[name="auth_type"]')).toBeNull()
    expect(document.querySelector('#accountProviderTokenForm input[name="base_url"]')).toBeNull()
    expect(document.querySelector('#accountProviderTokenForm input[name="provider_token"]')).toBeNull()
    expect(document.querySelector('#accountProviderTokenForm input[name="cookie"]')).toBeNull()

    document.querySelector('#accountProviderTokenForm input[name="user_token"]').value = 'user-secret-token'
    document.getElementById('accountProviderTokenForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await new Promise(resolve => setTimeout(resolve, 0))
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(userProviderPayloads).toEqual([
      {
        provider_type: 'smartcmp',
        instance_name: 'default',
        config: {
          user_token: 'user-secret-token'
        }
      }
    ])
  })
})
