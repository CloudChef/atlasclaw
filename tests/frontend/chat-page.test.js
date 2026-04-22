/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

beforeEach(() => {
  jest.resetModules()
  document.body.innerHTML = `
    <div id="sidebar-dynamic-content"></div>
    <div id="page-root"></div>
  `
  sessionStorage.clear()
  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    if (target.endsWith('/api/sessions/threads')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ session_key: 'session-a' })
      })
    }
    if (target.endsWith('/api/agent/info')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          name: 'AtlasClaw Enterprise AI Assistant',
          welcome_message: 'Welcome'
        })
      })
    }
    if (target.endsWith('/api/sessions/session-a/history')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ messages: [] })
      })
    }
    if (target.endsWith('/api/sessions')) {
      if (options.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ session_key: 'session-a' })
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve([
          { session_key: 'session-a', title: 'Query approvals', title_status: 'final' },
          { session_key: 'session-b', title: 'Create virtual machine', title_status: 'final' }
        ])
      })
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
})

const sessionStorageMock = (() => {
  let store = {}
  return {
    getItem: jest.fn((key) => store[key] || null),
    setItem: jest.fn((key, value) => { store[key] = value }),
    removeItem: jest.fn((key) => { delete store[key] }),
    clear: jest.fn(() => { store = {} })
  }
})()

Object.defineProperty(global, 'sessionStorage', { value: sessionStorageMock })

describe('chat page', () => {
  test('mount reuses loaded i18n state for chat labels and confirm dialog copy', async () => {
    jest.resetModules()
    document.body.innerHTML = `
      <div id="sidebar-dynamic-content"></div>
      <div id="page-root"></div>
    `
    sessionStorage.clear()

    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: 'zh-CN'
    })

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)

      if (target.includes('/locales/zh-CN.json')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            app: {
              newChat: '新建对话'
            },
            chat: {
              placeholder: '请输入问题...',
              session: {
                searchPlaceholder: '搜索对话...',
                deleteLabel: '删除对话'
              }
            },
            dialog: {
              confirmTitle: '确认操作',
              confirmMessage: '确定要继续吗？',
              cancel: '取消',
              confirm: '确认'
            }
          })
        })
      }

      if (target.endsWith('/api/sessions/threads')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ session_key: 'session-a' })
        })
      }
      if (target.endsWith('/api/agent/info')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            name: '企业助手',
            welcome_message: '欢迎使用'
          })
        })
      }
      if (target.endsWith('/api/sessions/session-a/history')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ messages: [] })
        })
      }
      if (target.endsWith('/api/sessions')) {
        if (options.method === 'POST') {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-a' })
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { session_key: 'session-a', title: '', title_status: 'empty' }
          ])
        })
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const i18n = await import('../../app/frontend/scripts/i18n.js')
    await i18n.initI18n()

    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)

    const sidebar = document.getElementById('sidebar-dynamic-content')
    const searchInput = sidebar.querySelector('#session-search-input')
    const deleteButton = sidebar.querySelector('[data-delete-session="session-a"]')
    const chatElement = container.querySelector('#chat')

    expect(sidebar.textContent).toContain('新建对话')
    expect(searchInput.placeholder).toBe('搜索对话...')
    expect(deleteButton.getAttribute('aria-label')).toBe('删除对话')
    expect(chatElement.textInput.placeholder.text).toBe('请输入问题...')

    deleteButton.click()

    expect(container.querySelector('#confirmDialog h3').textContent).toBe('确认操作')
    expect(container.querySelector('#confirmMessage').textContent).toBe('确定要继续吗？')
    expect(container.querySelector('.btn-cancel').textContent).toBe('取消')
    expect(container.querySelector('.btn-confirm').textContent).toBe('确认')
  })

  test('empty active session is reused when starting a new chat', async () => {
    jest.resetModules()
    document.body.innerHTML = `
      <div id="sidebar-dynamic-content"></div>
      <div id="page-root"></div>
    `
    sessionStorage.clear()

    global.fetch = jest.fn((url, options = {}) => {
      const target = String(url)
      if (target.endsWith('/api/sessions/threads')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ session_key: 'session-a' })
        })
      }
      if (target.endsWith('/api/agent/info')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            name: 'AtlasClaw Enterprise AI Assistant',
            welcome_message: 'Welcome'
          })
        })
      }
      if (target.endsWith('/api/sessions/session-a/history')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ messages: [] })
        })
      }
      if (target.endsWith('/api/sessions')) {
        if (options.method === 'POST') {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-a' })
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { session_key: 'session-a', title: '', title_status: 'empty' }
          ])
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const { startNewSession } = await import('../../app/frontend/scripts/session-manager.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)
    global.fetch.mockClear()

    const nextSessionKey = await startNewSession()

    expect(nextSessionKey).toBe('session-a')
    expect(global.fetch).not.toHaveBeenCalled()
  })

  test('mount renders searchable session titles without date grouping', async () => {
    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)

    const sidebar = document.getElementById('sidebar-dynamic-content')
    expect(sidebar.textContent).toContain('Query approvals')
    expect(sidebar.textContent).toContain('Create virtual machine')
    expect(sidebar.textContent).not.toContain('Today')

    const searchInput = sidebar.querySelector('#session-search-input')
    searchInput.value = 'approvals'
    searchInput.dispatchEvent(new Event('input'))

    expect(sidebar.textContent).toContain('Query approvals')
    expect(sidebar.textContent).not.toContain('Create virtual machine')
  })

  test('activateChatSession switches the mounted chat page to a fresh empty session', async () => {
    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)

    const activated = await chatPage.activateChatSession('session-c')

    expect(activated).toBe(true)
    expect(sessionStorage.getItem('atlasclaw_session_key')).toBe('session-c')

    const sidebar = document.getElementById('sidebar-dynamic-content')
    const activeButton = sidebar.querySelector('.session-list-row.active [data-session-key="session-c"]')
    expect(activeButton).not.toBeNull()
    expect(activeButton.textContent).toBe('New Chat')
  })

  test('user turn hides empty state immediately before assistant response returns', async () => {
    jest.resetModules()

    let capturedCallbacks = null
    jest.unstable_mockModule('../../app/frontend/scripts/chat-ui.js', () => ({
      initChat: jest.fn(async (_element, callbacks = {}) => {
        capturedCallbacks = callbacks
      }),
      activateSession: jest.fn(async () => false),
      refreshActiveSessionHistory: jest.fn(async () => false),
      abortCurrentStream: jest.fn(),
      getCurrentAgentInfo: jest.fn(() => ({ name: 'AtlasClaw Enterprise AI Assistant' }))
    }))

    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)

    capturedCallbacks.onConversationStateChange({
      hasMessages: false,
      agentInfo: {
        name: 'AtlasClaw Enterprise AI Assistant',
        welcome_message: 'Welcome'
      }
    })

    const emptyState = container.querySelector('#chat-empty-state')
    expect(emptyState.classList.contains('hidden')).toBe(false)

    capturedCallbacks.onUserTurnStarted({
      sessionKey: 'session-a',
      messageText: '你好'
    })

    expect(emptyState.classList.contains('hidden')).toBe(true)
    expect(container.classList.contains('chat-empty-mode')).toBe(false)
  })
})
