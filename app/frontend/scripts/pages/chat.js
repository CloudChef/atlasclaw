/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * chat.js - Chat Page Module
 */

import { initSession, getSessionKey, setSessionKey } from '../session-manager.js?v=19'
import { initChat, activateSession, abortCurrentStream, getCurrentAgentInfo, focusChatInput, cancelChatInputFocusRetry } from '../chat-ui.js?v=19'
import { listSessions, deleteSession } from '../api-client.js?v=19'
import { translateIfExists } from '../i18n.js'
import { updateHeaderTitleText } from '../components/header.js?v=19'
import { restoreInputFocus } from '../dom-utils.js?v=19'

let chatElement = null
let mounted = false
let currentSessionKey = null
let sessionsCache = []
let searchQuery = ''
let pageContainer = null
let currentAgentName = 'AtlasClaw'
let pendingDeleteSessionKey = null

function getTranslatedText(key, fallback) {
  return translateIfExists(key) || fallback
}

function getNewChatLabel() {
  return getTranslatedText('app.newChat', 'New Chat')
}

function getSessionSearchPlaceholder() {
  return getTranslatedText('chat.session.searchPlaceholder', 'Search chats...')
}

function getDeleteSessionLabel() {
  return getTranslatedText('chat.session.deleteLabel', 'Delete')
}

function getConfirmDeleteLabel() {
  return getTranslatedText('dialog.confirm', 'Confirm')
}

function getConfirmDeleteActionLabel() {
  return `${getConfirmDeleteLabel()} ${getDeleteSessionLabel()}`
}

