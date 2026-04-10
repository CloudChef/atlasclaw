let createdPayloads = []

beforeEach(() => {
  jest.resetModules()
  document.body.innerHTML = '<div id="page-root"></div>'
  window.history.replaceState({}, '', '/providers')
  createdPayloads = []

  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    const method = String(options.method || 'GET').toUpperCase()

    if (target.endsWith('/api/service-providers/available-instances')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          count: 2,
          providers: [
            {
              provider_type: 'smartcmp',
              instance_name: 'default',
              base_url: 'https://console.smartcmp.cloud',
              auth_type: 'user_token',
              config_keys: []
            },
            {
              provider_type: 'dingtalk',
              instance_name: 'default',
              base_url: 'https://oapi.dingtalk.com',
              auth_type: '',
              config_keys: ['app_key', 'agent_id']
            }
          ]
        })
      })
    }

    if (target.endsWith('/api/service-providers/definitions')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          count: 2,
          providers: [
            {
              provider_type: 'smartcmp',
              name_i18n_key: 'provider.catalog.smartcmp.name',
              display_name: 'SmartCMP',
              description_i18n_key: 'provider.catalog.smartcmp.description',
              description: 'Enterprise CMP workflow provider for approvals, service catalog queries, and fulfillment actions.',
              badge: 'CMP',
              icon: 'SC',
              accent: '#0f766e',
              schema: {
                fields: [
                  {
                    name: 'base_url',
                    label_i18n_key: 'provider.baseUrl',
                    label: 'Base URL',
                    placeholder_i18n_key: 'provider.baseUrlPlaceholder',
                    placeholder: 'https://example.com',
                    type: 'text',
                    required: true,
                    default: 'https://console.smartcmp.cloud'
                  },
                  {
                    name: 'auth_type',
                    type: 'hidden',
                    default: 'user_token'
                  },
                  {
                    name: 'user_token',
                    label_i18n_key: 'provider.userToken',
                    label: 'User Token',
                    placeholder_i18n_key: 'provider.userTokenPlaceholder',
                    placeholder: 'Enter user token',
                    type: 'password',
                    required: true,
                    sensitive: true,
                    auth_types: ['user_token']
                  },
                  {
                    name: 'username',
                    label_i18n_key: 'provider.username',
                    label: 'Username',
                    placeholder_i18n_key: 'provider.usernamePlaceholder',
                    placeholder: 'cmp-robot',
                    type: 'text',
                    required: true,
                    auth_types: ['credential']
                  },
                  {
                    name: 'password',
                    label_i18n_key: 'provider.password',
                    label: 'Password',
                    placeholder_i18n_key: 'provider.passwordPlaceholder',
                    placeholder: 'Enter password',
                    type: 'password',
                    required: true,
                    sensitive: true,
                    auth_types: ['credential']
                  }
                ]
              }
            },
            {
              provider_type: 'dingtalk',
              name_i18n_key: 'provider.catalog.dingtalk.name',
              display_name: 'DingTalk',
              description_i18n_key: 'provider.catalog.dingtalk.description',
              description: 'Enterprise messaging provider for org bots, app credentials, and downstream work notifications.',
              badge: 'COLLAB',
              icon: 'DT',
              accent: '#2952cc',
              schema: {
                fields: [
                  {
                    name: 'base_url',
                    label_i18n_key: 'provider.baseUrl',
                    label: 'Base URL',
                    placeholder_i18n_key: 'provider.baseUrlPlaceholder',
                    placeholder: 'https://oapi.dingtalk.com',
                    type: 'text',
                    required: true,
                    default: 'https://oapi.dingtalk.com'
                  },
                  {
                    name: 'auth_type',
                    type: 'hidden',
                    default: 'app_credentials'
                  },
                  {
                    name: 'app_key',
                    label_i18n_key: 'provider.appKey',
                    label: 'App Key',
                    placeholder_i18n_key: 'provider.appKeyPlaceholder',
                    placeholder: 'dingxxxx',
                    type: 'text',
                    required: true
                  },
                  {
                    name: 'app_secret',
                    label_i18n_key: 'provider.appSecret',
                    label: 'App Secret',
                    placeholder_i18n_key: 'provider.appSecretPlaceholder',
                    placeholder: 'Enter app secret',
                    type: 'password',
                    required: true,
                    sensitive: true
                  },
                  {
                    name: 'agent_id',
                    label_i18n_key: 'provider.agentId',
                    label: 'Agent ID',
                    placeholder_i18n_key: 'provider.agentIdPlaceholder',
                    placeholder: '1000001',
                    type: 'text',
                    required: true
                  }
                ]
              }
            }
          ]
        })
      })
    }

    if (target.includes('/api/provider-configs?')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          provider_configs: [
            {
              id: 'cfg-smartcmp-staging',
              provider_type: 'smartcmp',
              instance_name: 'staging',
              config: {
                base_url: 'https://cmp-staging.example.com',
                auth_type: 'cookie',
                cookie: 'secret-cookie'
              },
              is_active: true,
              created_at: '2026-04-10T08:00:00Z',
              updated_at: '2026-04-10T09:00:00Z'
            }
          ],
          total: 1
        })
      })
    }

    if (target.endsWith('/api/provider-configs') && method === 'POST') {
      const payload = JSON.parse(options.body)
      createdPayloads.push(payload)
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          id: 'cfg-smartcmp-team-a',
          provider_type: payload.provider_type,
          instance_name: payload.instance_name,
          config: payload.config,
          is_active: payload.is_active,
          created_at: '2026-04-10T10:00:00Z',
          updated_at: '2026-04-10T10:00:00Z'
        })
      })
    }

    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
})

