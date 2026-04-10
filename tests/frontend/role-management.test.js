const buildAdminPermissions = () => ({
  rbac: { manage_permissions: true },
  skills: { module_permissions: { view: true, enable_disable: true, manage_permissions: true }, skill_permissions: [] },
  channels: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  tokens: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  agent_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  provider_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  model_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  users: { view: true, create: true, edit: true, delete: true, reset_password: true, assign_roles: true, manage_permissions: true },
  roles: { view: true, create: true, edit: true, delete: true }
})

const buildAdminAuthInfo = () => ({
  username: 'atlas-admin',
  is_admin: true,
  permissions: buildAdminPermissions()
})

let mockCheckAuthUser = buildAdminAuthInfo()
let mockStoredAuthInfo = null

jest.mock('../../app/frontend/scripts/auth.js', () => ({
  checkAuth: jest.fn(() => Promise.resolve(mockCheckAuthUser))
}))

jest.mock('../../app/frontend/scripts/app.js', () => ({
  getAuthInfo: jest.fn(() => mockStoredAuthInfo)
}))

jest.mock('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

describe('role management page', () => {
  beforeEach(() => {
    jest.resetModules()
    document.head.innerHTML = ''
    document.body.innerHTML = '<div id="page-root"></div>'
    mockCheckAuthUser = buildAdminAuthInfo()
    mockStoredAuthInfo = null

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)

      if (target === '/api/auth/me') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(buildAdminAuthInfo())
        })
      }

      if (target === '/api/skills') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            skills: [
              { name: 'jira-manager', description: 'Jira integration' },
              { name: 'confluence', description: 'Confluence integration' }
            ]
          })
        })
      }

      if (target === '/api/roles?page=1&page_size=100' && (!options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            roles: [
              {
                id: 'role-admin',
                name: 'Administrator',
                identifier: 'admin',
                description: 'Built-in admin role',
                is_builtin: true,
                is_active: true,
                permissions: {
                  rbac: { manage_permissions: true },
                  skills: { module_permissions: { view: true, enable_disable: true, manage_permissions: true }, skill_permissions: [] },
                  channels: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  tokens: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  agent_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  provider_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  model_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  users: { view: true, create: true, edit: true, delete: true, reset_password: true, assign_roles: true, manage_permissions: true },
                  roles: { view: true, create: true, edit: true, delete: true }
                }
              },
              {
                id: 'role-ops',
                name: 'Operations',
                identifier: 'operations',
                description: 'Operations role',
                is_builtin: false,
                is_active: true,
                permissions: {
                  rbac: { manage_permissions: false },
                  skills: {
                    module_permissions: { view: true, enable_disable: true, manage_permissions: false },
                    skill_permissions: [
                      { skill_id: 'jira-manager', skill_name: 'jira-manager', description: 'Jira integration', authorized: true, enabled: true },
                      { skill_id: 'confluence', skill_name: 'confluence', description: 'Confluence integration', authorized: false, enabled: false }
                    ]
                  },
                  channels: { view: true, create: false, edit: true, delete: false, manage_permissions: false },
                  tokens: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  agent_configs: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  provider_configs: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  model_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  users: { view: true, create: false, edit: false, delete: false, reset_password: false, assign_roles: false, manage_permissions: false },
                  roles: { view: true, create: false, edit: false, delete: false }
                }
              }
            ]
          })
        })
      }

      if (target === '/api/roles' && options.method === 'POST') {
        const body = JSON.parse(options.body)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'role-ops',
            is_builtin: false,
            created_at: '2026-04-03T12:00:00Z',
            updated_at: '2026-04-03T12:00:00Z',
            ...body
          })
        })
      }

      if (target === '/api/roles/role-admin' && options.method === 'PUT') {
        const body = JSON.parse(options.body)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'role-admin',
            is_builtin: true,
            created_at: '2026-04-03T12:00:00Z',
            updated_at: '2026-04-03T12:00:00Z',
            ...body
          })
        })
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({})
      })
    })
  })

  test('mount renders roles list and editor', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(container.querySelector('#roleList .role-list-card')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-summary-card')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="rbac"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="skills"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="agent_configs"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="provider_configs"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="model_configs"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-editor-footer #saveRoleChanges')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-skill-chips')).toBeNull()
    expect(container.querySelector('#roleEditor [data-role-field="name"]').readOnly).toBe(true)
    expect(container.querySelector('#roleEditor [data-role-field="description"]').readOnly).toBe(true)
    expect(container.querySelector('#roleEditor [data-role-field="is_active"]').disabled).toBe(true)
    expect(container.querySelectorAll('#roleEditor .role-permission-grid-skills .role-permission-card')).toHaveLength(1)
    expect(container.querySelector('#roleEditor [data-skill-master-toggle="enabled"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-skill-toggle="authorized"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-skill-master-toggle="enabled"]').disabled).toBe(true)
    expect(container.querySelector('#roleEditor #saveRoleChanges').disabled).toBe(true)
  })

  test('builtin admin defaults all live skills to enabled when no explicit list is stored', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const enabledToggles = [...container.querySelectorAll('[data-skill-toggle="enabled"]')]
    expect(enabledToggles).toHaveLength(2)
    expect(enabledToggles.every(toggle => toggle.checked)).toBe(true)

    const skillsSummary = container.querySelector('[data-module-id="skills"] .role-module-copy span:last-child')
    expect(skillsSummary.textContent.trim()).toBe('3 enabled')
  })

  test('builtin admin permissions are read-only and do not submit updates', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const masterToggle = container.querySelector('[data-skill-master-toggle="enabled"]')
    expect(masterToggle.disabled).toBe(true)

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-admin' && options.method === 'PUT'
    ))
    expect(putCall).toBeUndefined()
  })

  test('existing custom roles keep identifier read-only', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    container.querySelector('[data-role-select="role-ops"]').click()

    const nameInput = container.querySelector('[data-role-field="name"]')
    const identifierInput = container.querySelector('[data-role-field="identifier"]')
    expect(nameInput.readOnly).toBe(false)
    expect(identifierInput.readOnly).toBe(true)
  })

  test('role access helpers allow catalog access without granting edit rights', async () => {
    const readOnlyViewer = {
      username: 'auditor',
      is_admin: false,
      permissions: {
        roles: { view: true, create: false, edit: false, delete: false }
      }
    }
    const { canAccessRoleManagement, hasPermission } = await import('../../app/frontend/scripts/permissions.js')

    expect(canAccessRoleManagement(readOnlyViewer)).toBe(true)
    expect(hasPermission(readOnlyViewer, 'roles.create')).toBe(false)
    expect(hasPermission(readOnlyViewer, 'roles.edit')).toBe(false)
  })

  test('admin badge alone does not bypass permission helpers', async () => {
    const { hasPermission } = await import('../../app/frontend/scripts/permissions.js')

    expect(hasPermission({ username: 'atlas-admin', is_admin: true }, 'roles.view')).toBe(false)
  })

  test('master skill toggle enables all visible skills', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()

    const masterToggle = container.querySelector('[data-skill-master-toggle="enabled"]')
    masterToggle.checked = true
    masterToggle.dispatchEvent(new Event('change', { bubbles: true }))

    const enabledToggles = [...container.querySelectorAll('[data-skill-toggle="enabled"]')]
    expect(enabledToggles).toHaveLength(2)
    expect(enabledToggles.every(toggle => toggle.checked)).toBe(true)
  })

  test('skills module summary excludes hidden internal flags from enabled count', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    container.querySelector('[data-role-select="role-ops"]').click()

    const skillsSummary = container.querySelector('[data-module-id="skills"] .role-module-copy span:last-child')
    expect(skillsSummary.textContent.trim()).toBe('1 enabled')
  })

  test('create role submits current permission-governance payload to roles API', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    document.getElementById('createRoleBtn').click()

    const rbacModule = container.querySelector('[data-module-id="rbac"]')
    rbacModule.click()
    const rbacManageToggle = container.querySelector('[data-module-toggle="rbac"][data-permission-toggle="manage_permissions"]')
    rbacManageToggle.checked = true
    rbacManageToggle.dispatchEvent(new Event('change', { bubbles: true }))

    const nameInput = container.querySelector('[data-role-field="name"]')
    const identifierInput = container.querySelector('[data-role-field="identifier"]')
    nameInput.value = 'Operations'
    nameInput.dispatchEvent(new Event('change', { bubbles: true }))
    identifierInput.value = 'operations'
    identifierInput.dispatchEvent(new Event('change', { bubbles: true }))

    const skillsModule = container.querySelector('[data-module-id="skills"]')
    skillsModule.click()
    const manageToggle = container.querySelector('[data-module-toggle="skills"][data-permission-toggle="manage_permissions"]')
    manageToggle.checked = true
    manageToggle.dispatchEvent(new Event('change', { bubbles: true }))

    const masterToggle = container.querySelector('[data-skill-master-toggle="enabled"]')
    masterToggle.checked = true
    masterToggle.dispatchEvent(new Event('change', { bubbles: true }))

    const usersModule = container.querySelector('[data-module-id="users"]')
    usersModule.click()
    const assignRolesToggle = container.querySelector('[data-module-toggle="users"][data-permission-toggle="assign_roles"]')
    assignRolesToggle.checked = true
    assignRolesToggle.dispatchEvent(new Event('change', { bubbles: true }))

    const userManageToggle = container.querySelector('[data-module-toggle="users"][data-permission-toggle="manage_permissions"]')
    userManageToggle.checked = true
    userManageToggle.dispatchEvent(new Event('change', { bubbles: true }))

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const postCall = global.fetch.mock.calls.find(([url, options]) => url === '/api/roles' && options.method === 'POST')
    expect(postCall).toBeTruthy()

    const [, options] = postCall
    const payload = JSON.parse(options.body)
    expect(payload.permissions.rbac).toEqual({
      manage_permissions: true
    })
    expect(payload.permissions.skills.module_permissions).toEqual({
      view: true,
      enable_disable: true,
      manage_permissions: true
    })
    expect(payload.permissions.skills.skill_permissions).toEqual([
      expect.objectContaining({
        skill_id: 'jira-manager',
        authorized: true,
        enabled: true
      }),
      expect.objectContaining({
        skill_id: 'confluence',
        authorized: true,
        enabled: true
      })
    ])
    expect(payload.permissions.users.assign_roles).toBe(true)
    expect(payload.permissions.users.manage_permissions).toBe(true)
  })

  test('delete modal stays hidden until a custom role opens it', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const deleteModal = document.getElementById('deleteRoleModal')
    expect(deleteModal.classList.contains('hidden')).toBe(true)

    container.querySelector('[data-role-select="role-ops"]').click()
    container.querySelector('#deleteRoleTrigger').click()

    expect(deleteModal.classList.contains('hidden')).toBe(false)

    document.getElementById('deleteRoleCancel').click()

    expect(deleteModal.classList.contains('hidden')).toBe(true)
  })
})
