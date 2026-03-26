/**
 * chat.js - Chat Page Module
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { initSession, startNewSession, clearSession, getSessionKey, setSessionKey } from '../session-manager.js'
import { initChat, abortCurrentStream } from '../chat-ui.js'
import { listSessions } from '../api-client.js'
import { t } from '../i18n.js'

let chatElement = null
let mounted = false
let currentSessionKey = null

/**
 * Mount chat page into container
 * @param {HTMLElement} container - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(container, { params, route } = {}) {
  console.log('[ChatPage] Mounting...')

  try {
    // Render chat page HTML
    container.innerHTML = `
      <div class="chat-wrapper">
        <deep-chat
          id="chat"
          style="width: 100%; height: 100%; display: flex; flex-direction: column;"
          chatStyle='{"backgroundColor": "#ffffff"}'
          messageStyles='{
            "default": {
              "shared": {
                "bubble": {
                  "borderRadius": "12px",
                  "padding": "14px 18px",
                  "fontSize": "15px",
                  "lineHeight": "1.6",
                  "maxWidth": "720px"
                },
                "outerContainer": {
                  "marginTop": "16px",
                  "marginBottom": "16px"
                }
              },
              "user": {
                "bubble": {
                  "backgroundColor": "#4b6cb7",
                  "color": "#ffffff"
                }
              },
              "ai": {
                "bubble": {
                  "backgroundColor": "#f7f7f8",
                  "color": "#333333"
                },
                "outerContainer": {
                  "justifyContent": "center"
                }
              }
            }
          }'
          inputAreaStyle='{"padding": "20px 24px", "backgroundColor": "#fafafa", "borderTop": "1px solid #e5e5e5"}'
          submitButtonStyles='{"submit": {"container": {"default": {"backgroundColor": "#4b6cb7", "borderRadius": "50%", "width": "40px", "height": "40px"}, "hover": {"backgroundColor": "#3a5a9a"}}}}'
          textMarkdown="true">
        </deep-chat>
      </div>
      
      <!-- Confirm Dialog -->
      <div id="confirmDialog" class="confirm-dialog hidden">
        <div class="confirm-content">
          <h3 data-i18n="dialog.confirmTitle">Confirm</h3>
          <p id="confirmMessage" data-i18n="dialog.confirmMessage"></p>
          <div class="confirm-buttons">
            <button class="btn-cancel" data-i18n="dialog.cancel">Cancel</button>
            <button class="btn-confirm" data-i18n="dialog.confirm">Confirm</button>
          </div>
        </div>
      </div>
    `

    // Initialize session with error handling
    try {
      await initSession()
      currentSessionKey = getSessionKey()
    } catch (sessionError) {
      console.error('[ChatPage] Failed to initialize session:', sessionError)
      container.innerHTML = `<div class="error-message">Failed to initialize session. Please refresh the page.</div>`
      return
    }

    // Initialize chat UI
    chatElement = container.querySelector('#chat')
    if (chatElement) {
      try {
        await initChat(chatElement)
      } catch (chatError) {
        console.error('[ChatPage] Failed to initialize chat:', chatError)
      }
    }

    // Load and render sessions in sidebar
    await loadSessions()

    // Bind dialog events
    bindDialogEvents(container)

    mounted = true
    console.log('[ChatPage] Mounted')
  } catch (error) {
    console.error('[ChatPage] Mount failed:', error)
    container.innerHTML = `<div class="error-message">Failed to load chat page. Please refresh the page.</div>`
  }
}

/**
 * Unmount chat page - cleanup
 */
export async function unmount() {
  console.log('[ChatPage] Unmounting...')

  // Abort any running stream
  abortCurrentStream()

  // Clear sidebar dynamic content
  const sidebarContent = document.getElementById('sidebar-dynamic-content')
  if (sidebarContent) sidebarContent.innerHTML = ''

  chatElement = null
  mounted = false
  currentSessionKey = null

  console.log('[ChatPage] Unmounted')
}

/**
 * Load and render sessions in the sidebar
 */
async function loadSessions() {
  const sidebarContent = document.getElementById('sidebar-dynamic-content')
  if (!sidebarContent) {
    console.warn('[ChatPage] Sidebar dynamic content element not found')
    return
  }

  try {
    const sessions = await listSessions()
    renderSessionList(sidebarContent, sessions)
  } catch (error) {
    console.error('[ChatPage] Failed to load sessions:', error)
    // Show empty state on error
    sidebarContent.innerHTML = ''
  }
}

/**
 * Render session list in sidebar
 * @param {HTMLElement} container - Sidebar content container
 * @param {Array} sessions - List of sessions
 */
function renderSessionList(container, sessions) {
  if (!sessions || sessions.length === 0) {
    container.innerHTML = ''
    return
  }

  // Group sessions by date
  const groups = groupSessionsByDate(sessions)
  
  let html = ''
  for (const [groupTitle, groupSessions] of Object.entries(groups)) {
    html += `<div class="chat-history-group">`
    html += `<div class="group-title">${escapeHtml(groupTitle)}</div>`
    
    for (const session of groupSessions) {
      const isActive = session.session_key === currentSessionKey
      const activeClass = isActive ? ' active' : ''
      
      // Active session shows "Current Chat" via i18n, others show "Chat - HH:mm"
      if (isActive) {
        html += `<div class="history-item${activeClass}" data-session-key="${escapeHtml(session.session_key)}" data-i18n="app.currentChat">`
        html += escapeHtml(t('app.currentChat'))
        html += `</div>`
      } else {
        const title = getSessionTitle(session)
        html += `<div class="history-item${activeClass}" data-session-key="${escapeHtml(session.session_key)}">`
        html += escapeHtml(title)
        html += `</div>`
      }
    }
    
    html += `</div>`
  }
  
  container.innerHTML = html
  
  // Bind click events for session items
  container.querySelectorAll('.history-item').forEach(item => {
    item.addEventListener('click', handleSessionClick)
  })
}

