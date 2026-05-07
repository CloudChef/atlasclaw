/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

const buildChannelPermissions = (overrides = {}) => [
  { channel_type: 'websocket', channel_name: 'WebSocket', allowed: true },
  { channel_type: 'feishu', channel_name: 'Feishu', allowed: true },
  { channel_type: 'dingtalk', channel_name: 'DingTalk', allowed: true }
].map(channel => ({ ...channel, ...overrides[channel.channel_type] }))

const buildChannelsPermission = (overrides = {}, managePermissions = false, extraPermissions = [], allowAll = false) => ({
  module_permissions: { manage_permissions: managePermissions },
  allow_all: allowAll,
  channel_permissions: [
    ...buildChannelPermissions(overrides),
    ...extraPermissions
  ]
})

const buildProviderPermissions = (overrides = {}, extraPermissions = []) => [
  { provider_type: 'smartcmp', display_name: 'SmartCMP', instance_name: 'default', allowed: true },
  { provider_type: 'jira', display_name: 'Jira', instance_name: 'prod', allowed: true }
].map(provider => ({
  ...provider,
  ...(overrides[`${provider.provider_type}::${provider.instance_name}`] || {})
})).concat(extraPermissions)

const buildProvidersPermission = (overrides = {}, managePermissions = false, extraPermissions = [], allowAll = false) => ({
  module_permissions: { manage_permissions: managePermissions },
  allow_all: allowAll,
  provider_permissions: buildProviderPermissions(overrides, extraPermissions)
})

const buildSkillPermissions = (overrides = {}) => [
  { skill_id: 'jira-manager', skill_name: 'jira-manager', description: 'Jira integration', runtime_enabled: true, authorized: true, enabled: true },
  { skill_id: 'confluence', skill_name: 'confluence', description: 'Confluence integration', runtime_enabled: true, authorized: true, enabled: true },
  { skill_id: 'pdf', skill_name: 'pdf', description: 'PDF helper', runtime_enabled: false, authorized: false, enabled: false }
].map(skill => ({
  ...skill,
  ...(overrides[skill.skill_id] || {})
}))

