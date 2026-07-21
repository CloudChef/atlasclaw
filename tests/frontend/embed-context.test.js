/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { parseEmbedSurface } from '../../app/frontend/scripts/embed/surface.js'
import { EmbedContextStore } from '../../app/frontend/scripts/embed/context-store.js'
import { EmbedContextBridge, EMBED_PROTOCOL } from '../../app/frontend/scripts/embed/context-bridge.js'
import { EmbedContextController } from '../../app/frontend/scripts/embed/context-controller.js'
import { renderContextObjectActions } from '../../app/frontend/scripts/chat-ui.js'
import { jest } from '@jest/globals'

describe('atlasclaw-embed/v1 frontend contract', () => {
  test('parses only controlled surfaces and never accepts a Host Chat Active Session override', () => {
    const surface = parseEmbedSurface(
      'https://agent.example/atlasclaw/?embedded=1&surface=menu&session_key=host-value'
    )

    expect(surface).toMatchObject({
      embedded: true,
      surface: 'menu',
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

    expect(bridge.openFullAgent).toBeUndefined()
  })

  test('floating Context renders only provider-declared object actions with Chat tones', () => {
    document.body.innerHTML = '<div id="actions" hidden></div>'
    const actions = document.getElementById('actions')

    expect(renderContextObjectActions(actions, contextActionReference('ITEM-20'))).toBe(true)
    expect(actions.hidden).toBe(false)
    expect(Array.from(actions.querySelectorAll('.object-action-text')).map((node) => node.textContent))
      .toEqual(['Open', 'Analyze', 'Approve', 'Reject'])
    expect(actions.textContent).not.toContain('List pending')
    expect(actions.querySelector('[data-object-action-payload*="approve"]').classList)
      .toContain('tone-success')
    expect(actions.querySelector('[data-object-action-payload*="reject"]').classList)
      .toContain('tone-danger')

    expect(renderContextObjectActions(actions, null)).toBe(false)
    expect(actions.hidden).toBe(true)
  })

  test('Context action keeps the page snapshot without exposing an exact Tool button', async () => {
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
    global.fetch = jest.fn()
      .mockResolvedValueOnce(okJsonResponse(firstPayload))
      .mockResolvedValueOnce(errorJsonResponse(409, 'Tool is no longer available'))
      .mockResolvedValueOnce(okJsonResponse(contextPayload(1, 'ctx-surface-b', 'ITEM-21')))

    const controller = new EmbedContextController({
      surface: {
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
        surface_id: 'abcdefghijklmnopqrstuvwxyz123456',
        generation: 0,
        path: '/main/request/detail/20'
      })

      const analyzeButton = Array.from(document.querySelectorAll('button.object-action-button'))
        .find((button) => button.textContent.includes('Analyze'))
      analyzeButton.click()
      await jest.runOnlyPendingTimersAsync()

      const runRequests = global.fetch.mock.calls.filter(([url]) => (
        String(url).includes('/api/agent/run')
      ))
      expect(runRequests).toHaveLength(1)
      const runBody = JSON.parse(runRequests[0][1].body)
      expect(runBody.context).toMatchObject({
        embed_context_id: 'ctx-surface-a',
        context_generation: 0
      })
      expect(runBody.context.embed_tool_name).toBeUndefined()
      expect(runBody.message).toBe('Analyze ITEM-20')
      expect(chat.addMessage).not.toHaveBeenCalled()
      let alerts = document.querySelectorAll('#actions [role="alert"]')
      expect(alerts).toHaveLength(1)
      expect(alerts[0].textContent).toBe('Tool is no longer available')

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
    skill: { ref: 'example:item', name: 'item' },
    object_actions: contextActionReference(objectId).object_actions
  }
}

function contextActionReference(objectId) {
  return {
    object_type: 'test',
    object_id: objectId,
    object_name: objectId,
    object_actions: [
      {
        action_id: 'open_detail',
        kind: 'open_url',
        display_label: { default: 'Open' },
        href: `https://host.example/#/main/items/${objectId}`,
        tone: 'default'
      },
      {
        action_id: 'analyze',
        kind: 'agent_prompt',
        display_label: { default: 'Analyze' },
        agent_prompt: { default: `Analyze ${objectId}` },
        tone: 'default'
      },
      {
        action_id: 'approve',
        kind: 'agent_prompt',
        display_label: { default: 'Approve' },
        agent_prompt: { default: `Approve ${objectId}` },
        tone: 'success'
      },
      {
        action_id: 'reject',
        kind: 'agent_prompt',
        display_label: { default: 'Reject' },
        agent_prompt: { default: `Reject ${objectId}` },
        tone: 'danger'
      }
    ]
  }
}
