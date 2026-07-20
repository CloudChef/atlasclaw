/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { parseEmbedSurface } from '../../app/frontend/scripts/embed/surface.js'
import { EmbedContextStore } from '../../app/frontend/scripts/embed/context-store.js'
import { EmbedContextBridge, EMBED_PROTOCOL } from '../../app/frontend/scripts/embed/context-bridge.js'
import { EmbedContextController } from '../../app/frontend/scripts/embed/context-controller.js'
import { renderObjectActionReferences } from '../../app/frontend/scripts/chat-ui.js'
import { jest } from '@jest/globals'

describe('atlasclaw-embed/v1 frontend contract', () => {
  test('parses only controlled surfaces and never accepts a Host Chat Active Session override', () => {
    const surface = parseEmbedSurface(
      'https://agent.example/atlasclaw/?embedded=1&surface=menu&integration=tenant-assistant&session_key=host-value'
    )

    expect(surface).toMatchObject({
      embedded: true,
      surface: 'menu',
      integrationId: 'tenant-assistant',
      hostOrigin: null,
      nonce: null,
      integrationMode: true
    })
    expect(surface.sessionKey).toBeUndefined()
  })

  test('clears old context synchronously and discards stale generation responses', () => {
    const store = new EmbedContextStore()
    store.beginResolve(1)
    store.completeResolve(1, contextPayload(1, 'ctx-a', 'A'))
    expect(store.getCurrent().object.id).toBe('A')

    store.beginResolve(2)
    expect(store.getCurrent()).toBeNull()
    expect(store.completeResolve(1, contextPayload(1, 'ctx-a', 'A'))).toBe(false)
    store.completeResolve(2, contextPayload(2, 'ctx-b', 'B'))
    expect(store.getCurrent().object.id).toBe('B')
  })

  test('rejects a turn while current page context is still resolving', async () => {
    jest.useFakeTimers()
    const store = new EmbedContextStore()
    store.beginResolve(9)
    const pending = store.getTurnContext(500)

    jest.advanceTimersByTime(500)
    await expect(pending).rejects.toMatchObject({ code: 'EMBED_CONTEXT_PENDING' })
    jest.useRealTimers()
  })

  test('unsupported pages allow ordinary Chat while matched failures fail closed', async () => {
    const unsupported = new EmbedContextStore()
    unsupported.beginResolve(10)
    unsupported.completeResolve(10, { generation: 10, status: 'unsupported' })
    await expect(unsupported.getTurnContext()).resolves.toBeNull()
    expect(unsupported.getStatus()).toBe('unsupported')

    const unavailable = new EmbedContextStore()
    unavailable.beginResolve(11)
    unavailable.completeResolve(11, { generation: 11, status: 'unavailable' })
    await expect(unavailable.getTurnContext()).rejects.toMatchObject({
      code: 'EMBED_CONTEXT_UNAVAILABLE'
    })
    expect(unavailable.getStatus()).toBe('unavailable')
  })

  test('bridge accepts only the minimal normalized PAGE_CHANGED payload', () => {
    const parent = { postMessage: jest.fn() }
    const listeners = {}
    const frameWindow = {
      parent,
      addEventListener: jest.fn((type, handler) => { listeners[type] = handler }),
      removeEventListener: jest.fn()
    }
    const onPageChanged = jest.fn()
    const bridge = new EmbedContextBridge({
      windowRef: frameWindow,
      integrationId: 'tenant-assistant',
      hostOrigin: 'https://host.example',
      nonce: 'abcdefghijklmnopqrstuvwxyz123456',
      onPageChanged
    })

    expect(bridge.start()).toBe(true)
    expect(parent.postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'ATLASCLAW_READY'
    }), 'https://host.example')
    expect(parent.postMessage).not.toHaveBeenCalledWith(expect.anything(), '*')

    const valid = {
      protocol: EMBED_PROTOCOL,
      type: 'PAGE_CHANGED',
      integration_id: 'tenant-assistant',
      nonce: 'abcdefghijklmnopqrstuvwxyz123456',
      generation: 3,
      path: '/portal/items/42'
    }
    listeners.message({ source: parent, origin: 'https://host.example', data: valid })
    expect(onPageChanged).toHaveBeenCalledWith({
      generation: 3,
      path: '/portal/items/42'
    })
    listeners.message({
      source: parent,
      origin: 'https://host.example',
      data: { ...valid, generation: 4, path: '/portal/items/42' }
    })
    expect(onPageChanged).toHaveBeenLastCalledWith({
      generation: 4,
      path: '/portal/items/42'
    })

    onPageChanged.mockClear()
    ;[
      { ...valid, title: 'not-in-v1' },
      { ...valid, selection: [] },
      { ...valid, path: 'https://host.example/main/detail/42' },
      { ...valid, path: '/main/detail/42?tab=a' },
      { ...valid, path: '//host.example/main/detail/42' },
      { ...valid, path: '/portal//detail/42' },
      { ...valid, path: '/portal/%2e%2e/detail/42' },
      { ...valid, path: '/portal/detail%2f42' },
      { ...valid, path: '/portal/detail%252f42' }
    ].forEach((data) => listeners.message({
      source: parent,
      origin: 'https://host.example',
      data
    }))
    listeners.message({ source: {}, origin: 'https://host.example', data: valid })
    listeners.message({ source: parent, origin: 'https://evil.example', data: valid })
    expect(onPageChanged).not.toHaveBeenCalled()

    bridge.openFullAgent()
    const payload = parent.postMessage.mock.calls.at(-1)[0]
    expect(payload.type).toBe('OPEN_FULL_AGENT')
    expect(payload.session_key).toBeUndefined()
    expect(payload.path).toBeUndefined()
  })

  test('floating Context reuses object action controls and inline input collection', () => {
    document.body.innerHTML = '<div id="actions" hidden></div>'
    const actions = document.getElementById('actions')

    expect(renderObjectActionReferences(actions, [objectActionReference()])).toBe(true)
    expect(actions.hidden).toBe(false)
    expect(Array.from(actions.querySelectorAll('.object-action-text')).map((node) => node.textContent))
      .toEqual(['View', 'Inspect', 'Update', 'Archive'])

    actions.querySelectorAll('button.object-action-button')[2].click()
    expect(actions.querySelector('.object-action-confirmation-title').textContent)
      .toBe('Provide information for Archive')

    expect(renderObjectActionReferences(actions, [])).toBe(false)
    expect(actions.hidden).toBe(true)
    expect(actions.querySelector('.object-action-confirmation-card')).toBeNull()
  })

  test('aborts a captured Context action when PAGE_CHANGED wins before fetch', () => {
    jest.useFakeTimers()
    const previousFetch = global.fetch
    global.fetch = jest.fn()
    try {
      document.body.innerHTML = '<div id="actions" hidden></div><deep-chat></deep-chat>'
      const actions = document.getElementById('actions')
      const chat = document.querySelector('deep-chat')
      chat.addMessage = jest.fn()
      chat.handler = jest.fn()
      const shadow = chat.attachShadow({ mode: 'open' })
      const chatContainer = document.createElement('div')
      chatContainer.id = 'container'
      shadow.appendChild(chatContainer)

      const store = new EmbedContextStore()
      store.beginResolve(20)
      const payload = contextPayload(20, 'ctx-a', 'ITEM-10')
      payload.object_actions = [objectActionReference()]
      store.completeResolve(20, payload)
      const turnContext = {
        embed_context_id: 'ctx-a',
        context_generation: 20,
        integration_id: 'tenant-assistant'
      }
      renderObjectActionReferences(actions, payload.object_actions, {
        turnContext,
        isContextCurrent: (candidate) => (
          candidate.integration_id === 'tenant-assistant' && store.isCurrent(candidate)
        )
      })

      actions.querySelectorAll('button.object-action-button')[0].click()
      store.beginResolve(21)
      jest.runOnlyPendingTimers()

      expect(global.fetch).not.toHaveBeenCalled()
      expect(chat.addMessage).toHaveBeenCalledWith(expect.objectContaining({
        role: 'ai',
        html: expect.stringContaining('page context changed')
      }))
    } finally {
      global.fetch = previousFetch
      jest.useRealTimers()
    }
  })

  test('Context actions submit once, replace local alerts, and clear errors on new Context', async () => {
    jest.useFakeTimers()
    const previousFetch = global.fetch
    sessionStorage.setItem('atlasclaw_session_key', 'session-context-action')
    document.body.innerHTML = [
      '<div id="context"></div>',
      '<div id="actions" hidden></div>',
      '<deep-chat></deep-chat>'
    ].join('')
    const chat = document.querySelector('deep-chat')
    chat.addMessage = jest.fn()
    chat.handler = jest.fn()
    const shadow = chat.attachShadow({ mode: 'open' })
    const chatContainer = document.createElement('div')
    chatContainer.id = 'container'
    shadow.appendChild(chatContainer)

    const firstPayload = contextPayload(0, 'ctx-surface-a', 'ITEM-20')
    firstPayload.object_actions = [objectActionReference()]
    global.fetch = jest.fn()
      .mockResolvedValueOnce(okJsonResponse(firstPayload))
      .mockResolvedValueOnce(errorJsonResponse(409, 'First page action error'))
      .mockResolvedValueOnce(errorJsonResponse(409, 'Replacement page action error'))
      .mockResolvedValueOnce(errorJsonResponse(409, 'First archive action error'))
      .mockResolvedValueOnce(errorJsonResponse(409, 'Replacement archive action error'))
      .mockResolvedValueOnce(okJsonResponse(contextPayload(1, 'ctx-surface-b', 'ITEM-21')))

    const controller = new EmbedContextController({
      surface: {
        integrationId: 'tenant-assistant',
        hostOrigin: 'https://host.example',
        nonce: 'abcdefghijklmnopqrstuvwxyz123456'
      },
      contextSlot: document.getElementById('context'),
      actionSlot: document.getElementById('actions'),
    })

    try {
      await controller._handlePageChanged({ generation: 0, path: '/main/request/detail/20' })
      const resolveBody = JSON.parse(global.fetch.mock.calls[0][1].body)
      expect(resolveBody).toEqual({
        integration_id: 'tenant-assistant',
        surface_id: 'abcdefghijklmnopqrstuvwxyz123456',
        generation: 0,
        path: '/main/request/detail/20'
      })

      const inspectButton = Array.from(document.querySelectorAll('button.object-action-button'))
        .find((button) => button.textContent.includes('Inspect'))
      inspectButton.click()
      inspectButton.click()
      expect(inspectButton.disabled).toBe(true)
      expect(document.getElementById('actions').getAttribute('aria-busy')).toBe('true')
      await jest.runOnlyPendingTimersAsync()

      const runRequestsAfterDoubleClick = global.fetch.mock.calls.filter(([url]) => (
        String(url).includes('/api/agent/run')
      ))
      expect(runRequestsAfterDoubleClick).toHaveLength(1)
      expect(chat.addMessage).not.toHaveBeenCalled()
      let alerts = document.querySelectorAll('#actions [role="alert"]')
      expect(alerts).toHaveLength(1)
      expect(alerts[0].textContent).toBe('First page action error')

      inspectButton.click()
      await jest.runOnlyPendingTimersAsync()
      alerts = document.querySelectorAll('#actions [role="alert"]')
      expect(alerts).toHaveLength(1)
      expect(alerts[0].textContent).toBe('Replacement page action error')
      expect(chat.addMessage).not.toHaveBeenCalled()

      const archiveButton = Array.from(document.querySelectorAll('button.object-action-button'))
        .find((button) => button.textContent.includes('Archive'))
      archiveButton.click()
      const card = document.querySelector('.object-action-confirmation-card')
      card.querySelector('[data-object-action-input-name="reason"]').value = 'No longer needed'
      const submitArchive = Array.from(card.querySelectorAll('button'))
        .find((button) => button.textContent.includes('Submit Archive'))
      submitArchive.click()
      submitArchive.click()
      expect(submitArchive.disabled).toBe(true)
      await jest.runOnlyPendingTimersAsync()

      expect(global.fetch.mock.calls.filter(([url]) => (
        String(url).includes('/api/agent/run')
      ))).toHaveLength(3)
      alerts = document.querySelectorAll('#actions [role="alert"]')
      expect(alerts).toHaveLength(1)
      expect(alerts[0].textContent).toBe('First archive action error')
      expect(card.querySelector('.object-action-confirmation-error').textContent).toBe('')
      expect(card.classList.contains('has-error')).toBe(false)
      expect(card.classList.contains('is-submitting')).toBe(false)
      expect(submitArchive.disabled).toBe(false)
      expect(document.getElementById('actions').hasAttribute('aria-busy')).toBe(false)
      expect(chat.addMessage).not.toHaveBeenCalled()

      submitArchive.click()
      await jest.runOnlyPendingTimersAsync()
      alerts = document.querySelectorAll('#actions [role="alert"]')
      expect(alerts).toHaveLength(1)
      expect(alerts[0].textContent).toBe('Replacement archive action error')
      expect(card.querySelector('.object-action-confirmation-error').textContent).toBe('')
      expect(submitArchive.disabled).toBe(false)

      await controller._handlePageChanged({ generation: 1, path: '/main/request/detail/21' })
      expect(document.querySelector('#actions [role="alert"]')).toBeNull()
    } finally {
      controller.destroy()
      global.fetch = previousFetch
      sessionStorage.removeItem('atlasclaw_session_key')
      jest.useRealTimers()
    }
  })
})