const buildAdminPermissions = () => ({
  skills: { module_permissions: { view: true, enable_disable: true, manage_permissions: true }, allow_all: true, skill_permissions: buildSkillPermissions() },
  providers: buildProvidersPermission({}, true, [], true),
  channels: buildChannelsPermission({}, true, [], true),
  tokens: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  agent_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  provider_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  model_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
  users: { view: true, create: true, edit: true, delete: true, assign_roles: true, manage_permissions: true },
  roles: { view: true, create: true, edit: true, delete: true, manage_permissions: true }
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
          json: () => Promise.resolve(mockCheckAuthUser)
        })
      }

      if (target === '/api/skills') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            skills: [
              { name: 'jira-manager', description: 'Jira integration', runtime_enabled: true, type: 'executable' },
              { name: 'confluence', description: 'Confluence integration', runtime_enabled: true, type: 'executable' },
              { name: 'pdf', description: 'PDF helper', runtime_enabled: false, type: 'markdown' }
            ]
          })
        })
      }

      if (target === '/api/service-providers/available-instances?include_all=true') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            providers: [
              {
                provider_type: 'smartcmp',
                display_name: 'SmartCMP',
                instance_name: 'default',
                base_url: 'https://cmp.example.com',
                auth_type: ['provider_token', 'user_token'],
                config_keys: []
              },
              {
                provider_type: 'jira',
                display_name: 'Jira',
                instance_name: 'prod',
                base_url: 'https://jira.example.com',
                auth_type: 'user_token',
                config_keys: []
              }
            ]
          })
        })
      }

      if (target === '/api/channels?include_all=true') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([
            { type: 'websocket', name: 'WebSocket', mode: 'bidirectional', connection_count: 1 },
            { type: 'feishu', name: 'Feishu', mode: 'bidirectional', connection_count: 0 },
            { type: 'dingtalk', name: 'DingTalk', mode: 'bidirectional', connection_count: 0 }
          ])
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
                  skills: { module_permissions: { view: true, enable_disable: true, manage_permissions: true }, allow_all: true, skill_permissions: buildSkillPermissions() },
                  providers: buildProvidersPermission({}, true, [], true),
                  channels: buildChannelsPermission({}, true, [], true),
                  tokens: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  agent_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  provider_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  model_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
                  users: { view: true, create: true, edit: true, delete: true, assign_roles: true, manage_permissions: true },
                  roles: { view: true, create: true, edit: true, delete: true, manage_permissions: true }
                }
              },
              {
                id: 'role-user',
                name: 'Standard User',
                identifier: 'user',
                description: 'Built-in user role',
                is_builtin: true,
                is_active: true,
                permissions: {
                  skills: { module_permissions: { view: false, enable_disable: false, manage_permissions: false }, allow_all: false, skill_permissions: buildSkillPermissions() },
                  providers: buildProvidersPermission(),
                  channels: buildChannelsPermission(),
                  tokens: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  agent_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  provider_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  model_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  users: { view: false, create: false, edit: false, delete: false, assign_roles: false, manage_permissions: false },
                  roles: { view: false, create: false, edit: false, delete: false, manage_permissions: false }
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
                  skills: {
                    module_permissions: { view: true, enable_disable: true, manage_permissions: false },
                    allow_all: false,
                    skill_permissions: [
                      { skill_id: 'jira-manager', skill_name: 'jira-manager', description: 'Jira integration', authorized: true, enabled: true },
                      { skill_id: 'confluence', skill_name: 'confluence', description: 'Confluence integration', authorized: false, enabled: false }
                    ]
                  },
                  providers: {
                    module_permissions: { manage_permissions: false },
                    provider_permissions: buildProviderPermissions(
                      { 'jira::prod': { allowed: false } },
                      [{ provider_type: 'ghost', display_name: 'Ghost', instance_name: 'old', allowed: true }]
                    )
                  },
                  channels: buildChannelsPermission(
                    { feishu: { allowed: false } },
                    false,
                    [{ channel_type: 'ghost', channel_name: 'Ghost', allowed: true }]
                  ),
                  tokens: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  agent_configs: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  provider_configs: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
                  model_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
                  users: { view: true, create: false, edit: false, delete: false, assign_roles: false, manage_permissions: false },
                  roles: { view: true, create: false, edit: false, delete: false, manage_permissions: false }
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

      if (target === '/api/roles/role-user' && options.method === 'PUT') {
        const body = JSON.parse(options.body)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'role-user',
            name: 'Standard User',
            identifier: 'user',
            description: 'Built-in user role',
            is_builtin: true,
            is_active: true,
            created_at: '2026-04-03T12:00:00Z',
            updated_at: '2026-04-03T12:00:00Z',
            ...body
          })
        })
      }

      if (target === '/api/roles/role-ops' && options.method === 'PUT') {
        const body = JSON.parse(options.body)
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            id: 'role-ops',
            name: 'Operations',
            identifier: 'operations',
            description: 'Operations role',
            is_builtin: false,
            is_active: true,
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
    expect(container.querySelector('#roleList .role-list-card p')).toBeNull()
    expect(container.querySelector('#roleEditor .role-summary-card')).not.toBeNull()
    const summaryFields = [...container.querySelectorAll('#roleEditor .role-summary-grid > *')]
    expect(summaryFields[0].querySelector('[data-role-field="name"]')).not.toBeNull()
    expect(summaryFields[1].textContent).toContain('Identifier:')
    expect(summaryFields[2].querySelector('[data-role-field="is_active"]')).not.toBeNull()
    expect(summaryFields[3].querySelector('[data-role-field="description"]').getAttribute('rows')).toBe('2')
    expect(container.querySelector('#roleEditor [data-module-id="rbac"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="skills"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="providers"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="tokens"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="agent_configs"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="provider_configs"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-module-id="model_configs"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-editor-footer #saveRoleChanges')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-skill-chips')).toBeNull()
    expect(container.querySelector('#roleEditor [data-role-field="name"]').readOnly).toBe(true)
    expect(container.querySelector('#roleEditor [data-role-field="description"]').readOnly).toBe(true)
    expect(container.querySelector('#roleEditor [data-role-field="is_active"]').disabled).toBe(true)
    expect(container.querySelectorAll('#roleEditor .role-permission-table tbody tr')).toHaveLength(2)
    expect(container.querySelector('#roleEditor .role-permission-table [data-permission-toggle="manage_permissions"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-permission-card')).toBeNull()
    expect(container.querySelector('#roleEditor .role-access-all-card')).toBeNull()
    expect(container.querySelector('#roleEditor [data-skill-master-toggle="enabled"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-access-all-toggle="skills"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-permission-table [data-access-all-toggle="skills"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-module-action="select-all"]')).toBeNull()
    expect(container.querySelector('#roleEditor [title]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-tooltip]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-skill-toggle="authorized"]')).toBeNull()
    expect(container.querySelector('#roleEditor .role-skill-note')).toBeNull()
    expect(container.querySelector('#roleEditor .role-allowlist-toolbar-controls')).not.toBeNull()
    expect(container.textContent).not.toContain('Search the live skill catalog and decide which skills this role can enable.')
    expect(container.textContent).not.toContain('New skill behavior')
    // Admin CAN manage access-all, while per-row selection is read-only when access-all is enabled.
    expect(container.querySelector('#roleEditor [data-access-all-toggle="skills"]').checked).toBe(true)
    expect(container.querySelector('#roleEditor [data-access-all-toggle="skills"]').disabled).toBe(false)
    expect(container.querySelector('#roleEditor [data-skill-master-toggle="enabled"]').disabled).toBe(true)
    expect(container.querySelector('#roleEditor #saveRoleChanges').disabled).toBe(false)

    container.querySelector('[data-module-id="providers"]').click()
    await Promise.resolve()
    expect(container.querySelector('#roleEditor .role-skill-note')).toBeNull()
    expect(container.querySelector('#roleEditor [data-provider-master-toggle="allowed"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-access-all-toggle="providers"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-permission-table [data-access-all-toggle="providers"]')).not.toBeNull()
    expect(container.textContent).not.toContain('Search registered provider instances and decide which ones this role can use.')
    expect(container.textContent).not.toContain('Default allow behavior')

    container.querySelector('[data-module-id="channels"]').click()
    await Promise.resolve()
    expect(container.querySelector('#roleEditor .role-permission-table tbody tr:first-child [data-permission-toggle="manage_permissions"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-permission-toggle="view"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-permission-toggle="create"]')).toBeNull()
    expect(container.querySelector('#roleEditor [data-channel-master-toggle="allowed"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor [data-access-all-toggle="channels"]')).not.toBeNull()
    expect(container.querySelector('#roleEditor .role-permission-table [data-access-all-toggle="channels"]')).not.toBeNull()
    expect(container.querySelectorAll('#roleEditor [data-channel-toggle="allowed"]')).toHaveLength(3)
    expect(container.querySelector('#roleEditor .role-skill-note')).toBeNull()
    expect(container.textContent).not.toContain('Search registered channel types and decide which ones this role can manage.')
    expect(container.textContent).not.toContain('Explicit allow behavior')
  })

  test('builtin admin renders initialized skill permissions', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const enabledToggles = [...container.querySelectorAll('[data-skill-toggle="enabled"]')]
    expect(enabledToggles).toHaveLength(3)
    expect(enabledToggles.filter(toggle => toggle.checked)).toHaveLength(2)
    expect(container.querySelector('[data-skill-id="pdf"]').checked).toBe(false)
    expect(container.querySelector('[data-skill-id="pdf"]').disabled).toBe(true)

    const skillsSummary = container.querySelector('[data-module-id="skills"] .role-module-copy span:last-child')
    expect(skillsSummary.textContent.trim()).toBe('2 enabled')
  })

  test('builtin admin access-all leaves item selection read-only and save is allowed', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const masterToggle = container.querySelector('[data-skill-master-toggle="enabled"]')
    expect(masterToggle.disabled).toBe(true)
    expect(container.querySelector('[data-skill-id="jira-manager"]').disabled).toBe(true)
    expect(container.querySelector('[data-access-all-toggle="skills"]').checked).toBe(true)
    expect(container.querySelector('[data-access-all-toggle="skills"]').disabled).toBe(false)

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-admin' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
  })

  test('skills access-all does not grant module view or enable-disable permissions', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-user"]').click()

    const accessAllToggle = container.querySelector('[data-access-all-toggle="skills"]')
    expect(accessAllToggle.checked).toBe(false)
    accessAllToggle.checked = true
    accessAllToggle.dispatchEvent(new Event('change', { bubbles: true }))

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-user' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.skills.allow_all).toBe(true)
    expect(payload.permissions.skills.module_permissions).toEqual({
      view: false,
      enable_disable: false,
      manage_permissions: false
    })
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
        roles: { view: true, create: false, edit: false, delete: false, manage_permissions: false }
      }
    }
    const {
      canAccessChannelManagement,
      canAccessRoleManagement,
      hasPermission
    } = await import('../../app/frontend/scripts/permissions.js')

    expect(canAccessRoleManagement(readOnlyViewer)).toBe(true)
    expect(hasPermission(readOnlyViewer, 'roles.create')).toBe(false)
    expect(hasPermission(readOnlyViewer, 'roles.edit')).toBe(false)
    expect(canAccessChannelManagement({
      permissions: { channels: { allow_all: true, channel_permissions: [] } }
    })).toBe(true)
  })

  test('page access guard rejects users without role management permissions', async () => {
    mockCheckAuthUser = {
      username: 'plain-user',
      is_admin: false,
      permissions: {
        channels: {
          module_permissions: { manage_permissions: true },
          channel_permissions: buildChannelPermissions()
        },
        users: {
          view: true,
          create: true,
          edit: true,
          delete: true,
          assign_roles: true,
          manage_permissions: true
        },
        roles: { view: false, create: false, edit: false, delete: false, manage_permissions: false }
      }
    }
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(container.querySelector('.role-management-page')).toBeNull()
  })

  test('roles.view users can inspect roles but cannot create save or delete', async () => {
    mockCheckAuthUser = {
      username: 'role-viewer',
      is_admin: false,
      permissions: {
        roles: { view: true, create: false, edit: false, delete: false, manage_permissions: false }
      }
    }
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    expect(container.querySelector('#createRoleBtn').disabled).toBe(true)
    expect(container.querySelector('#saveRoleChanges').disabled).toBe(true)
    expect(container.querySelector('#deleteRoleTrigger')).toBeNull()
    expect(container.querySelector('[data-role-field="name"]').readOnly).toBe(true)
    container.querySelector('[data-module-id="roles"]').click()
    expect(container.querySelector('[data-module-toggle="roles"][data-permission-toggle="manage_permissions"]').disabled).toBe(true)
  })

  test('module permission managers can only edit governed modules', async () => {
    mockCheckAuthUser = {
      username: 'channel-governor',
      is_admin: false,
      permissions: {
        roles: { view: true, create: false, edit: false, delete: false, manage_permissions: false },
        channels: { module_permissions: { manage_permissions: true }, channel_permissions: buildChannelPermissions() }
      }
    }
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()

    container.querySelector('[data-module-id="channels"]').click()
    expect(container.querySelector('[data-module-toggle="channels"][data-permission-toggle="manage_permissions"]').disabled).toBe(false)
    expect(container.querySelector('[data-channel-master-toggle="allowed"]').disabled).toBe(false)

    container.querySelector('[data-module-id="roles"]').click()
    expect(container.querySelector('[data-module-toggle="roles"][data-permission-toggle="view"]').disabled).toBe(true)
    expect(container.querySelector('[data-module-toggle="roles"][data-permission-toggle="manage_permissions"]').disabled).toBe(true)
  })

  test('system-managed builtin user role keeps metadata and locked module permissions read-only', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-user"]').click()

    expect(container.querySelector('[data-role-field="name"]').readOnly).toBe(true)
    expect(container.querySelector('[data-role-field="description"]').readOnly).toBe(true)
    expect(container.querySelector('[data-role-field="is_active"]').disabled).toBe(true)

    container.querySelector('[data-module-id="channels"]').click()
    expect(container.querySelector('[data-module-toggle="channels"][data-permission-toggle="view"]')).toBeNull()
    const managePermissionsToggle = container.querySelector('[data-module-toggle="channels"][data-permission-toggle="manage_permissions"]')
    expect(managePermissionsToggle.checked).toBe(false)
    managePermissionsToggle.checked = true
    managePermissionsToggle.dispatchEvent(new Event('change', { bubbles: true }))
    expect(container.querySelector('[data-module-toggle="channels"][data-permission-toggle="manage_permissions"]').checked).toBe(true)
    expect(container.querySelector('[data-channel-master-toggle="allowed"]').disabled).toBe(false)
    expect(container.querySelector('[data-module-action="select-all"]')).toBeNull()

    container.querySelector('[data-module-id="skills"]').click()
    expect(container.querySelector('[data-skill-master-toggle="enabled"]').disabled).toBe(false)
    expect(container.querySelector('[data-skill-id="jira-manager"]').checked).toBe(true)
    expect(container.querySelector('[data-skill-id="confluence"]').checked).toBe(true)
    expect(container.querySelector('[data-skill-id="pdf"]').checked).toBe(false)

    container.querySelector('[data-module-id="providers"]').click()
    expect(container.querySelector('[data-provider-master-toggle="allowed"]').disabled).toBe(false)
  })

  test('providers module saves explicit provider instance allowlist', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()
    container.querySelector('[data-module-id="providers"]').click()

    const smartcmpToggle = container.querySelector('[data-provider-key="smartcmp::default"]')
    const jiraToggle = container.querySelector('[data-provider-key="jira::prod"]')
    expect(smartcmpToggle.checked).toBe(true)
    expect(jiraToggle.checked).toBe(false)
    expect(container.querySelector('[data-provider-key="ghost::old"]')).toBeNull()
    expect(container.textContent).not.toContain('Ghost')
    expect(container.querySelector('.role-provider-card strong').textContent).toContain('SmartCMP / default')

    smartcmpToggle.checked = false
    smartcmpToggle.dispatchEvent(new Event('change', { bubbles: true }))

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-ops' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.providers.allow_all).toBe(false)
    expect(payload.permissions.providers.provider_permissions).toEqual([
      { provider_type: 'smartcmp', instance_name: 'default', allowed: false },
      { provider_type: 'jira', instance_name: 'prod', allowed: false }
    ])
    expect(payload.permissions.providers.provider_permissions.map(provider => provider.provider_type)).not.toContain('ghost')
  })

  test('channels module saves explicit channel type allowlist', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()
    container.querySelector('[data-module-id="channels"]').click()

    const websocketToggle = container.querySelector('[data-channel-key="websocket"]')
    const feishuToggle = container.querySelector('[data-channel-key="feishu"]')
    expect(websocketToggle.checked).toBe(true)
    expect(feishuToggle.checked).toBe(false)
    expect(container.querySelector('[data-channel-key="ghost"]')).toBeNull()
    expect(container.textContent).not.toContain('Ghost')
    expect(container.querySelector('[data-permission-toggle="view"]')).toBeNull()
    expect(container.querySelector('[data-permission-toggle="create"]')).toBeNull()

    websocketToggle.checked = false
    websocketToggle.dispatchEvent(new Event('change', { bubbles: true }))

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-ops' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.channels).toEqual({
      module_permissions: { manage_permissions: false },
      allow_all: false,
      channel_permissions: [
        { channel_type: 'websocket', channel_name: 'WebSocket', allowed: false },
        { channel_type: 'feishu', channel_name: 'Feishu', allowed: false },
        { channel_type: 'dingtalk', channel_name: 'DingTalk', allowed: true }
      ]
    })
    expect(payload.permissions.channels.channel_permissions.map(channel => channel.channel_type)).not.toContain('ghost')
  })

  test('skills providers and channels searches keep focus while filtering', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    const skillsSearchInput = container.querySelector('#skillsSearchInput')
    skillsSearchInput.focus()
    skillsSearchInput.value = 'jira'
    skillsSearchInput.setSelectionRange(skillsSearchInput.value.length, skillsSearchInput.value.length)
    skillsSearchInput.dispatchEvent(new Event('input', { bubbles: true }))

    const refreshedSkillsSearchInput = container.querySelector('#skillsSearchInput')
    expect(document.activeElement).toBe(refreshedSkillsSearchInput)
    expect(refreshedSkillsSearchInput.value).toBe('jira')
    expect(refreshedSkillsSearchInput.selectionStart).toBe('jira'.length)
    expect(container.querySelector('.role-skill-list').textContent).toContain('jira-manager')
    expect(container.querySelector('.role-skill-list').textContent).not.toContain('confluence')

    container.querySelector('[data-module-id="providers"]').click()
    const providersSearchInput = container.querySelector('#providersSearchInput')
    providersSearchInput.focus()
    providersSearchInput.value = 'jira'
    providersSearchInput.setSelectionRange(providersSearchInput.value.length, providersSearchInput.value.length)
    providersSearchInput.dispatchEvent(new Event('input', { bubbles: true }))

    const refreshedProvidersSearchInput = container.querySelector('#providersSearchInput')
    expect(document.activeElement).toBe(refreshedProvidersSearchInput)
    expect(refreshedProvidersSearchInput.value).toBe('jira')
    expect(refreshedProvidersSearchInput.selectionStart).toBe('jira'.length)
    expect(container.querySelector('.role-provider-list').textContent).toContain('Jira / prod')
    expect(container.querySelector('.role-provider-list').textContent).not.toContain('SmartCMP / default')

    container.querySelector('[data-module-id="channels"]').click()
    const channelsSearchInput = container.querySelector('#channelsSearchInput')
    channelsSearchInput.focus()
    channelsSearchInput.value = 'fei'
    channelsSearchInput.setSelectionRange(channelsSearchInput.value.length, channelsSearchInput.value.length)
    channelsSearchInput.dispatchEvent(new Event('input', { bubbles: true }))

    const refreshedChannelsSearchInput = container.querySelector('#channelsSearchInput')
    expect(document.activeElement).toBe(refreshedChannelsSearchInput)
    expect(refreshedChannelsSearchInput.value).toBe('fei')
    expect(refreshedChannelsSearchInput.selectionStart).toBe('fei'.length)
    expect(container.querySelector('.role-provider-list').textContent).toContain('Feishu')
    expect(container.querySelector('.role-provider-list').textContent).not.toContain('WebSocket')
  })

  test('builtin user role provider access can be edited and saved', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-user"]').click()
    container.querySelector('[data-module-id="providers"]').click()

    const jiraToggle = container.querySelector('[data-provider-key="jira::prod"]')
    expect(jiraToggle.checked).toBe(true)
    jiraToggle.checked = false
    jiraToggle.dispatchEvent(new Event('change', { bubbles: true }))

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-user' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.providers.allow_all).toBe(false)
    expect(payload.permissions.providers.provider_permissions).toEqual([
      { provider_type: 'smartcmp', instance_name: 'default', allowed: true },
      { provider_type: 'jira', instance_name: 'prod', allowed: false }
    ])
  })

  test('access-all permissions save payload and make item lists read-only previews', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()

    const skillAccessAll = container.querySelector('[data-access-all-toggle="skills"]')
    expect(skillAccessAll.checked).toBe(false)
    skillAccessAll.checked = true
    skillAccessAll.dispatchEvent(new Event('change', { bubbles: true }))
    expect(container.querySelector('[data-access-all-toggle="skills"]').checked).toBe(true)
    expect(container.querySelector('[data-skill-master-toggle="enabled"]').checked).toBe(true)
    expect(container.querySelector('[data-skill-master-toggle="enabled"]').disabled).toBe(true)
    expect(container.querySelector('[data-skill-id="jira-manager"]').disabled).toBe(true)
    expect(container.querySelector('[data-skill-id="confluence"]').checked).toBe(true)
    expect(container.querySelector('[data-skill-id="confluence"]').disabled).toBe(true)
    expect(container.querySelector('[data-skill-id="pdf"]').checked).toBe(false)

    container.querySelector('[data-module-id="providers"]').click()
    const providerAccessAll = container.querySelector('[data-access-all-toggle="providers"]')
    expect(providerAccessAll.checked).toBe(false)
    providerAccessAll.checked = true
    providerAccessAll.dispatchEvent(new Event('change', { bubbles: true }))
    expect(container.querySelector('[data-provider-master-toggle="allowed"]').checked).toBe(true)
    expect(container.querySelector('[data-provider-master-toggle="allowed"]').disabled).toBe(true)
    expect(container.querySelector('[data-provider-key="smartcmp::default"]').disabled).toBe(true)
    expect(container.querySelector('[data-provider-key="jira::prod"]').checked).toBe(true)
    expect(container.querySelector('[data-provider-key="jira::prod"]').disabled).toBe(true)

    container.querySelector('[data-module-id="channels"]').click()
    const channelAccessAll = container.querySelector('[data-access-all-toggle="channels"]')
    expect(channelAccessAll.checked).toBe(false)
    channelAccessAll.checked = true
    channelAccessAll.dispatchEvent(new Event('change', { bubbles: true }))
    expect(container.querySelector('[data-channel-master-toggle="allowed"]').checked).toBe(true)
    expect(container.querySelector('[data-channel-master-toggle="allowed"]').disabled).toBe(true)
    expect(container.querySelector('[data-channel-key="websocket"]').disabled).toBe(true)
    expect(container.querySelector('[data-channel-key="feishu"]').checked).toBe(true)
    expect(container.querySelector('[data-channel-key="feishu"]').disabled).toBe(true)

    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-ops' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()
    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.skills.allow_all).toBe(true)
    expect(payload.permissions.providers.allow_all).toBe(true)
    expect(payload.permissions.channels.allow_all).toBe(true)
    expect(payload.permissions.channels.channel_permissions.map(channel => channel.channel_type)).not.toContain('ghost')
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
    expect(enabledToggles).toHaveLength(3)
    expect(enabledToggles.filter(toggle => toggle.checked)).toHaveLength(2)
    expect(container.querySelector('[data-skill-id="pdf"]').checked).toBe(false)
  })

  test('skills module summary excludes hidden internal flags from enabled count', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    container.querySelector('[data-role-select="role-ops"]').click()

    const skillsSummary = container.querySelector('[data-module-id="skills"] .role-module-copy span:last-child')
    expect(skillsSummary.textContent.trim()).toBe('1 enabled')
  })

  test('allowlist module summaries include manage-permissions governance toggles', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()

    const providersSummary = () => container.querySelector('[data-module-id="providers"] .role-module-copy span:last-child')
    const channelsSummary = () => container.querySelector('[data-module-id="channels"] .role-module-copy span:last-child')

    expect(providersSummary().textContent.trim()).toBe('1 enabled')
    container.querySelector('[data-module-id="providers"]').click()
    const providerManageToggle = container.querySelector('[data-module-toggle="providers"][data-permission-toggle="manage_permissions"]')
    providerManageToggle.checked = true
    providerManageToggle.dispatchEvent(new Event('change', { bubbles: true }))
    expect(providersSummary().textContent.trim()).toBe('2 enabled')

    expect(channelsSummary().textContent.trim()).toBe('2 enabled')
    container.querySelector('[data-module-id="channels"]').click()
    const channelManageToggle = container.querySelector('[data-module-toggle="channels"][data-permission-toggle="manage_permissions"]')
    channelManageToggle.checked = true
    channelManageToggle.dispatchEvent(new Event('change', { bubbles: true }))
    expect(channelsSummary().textContent.trim()).toBe('3 enabled')
  })

  test('create role submits current permission-governance payload to roles API', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    document.getElementById('createRoleBtn').click()
    const summaryFields = [...container.querySelectorAll('#roleEditor .role-summary-grid > *')]
    expect(summaryFields[0].querySelector('[data-role-field="name"]')).not.toBeNull()
    expect(summaryFields[1].querySelector('[data-role-field="identifier"]')).not.toBeNull()
    expect(summaryFields[2].querySelector('[data-role-field="is_active"]')).not.toBeNull()
    expect(summaryFields[3].querySelector('[data-role-field="description"]').getAttribute('rows')).toBe('2')

    container.querySelector('[data-module-id="channels"]').click()
    expect([...container.querySelectorAll('[data-channel-toggle="allowed"]')].every(toggle => !toggle.checked)).toBe(true)

    const rolesModule = container.querySelector('[data-module-id="roles"]')
    rolesModule.click()
    const rolesManageToggle = container.querySelector('[data-module-toggle="roles"][data-permission-toggle="manage_permissions"]')
    rolesManageToggle.checked = true
    rolesManageToggle.dispatchEvent(new Event('change', { bubbles: true }))

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
    expect(payload.permissions.users).not.toHaveProperty('reset_password')
    expect(payload.permissions).not.toHaveProperty('rbac')
    expect(payload.permissions.roles.manage_permissions).toBe(true)
    expect(payload.permissions.skills.module_permissions).toEqual({
      view: true,
      enable_disable: true,
      manage_permissions: true
    })
    expect(payload.permissions.skills.allow_all).toBe(false)
    expect(payload.permissions.skills.skill_permissions).toEqual(expect.arrayContaining([
      expect.objectContaining({
        skill_id: 'jira-manager',
        authorized: true,
        enabled: true
      }),
      expect.objectContaining({
        skill_id: 'confluence',
        authorized: true,
        enabled: true
      }),
      expect.objectContaining({
        skill_id: 'pdf',
        authorized: false,
        enabled: false
      })
    ]))
    expect(payload.permissions.users.assign_roles).toBe(true)
    expect(payload.permissions.users.manage_permissions).toBe(true)
    expect(payload.permissions.channels).toEqual({
      module_permissions: { manage_permissions: false },
      allow_all: false,
      channel_permissions: []
    })
    expect(payload.permissions.providers).toEqual({
      module_permissions: { manage_permissions: false },
      allow_all: false,
      provider_permissions: []
    })
  })

  test('new role identifier is generated from the role name', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)

    document.getElementById('createRoleBtn').click()
    const nameInput = container.querySelector('[data-role-field="name"]')
    nameInput.value = 'Finance Operators'
    nameInput.dispatchEvent(new Event('change', { bubbles: true }))

    expect(container.querySelector('[data-role-field="identifier"]').value).toBe('finance-operators')
  })

  test('channels visible-row bulk toggle and restore defaults follow custom deny-all default', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()
    container.querySelector('[data-module-id="channels"]').click()

    const visibleRowsToggle = container.querySelector('[data-channel-master-toggle="allowed"]')
    expect(container.querySelector('[data-channel-key="feishu"]').checked).toBe(false)
    visibleRowsToggle.checked = true
    visibleRowsToggle.dispatchEvent(new Event('change', { bubbles: true }))
    expect([...container.querySelectorAll('[data-channel-toggle="allowed"]')].every(toggle => toggle.checked)).toBe(true)
    expect(container.querySelector('[data-module-toggle="channels"][data-permission-toggle="manage_permissions"]').checked).toBe(false)

    container.querySelector('[data-channel-key="websocket"]').checked = false
    container.querySelector('[data-channel-key="websocket"]').dispatchEvent(new Event('change', { bubbles: true }))
    expect(container.querySelector('[data-channel-key="websocket"]').checked).toBe(false)

    container.querySelector('[data-module-action="restore-defaults"]').click()
    expect([...container.querySelectorAll('[data-channel-toggle="allowed"]')].every(toggle => !toggle.checked)).toBe(true)
  })

  test('built-in user restore defaults keeps runtime access-all shape aligned with backend defaults', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-user"]').click()

    container.querySelector('[data-module-action="restore-defaults"]').click()
    expect(container.querySelector('[data-access-all-toggle="skills"]').checked).toBe(false)
    expect([...container.querySelectorAll('[data-skill-toggle="enabled"]')].every(toggle => !toggle.checked)).toBe(true)

    container.querySelector('[data-module-id="providers"]').click()
    container.querySelector('[data-module-action="restore-defaults"]').click()
    expect(container.querySelector('[data-access-all-toggle="providers"]').checked).toBe(false)
    expect([...container.querySelectorAll('[data-provider-toggle="allowed"]')].every(toggle => !toggle.checked)).toBe(true)

    container.querySelector('[data-module-id="channels"]').click()
    container.querySelector('[data-module-action="restore-defaults"]').click()
    expect(container.querySelector('[data-access-all-toggle="channels"]').checked).toBe(true)
    expect(container.querySelector('[data-channel-master-toggle="allowed"]').disabled).toBe(true)
  })

  test('saving a custom role preserves skill and module permissions', async () => {
    const page = await import('../../app/frontend/scripts/pages/role-management.js')
    const container = document.getElementById('page-root')

    await page.mount(container)
    container.querySelector('[data-role-select="role-ops"]').click()
    container.querySelector('#saveRoleChanges').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    const putCall = global.fetch.mock.calls.find(([url, options]) => (
      url === '/api/roles/role-ops' && options?.method === 'PUT'
    ))
    expect(putCall).toBeDefined()

    const payload = JSON.parse(putCall[1].body)
    expect(payload.permissions.skills.module_permissions).toEqual({
      view: true,
      enable_disable: true,
      manage_permissions: false
    })
    expect(payload.permissions.skills.allow_all).toBe(false)
    expect(payload.permissions.skills.skill_permissions).toEqual(expect.arrayContaining([
      expect.objectContaining({ skill_id: 'jira-manager', authorized: true, enabled: true }),
      expect.objectContaining({ skill_id: 'confluence', authorized: false, enabled: false }),
      expect.objectContaining({ skill_id: 'pdf', authorized: false, enabled: false })
    ]))
    expect(payload.permissions.providers).toEqual({
      module_permissions: { manage_permissions: false },
      allow_all: false,
      provider_permissions: [
        { provider_type: 'smartcmp', instance_name: 'default', allowed: true },
        { provider_type: 'jira', instance_name: 'prod', allowed: false }
      ]
    })
    expect(payload.permissions.providers.provider_permissions.map(provider => provider.provider_type)).not.toContain('ghost')
    expect(payload.permissions.channels).toEqual({
      module_permissions: { manage_permissions: false },
      allow_all: false,
      channel_permissions: [
        { channel_type: 'websocket', channel_name: 'WebSocket', allowed: true },
        { channel_type: 'feishu', channel_name: 'Feishu', allowed: false },
        { channel_type: 'dingtalk', channel_name: 'DingTalk', allowed: true }
      ]
    })
    expect(payload.permissions.channels.channel_permissions.map(channel => channel.channel_type)).not.toContain('ghost')
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
