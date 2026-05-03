/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

let userProviderPayloads = []

async function flushMicrotasks(count = 5) {
  for (let i = 0; i < count; i += 1) {
    await Promise.resolve()
  }
}

async function waitForText(container, text, count = 25) {
  for (let i = 0; i < count; i += 1) {
    if (container.textContent.includes(text)) return
    await Promise.resolve()
  }
}

async function waitForEnabledButton(container, selector, count = 25) {
  for (let i = 0; i < count; i += 1) {
    const button = container.querySelector(selector)
    if (button && !button.disabled) return button
    await Promise.resolve()
  }
  return container.querySelector(selector)
}

beforeEach(() => {
  jest.resetModules()
  document.body.innerHTML = '<div id="page-root"></div>'
  userProviderPayloads = []

  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: jest.fn(() => null),
      setItem: jest.fn(),
      removeItem: jest.fn(),
      clear: jest.fn()
    }
  })

  window.requestAnimationFrame = jest.fn((callback) => callback())
  window.history.replaceState({}, '', '/channels')

  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    const method = String(options.method || 'GET').toUpperCase()

    if (target.endsWith('/api/channels')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve([
          { type: 'websocket', name: 'WebSocket', mode: 'bidirectional', connection_count: 1 },
          { type: 'rest', name: 'REST', mode: 'request-response', connection_count: 0 },
          { type: 'sse', name: 'SSE', mode: 'stream', connection_count: 0 }
        ])
      })
    }

    if (target.endsWith('/api/channels/websocket/connections')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          channel_type: 'websocket',
          connections: []
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
              auth_type: ['provider_token', 'user_token'],
              base_url: 'https://console.smartcmp.example'
            },
            {
              provider_type: 'smartcmp',
              instance_name: 'system',
              auth_type: ['provider_token'],
              base_url: 'https://system.smartcmp.example'
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
                    name: 'user_token',
                    label: 'User Token',
                    placeholder: 'Enter user token',
                    type: 'password',
                    required: true,
                    sensitive: true,
                    auth_types: ['user_token']
                  },
                  {
                    name: 'provider_token',
                    label: 'Provider Token',
                    type: 'password',
                    sensitive: true,
                    auth_types: ['provider_token']
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
                configured: false,
                config: {},
                updated_at: null
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

describe('channels page', () => {
  test('renders provider user-token readiness panel instead of connection health', async () => {
    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    expect(container.querySelector('#providerTokenReadinessPanel').style.display).toBe('block')
    expect(container.textContent).toContain('Provider User Tokens')
    expect(container.textContent).toContain('IM messages can ask the agent to work with configured providers')
    expect(container.textContent).toContain('SmartCMP')
    expect(container.textContent).toContain('default')
    expect([...container.querySelectorAll('#channelProviderTokenPanel tbody tr')].map(row => row.children[1].textContent.trim())).toEqual(['default'])
    expect(container.textContent).not.toContain('Connection Health')
    expect(container.textContent).not.toContain('Average Latency')
    expect(container.textContent).not.toContain('Uptime (24h)')

    await channelsPage.unmount()
  })

  test('channel provider token modal saves only the current user token', async () => {
    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    document.querySelector('[data-channel-provider-token-configure]').click()

    expect(document.getElementById('channelProviderTokenModal')).not.toBeNull()
    expect(document.querySelector('#channelProviderTokenForm input[name="user_token"]')).not.toBeNull()
    expect(document.querySelector('#channelProviderTokenForm input[name="provider_token"]')).toBeNull()

    document.querySelector('#channelProviderTokenForm input[name="user_token"]').value = 'channel-user-secret'
    document.getElementById('channelProviderTokenForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await new Promise(resolve => setTimeout(resolve, 0))
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(userProviderPayloads).toEqual([
      {
        provider_type: 'smartcmp',
        instance_name: 'default',
        config: {
          user_token: 'channel-user-secret'
        }
      }
    ])

    await channelsPage.unmount()
  })

  test('mount keeps built-in channel types visible and shows planned placeholders', async () => {
    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    const cardTypes = [...container.querySelectorAll('.ch-type-card')].map((card) => card.dataset.type)

    expect(cardTypes).toEqual(['websocket', 'rest', 'sse', 'slack', 'discord'])

    container.querySelector('.ch-type-card[data-type="slack"]').click()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(container.querySelector('#btnCreateConnection').disabled).toBe(true)
    expect(container.querySelector('#providerTokenReadinessPanel').style.display).toBe('none')
    expect(
      global.fetch.mock.calls.some(([url]) => String(url).includes('/api/channels/slack/connections'))
    ).toBe(false)

    await channelsPage.unmount()
  })

  test('planned placeholders do not enter edit mode from a direct URL', async () => {
    window.history.replaceState({}, '', '/channels?type=slack&edit=new')

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    expect(window.location.search).toBe('?type=slack')
    expect(container.querySelector('#channelListView').style.display).toBe('block')
    expect(container.querySelector('#channelEditView').style.display).toBe('none')
    expect(container.querySelector('#btnCreateConnection').disabled).toBe(true)

    await channelsPage.unmount()
  })

  test('provisioning channel scan action opens QR modal while manual config remains available', async () => {
    window.history.replaceState({}, '', '/channels?type=feishu')

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      const method = String(options.method || 'GET').toUpperCase()

      if (target.endsWith('/api/channels')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              type: 'feishu',
              name: 'Feishu',
              mode: 'bidirectional',
              connection_count: 0,
              provisioning: {
                supported: true,
                default_mode: 'qr',
                manual_config_available: true,
                instructions_i18n_key: 'channel.provisioning.feishu.instructions'
              }
            }
          ])
        })
      }

      if (target.endsWith('/api/channels/feishu/connections')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            channel_type: 'feishu',
            connections: []
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/provisioning-sessions') && method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            session_id: 'session-1',
            channel_type: 'feishu',
            status: 'pending',
            qr_url: 'https://open.feishu.cn/app?state=abc',
            qr_image_url: 'https://open.feishu.cn/qr.png',
            expires_at: '2026-05-02T08:00:00Z',
            refresh_after_seconds: 60,
            instructions_i18n_key: 'channel.provisioning.feishu.instructions',
            connection: null
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    expect(container.querySelector('#btnCreateConnection')).not.toBeNull()
    expect(container.querySelector('#btnScanProvisioning')).not.toBeNull()

    container.querySelector('#btnScanProvisioning').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(window.location.search).toBe('?type=feishu')
    expect(container.querySelector('#channelEditView').style.display).toBe('none')
    expect(container.querySelector('#channelProvisioningModal')).not.toBeNull()
    expect(container.querySelector('[data-provisioning-manual]')).not.toBeNull()
    expect(container.querySelector('.ch-provisioning-qr img').getAttribute('src')).toBe('https://open.feishu.cn/qr.png')
    expect(
      global.fetch.mock.calls.some(([url, options]) => (
        String(url).endsWith('/api/channels/feishu/provisioning-sessions') &&
        String(options?.method || 'GET').toUpperCase() === 'POST'
      ))
    ).toBe(true)

    await channelsPage.unmount()
  })

  test('provisioning completion refreshes connections and closes QR modal', async () => {
    window.history.replaceState({}, '', '/channels?type=feishu')
    let pollCount = 0

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      const method = String(options.method || 'GET').toUpperCase()

      if (target.endsWith('/api/channels')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              type: 'feishu',
              name: 'Feishu',
              mode: 'bidirectional',
              connection_count: pollCount > 0 ? 1 : 0,
              provisioning: {
                supported: true,
                default_mode: 'qr',
                manual_config_available: true,
                instructions_i18n_key: 'channel.provisioning.feishu.instructions'
              }
            }
          ])
        })
      }

      if (target.endsWith('/api/channels/feishu/connections')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            channel_type: 'feishu',
            connections: pollCount > 0
              ? [{
                  id: 'conn-1',
                  name: 'Feishu QR Bot',
                  channel_type: 'feishu',
                  enabled: true,
                  is_default: false,
                  runtime_status: 'connecting',
                  config: {}
                }]
              : []
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/provisioning-sessions') && method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            session_id: 'session-1',
            channel_type: 'feishu',
            status: 'pending',
            qr_url: 'https://accounts.feishu.cn/oauth/v1/device/verify?user_code=QFXB-8X3X',
            qr_image_url: 'https://open.feishu.cn/qr.png',
            expires_at: '2026-05-02T08:00:00Z',
            refresh_after_seconds: 5,
            instructions_i18n_key: 'channel.provisioning.feishu.instructions',
            connection: null
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/provisioning-sessions/session-1/poll') && method === 'POST') {
        pollCount += 1
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            session_id: 'session-1',
            channel_type: 'feishu',
            status: 'completed',
            qr_url: 'https://accounts.feishu.cn/oauth/v1/device/verify?user_code=QFXB-8X3X',
            qr_image_url: 'https://open.feishu.cn/qr.png',
            expires_at: '2026-05-02T08:00:00Z',
            refresh_after_seconds: 5,
            instructions_i18n_key: 'channel.provisioning.feishu.instructions',
            connection: {
              id: 'conn-1',
              name: 'Feishu QR Bot',
              channel_type: 'feishu',
              enabled: true
            }
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    jest.useFakeTimers()
    try {
      await channelsPage.mount(container)

      container.querySelector('#btnScanProvisioning').click()
      await Promise.resolve()
      await Promise.resolve()

      expect(container.querySelector('#channelProvisioningModal')).not.toBeNull()

      await jest.advanceTimersByTimeAsync(5000)

      expect(container.textContent).toContain('Feishu QR Bot')
      expect(container.querySelector('#channelProvisioningModal')).not.toBeNull()

      await jest.advanceTimersByTimeAsync(900)

      expect(container.querySelector('#channelProvisioningModal')).toBeNull()
    } finally {
      jest.useRealTimers()
      await channelsPage.unmount()
    }
  })

  test('provisioning refresh handles immediate registration completion', async () => {
    window.history.replaceState({}, '', '/channels?type=feishu')
    let refreshCompleted = false

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      const method = String(options.method || 'GET').toUpperCase()

      if (target.endsWith('/api/channels')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              type: 'feishu',
              name: 'Feishu',
              mode: 'bidirectional',
              connection_count: refreshCompleted ? 1 : 0,
              provisioning: {
                supported: true,
                default_mode: 'qr',
                manual_config_available: true,
                instructions_i18n_key: 'channel.provisioning.feishu.instructions'
              }
            }
          ])
        })
      }

      if (target.endsWith('/api/channels/feishu/connections')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            channel_type: 'feishu',
            connections: refreshCompleted
              ? [{
                  id: 'conn-refresh',
                  name: 'Refreshed Feishu Bot',
                  channel_type: 'feishu',
                  enabled: true,
                  is_default: false,
                  runtime_status: 'connecting',
                  config: {}
                }]
              : []
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/provisioning-sessions') && method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            session_id: 'session-refresh',
            channel_type: 'feishu',
            status: 'pending',
            qr_url: 'https://accounts.feishu.cn/oauth/v1/device/verify?user_code=QFXB-8X3X',
            qr_image_url: 'https://open.feishu.cn/qr.png',
            expires_at: '2026-05-02T08:00:00Z',
            refresh_after_seconds: 5,
            instructions_i18n_key: 'channel.provisioning.feishu.instructions',
            connection: null
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/provisioning-sessions/session-refresh/refresh') && method === 'POST') {
        refreshCompleted = true
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            session_id: 'session-refresh',
            channel_type: 'feishu',
            status: 'completed',
            qr_url: 'https://accounts.feishu.cn/oauth/v1/device/verify?user_code=REFR-ESH1',
            qr_image_url: 'https://open.feishu.cn/qr-refreshed.png',
            expires_at: '2026-05-02T08:00:00Z',
            refresh_after_seconds: 5,
            instructions_i18n_key: 'channel.provisioning.feishu.instructions',
            connection: {
              id: 'conn-refresh',
              name: 'Refreshed Feishu Bot',
              channel_type: 'feishu',
              enabled: true
            }
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    jest.useFakeTimers()
    try {
      await channelsPage.mount(container)

      container.querySelector('#btnScanProvisioning').click()
      await flushMicrotasks(10)

      const refreshButton = await waitForEnabledButton(container, '[data-provisioning-refresh]')
      expect(refreshButton).not.toBeNull()
      expect(refreshButton.disabled).toBe(false)

      refreshButton.click()
      await waitForText(container, 'Refreshed Feishu Bot')

      expect(container.textContent).toContain('Refreshed Feishu Bot')
      expect(container.querySelector('#channelProvisioningModal')).not.toBeNull()

      await jest.advanceTimersByTimeAsync(900)

      expect(container.querySelector('#channelProvisioningModal')).toBeNull()
    } finally {
      jest.useRealTimers()
      await channelsPage.unmount()
    }
  })

  test('provisioning channel manual action still opens the schema form', async () => {
    window.history.replaceState({}, '', '/channels?type=feishu')

    global.fetch = jest.fn((url) => {
      const target = String(url)

      if (target.endsWith('/api/channels')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              type: 'feishu',
              name: 'Feishu',
              mode: 'bidirectional',
              connection_count: 0,
              provisioning: {
                supported: true,
                default_mode: 'qr',
                manual_config_available: true,
                instructions_i18n_key: 'channel.provisioning.feishu.instructions'
              }
            }
          ])
        })
      }

      if (target.endsWith('/api/channels/feishu/connections')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            channel_type: 'feishu',
            connections: []
          })
        })
      }

      if (target.endsWith('/api/channels/feishu/schema')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            type: 'object',
            properties: {
              connection_mode: { type: 'string', title: 'Connection Mode' },
              app_id: { type: 'string', title: 'App ID' },
              app_secret: { type: 'string', title: 'App Secret' }
            },
            required: ['app_id', 'app_secret'],
            provisioning: {
              supported: true,
              default_mode: 'qr',
              manual_config_available: true
            }
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    container.querySelector('#btnCreateConnection').click()
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(window.location.search).toBe('?type=feishu&edit=new')
    expect(container.querySelector('#channelEditView').style.display).toBe('block')
    expect(container.querySelector('#channelProvisioningModal')).toBeNull()

    await channelsPage.unmount()
  })

  test('enterprise channel edit form omits provider configuration controls', async () => {
    window.history.replaceState({}, '', '/channels?type=feishu&edit=new')

    global.fetch = jest.fn((url) => {
      const target = String(url)

      if (target.endsWith('/api/channels')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { type: 'feishu', name: 'Feishu', mode: 'bidirectional', connection_count: 0 },
            { type: 'websocket', name: 'WebSocket', mode: 'bidirectional', connection_count: 1 }
          ])
        })
      }

      if (target.endsWith('/api/channels/feishu/schema')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            type: 'object',
            properties: {
              connection_mode: {
                type: 'string',
                title: 'Connection Mode',
                enum: ['longconnection', 'webhook'],
                enumLabels: {
                  longconnection: 'Long Connection',
                  webhook: 'Webhook'
                },
                default: 'longconnection'
              },
              app_id: {
                type: 'string',
                title: 'App ID',
                showWhen: { connection_mode: 'longconnection' }
              }
            },
            required_by_mode: {
              longconnection: ['app_id'],
              webhook: []
            }
          })
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    const labels = [...container.querySelectorAll('.ch-form-label')].map((item) => item.textContent.trim())

    expect(container.querySelector('#channelEditView').style.display).toBe('block')
    expect(labels).toContain('CONNECTION MODE')
    expect(labels.some((label) => label.startsWith('APP ID'))).toBe(true)
    expect(labels).not.toContain('AUTHENTICATION METHOD')
    expect(labels).not.toContain('AUTHENTICATION INSTANCE')
    expect(container.querySelector('select[name="provider_type"]')).toBeNull()
    expect(container.querySelector('select[name="provider_binding"]')).toBeNull()
    expect(container.querySelector('button[data-clear-target="provider_type"]')).toBeNull()
    expect(container.querySelector('button[data-clear-target="provider_binding"]')).toBeNull()
    expect(container.querySelector('.ch-aurora-banner')).toBeNull()

    await channelsPage.unmount()
  })
})