function buildSessionDraftTitle(messageText) {
  const cleaned = String(messageText || '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[,.!?，。！？；：]+$/g, '')

  if (!cleaned) return getNewChatLabel()
  return cleaned.length > 24 ? `${cleaned.slice(0, 23).trim()}...` : cleaned
}

export async function mount(container) {
  pageContainer = container

  container.innerHTML = `
    <div class="chat-page-shell">
      <div class="chat-canvas-shell">
        <div id="chat-empty-state" class="chat-empty-state hidden">
          <div class="chat-empty-inner">
            <h1 class="chat-empty-title"></h1>
            <p class="chat-empty-copy"></p>
          </div>
        </div>
        <div class="chat-canvas-frame">
          <deep-chat
            id="chat"
            style="width: 100%; height: 100%; display: flex; flex-direction: column;"
            textMarkdown="true">
          </deep-chat>
        </div>
      </div>
    </div>
  `

  try {
    await initSession()
    currentSessionKey = getSessionKey()
  } catch (error) {
    console.error('[ChatPage] Failed to initialize session:', error)
    container.innerHTML = '<div class="error-message">Failed to initialize session.</div>'
    return
  }

  chatElement = container.querySelector('#chat')
  await initChat(chatElement, {
    onConversationStateChange: handleConversationStateChange,
    onUserTurnStarted: handleUserTurnStarted,
    onRunCompleted: handleRunCompleted
  })

  currentAgentName = getCurrentAgentInfo()?.name || currentAgentName
  await loadSessions()
  mounted = true
  focusChatInput()
}

export async function unmount() {
  cancelChatInputFocusRetry()
  abortCurrentStream()
  const sidebarContent = document.getElementById('sidebar-dynamic-content')
  if (sidebarContent) sidebarContent.innerHTML = ''
  pageContainer = null
  chatElement = null
  currentSessionKey = null
  sessionsCache = []
  searchQuery = ''
  pendingDeleteSessionKey = null
  mounted = false
}

export async function activateChatSession(nextKey) {
  if (!mounted || !nextKey) return false
  await switchActiveSession(nextKey)
  focusChatInput()
  return true
}

async function loadSessions() {
  const sidebarContent = document.getElementById('sidebar-dynamic-content')
  if (!sidebarContent) return

  try {
    sessionsCache = await listSessions()
  } catch (error) {
    console.error('[ChatPage] Failed to load sessions:', error)
    sessionsCache = []
  }

  pendingDeleteSessionKey = null
  ensureActiveSessionEntry()
  renderSidebarContent(sidebarContent)
  syncHeaderTitle()
}

function ensureActiveSessionEntry() {
  if (!currentSessionKey) return
  const exists = sessionsCache.some((session) => session.session_key === currentSessionKey)
  if (!exists) {
    sessionsCache.unshift({
      session_key: currentSessionKey,
      title: getNewChatLabel(),
      title_status: 'empty'
    })
  }
}

function renderSidebarContent(container) {
  if (!container) return
  const filtered = getFilteredSessions()
  if (pendingDeleteSessionKey && !filtered.some((session) => session.session_key === pendingDeleteSessionKey)) {
    pendingDeleteSessionKey = null
  }
  const itemsHtml = filtered.map((session) => {
    const isActive = session.session_key === currentSessionKey
    const isPendingDelete = session.session_key === pendingDeleteSessionKey
    const title = getSessionTitle(session)
    const activityLabel = getSessionActivityLabel(session)
    const actionLabel = isPendingDelete ? getConfirmDeleteActionLabel() : getDeleteSessionLabel()
    return `
      <div class="session-list-row${isActive ? ' active' : ''}${isPendingDelete ? ' delete-pending' : ''}">
        <button class="session-list-item" type="button" data-session-key="${escapeHtml(session.session_key)}">${escapeHtml(title)}</button>
        <button class="session-delete-btn" type="button" data-delete-session="${escapeHtml(session.session_key)}" aria-label="${escapeHtml(actionLabel)}">
          <span class="session-age" aria-hidden="true">${escapeHtml(activityLabel)}</span>
          <span class="session-delete-icon" aria-hidden="true">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.15" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 6h18"></path>
              <path d="M8 6V4h8v2"></path>
              <path d="M19 6l-1 14H6L5 6"></path>
              <path d="M10 11v5"></path>
              <path d="M14 11v5"></path>
            </svg>
          </span>
          <span class="session-confirm-label">${escapeHtml(getConfirmDeleteLabel())}</span>
        </button>
      </div>
    `
  }).join('')

  container.innerHTML = `
    <div class="session-sidebar-shell">
      <div class="session-search-shell">
        <input id="session-search-input" class="session-search-input" type="search" placeholder="${escapeHtml(getSessionSearchPlaceholder())}" value="${escapeHtml(searchQuery)}" />
      </div>
      <div class="session-list">${itemsHtml}</div>
    </div>
  `

  const input = container.querySelector('#session-search-input')
  if (input) {
    input.addEventListener('input', (event) => {
      const selectionStart = event.target.selectionStart
      const selectionEnd = event.target.selectionEnd
      searchQuery = event.target.value || ''
      pendingDeleteSessionKey = null
      renderSidebarContent(container)
      restoreInputFocus(container, '#session-search-input', selectionStart, selectionEnd)
    })
  }

  container.querySelectorAll('[data-session-key]').forEach((button) => {
    button.addEventListener('click', handleSessionClick)
  })
  container.querySelectorAll('[data-delete-session]').forEach((button) => {
    button.addEventListener('click', handleDeleteSessionClick)
  })
}

function getFilteredSessions() {
  const normalizedQuery = searchQuery.trim().toLowerCase()
  if (!normalizedQuery) return sessionsCache
  return sessionsCache.filter((session) => getSessionTitle(session).toLowerCase().includes(normalizedQuery))
}

function getSessionTitle(session) {
  return (session?.title || '').trim() || getNewChatLabel()
}

function getSessionActivityLabel(session) {
  const timestamp = session?.last_activity || session?.created_at
  if (!timestamp) return ''

  const activityDate = new Date(timestamp)
  const activityTime = activityDate.getTime()
  if (!Number.isFinite(activityTime)) return ''

  const diffMs = Math.max(0, Date.now() - activityTime)
  const hours = Math.floor(diffMs / (60 * 60 * 1000))
  if (hours < 24) return `${hours}h`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`

  return `${Math.max(1, Math.floor(days / 7))}w`
}

async function handleSessionClick(event) {
  const nextKey = event.currentTarget.getAttribute('data-session-key')
  if (!nextKey) return
  if (nextKey === currentSessionKey) {
    if (pendingDeleteSessionKey) {
      pendingDeleteSessionKey = null
      renderSidebarContent(document.getElementById('sidebar-dynamic-content'))
    }
    return
  }

  pendingDeleteSessionKey = null
  await switchActiveSession(nextKey)
  syncHeaderTitle()
}

async function switchActiveSession(nextKey) {
  abortCurrentStream()
  pendingDeleteSessionKey = null
  setSessionKey(nextKey)
  currentSessionKey = nextKey
  await activateSession(nextKey)
  ensureActiveSessionEntry()
  renderSidebarContent(document.getElementById('sidebar-dynamic-content'))
}

function handleUserTurnStarted({ sessionKey, messageText }) {
  currentSessionKey = sessionKey
  pendingDeleteSessionKey = null
  const draftTitle = buildSessionDraftTitle(messageText)
  upsertSession({ session_key: sessionKey, title: draftTitle, title_status: 'draft' })
  const emptyState = pageContainer?.querySelector('#chat-empty-state')
  if (emptyState) {
    emptyState.classList.add('hidden')
  }
  pageContainer?.classList.remove('chat-empty-mode')
  renderSidebarContent(document.getElementById('sidebar-dynamic-content'))
  syncHeaderTitle()
}

async function handleRunCompleted() {
  await loadSessions()
}

function handleConversationStateChange({ hasMessages, agentInfo }) {
  const emptyState = pageContainer?.querySelector('#chat-empty-state')
  if (!emptyState) return

  currentAgentName = agentInfo?.name || currentAgentName
  const emptyTitle = emptyState.querySelector('.chat-empty-title')
  const emptyCopy = emptyState.querySelector('.chat-empty-copy')
  if (emptyTitle) {
    emptyTitle.textContent = currentAgentName
  }
  if (emptyCopy) {
    emptyCopy.textContent = agentInfo?.welcome_message || ''
  }

  emptyState.classList.toggle('hidden', hasMessages)
  pageContainer.classList.toggle('chat-empty-mode', !hasMessages)
  syncHeaderTitle(hasMessages)
}

function syncHeaderTitle(hasMessages = true) {
  const active = sessionsCache.find((session) => session.session_key === currentSessionKey)
  const title = hasMessages && active ? getSessionTitle(active) : currentAgentName
  updateHeaderTitleText(title || currentAgentName)
}

function upsertSession(nextSession) {
  const idx = sessionsCache.findIndex((session) => session.session_key === nextSession.session_key)
  if (idx >= 0) {
    sessionsCache[idx] = { ...sessionsCache[idx], ...nextSession }
    return
  }
  sessionsCache.unshift(nextSession)
}

function buildDraftTitle(messageText) {
  const cleaned = String(messageText || '').replace(/\s+/g, ' ').trim().replace(/[,.!?，。！？；：]+$/g, '')
  if (!cleaned) return 'New Chat'
  return cleaned.length > 24 ? `${cleaned.slice(0, 23).trim()}…` : cleaned
}

async function handleDeleteSessionClick(event) {
  event.stopPropagation()
  const sessionKey = event.currentTarget.getAttribute('data-delete-session')
  if (!sessionKey) return

  if (pendingDeleteSessionKey === sessionKey) {
    await deleteCurrentSession(sessionKey)
    return
  }

  pendingDeleteSessionKey = sessionKey
  renderSidebarContent(document.getElementById('sidebar-dynamic-content'))
  document.querySelector(`[data-delete-session="${cssEscape(sessionKey)}"]`)?.focus()
}

async function deleteCurrentSession(sessionKey) {
  try {
    await deleteSession(sessionKey)
    sessionsCache = sessionsCache.filter((session) => session.session_key !== sessionKey)
    if (sessionKey === currentSessionKey) {
      const nextSession = sessionsCache[0]
      currentSessionKey = nextSession?.session_key || null
      setSessionKey(currentSessionKey)
      await activateSession(currentSessionKey)
    }
    syncHeaderTitle()
  } catch (error) {
    console.error('[ChatPage] Failed to delete session:', error)
  } finally {
    pendingDeleteSessionKey = null
    renderSidebarContent(document.getElementById('sidebar-dynamic-content'))
  }
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value)
  return String(value || '').replace(/["\\]/g, '\\$&')
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}
