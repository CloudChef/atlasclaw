/**
 * DeepChat UI Configuration and Interaction
 * Configure DeepChat component integration with AtlasClaw API
 */

import { getSessionKey, initSession, setSessionKey } from './session-manager.js'
import { getAgentInfo, getSessionHistory } from './api-client.js'
import { createStreamHandler } from './stream-handler.js'
import { buildApiUrl } from './config.js'
import { t, isLocaleLoaded } from './i18n.js'

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

const THINKING_STYLES = `
<style>
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
.thinking-block{margin-bottom:8px}
.thinking-block.thinking{margin-bottom:8px}
.thinking-header{display:inline-flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer;user-select:none;color:#8b8b8b;font-size:14px;transition:color .2s ease}
.thinking-header:hover{color:#666}
.thinking-icon{font-size:14px;line-height:1;display:inline-flex;align-items:center}
.thinking-block.thinking .thinking-icon{animation:thinking-pulse-minimal 1.5s ease-in-out infinite}
.thinking-label{font-size:14px;font-weight:400}
.thinking-timer{font-size:14px;font-variant-numeric:tabular-nums}
.thinking-toggle{font-size:12px;transition:transform .15s ease;display:inline-flex}
.thinking-block.open .thinking-toggle{transform:rotate(90deg)}
.thinking-body{max-height:0;overflow:hidden;transition:max-height .15s ease,opacity .1s ease;opacity:0;padding-left:20px;font-size:14px;line-height:1.6;color:#8b8b8b}
.thinking-block.open .thinking-body{max-height:60vh;opacity:1;padding:8px 0 8px 20px;overflow-y:auto}
.thinking-block.thinking .thinking-body{max-height:50vh;opacity:1;padding:8px 0 8px 20px;overflow-y:auto}
.thinking-content-text{white-space:pre-wrap;word-break:break-word}
details.thinking-block{margin-bottom:8px}
details.thinking-block>summary{display:inline-flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer;user-select:none;color:#8b8b8b;font-size:14px;transition:color .2s ease;list-style:none}
details.thinking-block>summary::-webkit-details-marker{display:none}
details.thinking-block>summary::marker{display:none}
details.thinking-block>summary:hover{color:#666}
details.thinking-block .thinking-toggle{font-size:12px;transition:transform .15s ease;display:inline-flex}
details.thinking-block[open] .thinking-toggle{transform:rotate(90deg)}
details.thinking-block .thinking-body{padding:8px 0 8px 20px;font-size:14px;line-height:1.6;color:#8b8b8b;max-height:60vh;overflow-y:auto;opacity:1}
</style>
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
          timeout_seconds: 600
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

    signals.onResponse({
      html: `${THINKING_STYLES}<div class="thinking-loading"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>`
    })

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
  const hasHistory = await refreshActiveSessionHistory()
  if (typeof chatCallbacks.onRunCompleted === 'function') {
    await chatCallbacks.onRunCompleted({ sessionKey, hasHistory })
  }
  notifyConversationState(hasHistory)
}

function buildMessageText(thinkingContent, responseContent, elapsedSeconds = null, isThinking = false) {
  if (thinkingContent) {
    let html = ''
    if (isThinking) {
      html += `<div class="thinking-block thinking"><div class="thinking-header"><span class="thinking-icon">?</span><span class="thinking-label">Thinking<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></span><span class="thinking-toggle">?</span></div><div class="thinking-body"><div class="thinking-content-text">${escapeHtml(thinkingContent)}</div></div></div>`
      return { html }
    }

    html += `<details class="thinking-block"><summary><span class="thinking-icon">?</span><span class="thinking-label">Thought process</span>${elapsedSeconds !== null ? `<span class="thinking-timer">${elapsedSeconds}s</span>` : ''}<span class="thinking-toggle">?</span></summary><div class="thinking-body"><div class="thinking-content-text">${escapeHtml(thinkingContent)}</div></div></details>`
    if (responseContent) {
      return { html, text: responseContent }
    }
    return { html }
  }

  return { html: `<div class="response-content">${escapeHtml(responseContent || '')}</div>` }
}

function buildMessageContent(thinkingContent, responseContent, elapsedSeconds = null, isThinking = false) {
  const result = buildMessageText(thinkingContent, responseContent, elapsedSeconds, isThinking)
  if (result.html && result.text) {
    return { html: `<div class="message-wrapper">${result.html}<div class="response-content">${escapeHtml(result.text)}</div></div>` }
  }
  if (result.text !== undefined && !result.html) {
    return { html: `<div class="response-content">${escapeHtml(result.text)}</div>` }
  }
  if (result.html) {
    return { html: `<div class="message-wrapper">${result.html}</div>` }
  }
  return result
}

function escapeHtml(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
    .replace(/\n/g, '<br>')
}

async function handleStreamWithSignals(runId, signals, context) {
  let aiMessageContent = ''
  let hasRenderedDelta = false
  let thinkingContent = ''
  let thinkingStartTime = null
  let thinkingElapsedSeconds = 0
  let thinkingTimerInterval = null
  let thinkingFinalized = false
  let hasThinkingContent = false
  let settled = false
  let statusPollTimer = null

  async function fetchRunStatus() {
    const response = await fetch(buildApiUrl(`/api/agent/runs/${runId}`), {
      method: 'GET',
      credentials: 'include'
    })
    if (!response.ok) {
      throw new Error(`Run status request failed: ${response.status}`)
    }
    return response.json()
  }

  function cleanupStatusPoll() {
    if (statusPollTimer) {
      clearInterval(statusPollTimer)
      statusPollTimer = null
    }
  }

  function updateUI() {
    try {
      const content = buildMessageContent(
        thinkingContent,
        aiMessageContent,
        thinkingElapsedSeconds,
        !thinkingFinalized && hasThinkingContent
      )
      if (content.html) {
        signals.onResponse({ html: content.html, overwrite: true })
      }
      setupScrollListener()
      scrollToBottom()
    } catch (e) {
      console.warn('[ChatUI] Failed to update UI:', e)
    }
  }

  async function settleRun({ isError = false, errorMessage = '' } = {}) {
    if (settled) return
    settled = true
    cleanupStatusPoll()
    assistantUpdatePending = false
    thinkingFinalized = true
    stopThinkingTimer()

    if (isError) {
      try {
        signals.onResponse({
          html: `<p style="color: #d32f2f;">Error: ${escapeHtml(errorMessage || 'Unknown error')}</p>`,
          overwrite: true
        })
      } catch (_error) {}
    } else if (hasRenderedDelta || hasThinkingContent) {
      updateUI()
    }

    await notifyRunCompleted(context.sessionKey)
    signals.onClose()
    if (currentStreamHandler) {
      currentStreamHandler.abort()
      currentStreamHandler = null
    }
  }

  function startThinkingTimer() {
    if (thinkingTimerInterval) return
    thinkingStartTime = Date.now()
    thinkingTimerInterval = setInterval(() => {
      thinkingElapsedSeconds = Math.round((Date.now() - thinkingStartTime) / 100) / 10
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

  return new Promise((resolve) => {
    statusPollTimer = setInterval(async () => {
      if (settled) return
      try {
        const status = await fetchRunStatus()
        if (status.status === 'completed') {
          await settleRun()
          resolve()
          return
        }
        if (status.status === 'error' || status.status === 'timeout' || status.status === 'aborted') {
          await settleRun({
            isError: true,
            errorMessage: status.error || status.status
          })
          resolve()
        }
      } catch (error) {
        console.warn('[ChatUI] Failed to poll run status:', error)
      }
    }, 1500)

    currentStreamHandler = createStreamHandler(runId, {
      onStart: () => {},
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
            try {
              if (hasThinkingContent) {
                const content = buildMessageContent(thinkingContent, aiMessageContent, thinkingElapsedSeconds, false)
                const htmlContent = content.html || `<div class="response-content">${escapeHtml(content.text || '')}</div>`
                signals.onResponse({ html: htmlContent, overwrite: true })
              } else {
                signals.onResponse({ html: `<div class="response-content">${escapeHtml(aiMessageContent)}</div>`, overwrite: true })
              }
              setupScrollListener()
              scrollToBottom()
            } catch (e) {
              console.warn('[ChatUI] Failed to update message:', e)
            }
          }, 100)
        }
      },
      onToolStart: () => {},
      onToolEnd: () => {},
      onThinkingStart: () => {
        hasThinkingContent = true
        startThinkingTimer()
        userHasScrolledUp = false
        thinkingBlockId = `tb-${Date.now()}`
        const initialHtml = `<div class="message-wrapper">${THINKING_STYLES}
          <div class="thinking-block thinking" id="${thinkingBlockId}">
            <div class="thinking-header">
              <span class="thinking-icon">*</span>
              <span class="thinking-label">Thinking<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></span>
              <span class="thinking-toggle">▸</span>
            </div>
            <div class="thinking-body">
              <div class="thinking-content-text" id="${thinkingBlockId}-content"></div>
            </div>
          </div>
        </div>`
        signals.onResponse({ html: initialHtml, overwrite: true })
      },
      onThinkingDelta: (data) => {
        const content = data?.content || ''
        if (!content) return
        if (!thinkingStartTime) {
          hasThinkingContent = true
          startThinkingTimer()
        }
        thinkingContent += content
        const dc = document.querySelector('deep-chat')
        if (dc?.shadowRoot && thinkingBlockId) {
          const contentEl = dc.shadowRoot.querySelector(`#${thinkingBlockId}-content`)
          if (contentEl) {
            const escapedContent = content
              .replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/\n/g, '<br>')
            contentEl.insertAdjacentHTML('beforeend', escapedContent)
          }
        }
        if (!thinkingScrollPending) {
          thinkingScrollPending = true
          setTimeout(() => {
            thinkingScrollPending = false
            setupScrollListener()
            scrollToBottom()
          }, 100)
        }
      },
      onThinkingEnd: (data) => {
        thinkingFinalized = true
        if (data?.elapsed && data.elapsed > 0) {
          thinkingElapsedSeconds = data.elapsed
        }
        stopThinkingTimer()
        updateUI()
      },
      onEnd: () => {
        const doFinalRender = async () => {
          await settleRun()
          resolve()
        }
        setTimeout(() => {
          void doFinalRender()
        }, 200)
      },
      onError: async (error) => {
        try {
          const status = await fetchRunStatus()
          if (status.status === 'completed') {
            await settleRun()
            resolve()
            return
          }
          if (status.status === 'error' || status.status === 'timeout' || status.status === 'aborted') {
            await settleRun({
              isError: true,
              errorMessage: status.error || error?.message || status.status
            })
            resolve()
            return
          }
          console.warn('[ChatUI] Stream error while run is still active, waiting for poll fallback:', error)
          return
        } catch (_statusError) {
          await settleRun({
            isError: true,
            errorMessage: error?.message || 'Unknown error'
          })
          resolve()
        }
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
