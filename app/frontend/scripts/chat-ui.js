/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * DeepChat UI Configuration and Interaction
 * Configure DeepChat component integration with AtlasClaw API
 */

import { getSessionKey, initSession, setSessionKey, setSessionHasMessages } from './session-manager.js?v=19'
import { getAgentInfo, getSessionHistory } from './api-client.js?v=19'
import { createStreamHandler } from './stream-handler.js?v=19'
import { buildApiUrl } from './config.js?v=19'
import { translateIfExists, getCurrentLocale } from './i18n.js'

let chatElement = null
let currentStreamHandler = null
let assistantUpdatePending = false
let thinkingBlockId = null
let thinkingScrollPending = false
let userHasScrolledUp = false
let chatCallbacks = {}
let currentSessionKey = null
let currentAgentInfo = null
let isComposing = false // Track IME composition state for macOS/Asian input
let blockNextEnterAfterComposition = false
let blockNextEnterStartedAt = 0

const IME_ENTER_GUARD_MS = 150

const SCROLL_THRESHOLD = 50

function clearImeEnterGuard() {
  blockNextEnterAfterComposition = false
  blockNextEnterStartedAt = 0
}

function armImeEnterGuard() {
  blockNextEnterAfterComposition = true
  blockNextEnterStartedAt = Date.now()
}

function hasActiveImeEnterGuard() {
  if (!blockNextEnterAfterComposition) return false
  if ((Date.now() - blockNextEnterStartedAt) > IME_ENTER_GUARD_MS) {
    clearImeEnterGuard()
    return false
  }
  return true
}

function shouldBlockImeEnter(event) {
  if (event?.key !== 'Enter') return false
  const activelyComposing = isComposing ||
    event.isComposing === true ||
    event.keyCode === 229 ||
    event.which === 229

  if (!activelyComposing && event.shiftKey) {
    clearImeEnterGuard()
    return false
  }

  return activelyComposing || hasActiveImeEnterGuard()
}

function getMessageContainer() {
  const dc = document.querySelector('deep-chat')
  if (!dc?.shadowRoot) return null
  return dc.shadowRoot.querySelector('.messages-container') ||
    dc.shadowRoot.querySelector('[class*="message-container"]') ||
    dc.shadowRoot.querySelector('#messages')
}

/**
 * Set up IME composition event listeners for macOS/Asian input handling.
 * This prevents Enter from submitting while composing and for the first
 * commit Enter right after composition ends on macOS browsers.
 */
function setupCompositionListeners() {
  const dc = document.querySelector('deep-chat')
  if (!dc?.shadowRoot) {
    // Retry after a delay if shadow root not ready
    setTimeout(setupCompositionListeners, 500)
    return
  }
  
  // Find the input element (textarea, input, or contenteditable)
  const inputElement = dc.shadowRoot.querySelector('textarea') ||
                      dc.shadowRoot.querySelector('input[type="text"]') ||
                      dc.shadowRoot.querySelector('[contenteditable="true"]')
  
  if (!inputElement) {
    console.warn('[ChatUI] No input element found for composition listeners, retrying...')
    setTimeout(setupCompositionListeners, 500)
    return
  }
  
  // Check if already attached
  if (inputElement._compositionListenersAttached) {
    return
  }
  
  // Track composition state
  inputElement.addEventListener('compositionstart', () => {
    isComposing = true
    clearImeEnterGuard()
    console.debug('[ChatUI] IME composition started')
  })
  
  inputElement.addEventListener('compositionend', () => {
    isComposing = false
    armImeEnterGuard()
    console.debug('[ChatUI] IME composition ended')
  })
  
  // Intercept Enter both during composition and for the first macOS commit Enter
  inputElement.addEventListener('keydown', (e) => {
    if (!shouldBlockImeEnter(e)) {
      return
    }

    if (hasActiveImeEnterGuard() && !isComposing && e.isComposing !== true) {
      clearImeEnterGuard()
    }

    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      e.stopImmediatePropagation()
      console.debug('[ChatUI] Enter key blocked during IME composition')
    }
  }, true) // Use capture phase to intercept before Deep Chat
  
  inputElement._compositionListenersAttached = true
  console.log('[ChatUI] IME composition listeners attached to:', inputElement.tagName)
}

function getLatestRuntimePanel(container) {
  if (!container) return null
  const panels = container.querySelectorAll('details.runtime-panel')
  if (!panels.length) return null
  return panels[panels.length - 1]
}

function setupScrollListener() {
  const container = getMessageContainer()
  if (!container || container._scrollListenerAttached) return

  container.addEventListener('scroll', () => {
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < SCROLL_THRESHOLD
    userHasScrolledUp = !isNearBottom
  })
  container._scrollListenerAttached = true
}

function scrollToBottom() {
  if (userHasScrolledUp) return
  const container = getMessageContainer()
  if (!container) return
  container.scrollTop = container.scrollHeight
}

function syncRuntimePanelState(shouldOpen) {
  setTimeout(() => {
    const container = getMessageContainer()
    if (!container) return
    const details = getLatestRuntimePanel(container)
    if (!details) return
    if (shouldOpen) {
      details.setAttribute('open', '')
    } else {
      details.removeAttribute('open')
    }
  }, 0)
}

