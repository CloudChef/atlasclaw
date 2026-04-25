/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */
const { readFileSync } = require('node:fs')
const { resolve } = require('node:path')

let userProviderPayloads = []

beforeEach(() => {
  jest.resetModules()
  document.body.innerHTML = '<div id="page-root"></div>'
  window.history.replaceState({}, '', '/providers')
  userProviderPayloads = []

  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    const method = String(options.method || 'GET').toUpperCase()

    if (target.endsWith('/api/service-providers/available-instances')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          count: 4,
          providers: [
            {
              provider_type: 'smartcmp',
              instance_name: 'default',
              base_url: 'https://console.smartcmp.cloud',
              auth_type: ['cookie', 'provider_token', 'user_token', 'credential'],
              config_keys: ['username']
            },
            {
              provider_type: 'smartcmp',
              instance_name: 'personal',
              base_url: 'https://personal.smartcmp.cloud',
              auth_type: 'user_token',
              config_keys: []
            },
            {
              provider_type: 'smartcmp',
              instance_name: 'backup',
              base_url: 'https://backup.smartcmp.cloud',
              auth_type: 'cookie',
              config_keys: []
            },
            {
              provider_type: 'dingtalk',
              instance_name: 'ops',
              base_url: 'https://oapi.dingtalk.com',
              auth_type: 'app_credentials',
              config_keys: []
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
                    placeholder: 'https://console.smartcmp.cloud',
                    type: 'text',
                    required: true,
                    default: 'https://console.smartcmp.cloud'
                  },
                  {
                    name: 'auth_type',
                    type: 'hidden',
                    default: ['cookie', 'provider_token', 'user_token', 'credential']
                  },
                  {
                    name: 'provider_token',
                    label_i18n_key: 'provider.providerToken',
                    label: 'Provider Token',
                    placeholder_i18n_key: 'provider.providerTokenPlaceholder',
                    placeholder: 'Enter shared provider token',
                    type: 'password',
                    required: true,
                    sensitive: true,
                    auth_types: ['provider_token']
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
                    name: 'cookie',
                    label_i18n_key: 'provider.cookie',
                    label: 'Cookie',
                    placeholder_i18n_key: 'provider.cookiePlaceholder',
                    placeholder: 'session=...',
                    type: 'password',
                    required: true,
                    sensitive: true,
                    auth_types: ['cookie']
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

    if (target.endsWith('/api/users/me/provider-settings') && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          providers: {
            smartcmp: {
              default: {
                configured: true,
                config: {
                  auth_type: ['cookie', 'provider_token', 'user_token', 'credential']
                },
                updated_at: '2026-04-13T10:00:00Z'
              },
              backup: {
                configured: true,
                config: {
                  auth_type: 'cookie'
                },
                updated_at: '2026-04-13T10:05:00Z'
              }
            },
            dingtalk: {
              ops: {
                configured: true,
                config: {
                  auth_type: 'app_credentials',
                  app_key: 'ding-app-key',
                  agent_id: '1000001'
                },
                updated_at: '2026-04-13T10:10:00Z'
              }
            }
          }
        })
      })
    }

    if (target.endsWith('/api/users/me/provider-settings') && method === 'PUT') {
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

    if (target.includes('/api/provider-configs')) {
      return Promise.reject(new Error('providers page should not use /api/provider-configs for personal credentials'))
    }

    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
})

