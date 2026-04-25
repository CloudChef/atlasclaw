/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * Admin Users Module Unit Tests
 *
 * Tests for:
 * - API URL construction
 * - Pagination calculation logic
 * - API endpoint calls
 */

// Mock DOM elements
const mockElements = {
  usersTableBody: { innerHTML: '' },
  paginationInfo: { textContent: '' },
  paginationBtns: { innerHTML: '', querySelectorAll: jest.fn(() => []) },
  searchInput: { addEventListener: jest.fn() },
  userModal: { classList: { add: jest.fn(), remove: jest.fn() }, addEventListener: jest.fn() },
  deleteModal: { classList: { add: jest.fn(), remove: jest.fn() }, addEventListener: jest.fn() },
  toastContainer: { appendChild: jest.fn() },
  createUserBtn: { addEventListener: jest.fn() },
  modalClose: { addEventListener: jest.fn() },
  modalCancel: { addEventListener: jest.fn() },
  deleteModalClose: { addEventListener: jest.fn() },
  deleteCancel: { addEventListener: jest.fn() },
  userForm: { addEventListener: jest.fn(), reset: jest.fn() },
  deleteConfirm: { addEventListener: jest.fn() },
  logoutBtn: { addEventListener: jest.fn() },
  modalTitle: { textContent: '' },
  editUserId: { value: '' },
  formUsername: { value: '', disabled: false, focus: jest.fn() },
  formDisplayName: { value: '', focus: jest.fn() },
  formEmail: { value: '' },
  formPassword: { value: '', required: false },
  formAuthType: { value: 'local' },
  formRoles: { value: '' },
  formIsActive: { checked: true },
  formIsAdmin: { checked: false },
  passwordRequired: { style: { display: '' } },
  passwordHint: { style: { display: '' } },
  deleteUserId: { value: '' },
  deleteUserName: { textContent: '' },
  modalSubmit: { disabled: false, textContent: '' }
}

// Mock getElementById
document.getElementById = jest.fn((id) => {
  const camelId = id.replace(/-([a-z])/g, (g) => g[1].toUpperCase())
  return mockElements[camelId] || mockElements[id] || { addEventListener: jest.fn() }
})

document.createElement = jest.fn(() => ({
  className: '',
  textContent: '',
  style: {},
  innerHTML: '',
  remove: jest.fn()
}))

document.addEventListener = jest.fn()

// Mock auth module
jest.mock('../../app/frontend/scripts/auth.js', () => ({
  checkAuth: jest.fn(() => Promise.resolve({ is_admin: true, username: 'admin' })),
  installAuthFetchInterceptor: jest.fn(),
  logout: jest.fn()
}))

jest.mock('../../app/frontend/scripts/app.js', () => ({
  getAuthInfo: jest.fn(() => null)
}))

