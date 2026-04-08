/**
 * DeepChat UI Configuration and Interaction
 * Configure DeepChat component integration with AtlasClaw API
 */

import { getSessionKey, initSession, setSessionKey } from './session-manager.js'
import { getAgentInfo, getSessionHistory } from './api-client.js'
import { createStreamHandler } from './stream-handler.js'
import { buildApiUrl } from './config.js'
import { t, isLocaleLoaded, getCurrentLocale } from './i18n.js'

let chatElement = null
let currentStreamHandler = null
let assistantUpdatePending = false
let thinkingBlockId = null
let thinkingScrollPending = false
let userHasScrolledUp = false
let chatCallbacks = {}
let currentSessionKey = null
let currentAgentInfo = null

const SCROLL_THRESHOLD = 50

function getMessageContainer() {
  const dc = document.querySelector('deep-chat')
  if (!dc?.shadowRoot) return null
  return dc.shadowRoot.querySelector('.messages-container') ||
    dc.shadowRoot.querySelector('[class*="message-container"]') ||
    dc.shadowRoot.querySelector('#messages')
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
.runtime-log-label{min-width:120px;font-weight:600;color:#1f2937}
.runtime-log-time{min-width:44px;font-size:12px;font-variant-numeric:tabular-nums;color:#94a3b8}
.runtime-log-message{flex:1}
.response-content{word-break:break-word}
.response-content p{margin:0 0 12px 0;line-height:1.75}
.response-content ul,.response-content ol{margin:0 0 12px 20px;padding:0}
.response-content li{margin:4px 0;line-height:1.7}
.response-content h1,.response-content h2,.response-content h3{margin:0 0 10px 0;line-height:1.4}
.response-content code{padding:2px 6px;border-radius:6px;background:#eef2f7;font-size:.95em}
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

  const placeholder = isLocaleLoaded() ? t('chat.placeholder') : 'Enter your question...'
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
  if (typeof chatCallbacks.onConversationStateChange === 'function') {
    chatCallbacks.onConversationStateChange({ hasMessages, agentInfo: currentAgentInfo })
  }
}

function notifyUserTurnStarted(sessionKey, messageText) {
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
  answered: 'Answered',
  failed: 'Failed'
}

function buildThinkingHtml(thinkingContent, elapsedSeconds = null, isThinking = false) {
  if (!thinkingContent) return ''
  return `<div class="thinking-body"><div class="thinking-caption">Model thinking</div><div class="thinking-content-text">${escapeHtmlWithBreaks(thinkingContent)}</div></div>`
}

function formatRuntimeHeaderElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs = null, isThinking = false, panelOpen = null) {
  const entries = Array.isArray(runtimeEntries) ? runtimeEntries : []
  const hasThinkingText = !!(thinkingContent && thinkingContent.trim())
  let visibleEntries = entries
  if (!hasThinkingText) {
    const firstEntry = entries[0] || null
    const warningEntries = entries.filter((entry) => entry.state === 'warning')
    const terminalEntry = [...entries].reverse().find((entry) => entry.state === 'answered' || entry.state === 'failed')
    const compact = []
    if (firstEntry) compact.push(firstEntry)
    for (const entry of warningEntries) {
      if (!compact.includes(entry)) compact.push(entry)
    }
    if (terminalEntry && !compact.includes(terminalEntry)) compact.push(terminalEntry)
    visibleEntries = compact
  }
  if (!visibleEntries.length && !hasThinkingText) {
    return ''
  }
  const chipEntries = visibleEntries.filter((entry, index) => {
    if (index === 0) return true
    return entry.state !== visibleEntries[index - 1].state
  })
  const chips = chipEntries.map((entry, index) => {
    const label = RUNTIME_STATE_LABELS[entry.state] || entry.state
    const activeClass = index === chipEntries.length - 1 ? ' active' : ''
    return `<span class="runtime-chip ${entry.state || ''}${activeClass}">${escapeHtml(label)}</span>`
  }).join('')
  const logs = visibleEntries.map((entry) => {
    const label = RUNTIME_STATE_LABELS[entry.state] || entry.state || 'Runtime'
    const message = entry.message ? escapeHtml(entry.message) : ''
    const time = formatElapsed(entry.elapsedMs)
    return `<div class="runtime-log-item"><span class="runtime-log-time">${escapeHtml(time)}</span><span class="runtime-log-label">${escapeHtml(label)}</span><span class="runtime-log-message">${message}</span></div>`
  }).join('')
  const thinkingHtml = buildThinkingHtml(thinkingContent, elapsedMs, isThinking)
  const hasAnswered = visibleEntries.some((entry) => entry.state === 'answered')
  const hasFailed = visibleEntries.some((entry) => entry.state === 'failed')
  const titleIcon = hasAnswered
    ? '<span class="runtime-state-icon done">✓</span>'
    : !hasFailed
    ? '<span class="thinking-dots thinking-title-dots"><span>.</span><span>.</span><span>.</span></span>'
    : ''
  const titleElapsed = formatRuntimeHeaderElapsed(elapsedMs)
  const titleElapsedHtml = titleElapsed
    ? `<span class="runtime-title-elapsed">${escapeHtml(titleElapsed)}</span>`
    : ''
  const shouldOpen = typeof panelOpen === 'boolean' ? panelOpen : isThinking
  const detailsAttrs = shouldOpen ? ' open' : ''
  return `<details class="runtime-panel"${detailsAttrs}><summary><div class="runtime-summary-left"><span class="runtime-title">Runtime</span>${titleIcon}${titleElapsedHtml}</div><div class="runtime-summary-right"><span class="runtime-toggle">></span></div></summary><div class="runtime-body">${chips ? `<div class="runtime-statuses">${chips}</div>` : ''}${logs ? `<div class="runtime-log">${logs}</div>` : ''}${thinkingHtml}</div></details>`
}

function formatElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  if (elapsedMs < 1000) {
    return `${Math.max(1, Math.round(elapsedMs))}ms`
  }
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function buildMessageContent(runtimeEntries, thinkingContent, responseContent, elapsedMs = null, isThinking = false, panelOpen = null) {
  const runtimeHtml = buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs, isThinking, panelOpen)
  const responseHtml = responseContent
    ? `<div class="response-content">${renderAssistantMarkdown(responseContent)}</div>`
    : ''
  if (!runtimeHtml && !responseHtml) {
    return { html: '' }
  }
  return { html: `<div class="message-wrapper">${runtimeHtml}${responseHtml}</div>` }
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
  const escaped = escapeHtml(text || '').replace(/\r\n/g, '\n')
  if (!escaped.trim()) return ''

  const lines = escaped.split('\n')
  const htmlParts = []
  let paragraph = []
  let listType = null

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

  for (const rawLine of lines) {
    const line = (rawLine || '').trim()
    if (!line) {
      flushParagraph()
      flushList()
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

    flushList()
    paragraph.push(line)
  }

  flushParagraph()
  flushList()
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

  function currentElapsedMs() {
    if (runStartTime) {
      return Math.max(0, Date.now() - runStartTime)
    }
    return 0
  }

  function pushRuntimeEntry(state, message, metadata = {}) {
    const nextEntry = {
      state: state || 'reasoning',
      message: message || '',
      metadata,
      elapsedMs: currentElapsedMs()
    }
    const lastEntry = runtimeEntries[runtimeEntries.length - 1]
    if (lastEntry && lastEntry.state === nextEntry.state && lastEntry.message === nextEntry.message) {
      runtimeEntries = [...runtimeEntries.slice(0, -1), nextEntry]
      return
    }
    runtimeEntries = [...runtimeEntries, nextEntry]
  }

  function autoPanelShouldOpen() {
    return !thinkingFinalized
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
      const panelShouldOpen = currentPanelShouldOpen()
      const content = buildMessageContent(
        runtimeEntries,
        thinkingContent,
        aiMessageContent,
        currentElapsedMs(),
        !thinkingFinalized,
        panelShouldOpen
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
      const hasTerminalState = runtimeEntries.some((entry) => entry.state === 'answered' || entry.state === 'failed')
      if (hasTerminalState && thinkingFinalized) {
        stopRunTimer()
        return
      }
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
    startRunTimer()
    currentStreamHandler = createStreamHandler(runId, {
      onStart: () => {
        pushRuntimeEntry('reasoning', 'Model accepted the request and started reasoning.', { phase: 'start' })
        updateUI()
      },
      onDelta: (data) => {
        if (!data.content) return
        if (!thinkingFinalized) {
          thinkingFinalized = true
          stopThinkingTimer()
        }
        pushRuntimeEntry('answered', 'Final answer is streaming.', { phase: 'answering' })
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
        pushRuntimeEntry(data.state, data.message, data.metadata || {})
        updateUI()
      },
      onEnd: () => {
        const doFinalRender = async () => {
          assistantUpdatePending = false
          thinkingFinalized = true
          stopThinkingTimer()
          if (!runtimeEntries.some((entry) => entry.state === 'answered' || entry.state === 'failed')) {
            if (aiMessageContent.trim()) {
              pushRuntimeEntry('answered', 'Run completed.', { phase: 'completed' })
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