/**
 * Group sessions by date (Today, Yesterday, Last 7 Days, Older)
 * @param {Array} sessions - List of sessions
 * @returns {Object} Grouped sessions
 */
function groupSessionsByDate(sessions) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000)
  const lastWeek = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)
  
  const groups = {
    [t('chat.session.today')]: [],
    [t('chat.session.yesterday')]: [],
    [t('chat.session.last7Days')]: [],
    [t('chat.session.older')]: []
  }
  
  const todayKey = t('chat.session.today')
  const yesterdayKey = t('chat.session.yesterday')
  const last7DaysKey = t('chat.session.last7Days')
  const olderKey = t('chat.session.older')
  
  for (const session of sessions) {
    const sessionDate = new Date(session.last_activity || session.created_at)
    const sessionDay = new Date(sessionDate.getFullYear(), sessionDate.getMonth(), sessionDate.getDate())
    
    if (sessionDay.getTime() >= today.getTime()) {
      groups[todayKey].push(session)
    } else if (sessionDay.getTime() >= yesterday.getTime()) {
      groups[yesterdayKey].push(session)
    } else if (sessionDay.getTime() >= lastWeek.getTime()) {
      groups[last7DaysKey].push(session)
    } else {
      groups[olderKey].push(session)
    }
  }
  
  // Remove empty groups
  const result = {}
  for (const [key, value] of Object.entries(groups)) {
    if (value.length > 0) {
      result[key] = value
    }
  }
  
  return result
}

/**
 * Get a display title for a session
 * @param {Object} session - Session object
 * @returns {string} Display title
 */
function getSessionTitle(session) {
  // Use message count as a simple indicator, or format the date
  const date = new Date(session.last_activity || session.created_at)
  const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const msgCount = session.message_count || 0
  
  if (msgCount > 0) {
    return `${t('chat.session.defaultTitle')} (${msgCount} messages) - ${timeStr}`
  }
  return `${t('chat.session.defaultTitle')} - ${timeStr}`
}

/**
 * Handle click on a session item
 * @param {Event} event - Click event
 */
async function handleSessionClick(event) {
  const sessionKey = event.currentTarget.dataset.sessionKey
  if (!sessionKey || sessionKey === currentSessionKey) return
  
  console.log('[ChatPage] Switching to session:', sessionKey)
  
  // Update active state in UI
  document.querySelectorAll('.history-item').forEach(item => {
    item.classList.remove('active')
  })
  event.currentTarget.classList.add('active')
  
  // Abort current stream
  abortCurrentStream()
  
  // Switch to selected session
  setSessionKey(sessionKey)
  currentSessionKey = sessionKey
  
  // Reload the page to reinitialize with the new session
  // This is the simplest approach that matches the original behavior
  window.location.reload()
}

/**
 * Escape HTML special characters
 * @param {string} text - Text to escape
 * @returns {string} Escaped text
 */
function escapeHtml(text) {
  if (!text) return ''
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

/**
 * Bind confirm dialog events
 */
function bindDialogEvents(container) {
  const confirmBtn = container.querySelector('.btn-confirm')
  const cancelBtn = container.querySelector('.btn-cancel')

  if (confirmBtn) {
    confirmBtn.addEventListener('click', () => handleConfirm(true))
  }
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => handleConfirm(false))
  }
}

// Confirm dialog state
let pendingActionId = null

/**
 * Show confirm dialog
 */
export function showConfirmDialog(actionId, message) {
  pendingActionId = actionId
  const dialog = document.getElementById('confirmDialog')
  const messageEl = document.getElementById('confirmMessage')

  if (dialog && messageEl) {
    messageEl.textContent = message
    dialog.classList.remove('hidden')
  }
}

/**
 * Hide confirm dialog
 */
export function hideConfirmDialog() {
  const dialog = document.getElementById('confirmDialog')
  if (dialog) {
    dialog.classList.add('hidden')
  }
  pendingActionId = null
}

/**
 * Handle confirm/cancel action
 */
async function handleConfirm(confirmed) {
  hideConfirmDialog()

  if (!pendingActionId) return

  try {
    const response = await fetch('/api/chat/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action_id: pendingActionId,
        confirmed: confirmed
      })
    })

    const result = await response.json()

    if (chatElement) {
      if (confirmed) {
        chatElement.addMessage({
          text: `✅ ${result.message || t('dialog.operationExecuted')}`,
          role: 'ai'
        })
      } else {
        chatElement.addMessage({
          text: `❌ ${t('dialog.operationCancelled')}`,
          role: 'ai'
        })
      }
    }
  } catch (error) {
    console.error('[ChatPage] Confirm error:', error)
  }
}

export default { mount, unmount, showConfirmDialog, hideConfirmDialog }
