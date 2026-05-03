/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

beforeEach(() => {
  jest.resetModules()
  jest.spyOn(Date, 'now').mockReturnValue(new Date('2026-05-03T12:00:00Z').getTime())
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
          {
            session_key: 'session-a',
            title: 'Query approvals',
            title_status: 'final',
            last_activity: '2026-05-03T06:00:00Z',
            created_at: '2026-05-03T06:00:00Z'
          },
          {
            session_key: 'session-b',
            title: 'Create virtual machine',
            title_status: 'final',
            last_activity: '2026-05-02T12:00:00Z',
            created_at: '2026-05-02T12:00:00Z'
          }
        ])
      })
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
})

afterEach(() => {
  jest.restoreAllMocks()
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
  test('mount reuses loaded i18n state for chat labels and inline delete confirmation', async () => {
    jest.resetModules()
    jest.spyOn(Date, 'now').mockReturnValue(new Date('2026-05-03T12:00:00Z').getTime())
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
            {
              session_key: 'session-a',
              title: '',
              title_status: 'empty',
              last_activity: '2026-05-03T11:00:00Z',
              created_at: '2026-05-03T11:00:00Z'
            }
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

    const confirmButton = sidebar.querySelector('.session-list-row.delete-pending [data-delete-session="session-a"]')
    expect(container.querySelector('#confirmDialog')).toBeNull()
    expect(confirmButton.getAttribute('aria-label')).toBe('确认 删除对话')
    expect(confirmButton.querySelector('.session-confirm-label').textContent).toBe('确认')
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
            {
              session_key: 'session-a',
              title: '',
              title_status: 'empty',
              last_activity: '2026-05-03T11:00:00Z',
              created_at: '2026-05-03T11:00:00Z'
            }
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
    searchInput.focus()
    searchInput.value = 'approvals'
    searchInput.setSelectionRange(searchInput.value.length, searchInput.value.length)
    searchInput.dispatchEvent(new Event('input'))

    expect(sidebar.textContent).toContain('Query approvals')
    expect(sidebar.textContent).not.toContain('Create virtual machine')
    const refreshedSearchInput = sidebar.querySelector('#session-search-input')
    expect(document.activeElement).toBe(refreshedSearchInput)
    expect(refreshedSearchInput.value).toBe('approvals')
    expect(refreshedSearchInput.selectionStart).toBe('approvals'.length)
  })

  test('mount renders compact h d w session activity labels', async () => {
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
            {
              session_key: 'session-a',
              title: 'Recent activity',
              title_status: 'final',
              last_activity: '2026-05-03T06:00:00Z',
              created_at: '2026-05-03T06:00:00Z'
            },
            {
              session_key: 'session-b',
              title: 'Daily activity',
              title_status: 'final',
              last_activity: '2026-05-01T12:00:00Z',
              created_at: '2026-05-01T12:00:00Z'
            },
            {
              session_key: 'session-c',
              title: 'Weekly activity',
              title_status: 'final',
              last_activity: '2026-04-19T12:00:00Z',
              created_at: '2026-04-19T12:00:00Z'
            }
          ])
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({})
      })
    })

    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)

    const sidebar = document.getElementById('sidebar-dynamic-content')
    expect(sidebar.querySelector('[data-delete-session="session-a"] .session-age').textContent).toBe('6h')
    expect(sidebar.querySelector('[data-delete-session="session-b"] .session-age').textContent).toBe('2d')
    expect(sidebar.querySelector('[data-delete-session="session-c"] .session-age').textContent).toBe('2w')
  })

  test('delete session requires inline confirmation before calling the API', async () => {
    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)
    global.fetch.mockClear()

    const sidebar = document.getElementById('sidebar-dynamic-content')
    const firstDeleteButton = sidebar.querySelector('[data-delete-session="session-b"]')

    firstDeleteButton.click()

    expect(sidebar.querySelector('.session-list-row.delete-pending [data-delete-session="session-b"]')).not.toBeNull()
    expect(global.fetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/api/sessions/session-b'),
      expect.objectContaining({ method: 'DELETE' })
    )

    sidebar.querySelector('[data-delete-session="session-b"]').click()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/sessions/session-b'),
      expect.objectContaining({ method: 'DELETE' })
    )
    expect(sidebar.querySelector('[data-session-key="session-b"]')).toBeNull()
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

  test('activateChatSession focuses the chat input after new chat activation', async () => {
    jest.resetModules()

    const focusChatInput = jest.fn()
    const cancelChatInputFocusRetry = jest.fn()
    jest.unstable_mockModule('../../app/frontend/scripts/chat-ui.js', () => ({
      initChat: jest.fn(async () => {}),
      activateSession: jest.fn(async () => false),
      abortCurrentStream: jest.fn(),
      getCurrentAgentInfo: jest.fn(() => ({ name: 'AtlasClaw Enterprise AI Assistant' })),
      focusChatInput,
      cancelChatInputFocusRetry
    }))

    const chatPage = await import('../../app/frontend/scripts/pages/chat.js')
    const container = document.getElementById('page-root')

    await chatPage.mount(container)
    focusChatInput.mockClear()

    const activated = await chatPage.activateChatSession('session-c')

    expect(activated).toBe(true)
    expect(focusChatInput).toHaveBeenCalledTimes(1)

    await chatPage.unmount()
    expect(cancelChatInputFocusRetry).toHaveBeenCalledTimes(1)
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
      getCurrentAgentInfo: jest.fn(() => ({ name: 'AtlasClaw Enterprise AI Assistant' })),
      focusChatInput: jest.fn(),
      cancelChatInputFocusRetry: jest.fn()
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