function okJsonResponse(payload) {
  return {
    ok: true,
    json: () => Promise.resolve(payload)
  }
}

function errorJsonResponse(status, detail) {
  return {
    ok: false,
    status,
    statusText: 'Conflict',
    json: () => Promise.resolve({ detail })
  }
}

function contextPayload(generation, contextId, objectId) {
  return {
    generation,
    status: 'resolved',
    context_id: contextId,
    object: { id: objectId, type: 'test', name: objectId },
  }
}

function objectActionReference() {
  const action = (actionId, label, extra = {}) => ({
    action_id: actionId,
    kind: 'agent_prompt',
    display_label: { default: label },
    agent_prompt: { default: `${label} ITEM-10` },
    ...extra
  })
  return {
    object_type: 'item',
    object_id: 'ITEM-10',
    object_name: 'Managed item',
    object_actions: [
      {
        action_id: 'open',
        kind: 'open_url',
        display_label: { default: 'View' },
        href: 'https://host.example/#/items/ITEM-10'
      },
      action('inspect', 'Inspect'),
      action('update', 'Update'),
      action('archive', 'Archive', {
        tone: 'danger',
        agent_prompt_template: { default: 'Archive ITEM-10 because {{reason}}' },
        inputs: [
          {
            name: 'reason',
            display_label: { default: 'Reason' },
            required: true
          }
        ]
      })
    ]
  }
}