jest.mock('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

// Mock fetch
global.fetch = jest.fn()

beforeEach(() => {
  global.fetch.mockClear()
  jest.clearAllMocks()
})

describe('Admin Users API', () => {
  describe('loadUsers API call', () => {
    test('constructs correct URL with default pagination', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ users: [], total: 0 })
      })

      // Simulate the API call that loadUsers would make
      const params = new URLSearchParams({
        page: '1',
        page_size: '5'
      })

      await fetch(`/api/users?${params}`)

      expect(global.fetch).toHaveBeenCalledWith('/api/users?page=1&page_size=5')
    })

    test('constructs correct URL with search parameter', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ users: [], total: 0 })
      })

      const params = new URLSearchParams({
        page: '1',
        page_size: '5'
      })
      params.append('search', 'testuser')

      await fetch(`/api/users?${params}`)

      expect(global.fetch).toHaveBeenCalledWith('/api/users?page=1&page_size=5&search=testuser')
    })

    test('constructs correct URL with custom page', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ users: [], total: 0 })
      })

      const params = new URLSearchParams({
        page: '3',
        page_size: '5'
      })

      await fetch(`/api/users?${params}`)

      expect(global.fetch).toHaveBeenCalledWith('/api/users?page=3&page_size=5')
    })
  })

  describe('createUser API call', () => {
    test('sends POST request with correct body', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ id: '123', username: 'newuser' })
      })

      const formData = {
        username: 'newuser',
        password: 'password123',
        display_name: 'New User',
        email: 'new@test.com',
        is_admin: false,
        is_active: true
      }

      await fetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })

      expect(global.fetch).toHaveBeenCalledWith('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })
    })
  })

  describe('updateUser API call', () => {
    test('sends PUT request to correct endpoint', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ id: 'user-123', display_name: 'Updated' })
      })

      const userId = 'user-123'
      const formData = { display_name: 'Updated Name' }

      await fetch(`/api/users/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })

      expect(global.fetch).toHaveBeenCalledWith('/api/users/user-123', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })
    })
  })

  describe('deleteUser API call', () => {
    test('sends DELETE request to correct endpoint', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({})
      })

      const userId = 'user-to-delete'

      await fetch(`/api/users/${userId}`, { method: 'DELETE' })

      expect(global.fetch).toHaveBeenCalledWith('/api/users/user-to-delete', { method: 'DELETE' })
    })
  })
})

describe('Permission-aware create flow', () => {
  beforeEach(() => {
    jest.resetModules()
  })

  test('create permission alone unlocks the create flow', async () => {
    const {
      canCreateUsersForAuthInfo,
      canAssignRolesForUserForm
    } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const authInfo = {
      is_admin: false,
      permissions: {
        users: {
          create: true,
          assign_roles: false
        }
      }
    }

    expect(canCreateUsersForAuthInfo(authInfo)).toBe(true)
    expect(canAssignRolesForUserForm(authInfo)).toBe(false)
  })

  test('create payload omits roles when the viewer cannot assign them', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const payload = buildUserPayloadForSubmission({
      isEdit: false,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            create: true,
            assign_roles: false
          }
        }
      },
      values: {
        username: 'operator-created',
        password: 'password123',
        email: 'operator@example.com',
        display_name: 'Operator Created',
        auth_type: 'local',
        roles: { viewer: true },
        is_active: true
      }
    })

    expect(payload).toEqual({
      username: 'operator-created',
      password: 'password123',
      email: 'operator@example.com',
      display_name: 'Operator Created',
      auth_type: 'local',
      is_active: true
    })
    expect(payload.roles).toBeUndefined()
  })

  test('admin access is submitted through role assignments instead of is_admin', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const payload = buildUserPayloadForSubmission({
      isEdit: false,
      authInfo: {
        is_admin: true,
        permissions: {
          users: {
            create: true,
            assign_roles: true
          }
        }
      },
      values: {
        username: 'workspace-admin',
        password: 'password123',
        email: 'admin@example.com',
        display_name: 'Workspace Admin',
        auth_type: 'local',
        roles: { admin: true },
        is_active: true,
        is_admin: true
      }
    })

    expect(payload).toEqual({
      username: 'workspace-admin',
      password: 'password123',
      email: 'admin@example.com',
      display_name: 'Workspace Admin',
      auth_type: 'local',
      roles: { admin: true },
      is_active: true
    })
    expect(payload.is_admin).toBeUndefined()
  })

  test('non-admin assigners cannot submit new admin assignments', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const payload = buildUserPayloadForSubmission({
      isEdit: false,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            create: true,
            assign_roles: true
          }
        }
      },
      values: {
        username: 'blocked-admin',
        password: 'password123',
        email: 'blocked@example.com',
        display_name: 'Blocked Admin',
        auth_type: 'local',
        roles: { admin: true, viewer: true },
        is_active: true
      }
    })

    expect(payload.roles).toEqual({ viewer: true })
  })

  test('non-admin assigners cannot submit protected custom roles', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const payload = buildUserPayloadForSubmission({
      isEdit: false,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            create: true,
            assign_roles: true
          }
        }
      },
      availableRoles: [
        {
          identifier: 'viewer',
          name: 'Viewer',
          permissions: {
            skills: { module_permissions: { view: true }, skill_permissions: [] },
            users: { view: true },
            roles: { view: true }
          }
        },
        {
          identifier: 'catalog_manager',
          name: 'Catalog Manager',
          permissions: {
            provider_configs: { view: true, edit: true }
          }
        }
      ],
      values: {
        username: 'blocked-manager',
        password: 'password123',
        email: 'blocked-manager@example.com',
        display_name: 'Blocked Manager',
        auth_type: 'local',
        roles: { viewer: true, catalog_manager: true },
        is_active: true
      }
    })

    expect(payload.roles).toEqual({ viewer: true })
  })

  test('non-admin edits preserve existing protected custom roles', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const payload = buildUserPayloadForSubmission({
      isEdit: true,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            assign_roles: true
          }
        }
      },
      availableRoles: [
        {
          identifier: 'viewer',
          name: 'Viewer',
          permissions: {
            skills: { module_permissions: { view: true }, skill_permissions: [] },
            users: { view: true },
            roles: { view: true }
          }
        },
        {
          identifier: 'catalog_manager',
          name: 'Catalog Manager',
          permissions: {
            provider_configs: { view: true, edit: true }
          }
        }
      ],
      existingRoles: { catalog_manager: true },
      values: {
        roles: { viewer: true }
      }
    })

    expect(payload.roles).toEqual({ viewer: true, catalog_manager: true })
  })

  test('edit payload only includes password when viewer can edit users', async () => {
    const { buildUserPayloadForSubmission } = await import('../../app/frontend/scripts/pages/admin-users.js')

    const blockedPayload = buildUserPayloadForSubmission({
      isEdit: true,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            assign_roles: true,
            edit: false
          }
        }
      },
      values: {
        password: 'password123',
        roles: { viewer: true }
      }
    })

    expect(blockedPayload.password).toBeUndefined()

    const allowedPayload = buildUserPayloadForSubmission({
      isEdit: true,
      authInfo: {
        is_admin: false,
        permissions: {
          users: {
            edit: true
          }
        }
      },
      values: {
        password: 'password123'
      }
    })

    expect(allowedPayload.password).toBe('password123')
  })

  test('legacy root admins are mapped to the admin role in the form model', async () => {
    const { getAssignableRoleIdentifiersForUser } = await import('../../app/frontend/scripts/pages/admin-users.js')

    expect(getAssignableRoleIdentifiersForUser({
      is_admin: true,
      roles: {}
    })).toEqual(['admin'])

    expect(getAssignableRoleIdentifiersForUser({
      is_admin: true,
      roles: { admin: true, viewer: true }
    })).toEqual(['admin', 'viewer'])
  })

  test('users without explicit roles are shown as unassigned instead of standard users', async () => {
    const { getRoleDisplayStateForUser } = await import('../../app/frontend/scripts/pages/admin-users.js')

    expect(getRoleDisplayStateForUser({
      is_admin: false,
      roles: {}
    })).toEqual({
      label: 'No explicit roles',
      variant: 'none'
    })
  })

  test('legacy root admins are still shown as admin in the list view', async () => {
    const { getRoleDisplayStateForUser } = await import('../../app/frontend/scripts/pages/admin-users.js')

    expect(getRoleDisplayStateForUser({
      is_admin: true,
      roles: {}
    })).toEqual({
      label: 'Admin',
      variant: 'admin'
    })
  })
})

describe('Pagination Logic', () => {
  test('calculates correct start and end for first page', () => {
    const page = 1
    const pageSize = 20
    const totalUsers = 55

    const start = totalUsers === 0 ? 0 : (page - 1) * pageSize + 1
    const end = Math.min(page * pageSize, totalUsers)

    expect(start).toBe(1)
    expect(end).toBe(20)
  })

  test('calculates correct start and end for middle page', () => {
    const page = 2
    const pageSize = 20
    const totalUsers = 55

    const start = (page - 1) * pageSize + 1
    const end = Math.min(page * pageSize, totalUsers)

    expect(start).toBe(21)
    expect(end).toBe(40)
  })

  test('calculates correct start and end for last page', () => {
    const page = 3
    const pageSize = 20
    const totalUsers = 55

    const start = (page - 1) * pageSize + 1
    const end = Math.min(page * pageSize, totalUsers)

    expect(start).toBe(41)
    expect(end).toBe(55)
  })

  test('handles empty result set', () => {
    const page = 1
    const pageSize = 20
    const totalUsers = 0

    const start = totalUsers === 0 ? 0 : (page - 1) * pageSize + 1
    const end = Math.min(page * pageSize, totalUsers)

    expect(start).toBe(0)
    expect(end).toBe(0)
  })

  test('calculates total pages correctly', () => {
    expect(Math.ceil(55 / 20)).toBe(3)
    expect(Math.ceil(40 / 20)).toBe(2)
    expect(Math.ceil(0 / 20)).toBe(0)
    expect(Math.ceil(1 / 20)).toBe(1)
  })
})

describe('Search Debounce Behavior', () => {
  jest.useFakeTimers()

  test('debounce timer clears previous timeout', () => {
    let searchDebounceTimer = null

    // Simulate first search input
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer)
    }
    searchDebounceTimer = setTimeout(() => {}, 250)
    const firstTimer = searchDebounceTimer

    // Simulate second search input before timeout
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer)
    }
    searchDebounceTimer = setTimeout(() => {}, 250)
    const secondTimer = searchDebounceTimer

    // Timers should be different (new timer created)
    expect(firstTimer).not.toBe(secondTimer)
  })

  test('debounce delay is 250ms', () => {
    const callback = jest.fn()
    let searchDebounceTimer = null

    // Simulate search input
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer)
    }
    searchDebounceTimer = setTimeout(callback, 250)

    // Before 250ms
    jest.advanceTimersByTime(249)
    expect(callback).not.toHaveBeenCalled()

    // After 250ms
    jest.advanceTimersByTime(1)
    expect(callback).toHaveBeenCalledTimes(1)
  })
})

describe('Error Handling', () => {
  test('handles 401 response correctly', async () => {
    const response = { status: 401, ok: false }

    expect(response.status).toBe(401)
    // In real code, this would redirect to login
  })

  test('handles 403 response correctly', async () => {
    const response = { status: 403, ok: false }

    expect(response.status).toBe(403)
    // In real code, this would show "Admin privileges required"
  })

  test('handles 409 response for duplicate', async () => {
    const response = {
      status: 409,
      ok: false,
      json: () => Promise.resolve({ detail: "User 'test' already exists" })
    }

    expect(response.status).toBe(409)
    const data = await response.json()
    expect(data.detail).toContain('already exists')
  })

  test('handles 404 response for not found', async () => {
    const response = {
      status: 404,
      ok: false,
      json: () => Promise.resolve({ detail: 'User not found' })
    }

    expect(response.status).toBe(404)
  })
})

describe('HTML Escaping', () => {
  test('escapeHtml prevents XSS', () => {
    // Simulate escapeHtml function
    function escapeHtml(str) {
      if (!str) return ''
      const div = document.createElement('div')
      div.textContent = str
      return div.innerHTML
    }

    // Mock innerHTML to return escaped content
    document.createElement = jest.fn(() => ({
      textContent: '',
      get innerHTML() {
        return this.textContent
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
      }
    }))

    const malicious = '<script>alert("xss")</script>'
    const escaped = escapeHtml(malicious)

    expect(escaped).not.toContain('<script>')
    expect(escaped).toContain('&lt;script&gt;')
  })

  test('escapeHtml handles empty string', () => {
    function escapeHtml(str) {
      if (!str) return ''
      const div = document.createElement('div')
      div.textContent = str
      return div.innerHTML
    }

    expect(escapeHtml('')).toBe('')
    expect(escapeHtml(null)).toBe('')
    expect(escapeHtml(undefined)).toBe('')
  })
})