describe('providers page', () => {
  test('mount renders service provider cards and combined instance inventory', async () => {
    const providersPage = await import('../../app/frontend/scripts/pages/providers.js')
    const container = document.getElementById('page-root')

    await providersPage.mount(container)

    const cardTypes = [...container.querySelectorAll('.pv-type-card')].map((card) => card.dataset.type)
    expect(cardTypes).toEqual(['smartcmp', 'dingtalk'])

    expect(container.querySelector('[data-i18n="provider.pageTitle"]')).not.toBeNull()
    expect(container.querySelector('[data-i18n="provider.inventoryEyebrow"]')).not.toBeNull()
    expect(container.querySelector('.pv-panel-title').textContent).toContain('SmartCMP Instances')
    expect(container.querySelector('.pv-overview-panel')).toBeNull()
    expect(container.textContent).toContain('https://console.smartcmp.cloud')
    expect(container.textContent).toContain('default')
    expect(container.textContent).toContain('Config file')
    expect(container.textContent).toContain('Managed')
    expect(container.textContent).not.toContain('Auth Type')
    expect(container.textContent).not.toContain('Params')
    const firstRow = container.querySelector('tbody tr')
    expect(firstRow.querySelector('.pv-instance-heading .pv-instance-readonly-badge')).not.toBeNull()
    expect(firstRow.querySelector('.pv-instance-readonly-badge').textContent).toContain('Read only')
    expect(firstRow.querySelector('td:nth-child(4)').textContent).toContain('Active')
    expect(firstRow.querySelector('td:nth-child(5)').textContent.trim()).toBe('--')
    expect(container.querySelector('[data-view-config]')).not.toBeNull()
    expect(container.querySelector('tbody tr td:last-child').textContent).toContain('View')
    expect(firstRow.querySelector('[data-view-config]').className).toContain('btn-small')

    const managedRow = container.querySelectorAll('tbody tr')[1]
    expect(managedRow.querySelector('[data-edit-config]').className).toContain('btn-small')
    expect(managedRow.querySelector('[data-toggle-config]').className).toContain('btn-small')
    expect(managedRow.querySelector('[data-delete-config]').className).toContain('btn-delete')

    container.querySelector('[data-view-config]').click()
    expect(container.querySelector('#providerModal')).not.toBeNull()
    expect(container.querySelector('#providerModal').textContent).toContain('View Provider Instance')
    expect(container.querySelector('#providerModal').textContent).toContain('https://console.smartcmp.cloud')

    await providersPage.unmount()
  })

  test('create modal renders backend schema and submits hidden auth_type defaults', async () => {
    const providersPage = await import('../../app/frontend/scripts/pages/providers.js')
    const container = document.getElementById('page-root')

    await providersPage.mount(container)

    container.querySelector('#btnCreateProviderInstance').click()
    expect(container.querySelector('#providerModal')).not.toBeNull()
    expect(container.querySelector('#providerModal').textContent).toContain('New Provider Instance')
    expect(container.querySelector('select[name="auth_type"]')).toBeNull()
    expect(container.querySelector('#providerModal').textContent).not.toContain('Schema note')
    expect(container.querySelector('#providerModal').textContent).not.toContain('auth type')
    expect(container.querySelector('input[name="username"]')).toBeNull()
    expect(container.querySelector('#providerModal .pv-modal-stack')).not.toBeNull()

    const passwordInput = container.querySelector('input[name="user_token"]')
    const toggleButton = container.querySelector('[data-toggle-secret="user_token"]')
    const baseUrlInput = container.querySelector('input[name="base_url"]')
    expect(passwordInput).not.toBeNull()
    expect(passwordInput.type).toBe('password')
    expect(toggleButton).not.toBeNull()
    expect(baseUrlInput.value).toBe('https://console.smartcmp.cloud')
    toggleButton.click()
    expect(passwordInput.type).toBe('text')

    container.querySelector('input[name="instance_name"]').value = 'team-a'
    passwordInput.value = 'secret-token'

    container.querySelector('#providerModalForm').dispatchEvent(new Event('submit', {
      bubbles: true,
      cancelable: true
    }))

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(createdPayloads).toHaveLength(1)
    expect(createdPayloads[0]).toEqual({
      provider_type: 'smartcmp',
      instance_name: 'team-a',
      config: {
        base_url: 'https://console.smartcmp.cloud',
        auth_type: 'user_token',
        user_token: 'secret-token'
      },
      is_active: true
    })

    expect(document.body.textContent).toContain('Provider instance created successfully')

    await providersPage.unmount()
  })
})