const THINKING_STYLES = `
@keyframes thinking-dot-minimal{0%,100%{opacity:.4;transform:translateY(0)}50%{opacity:.8;transform:translateY(-3px)}}
@keyframes thinking-pulse-minimal{0%,100%{opacity:1}50%{opacity:.5}}
@keyframes dot-blink{0%,20%{opacity:0}50%{opacity:1}80%,100%{opacity:0}}
.thinking-loading{display:inline-flex;align-items:center;gap:4px;padding:2px 0}
.thinking-loading .dot{width:6px;height:6px;border-radius:50%;background:#999;animation:thinking-dot-minimal 1.2s ease-in-out infinite}
.thinking-loading .dot:nth-child(2){animation-delay:.15s}
.thinking-loading .dot:nth-child(3){animation-delay:.3s}
.thinking-dots{display:inline-flex;margin-left:2px}
.thinking-dots span{animation:dot-blink 1.4s infinite}
.thinking-dots span:nth-child(1){animation-delay:0s}
.thinking-dots span:nth-child(2){animation-delay:0.2s}
.thinking-dots span:nth-child(3){animation-delay:0.4s}
.thinking-body{padding:8px 0 0 0;font-size:14px;line-height:1.7;color:#8b8b8b;max-height:none;overflow:visible}
.thinking-caption{font-size:12px;font-weight:600;letter-spacing:.02em;color:#64748b;margin-bottom:6px;text-transform:uppercase}
.thinking-content-text{white-space:pre-wrap;word-break:break-word}
details.runtime-panel{margin-bottom:16px;padding:14px 16px;border:1px solid rgba(148,163,184,.20);border-radius:18px;background:rgba(248,250,252,.92)}
details.runtime-panel>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;user-select:none;list-style:none}
details.runtime-panel>summary::-webkit-details-marker{display:none}
details.runtime-panel>summary::marker{display:none}
.runtime-summary-left{display:flex;align-items:center;gap:8px}
.runtime-summary-right{display:flex;align-items:center;gap:10px}
.runtime-state-icon{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;font-size:14px;color:#7c889d}
.runtime-state-icon.live{animation:thinking-pulse-minimal 1.5s ease-in-out infinite}
.runtime-state-icon.done{color:#16a34a}
.runtime-state-icon .thinking-dots{margin-left:0}
.runtime-title{font-size:15px;font-weight:500;letter-spacing:0;color:#7c889d}
.runtime-title-elapsed{font-size:13px;font-weight:500;color:#94a3b8;font-variant-numeric:tabular-nums}
.runtime-toggle{font-size:12px;transition:transform .15s ease;color:#94a3b8}
details.runtime-panel[open] .runtime-toggle{transform:rotate(90deg)}
.runtime-body{display:flex;flex-direction:column;gap:10px;padding-top:12px}
.runtime-statuses{display:flex;flex-wrap:wrap;gap:8px}
.runtime-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;font-size:13px;font-weight:500;background:#e2e8f0;color:#334155}
.runtime-chip.active{box-shadow:0 0 0 1px rgba(59,130,246,.16) inset}
.runtime-chip.reasoning{background:#f3f6ff;color:#5d6ea8}
.runtime-chip.retrying{background:#fff7ed;color:#c2410c}
.runtime-chip.waiting_for_tool{background:#ecfeff;color:#155e75}
.runtime-chip.tool_running{background:#eff6ff;color:#1d4ed8}
.runtime-chip.controlled_path{background:#f5f3ff;color:#6d28d9}
.runtime-chip.answered{background:#dcfce7;color:#15803d}
.runtime-chip.failed{background:#fef2f2;color:#b91c1c}
.runtime-log{display:flex;flex-direction:column;gap:8px}
.runtime-log-item{display:flex;gap:10px;align-items:flex-start;font-size:14px;line-height:1.5;color:#475569}
.runtime-log-item.active .runtime-log-message{color:#334155}
.runtime-log-label{min-width:120px;font-weight:600;color:#1f2937}
.runtime-log-live-dot{display:inline-block;width:7px;height:7px;margin-right:8px;border-radius:50%;background:#60a5fa;animation:thinking-pulse-minimal 1.5s ease-in-out infinite;vertical-align:middle}
.runtime-log-time{min-width:44px;font-size:12px;font-variant-numeric:tabular-nums;color:#94a3b8}
.runtime-log-message{flex:1}
.response-content{word-break:break-word}
.response-content p{margin:0 0 12px 0;line-height:1.75}
.response-content ul,.response-content ol{margin:0 0 12px 20px;padding:0}
.response-content li{margin:4px 0;line-height:1.7}
.response-content h1,.response-content h2,.response-content h3{margin:0 0 10px 0;line-height:1.4}
.response-content pre{margin:0 0 12px 0;padding:18px 20px;overflow-x:auto;border-radius:16px;background:#1e293b;color:#e2e8f0}
.response-content code{padding:2px 6px;border-radius:6px;background:#eef2f7;font-size:.95em}
.response-content pre code{display:block;padding:0;border-radius:0;background:transparent;color:inherit;font-size:13px;line-height:1.7;white-space:pre;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace}
.response-content a{color:#2563eb;text-decoration:none}
.response-content a:hover{text-decoration:underline}
.message-wrapper{display:flex;flex-direction:column;gap:12px}
`

