/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

beforeEach(() => {
  jest.resetModules()
  document.body.innerHTML = '<div id="page-root"></div>'

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

  global.fetch = jest.fn((url) => {
    const target = String(url)

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

    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
})

describe('channels page', () => {
  test('mount keeps built-in channel types visible and shows planned placeholders', async () => {
    const channelsPage = await import('../../app/frontend/scripts/pages/channels.js')
    const container = document.getElementById('page-root')

    await channelsPage.mount(container)

    const cardTypes = [...container.querySelectorAll('.ch-type-card')].map((card) => card.dataset.type)

    expect(cardTypes).toEqual(['websocket', 'rest', 'sse', 'slack', 'discord'])

    container.querySelector('.ch-type-card[data-type="slack"]').click()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(container.querySelector('#btnCreateConnection').disabled).toBe(true)
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
