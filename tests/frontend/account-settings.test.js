jest.mock('../../app/frontend/scripts/auth.js', () => ({
  checkAuth: jest.fn(() => Promise.resolve({ username: 'atlas-admin', is_admin: true })),
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

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)

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
})