export async function initChat(element, callbacks = {}) {
  chatElement = element
  chatCallbacks = callbacks || {}

  try {
    currentSessionKey = await initSession()
  } catch (sessionError) {
    console.error('[ChatUI] Failed to initialize session:', sessionError)
  }

  currentAgentInfo = await loadAgentInfo()
  configureHandler(element)
  configureI18nAttributes(element)
  
  // Set up IME composition handling for macOS/Asian input
  setupCompositionListeners()
  
  await activateSession(getSessionKey())

  console.log('[ChatUI] Initialized')
}

export async function activateSession(sessionKey) {
  if (!chatElement) return false
  currentSessionKey = sessionKey || getSessionKey()
  if (currentSessionKey) {
    setSessionKey(currentSessionKey)
  }
  const hasHistory = await restoreSessionHistory(chatElement, currentSessionKey)
  setSessionHasMessages(hasHistory)
  notifyConversationState(hasHistory)
  return hasHistory
}

export async function refreshActiveSessionHistory() {
  return activateSession(currentSessionKey || getSessionKey())
}

export function getCurrentAgentInfo() {
  return currentAgentInfo
}

async function loadAgentInfo() {
  try {
    const agentInfo = await getAgentInfo()
    console.log('[ChatUI] Agent info loaded:', agentInfo)
    return agentInfo
  } catch (error) {
    console.error('[ChatUI] Failed to load agent info:', error)
    return null
  }
}

async function restoreSessionHistory(element, sessionKey) {
  if (!sessionKey) {
    applyHistoryToElement(element, [])
    return false
  }

  try {
    const payload = await getSessionHistory(sessionKey)
    const history = (payload.messages || [])
      .map((message) => mapTranscriptMessageToHistory(message))
      .filter(Boolean)

    applyHistoryToElement(element, history)
    return history.length > 0
  } catch (error) {
    console.warn('[ChatUI] Failed to restore session history:', error)
    applyHistoryToElement(element, [])
    return false
  }
}

function applyHistoryToElement(element, history) {
  if (!element) return
  clearRenderedMessages(element)
  if (typeof element.loadHistory === 'function') {
    element.loadHistory(history)
  } else {
    element.history = history
    if (typeof element.refreshMessages === 'function') {
      element.refreshMessages()
    }
  }
  element.introMessage = null
}

function clearRenderedMessages(element) {
  const root = element?.shadowRoot
  if (!root) return
  const containers = [
    root.querySelector('.messages-container'),
    root.querySelector('#messages'),
    root.querySelector('[class*="message-container"]')
  ].filter(Boolean)
  for (const container of containers) {
    container.innerHTML = ''
  }
}

function mapTranscriptMessageToHistory(message) {
  if (!message?.content) return null
  if (message.role === 'user') {
    return { role: 'user', text: message.content }
  }
  if (message.role === 'assistant') {
    return { role: 'ai', text: message.content }
  }
  return null
}

function configureHandler(element) {
  const handlerFn = async (body, signals) => {
    const messageText = extractMessageFromBody(body)
    if (!messageText) {
      signals.onClose()
      return
    }

    let sessionKey = getSessionKey()
    if (!sessionKey) {
      sessionKey = await initSession()
      currentSessionKey = sessionKey
    }

    notifyUserTurnStarted(sessionKey, messageText)

    let runId
    try {
      const response = await fetch(buildApiUrl('/api/agent/run'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_key: sessionKey || '',
          message: messageText || '',
          timeout_seconds: 600,
          context: {
            ui_locale: getCurrentLocale(),
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || ''
          }
        })
      })

      if (!response.ok) {
        signals.onResponse({ html: `<p style="color: #d32f2f;">Error: ${response.status} ${response.statusText}</p>` })
        signals.onClose()
        return
      }

      const data = await response.json()
      runId = data.run_id || data.runId || data.id
      if (!runId) {
        signals.onResponse({ html: `<p style="color: #d32f2f;">${escapeHtml(data.detail || 'Error: No run_id')}</p>` })
        signals.onClose()
        return
      }
    } catch (err) {
      console.error('[ChatUI] API call failed:', err)
      signals.onResponse({ html: `<p style="color: #d32f2f;">Error: ${escapeHtml(err.message)}</p>` })
      signals.onClose()
      return
    }

    const initialPayload = buildMessageContent(
      [{ state: 'reasoning', message: 'Starting response analysis.' }],
      '',
      '',
      0,
      true
    )
    if (initialPayload.html) {
      signals.onResponse({
        html: initialPayload.html,
        overwrite: true
      })
    }

    await handleStreamWithSignals(runId, signals, { sessionKey, messageText })
  }

  element.handler = handlerFn
  element.connect = { handler: handlerFn, stream: true }
}