describe('providers page', () => {
  test('list typography uses one body text style across table cells', () => {
    const styles = readFileSync(
      resolve(__dirname, '../../app/frontend/styles/providers.css'),
      'utf-8'
    )

    expect(styles).toMatch(/\.pv-instance-cell strong\s*\{[^}]*font-size:\s*14px;[^}]*font-weight:\s*400;[^}]*line-height:\s*1\.5;[^}]*\}/s)
    expect(styles).toMatch(/\.pv-cell-muted\s*\{[^}]*font-size:\s*14px;[^}]*line-height:\s*1\.5;[^}]*\}/s)
    expect(styles).toMatch(/\.pv-config-key\s*\{[^}]*font-size:\s*14px;[^}]*font-weight:\s*400;[^}]*letter-spacing:\s*normal;[^}]*text-transform:\s*none;[^}]*\}/s)
    expect(styles).toMatch(/\.pv-config-value\s*\{[^}]*font-size:\s*14px;[^}]*font-weight:\s*400;[^}]*line-height:\s*1\.5;[^}]*\}/s)
  })

  test('mount renders only user-token-capable provider instances without exposing auth details', async () => {
    const providersPage = await import('../../app/frontend/scripts/pages/providers.js')
    const container = document.getElementById('page-root')

    await providersPage.mount(container)

    const sectionTitles = [...container.querySelectorAll('[data-provider-section] .pv-panel-title')]
      .map((title) => title.textContent.trim())

    expect(sectionTitles).toEqual(['SmartCMP'])
    expect(container.textContent).toContain('Authentication Configuration')
    expect(container.textContent).toContain('Set your personal provider tokens')
    expect(container.textContent).toContain('default')
    expect(container.textContent).toContain('personal')
    expect(container.textContent).toContain('Personal Token')
    expect(container.textContent).toContain('Configured')
    expect(container.textContent).toContain('Not configured')
    expect(container.textContent).not.toContain('backup')
    expect(container.textContent).not.toContain('DingTalk')
    expect(container.textContent).not.toContain('https://console.smartcmp.cloud')
    expect(container.textContent).not.toContain('Base URL')
    expect(container.textContent).not.toContain('Access Configuration')
    expect(container.textContent).not.toContain('Provider Token')
    expect(container.textContent).not.toContain('Cookie')
    expect(container.textContent).not.toContain('Username')
    expect(container.textContent).not.toContain('Password')
    expect(container.textContent).not.toContain('App Key')
    expect(container.textContent).not.toContain('App Secret')
    expect(container.textContent).not.toContain('Agent ID')
    expect(container.textContent).not.toContain('ding-app-key')
    expect(container.textContent).not.toContain('1000001')
    expect(container.textContent).not.toContain('secret-token')
    expect(container.querySelectorAll('[data-provider-section] .pv-table')).toHaveLength(1)
    expect(container.querySelectorAll('[data-configure-template]')).toHaveLength(2)
    expect(
      [...container.querySelectorAll('[data-configure-template]')].map((button) => button.dataset.instanceName)
    ).toEqual(['default', 'personal'])

    await providersPage.unmount()
  })

  test('configure modal only edits the user token and preserves saved values when blank', async () => {
    const providersPage = await import('../../app/frontend/scripts/pages/providers.js')
    const container = document.getElementById('page-root')

    await providersPage.mount(container)

    container.querySelector('[data-configure-template][data-provider-type="smartcmp"][data-instance-name="default"]').click()
    expect(container.querySelector('#providerModal')).not.toBeNull()
    expect(container.querySelector('#providerModal .pv-modal-kicker').textContent).toBe('Authentication Configuration')
    expect(container.querySelector('#providerModal h2').textContent).toBe('Set SmartCMP User Token')
    expect(container.querySelector('#providerModal .pv-modal-description').textContent).toContain('personal token')
    expect(container.querySelector('#providerModal button[type="submit"]').textContent).toBe('Save')

    const modalText = container.querySelector('#providerModal').textContent
    expect(modalText).toContain('Instance')
    expect(modalText).toContain('default')
    expect(modalText).toContain('User Token')
    expect(modalText).not.toContain('Base URL')
    expect(modalText).not.toContain('Access Configuration')
    expect(modalText).not.toContain('Provider Token')
    expect(modalText).not.toContain('Cookie')
    expect(modalText).not.toContain('Username')
    expect(modalText).not.toContain('Password')
    expect(container.querySelector('input[name="base_url"]')).toBeNull()
    expect(container.querySelector('input[name="auth_type"]')).toBeNull()
    expect(container.querySelector('input[name="provider_token"]')).toBeNull()
    expect(container.querySelector('input[name="cookie"]')).toBeNull()
    expect(container.querySelector('input[name="username"]')).toBeNull()
    expect(container.querySelector('input[name="password"]')).toBeNull()

    const tokenInput = container.querySelector('input[name="user_token"]')
    expect(tokenInput).not.toBeNull()
    expect(tokenInput.type).toBe('password')
    expect(tokenInput.required).toBe(false)
    expect(tokenInput.value).toBe('')
    expect(container.querySelector('[data-toggle-secret="user_token"]')).toBeNull()

    container.querySelector('#providerModalForm').dispatchEvent(new Event('submit', {
      bubbles: true,
      cancelable: true
    }))

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(userProviderPayloads).toHaveLength(1)
    expect(userProviderPayloads[0]).toEqual({
      provider_type: 'smartcmp',
      instance_name: 'default',
      config: {}
    })

    await providersPage.unmount()
  })

  test('new user-token configuration submits only user_token', async () => {
    const providersPage = await import('../../app/frontend/scripts/pages/providers.js')
    const container = document.getElementById('page-root')

    await providersPage.mount(container)

    container.querySelector('[data-configure-template][data-provider-type="smartcmp"][data-instance-name="personal"]').click()
    const tokenInput = container.querySelector('input[name="user_token"]')

    expect(tokenInput).not.toBeNull()
    expect(tokenInput.required).toBe(true)
    tokenInput.value = 'personal-secret-token'

    container.querySelector('#providerModalForm').dispatchEvent(new Event('submit', {
      bubbles: true,
      cancelable: true
    }))

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(userProviderPayloads).toHaveLength(1)
    expect(userProviderPayloads[0]).toEqual({
      provider_type: 'smartcmp',
      instance_name: 'personal',
      config: {
        user_token: 'personal-secret-token'
      }
    })

    await providersPage.unmount()
  })
})