function extractMessageFromBody(body) {
  if (!body) return ''
  if (body.messages && Array.isArray(body.messages) && body.messages.length > 0) {
    const lastMsg = body.messages[body.messages.length - 1]
    if (typeof lastMsg === 'string') return lastMsg
    return lastMsg.text || lastMsg.content || ''
  }
  if (body.text) return body.text
  if (body.message) return body.message
  return ''
}

function configureI18nAttributes(element) {
  element.chatStyle = { backgroundColor: 'transparent' }
  element.messageStyles = {
    default: {
      shared: {
        bubble: {
          padding: '16px 20px',
          fontSize: '16px',
          lineHeight: '1.75',
          borderRadius: '24px'
        },
        outerContainer: {
          marginTop: '12px',
          marginBottom: '12px'
        }
      },
      user: {
        bubble: {
          backgroundColor: '#edf2fb',
          color: '#1f2937',
          boxShadow: 'none'
        },
        outerContainer: {
          justifyContent: 'flex-end',
          paddingLeft: '30%',
          paddingRight: '8%'
        }
      },
      ai: {
        bubble: {
          backgroundColor: 'transparent',
          color: '#1f2937',
          padding: '0',
          borderRadius: '0',
          boxShadow: 'none',
          maxWidth: '920px'
        },
        outerContainer: {
          justifyContent: 'center',
          paddingLeft: '8%',
          paddingRight: '18%'
        }
      }
    }
  }
  element.auxiliaryStyle = `
    :host { border: none !important; background: transparent !important; box-shadow: none !important; }
    #container, #chat-view, #messages, .messages, .messages-container { border: none !important; background: transparent !important; box-shadow: none !important; }
    ${THINKING_STYLES}
  `

  const placeholder = translateIfExists('chat.placeholder') || 'Enter your question...'
  element.textInput = {
    placeholder: {
      text: placeholder,
      style: { color: '#8f99ab' }
    },
    styles: {
      container: {
        borderRadius: '32px',
        border: 'none',
        padding: '18px 22px',
        backgroundColor: '#ffffff',
        boxShadow: '0 22px 60px rgba(15, 23, 42, 0.08)'
      },
      text: {
        fontSize: '18px',
        color: '#1f2937'
      }
    }
  }
}

function notifyConversationState(hasMessages) {
  setSessionHasMessages(hasMessages)
  if (typeof chatCallbacks.onConversationStateChange === 'function') {
    chatCallbacks.onConversationStateChange({ hasMessages, agentInfo: currentAgentInfo })
  }
}

function notifyUserTurnStarted(sessionKey, messageText) {
  setSessionHasMessages(true)
  if (typeof chatCallbacks.onUserTurnStarted === 'function') {
    chatCallbacks.onUserTurnStarted({ sessionKey, messageText })
  }
}

async function notifyRunCompleted(sessionKey) {
  const hasHistory = true
  if (typeof chatCallbacks.onRunCompleted === 'function') {
    await chatCallbacks.onRunCompleted({ sessionKey, hasHistory })
  }
  notifyConversationState(hasHistory)
}

const RUNTIME_STATE_LABELS = {
  reasoning: 'Thinking',
  retrying: 'Retrying',
  waiting_for_tool: 'Waiting for tool',
  tool_running: 'Running tool',
  controlled_path: 'Controlled path',
  failed: 'Failed'
}

const EARLY_RUNTIME_PHASES = [
  {
    delayMs: 120,
    state: 'reasoning',
    message: 'Preparing model request context.',
    metadata: { phase: 'model_message_history_build' }
  },
  {
    delayMs: 260,
    state: 'reasoning',
    message: 'Starting model session.',
    metadata: { phase: 'agent_iter_open' }
  },
  {
    delayMs: 420,
    state: 'reasoning',
    message: 'Waiting for model tool decision.',
    metadata: { phase: 'agent_first_node_wait' }
  }
]

function buildThinkingHtml(thinkingContent, elapsedSeconds = null, isThinking = false) {
  if (!thinkingContent) return ''
  return `<div class="thinking-body"><div class="thinking-caption">Model thinking</div><div class="thinking-content-text">${escapeHtmlWithBreaks(thinkingContent)}</div></div>`
}

function formatRuntimeHeaderElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs = null, isThinking = false, panelOpen = null, isComplete = false) {
  const entries = Array.isArray(runtimeEntries) ? runtimeEntries : []
  const hasThinkingText = !!(thinkingContent && thinkingContent.trim())
  const visibleEntries = entries
  if (!visibleEntries.length && !hasThinkingText) {
    return ''
  }
  const hasAnswered = !!isComplete
  const hasFailed = visibleEntries.some((entry) => entry.state === 'failed')
  const displayEntries = visibleEntries.filter((entry) => (
    entry.state !== 'answered' &&
    entry.state !== 'answering' &&
    String(entry.message || '').trim() !== 'Reasoning phase completed.'
  ))
  const chipEntries = displayEntries.filter((entry, index) => {
    if (index === 0) return true
    return entry.state !== displayEntries[index - 1].state
  })
  const chips = chipEntries.map((entry, index) => {
    const label = RUNTIME_STATE_LABELS[entry.state] || entry.state
    const activeClass = index === chipEntries.length - 1 ? ' active' : ''
    return `<span class="runtime-chip ${entry.state || ''}${activeClass}">${escapeHtml(label)}</span>`
  }).join('')
  const logs = displayEntries.map((entry, index) => {
    const label = RUNTIME_STATE_LABELS[entry.state] || entry.state || 'Runtime'
    const message = entry.message ? escapeHtml(entry.message) : ''
    const isActiveEntry = !hasAnswered && !hasFailed && index === displayEntries.length - 1
    const effectiveElapsedMs = (
      isActiveEntry && typeof elapsedMs === 'number' && !Number.isNaN(elapsedMs)
    )
      ? Math.max(entry.elapsedMs || 0, elapsedMs)
      : entry.elapsedMs
    const time = formatElapsed(effectiveElapsedMs)
    const activeClass = isActiveEntry ? ' active' : ''
    const liveBadge = isActiveEntry ? '<span class="runtime-log-live-dot"></span>' : ''
    return `<div class="runtime-log-item${activeClass}"><span class="runtime-log-time">${escapeHtml(time)}</span><span class="runtime-log-label">${liveBadge}${escapeHtml(label)}</span><span class="runtime-log-message">${message}</span></div>`
  }).join('')
  const thinkingHtml = buildThinkingHtml(thinkingContent, elapsedMs, isThinking)
  const titleIcon = hasAnswered
    ? '<span class="runtime-state-icon done">✓</span>'
    : !hasFailed
    ? '<span class="thinking-dots thinking-title-dots"><span>.</span><span>.</span><span>.</span></span>'
    : ''
  const titleElapsed = formatRuntimeHeaderElapsed(elapsedMs)
  const titleElapsedHtml = titleElapsed
    ? `<span class="runtime-title-elapsed">${escapeHtml(titleElapsed)}</span>`
    : ''
  const shouldOpen = typeof panelOpen === 'boolean' ? panelOpen : false
  const detailsAttrs = shouldOpen ? ' open' : ''
  return `<details class="runtime-panel"${detailsAttrs}><summary><div class="runtime-summary-left"><span class="runtime-title">Thinking</span>${titleIcon}${titleElapsedHtml}</div><div class="runtime-summary-right"><span class="runtime-toggle">></span></div></summary><div class="runtime-body">${chips ? `<div class="runtime-statuses">${chips}</div>` : ''}${logs ? `<div class="runtime-log">${logs}</div>` : ''}${thinkingHtml}</div></details>`
}

function formatElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  if (elapsedMs < 1000) {
    return `${Math.max(1, Math.round(elapsedMs))}ms`
  }
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function buildMessageContent(runtimeEntries, thinkingContent, responseContent, elapsedMs = null, isThinking = false, panelOpen = null, isComplete = false, renderRevision = 0) {
  const runtimeHtml = buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs, isThinking, panelOpen, isComplete)
  const responseHtml = responseContent
    ? `<div class="response-content">${renderAssistantMarkdown(responseContent)}</div>`
    : ''
  if (!runtimeHtml && !responseHtml) {
    return { html: '' }
  }
  return {
    html: `<div class="message-wrapper" data-render-revision="${renderRevision}">${runtimeHtml}${responseHtml}</div>`
  }
}

function escapeHtml(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function escapeHtmlWithBreaks(text) {
  return escapeHtml(text).replace(/\n/g, '<br>')
}

function sanitizeLinkUrl(url) {
  const normalized = (url || '').trim()
  if (!normalized) return '#'
  if (/^https?:\/\//i.test(normalized)) return normalized
  return '#'
}

function stripWrapperHeading(text) {
  let normalized = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/^[\uFEFF\u200B\u200C\u200D\s]+/, '')
  if (!normalized.trim()) return ''
  const wrapperPattern = /^(answer|result|response|回答|结果|回复)\s*[:：-]?$/i

  while (normalized.trim()) {
    const lines = normalized.split('\n')
    const firstLine = (lines[0] || '').trim()
    const secondLine = (lines[1] || '').trim()

    if (wrapperPattern.test(firstLine) && /^=+\s*$/.test(secondLine)) {
      normalized = lines.slice(2).join('\n').replace(/^[\uFEFF\u200B\u200C\u200D\s]+/, '')
      continue
    }
    if (/^#{1,3}\s+/.test(firstLine)) {
      const headingText = firstLine.replace(/^#{1,3}\s+/, '').trim()
      if (wrapperPattern.test(headingText)) {
        normalized = lines.slice(1).join('\n').replace(/^[\uFEFF\u200B\u200C\u200D\s]+/, '')
        continue
      }
    }
    if (wrapperPattern.test(firstLine)) {
      normalized = lines.slice(1).join('\n').replace(/^[\uFEFF\u200B\u200C\u200D\s]+/, '')
      continue
    }
    break
  }
  return normalized
}

function renderInlineMarkdown(line) {
  let html = line || ''
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label, url) => {
    const safeUrl = sanitizeLinkUrl(url)
    return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  return html
}

function renderAssistantMarkdown(text) {
  const cleaned = stripWrapperHeading(text || '')
  const escaped = escapeHtml(cleaned).replace(/\r\n/g, '\n')
  if (!escaped.trim()) return ''

  const lines = escaped.split('\n')
  const htmlParts = []
  let paragraph = []
  let listType = null
  let insideFencedCodeBlock = false
  let fencedCodeLanguage = ''
  let fencedCodeLines = []

  const flushParagraph = () => {
    if (!paragraph.length) return
    htmlParts.push(`<p>${renderInlineMarkdown(paragraph.join('<br>'))}</p>`)
    paragraph = []
  }

  const flushList = () => {
    if (!listType) return
    htmlParts.push(listType === 'ul' ? '</ul>' : '</ol>')
    listType = null
  }

  const flushCodeBlock = () => {
    if (!insideFencedCodeBlock && !fencedCodeLines.length && !fencedCodeLanguage) return
    const languageClass = fencedCodeLanguage
      ? ` class="language-${fencedCodeLanguage}"`
      : ''
    htmlParts.push(`<pre><code${languageClass}>${fencedCodeLines.join('\n')}</code></pre>`)
    insideFencedCodeBlock = false
    fencedCodeLanguage = ''
    fencedCodeLines = []
  }

  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index]
    const line = (rawLine || '').trim()

    if (insideFencedCodeBlock) {
      if (/^```/.test(line)) {
        flushCodeBlock()
      } else {
        fencedCodeLines.push(rawLine || '')
      }
      continue
    }

    const fencedCodeMatch = /^```([a-zA-Z0-9_-]+)?\s*$/.exec(line)
    if (fencedCodeMatch) {
      flushParagraph()
      flushList()
      insideFencedCodeBlock = true
      fencedCodeLanguage = (fencedCodeMatch[1] || '').toLowerCase()
      fencedCodeLines = []
      continue
    }

    if (!line) {
      flushParagraph()
      flushList()
      continue
    }

    const nextLine = (lines[index + 1] || '').trim()
    if (
      line &&
      !/^(#{1,3})\s+/.test(line) &&
      !/^[-*]\s+/.test(line) &&
      !/^\d+\.\s+/.test(line) &&
      /^=+$/.test(nextLine)
    ) {
      flushParagraph()
      flushList()
      htmlParts.push(`<h1>${renderInlineMarkdown(line)}</h1>`)
      index += 1
      continue
    }
    if (
      line &&
      !/^(#{1,3})\s+/.test(line) &&
      !/^[-*]\s+/.test(line) &&
      !/^\d+\.\s+/.test(line) &&
      /^-+$/.test(nextLine)
    ) {
      flushParagraph()
      flushList()
      htmlParts.push(`<h2>${renderInlineMarkdown(line)}</h2>`)
      index += 1
      continue
    }

    const headingMatch = /^(#{1,3})\s+(.+)$/.exec(line)
    if (headingMatch) {
      flushParagraph()
      flushList()
      const level = headingMatch[1].length
      htmlParts.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`)
      continue
    }

    const ulMatch = /^[-*]\s+(.+)$/.exec(line)
    if (ulMatch) {
      flushParagraph()
      if (listType !== 'ul') {
        flushList()
        htmlParts.push('<ul>')
        listType = 'ul'
      }
      htmlParts.push(`<li>${renderInlineMarkdown(ulMatch[1])}</li>`)
      continue
    }

    const olMatch = /^\d+\.\s+(.+)$/.exec(line)
    if (olMatch) {
      flushParagraph()
      if (listType !== 'ol') {
        flushList()
        htmlParts.push('<ol>')
        listType = 'ol'
      }
      htmlParts.push(`<li>${renderInlineMarkdown(olMatch[1])}</li>`)
      continue
    }

    const pipeFieldMatch = /^\|\s*(.+?)\s*[:：]\s*(.+)$/.exec(line)
    if (pipeFieldMatch) {
      flushParagraph()
      if (listType !== 'ul') {
        flushList()
        htmlParts.push('<ul>')
        listType = 'ul'
      }
      htmlParts.push(
        `<li>${renderInlineMarkdown(`${pipeFieldMatch[1]}: ${pipeFieldMatch[2]}`)}</li>`
      )
      continue
    }

    if (/^[=+\-|]{8,}\s*$/.test(line) || line === '|') {
      flushParagraph()
      flushList()
      continue
    }

    flushList()
    paragraph.push(line)
  }

  flushParagraph()
  flushList()
  flushCodeBlock()
  return htmlParts.join('')
}

async function handleStreamWithSignals(runId, signals, context) {
  let aiMessageContent = ''
  let hasRenderedDelta = false
  let thinkingContent = ''
  let runStartTime = Date.now()
  let runTimerInterval = null
  let thinkingStartTime = null
  let thinkingElapsedSeconds = 0
  let thinkingTimerInterval = null
  let thinkingFinalized = false
  let hasThinkingContent = false
  let runtimePanelUserOverride = false
  let runtimePanelOpen = null
  let runtimeEntries = [{ state: 'reasoning', message: 'Starting response analysis.' }]
  let finalAnswerReady = false
  let serverRuntimeSeen = false
  let localRuntimeSeedTimers = []
  let renderRevision = 0

  function currentElapsedMs() {
    if (runStartTime) {
      return Math.max(0, Date.now() - runStartTime)
    }
    return 0
  }

  function pushRuntimeEntry(state, message, metadata = {}, options = {}) {
    const forceAppend = !!options.forceAppend
    const normalizedState = String(state || 'reasoning').trim().toLowerCase()
    if (normalizedState === 'answered' || normalizedState === 'answering') {
      if (/final answer ready/i.test(String(message || ''))) {
        finalAnswerReady = true
      }
      return
    }
    const serverElapsed = typeof metadata?.elapsed === 'number' && !Number.isNaN(metadata.elapsed)
      ? Math.max(0, Math.round(metadata.elapsed * 1000))
      : null
    const nowElapsedMs = currentElapsedMs()
    const nextEntry = {
      state: normalizedState || 'reasoning',
      message: message || '',
      metadata,
      elapsedMs: serverElapsed ?? nowElapsedMs,
      reportedElapsedMs: serverElapsed,
      createdAtMs: nowElapsedMs
    }
    const lastEntry = runtimeEntries[runtimeEntries.length - 1]
    if (!forceAppend && lastEntry && lastEntry.state === nextEntry.state && lastEntry.message === nextEntry.message) {
      runtimeEntries = [...runtimeEntries.slice(0, -1), {
        ...nextEntry,
        createdAtMs: lastEntry.createdAtMs ?? nextEntry.createdAtMs,
        reportedElapsedMs: nextEntry.reportedElapsedMs ?? lastEntry.reportedElapsedMs ?? null
      }]
      return
    }
    runtimeEntries = [...runtimeEntries, nextEntry]
  }

  function clearLocalRuntimeSeedTimers() {
    for (const timerId of localRuntimeSeedTimers) {
      clearTimeout(timerId)
    }
    localRuntimeSeedTimers = []
  }

  function scheduleLocalEarlyRuntimePhases() {
    clearLocalRuntimeSeedTimers()
    localRuntimeSeedTimers = EARLY_RUNTIME_PHASES.map((phase) => setTimeout(() => {
      if (serverRuntimeSeen || finalAnswerReady) return
      pushRuntimeEntry(
        phase.state,
        phase.message,
        {
          ...(phase.metadata || {}),
          elapsed: currentElapsedMs() / 1000,
          synthetic: true
        }
      )
      updateUI()
    }, phase.delayMs))
  }

  function refreshActiveRuntimeEntry() {
    const lastEntry = runtimeEntries[runtimeEntries.length - 1]
    if (!lastEntry) return
    if (lastEntry.state === 'failed') return
    const nowElapsedMs = currentElapsedMs()
    const phaseStartedMs = typeof lastEntry.createdAtMs === 'number'
      ? lastEntry.createdAtMs
      : nowElapsedMs
    const reportedElapsedMs = typeof lastEntry.reportedElapsedMs === 'number'
      ? lastEntry.reportedElapsedMs
      : null
    const effectiveElapsedMs = Math.max(reportedElapsedMs ?? 0, nowElapsedMs)
    const metadata = { ...(lastEntry.metadata || {}), elapsed: effectiveElapsedMs / 1000 }
    const phase = String(lastEntry.metadata?.phase || '')
    if (
      phase === 'agent_first_node_wait' &&
      !lastEntry.metadata?.waitProgressShown &&
      nowElapsedMs - phaseStartedMs >= 4500
    ) {
      pushRuntimeEntry(
        lastEntry.state,
        'Still waiting for model tool decision.',
        {
          ...metadata,
          phase: 'agent_first_node_wait_progress',
          waitProgressShown: true
        },
        { forceAppend: true }
      )
      return
    }
    pushRuntimeEntry(lastEntry.state, lastEntry.message, metadata)
  }

  function autoPanelShouldOpen() {
    return false
  }

  function currentPanelShouldOpen() {
    if (runtimePanelUserOverride && typeof runtimePanelOpen === 'boolean') {
      return runtimePanelOpen
    }
    return autoPanelShouldOpen()
  }

  function bindRuntimePanelToggle() {
    setTimeout(() => {
      const container = getMessageContainer()
      if (!container) return
      const details = getLatestRuntimePanel(container)
      if (!details || details._runtimeToggleBound) return
      details._runtimeToggleBound = true
      details.addEventListener('toggle', () => {
        runtimePanelUserOverride = true
        runtimePanelOpen = !!details.open
      })
    }, 0)
  }

  function updateUI() {
    try {
      renderRevision += 1
      const panelShouldOpen = currentPanelShouldOpen()
      const content = buildMessageContent(
        runtimeEntries,
        thinkingContent,
        aiMessageContent,
        currentElapsedMs(),
        !thinkingFinalized,
        panelShouldOpen,
        finalAnswerReady,
        renderRevision
      )
      if (content.html) {
        signals.onResponse({ html: content.html, overwrite: true })
        syncRuntimePanelState(panelShouldOpen)
        bindRuntimePanelToggle()
      }
      setupScrollListener()
      scrollToBottom()
    } catch (e) {
      console.warn('[ChatUI] Failed to update UI:', e)
    }
  }

  function startThinkingTimer() {
    if (thinkingTimerInterval) return
    thinkingStartTime = Date.now()
    thinkingTimerInterval = setInterval(() => {
      thinkingElapsedSeconds = Math.round((Date.now() - thinkingStartTime) / 100) / 10
      if (!thinkingFinalized) {
        updateUI()
      }
    }, 100)
  }

  function stopThinkingTimer() {
    if (thinkingTimerInterval) {
      clearInterval(thinkingTimerInterval)
      thinkingTimerInterval = null
    }
    if (thinkingStartTime) {
      const clientElapsed = Math.round((Date.now() - thinkingStartTime) / 100) / 10
      if (thinkingElapsedSeconds <= 0.1) {
        thinkingElapsedSeconds = clientElapsed
      }
      updateUI()
    }
  }

  function startRunTimer() {
    if (runTimerInterval) return
    runTimerInterval = setInterval(() => {
      const hasTerminalState = finalAnswerReady || runtimeEntries.some((entry) => entry.state === 'failed')
      if (hasTerminalState && thinkingFinalized) {
        stopRunTimer()
        return
      }
      refreshActiveRuntimeEntry()
      updateUI()
    }, 100)
  }

  function stopRunTimer() {
    if (runTimerInterval) {
      clearInterval(runTimerInterval)
      runTimerInterval = null
    }
  }

  return new Promise((resolve) => {
    scheduleLocalEarlyRuntimePhases()
    startRunTimer()
    currentStreamHandler = createStreamHandler(runId, {
      onStart: () => {
        updateUI()
      },
      onDelta: (data) => {
        if (!data.content) return
        if (!thinkingFinalized) {
          thinkingFinalized = true
          stopThinkingTimer()
        }
        aiMessageContent += data.content
        hasRenderedDelta = true
        if (!assistantUpdatePending) {
          assistantUpdatePending = true
          setTimeout(() => {
            assistantUpdatePending = false
            updateUI()
          }, 100)
        }
      },
      onToolStart: (data) => {
        pushRuntimeEntry('tool_running', `Running tool: ${data?.tool_name || 'tool'}`, { phase: 'running_tool' })
        updateUI()
      },
      onToolEnd: (data) => {
        pushRuntimeEntry('waiting_for_tool', `Tool completed: ${data?.tool_name || 'tool'}`, { phase: 'tool_completed' })
        updateUI()
      },
      onThinkingStart: () => {
        hasThinkingContent = true
        thinkingFinalized = false
        startThinkingTimer()
        userHasScrolledUp = false
        pushRuntimeEntry('reasoning', 'Collecting model reasoning.', { phase: 'thinking' })
        updateUI()
      },
      onThinkingDelta: (data) => {
        const content = data?.content || ''
        if (!content) return
        if (!thinkingStartTime) {
          hasThinkingContent = true
          thinkingFinalized = false
          startThinkingTimer()
        }
        thinkingContent += content
        if (!thinkingScrollPending) {
          thinkingScrollPending = true
          setTimeout(() => {
            thinkingScrollPending = false
            updateUI()
          }, 80)
        }
      },
      onThinkingEnd: (data) => {
        thinkingFinalized = true
        if (data?.elapsed && data.elapsed > 0) {
          thinkingElapsedSeconds = data.elapsed
        }
        stopThinkingTimer()
        pushRuntimeEntry('reasoning', 'Reasoning phase completed.', { phase: 'completed' })
        updateUI()
      },
      onRuntime: (data) => {
        serverRuntimeSeen = true
        clearLocalRuntimeSeedTimers()
        pushRuntimeEntry(data.state, data.message, data.metadata || {})
        updateUI()
      },
      onHeartbeat: () => {
        refreshActiveRuntimeEntry()
        updateUI()
      },
      onEnd: () => {
        const doFinalRender = async () => {
          clearLocalRuntimeSeedTimers()
          assistantUpdatePending = false
          thinkingFinalized = true
          stopThinkingTimer()
          if (!runtimeEntries.some((entry) => entry.state === 'failed')) {
            if (aiMessageContent.trim()) {
              finalAnswerReady = true
            } else {
              pushRuntimeEntry('failed', 'Run ended without a usable answer.', { phase: 'completed' })
            }
          }
          updateUI()
          stopRunTimer()
          await notifyRunCompleted(context.sessionKey)
          signals.onClose()
          currentStreamHandler = null
          resolve()
        }
        setTimeout(() => {
          void doFinalRender()
        }, 200)
      },
      onError: async (error) => {
        clearLocalRuntimeSeedTimers()
        thinkingFinalized = true
        stopThinkingTimer()
        pushRuntimeEntry('failed', error?.message || 'Unknown error', { phase: 'error' })
        updateUI()
        stopRunTimer()
        await notifyRunCompleted(context.sessionKey)
        signals.onClose()
        currentStreamHandler = null
        resolve()
      }
    })

    currentStreamHandler.start()
  })
}

export function abortCurrentStream() {
  if (currentStreamHandler) {
    currentStreamHandler.abort()
    currentStreamHandler = null
  }
}

export function getChatElement() {
  return chatElement
}

export default {
  initChat,
  activateSession,
  refreshActiveSessionHistory,
  abortCurrentStream,
  getChatElement,
  getCurrentAgentInfo,
  configureI18nAttributes
}
