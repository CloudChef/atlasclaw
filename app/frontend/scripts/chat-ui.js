/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * DeepChat UI Configuration and Interaction
 * Configure DeepChat component integration with AtlasClaw API
 */

import { getSessionKey, initSession, setSessionKey, setSessionHasMessages } from './session-manager.js?v=24'
import { buildWorkspaceFileDownloadUrl, getAgentInfo, getSessionHistory } from './api-client.js?v=24'
import { createStreamHandler } from './stream-handler.js?v=24'
import { buildApiUrl } from './config.js?v=24'
import { translateIfExists, getCurrentLocale } from './i18n.js'
import { setupSlashCapabilityPicker, prepareSlashCapabilityMessage } from './slash-picker.js?v=24'

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
let focusRetryGeneration = 0
let sessionActivationGeneration = 0

const IME_ENTER_GUARD_MS = 150
const SCROLL_THRESHOLD = 50
const CHAT_INPUT_FOCUS_RETRY_ATTEMPTS = 100
const CHAT_INPUT_FOCUS_RETRY_DELAY_MS = 100
const USER_MESSAGE_COPY_RETRY_DELAY_MS = 250
const USER_MESSAGE_COPY_RESET_MS = 1200
const OBJECT_ACTION_BIND_RETRY_DELAY_MS = 250
const STREAM_RESULT_STATUS = {
  aborted: 'aborted',
  completed: 'completed',
  failed: 'failed'
}

const COPY_MESSAGE_ICON = `
<svg class="atlas-user-message-copy-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <rect x="8" y="8" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"></rect>
  <path d="M5 15V7a2 2 0 0 1 2-2h8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
</svg>`

const COPIED_MESSAGE_ICON = `
<svg class="atlas-user-message-copy-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <path d="M20 6 9 17l-5-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>
</svg>`

const WORKSPACE_DOWNLOAD_ICON = `
<svg class="workspace-download-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <path d="M12 3v11" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
  <path d="m7 10 5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
  <path d="M5 20h14" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
</svg>`

const OBJECT_ACTION_ICON = `
<svg class="object-action-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <path d="M7 17 17 7" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
  <path d="M9 7h8v8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
  <path d="M19 19H5V5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
</svg>`

const OBJECT_ACTION_CONTEXT_FIELDS = [
  'index',
  'object_type',
  'object_id',
  'object_name'
]

const OBJECT_ACTION_INDEX_HEADER_KEYS = new Set(['#', 'index', '??', '??'])
const OBJECT_ACTION_ID_HEADER_KEYS = new Set([
  'objectid',
  '??id'
])
const OBJECT_ACTION_NAME_HEADER_KEYS = new Set([
  'objectname',
  '????'
])

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

function isDeepChatInputElement(element) {
  return !!element &&
    typeof element.matches === 'function' &&
    // Deep Chat can recreate its editor as a textarea, text input, or contenteditable node.
    element.matches('textarea, input[type="text"], [contenteditable="true"]')
}

// Resolve the real input from a composed event path so delegated listeners still
// work when the event crosses Deep Chat's shadow DOM boundary.
function getDeepChatInputFromEvent(event) {
  const path = typeof event?.composedPath === 'function' ? event.composedPath() : []
  const pathInput = path.find((node) => isDeepChatInputElement(node))
  if (pathInput) return pathInput

  return isDeepChatInputElement(event?.target) ? event.target : null
}

// Track IME state only for events that originate from Deep Chat's editable input.
function handleImeCompositionStart(event) {
  if (!getDeepChatInputFromEvent(event)) return
  isComposing = true
  clearImeEnterGuard()
  console.debug('[ChatUI] IME composition started')
}

function handleImeCompositionEnd(event) {
  if (!getDeepChatInputFromEvent(event)) return
  isComposing = false
  armImeEnterGuard()
  console.debug('[ChatUI] IME composition ended')
}

function handleImeKeyDown(event) {
  if (!getDeepChatInputFromEvent(event) || !shouldBlockImeEnter(event)) {
    return
  }

  if (hasActiveImeEnterGuard() && !isComposing && event.isComposing !== true) {
    clearImeEnterGuard()
  }

  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  console.debug('[ChatUI] Enter key blocked during IME composition')
}

// Attach in capture phase to intercept Enter before Deep Chat submits, and attach
// to stable containers so the guard survives internal input replacement.
function attachImeGuardListeners(target) {
  if (!target || target._imeCompositionGuardAttached) return false

  target.addEventListener('compositionstart', handleImeCompositionStart, true)
  target.addEventListener('compositionend', handleImeCompositionEnd, true)
  target.addEventListener('keydown', handleImeKeyDown, true)
  target._imeCompositionGuardAttached = true
  return true
}

function getMessageContainer() {
  const dc = document.querySelector('deep-chat')
  return getMessageContainerForElement(dc)
}

function getMessageContainerForElement(element) {
  if (!element?.shadowRoot) return null
  return element.shadowRoot.querySelector('.messages-container') ||
    element.shadowRoot.querySelector('#messages') ||
    element.shadowRoot.querySelector('[class*="message-container"]')
}

function getChatInputElement(element = chatElement) {
  if (!element?.shadowRoot) return null
  return element.shadowRoot.querySelector('textarea') ||
    element.shadowRoot.querySelector('input[type="text"]') ||
    element.shadowRoot.querySelector('[contenteditable="true"]')
}

function placeCaretAtEnd(inputElement) {
  if (!inputElement || !(inputElement.isContentEditable || inputElement.getAttribute?.('contenteditable') === 'true')) {
    return
  }
  const selection = window.getSelection()
  if (!selection) return
  const range = document.createRange()
  range.selectNodeContents(inputElement)
  range.collapse(false)
  selection.removeAllRanges()
  selection.addRange(range)
}

/**
 * Focus the chat composer after route/session changes, retrying while DeepChat initializes its shadow input.
 */
export function focusChatInput({
  retry = true,
  attempts = CHAT_INPUT_FOCUS_RETRY_ATTEMPTS,
  delayMs = CHAT_INPUT_FOCUS_RETRY_DELAY_MS
} = {}) {
  const generation = ++focusRetryGeneration
  return focusChatInputWithRetry({
    retry,
    attempts,
    delayMs,
    generation,
    hasFocused: false,
    focusedInput: null
  })
}

function shouldRefocusReplacementInput(inputElement, focusedInput) {
  if (!inputElement || !focusedInput || inputElement === focusedInput) return false
  const activeElement = document.activeElement
  return !focusedInput.isConnected &&
    (!activeElement || activeElement === document.body || activeElement === chatElement)
}

function focusChatInputWithRetry({ retry, attempts, delayMs, generation, hasFocused, focusedInput }) {
  if (generation !== focusRetryGeneration) return false

  const inputElement = getChatInputElement()
  if (inputElement) {
    setupCompositionListeners()
    setupSlashCapabilityPicker(chatElement)
    if (!hasFocused || shouldRefocusReplacementInput(inputElement, focusedInput)) {
      try {
        inputElement.focus({ preventScroll: true })
      } catch (_error) {
        inputElement.focus()
      }
      placeCaretAtEnd(inputElement)
    }
    hasFocused = true
    focusedInput = inputElement
  }

  if (retry && attempts > 0) {
    // DeepChat may replace its shadow input after history renders; keep rebinding
    // slash picker for a bounded window without stealing focus after the first success.
    setTimeout(() => focusChatInputWithRetry({
      retry,
      attempts: attempts - 1,
      delayMs,
      generation,
      hasFocused,
      focusedInput
    }), delayMs)
  }
  return hasFocused
}

/**
 * Cancel pending chat-input focus retries when leaving the chat page.
 */
export function cancelChatInputFocusRetry() {
  focusRetryGeneration += 1
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

  const attachedToRoot = attachImeGuardListeners(dc.shadowRoot)
  const attachedToHost = attachImeGuardListeners(dc)
  if (attachedToRoot || attachedToHost) {
    console.log('[ChatUI] IME composition guard attached to Deep Chat')
  }
}

function getTranslatedChatLabel(key, fallback) {
  return translateIfExists(key) || fallback
}

function scheduleUserMessageCopySetup(element) {
  if (!element || element.nodeType !== 1 || element._userMessageCopySetupTimer) return
  element._userMessageCopySetupTimer = setTimeout(() => {
    element._userMessageCopySetupTimer = null
    setupUserMessageCopyActions(element)
  }, USER_MESSAGE_COPY_RETRY_DELAY_MS)
}

function setupUserMessageCopyActions(element = chatElement) {
  if (!element?.shadowRoot) {
    scheduleUserMessageCopySetup(element)
    return false
  }

  setupUserMessageCopyRootObserver(element)
  const container = getMessageContainerForElement(element)
  if (!container) {
    scheduleUserMessageCopySetup(element)
    return false
  }

  decorateUserMessagesWithCopy(container)
  if (typeof MutationObserver === 'undefined') return true
  if (element._userMessageCopyContainer === container && element._userMessageCopyObserver) return true
  if (element._userMessageCopyObserver) {
    element._userMessageCopyObserver.disconnect()
  }

  const observer = new MutationObserver(() => {
    decorateUserMessagesWithCopy(container)
  })
  observer.observe(container, { childList: true, subtree: true })
  element._userMessageCopyContainer = container
  element._userMessageCopyObserver = observer
  return true
}

function setupUserMessageCopyRootObserver(element) {
  if (typeof MutationObserver === 'undefined' || element._userMessageCopyRootObserver) return
  const observer = new MutationObserver(() => {
    setupUserMessageCopyActions(element)
  })
  observer.observe(element.shadowRoot, { childList: true, subtree: true })
  element._userMessageCopyRootObserver = observer
}

function scheduleObjectActionHandlers(element = chatElement) {
  if (!element || element.nodeType !== 1 || element._objectActionBindTimer) return
  element._objectActionBindTimer = setTimeout(() => {
    element._objectActionBindTimer = null
    bindObjectActionHandlers(element)
  }, OBJECT_ACTION_BIND_RETRY_DELAY_MS)
}

function setupObjectActionRootObserver(element) {
  if (typeof MutationObserver === 'undefined' || element._objectActionRootObserver) return
  const observer = new MutationObserver(() => {
    bindObjectActionHandlers(element)
  })
  observer.observe(element.shadowRoot, { childList: true, subtree: true })
  element._objectActionRootObserver = observer
}

function decorateUserMessagesWithCopy(container) {
  if (!container) return
  const userBubbles = container.querySelectorAll('.message-bubble.user-message-text, .user-message-text')
  userBubbles.forEach((bubble) => {
    if (!bubble || bubble.dataset?.copyEnhanced === 'true') {
      refreshUserMessageCopyButtonLabels(bubble?.nextElementSibling)
      return
    }

    const button = createUserMessageCopyButton(bubble)
    bubble.insertAdjacentElement('afterend', button)
    bubble.dataset.copyEnhanced = 'true'
  })
}

function createUserMessageCopyButton(messageBubble) {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'atlas-user-message-copy-btn'
  button.innerHTML = COPY_MESSAGE_ICON
  applyUserMessageCopyButtonLabels(button)

  button.addEventListener('click', async (event) => {
    event.preventDefault()
    event.stopPropagation()

    const text = readUserMessageText(messageBubble)
    if (!text) return

    const copied = await copyTextToClipboard(text)
    if (copied) {
      showUserMessageCopySuccess(button)
    }
  })

  return button
}

function refreshUserMessageCopyButtonLabels(button) {
  if (!button?.classList?.contains('atlas-user-message-copy-btn')) return
  applyUserMessageCopyButtonLabels(button)
}

function applyUserMessageCopyButtonLabels(button) {
  const label = getTranslatedChatLabel('chat.copyMessage', 'Copy message')
  button.title = label
  button.setAttribute('aria-label', label)
}

function readUserMessageText(messageBubble) {
  const renderedText = typeof messageBubble?.innerText === 'string'
    ? messageBubble.innerText
    : messageBubble?.textContent || ''
  return String(renderedText).replace(/\r?\n$/, '')
}

async function copyTextToClipboard(text) {
  const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : null
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text)
      return true
    } catch (error) {
      console.warn('[ChatUI] Clipboard API copy failed, falling back:', error)
    }
  }

  return fallbackCopyText(text)
}

function fallbackCopyText(text) {
  if (!document?.body || typeof document.execCommand !== 'function') {
    return false
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.top = '0'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()

  try {
    return document.execCommand('copy')
  } catch (error) {
    console.warn('[ChatUI] Fallback copy failed:', error)
    return false
  } finally {
    textarea.remove()
  }
}

function showUserMessageCopySuccess(button) {
  button.classList.add('copied')
  button.innerHTML = COPIED_MESSAGE_ICON
  applyUserMessageCopyButtonLabels(button)

  clearTimeout(button._copyResetTimer)
  button._copyResetTimer = setTimeout(() => {
    button.classList.remove('copied')
    button.innerHTML = COPY_MESSAGE_ICON
    applyUserMessageCopyButtonLabels(button)
    button._copyResetTimer = null
  }, USER_MESSAGE_COPY_RESET_MS)
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

function applyRuntimePanelState(details, shouldOpen) {
  if (!details) return
  if (shouldOpen) {
    details.setAttribute('open', '')
  } else {
    details.removeAttribute('open')
  }
}

function readRenderedRuntimePanelOpen() {
  const container = getMessageContainer()
  if (!container) return null
  const details = getLatestRuntimePanel(container)
  if (!details) return null
  return !!details.open
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
details.runtime-panel{box-sizing:border-box;width:fit-content;min-width:min(260px,100%);max-width:100%;margin-bottom:12px;padding:10px 14px;border:1px solid rgba(148,163,184,.20);border-radius:14px;background:rgba(248,250,252,.92)}
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
.runtime-body{display:flex;flex-direction:column;gap:10px;padding-top:8px}
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
:host{--atlas-chat-side-gutter:10%}
#input{box-sizing:border-box!important;padding-left:var(--atlas-chat-side-gutter)!important;padding-right:var(--atlas-chat-side-gutter)!important}
#text-input-container{box-sizing:border-box!important;width:100%!important}
#messages,.messages,.messages-container{scrollbar-width:none!important}
#messages::-webkit-scrollbar,.messages::-webkit-scrollbar,.messages-container::-webkit-scrollbar{display:none!important}
.outer-message-container:has(.response-table-wrap-wide){padding-left:var(--atlas-chat-side-gutter)!important;padding-right:var(--atlas-chat-side-gutter)!important}
.outer-message-container:has(.message-bubble.ai-message,.message-bubble.ai-message-text) .inner-message-container,.outer-message-container:has(.message-bubble.user-message,.message-bubble.user-message-text) .inner-message-container{width:100%!important;max-width:100%!important;margin-left:0!important;margin-right:0!important}
.outer-message-container:has(.message-bubble.user-message,.message-bubble.user-message-text) .inner-message-container{position:relative}
.outer-message-container:has(.message-bubble.user-message,.message-bubble.user-message-text) .inner-message-container::after{content:"";position:absolute;top:0;right:-46px;width:46px;height:100%;min-height:46px;pointer-events:auto}
.outer-message-container:has(.message-bubble.user-message,.message-bubble.user-message-text) .atlas-user-message-copy-btn{position:absolute;right:-38px;top:8px;margin:0;z-index:1}
.outer-message-container:has(.response-table-wrap-wide) .inner-message-container{width:100%!important;max-width:100%!important}
.message-bubble.ai-message:has(.response-table-wrap-wide){width:100%!important;max-width:100%!important}
.response-table-wrap{box-sizing:border-box;max-width:100%;overflow-x:auto;margin:4px 0 14px 0;border:1px solid #e2e8f0;border-radius:10px;background:#fff}
.response-table-wrap-wide{display:block;width:100%;max-width:100%;max-height:min(60vh,640px);overflow:auto;margin-left:auto;margin-right:auto}
.response-table-wrap-compact{display:inline-block;width:auto}
.response-table{width:auto;border-collapse:separate;border-spacing:0;font-size:13px;line-height:1.45;color:#1f2937}
.response-table-wrap-wide .response-table{width:100%;min-width:860px}
.response-table-wrap-compact .response-table{min-width:0}
.response-table th,.response-table td{padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle;white-space:nowrap}
.response-table-wrap-compact .response-table th,.response-table-wrap-compact .response-table td{padding:6px 12px}
.response-table th{position:sticky;top:0;background:#f8fafc;color:#475569;font-size:12px;font-weight:700}
.response-table td{font-variant-numeric:tabular-nums}
.response-table td.response-table-number{text-align:right}
.response-table tr:last-child td{border-bottom:0}
.response-table tbody tr:nth-child(even) td{background:#fbfdff}
.response-content pre{margin:0 0 12px 0;padding:18px 20px;overflow-x:auto;border-radius:16px;background:#1e293b;color:#e2e8f0}
.response-content code{padding:2px 6px;border-radius:6px;background:#eef2f7;font-size:.95em}
.response-content pre code{display:block;padding:0;border-radius:0;background:transparent;color:inherit;font-size:13px;line-height:1.7;white-space:pre;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace}
.response-content a{color:#2563eb;text-decoration:none}
.response-content a:hover{text-decoration:underline}
.response-content a.workspace-download-link{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:3px 8px;border:1px solid rgba(37,99,235,.20);border-radius:8px;background:#eff6ff;color:#1d4ed8;font-weight:600;line-height:1.45;vertical-align:baseline}
.response-content a.workspace-download-link:hover{border-color:rgba(37,99,235,.36);background:#dbeafe;text-decoration:none}
.response-content .workspace-generated-downloads{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}
.workspace-download-icon{width:14px;height:14px;flex:0 0 14px}
.response-table th.response-table-action-header,.response-table td.response-table-action{position:sticky;right:0;min-width:146px;background:#fff;box-shadow:-8px 0 12px rgba(255,255,255,.82)}
.response-table th.response-table-action-header{background:#f8fafc;text-align:left}
.response-table td.response-table-action{text-align:right}
.response-table tbody tr:nth-child(even) td.response-table-action{background:#fbfdff}
.response-content .object-actions{display:inline-flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:6px;max-width:100%;vertical-align:baseline}
.response-content>.object-actions{display:flex;justify-content:flex-start;gap:8px;width:fit-content;max-width:100%;margin-top:12px;padding:7px;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc}
.response-table td.response-table-action .object-actions{flex-wrap:nowrap;gap:5px;justify-content:flex-end}
/* Row action cards live inside right-aligned nowrap cells, so reset prose layout locally. */
.response-table td.response-table-action .object-action-confirmation-card{white-space:normal;text-align:left}
.response-content a.object-action-link,.response-content button.object-action-button{position:relative;display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;gap:5px;max-width:100%;height:28px;min-height:28px;padding:0 8px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;color:#334155;box-shadow:0 1px 1px rgba(15,23,42,.04);font:inherit;font-size:13px;font-weight:650;line-height:1;vertical-align:baseline;white-space:nowrap;cursor:pointer;transition:background .14s ease,border-color .14s ease,box-shadow .14s ease,color .14s ease,transform .14s ease}
.response-content a.object-action-link:hover,.response-content button.object-action-button:hover{border-color:#94a3b8;background:#f8fafc;box-shadow:0 3px 8px rgba(15,23,42,.08);text-decoration:none;transform:translateY(-1px)}
.response-content a.object-action-link:active,.response-content button.object-action-button:active{box-shadow:0 1px 2px rgba(15,23,42,.08);transform:translateY(0)}
.response-content a.object-action-link:focus-visible,.response-content button.object-action-button:focus-visible{outline:2px solid rgba(37,99,235,.38);outline-offset:2px}
.response-content button.object-action-button::before{content:"";width:6px;height:6px;flex:0 0 6px;border-radius:999px;background:#64748b;box-shadow:0 0 0 3px rgba(100,116,139,.12)}
.response-content a.object-action-link,.response-content button.object-action-open-button{border-color:rgba(20,184,166,.28);background:#f0fdfa;color:#0f766e}
.response-content a.object-action-link:hover,.response-content button.object-action-open-button:hover{border-color:rgba(13,148,136,.46);background:#ccfbf1;color:#0f766e}
.response-content button.object-action-open-button::before{content:none}
.response-content button.object-action-button.tone-success{border-color:rgba(22,163,74,.28);background:#f0fdf4;color:#15803d}
.response-content button.object-action-button.tone-success::before{background:#16a34a;box-shadow:0 0 0 3px rgba(22,163,74,.13)}
.response-content button.object-action-button.tone-success:hover{border-color:rgba(22,163,74,.46);background:#dcfce7;color:#166534}
.response-content button.object-action-button.tone-warning{border-color:rgba(217,119,6,.32);background:#fffbeb;color:#b45309}
.response-content button.object-action-button.tone-warning::before{background:#d97706;box-shadow:0 0 0 3px rgba(217,119,6,.14)}
.response-content button.object-action-button.tone-warning:hover{border-color:rgba(217,119,6,.50);background:#fef3c7;color:#92400e}
.response-content button.object-action-button.tone-danger{border-color:rgba(220,38,38,.28);background:#fef2f2;color:#b91c1c}
.response-content button.object-action-button.tone-danger::before{background:#dc2626;box-shadow:0 0 0 3px rgba(220,38,38,.13)}
.response-content button.object-action-button.tone-danger:hover{border-color:rgba(220,38,38,.48);background:#fee2e2;color:#991b1b}
.response-content .object-actions.is-confirming button.object-action-button{opacity:.52;pointer-events:none}
.object-action-confirmation-card{box-sizing:border-box;width:min(100%,420px);margin-top:8px;padding:10px;border:1px solid #dbe3ee;border-radius:8px;background:#fff;box-shadow:0 8px 22px rgba(15,23,42,.08);color:#1f2937}
.object-action-confirmation-title{font-size:13px;font-weight:700;line-height:1.45}
.object-action-confirmation-help{margin-top:3px;color:#64748b;font-size:12px;line-height:1.45}
.object-action-confirmation-inputs{display:grid;gap:8px;margin-top:9px}
.object-action-input-label{display:grid;gap:5px;color:#475569;font-size:12px;font-weight:650}
.object-action-input,.object-action-textarea{box-sizing:border-box;width:100%;border:1px solid #cbd5e1;border-radius:7px;background:#fff;color:#0f172a;font:inherit;font-size:13px;line-height:1.45;outline:none;transition:border-color .14s ease,box-shadow .14s ease}
.object-action-input{height:32px;padding:0 9px}
.object-action-textarea{min-height:72px;padding:8px 9px;resize:vertical}
.object-action-input:focus,.object-action-textarea:focus{border-color:#7c83fd;box-shadow:0 0 0 3px rgba(124,131,253,.14)}
.object-action-confirmation-error{display:none;margin-top:7px;color:#b91c1c;font-size:12px;font-weight:650;line-height:1.4}
.object-action-confirmation-card.has-error .object-action-confirmation-error{display:block}
.object-action-confirmation-buttons{display:flex;flex-wrap:wrap;gap:7px;justify-content:flex-end;margin-top:10px}
.response-content .object-action-confirmation-buttons button.object-action-button{height:30px;min-height:30px}
.response-content button.object-action-cancel-button::before{display:none}
.object-action-confirmation-card.is-submitting .object-action-input,.object-action-confirmation-card.is-submitting .object-action-textarea,.object-action-confirmation-card.is-submitting button{opacity:.58;pointer-events:none}
.object-action-icon{width:11px;height:11px;flex:0 0 11px}
.object-action-text{min-width:0;overflow:hidden;text-overflow:ellipsis}
.message-wrapper{display:flex;flex-direction:column;gap:12px}
.atlas-user-message-copy-btn{width:30px;height:30px;margin-top:12px;margin-left:8px;border:1px solid rgba(148,163,184,.34);border-radius:999px;background:rgba(255,255,255,.92);color:#64748b;box-shadow:0 10px 24px rgba(15,23,42,.10);display:inline-flex;align-items:center;justify-content:center;flex:0 0 30px;cursor:pointer;opacity:0;pointer-events:auto;transform:translateY(2px) scale(.96);transition:opacity .16s ease,transform .16s ease,color .16s ease,border-color .16s ease,background .16s ease}
.atlas-user-message-copy-btn:hover{color:#1f2937;border-color:rgba(124,131,253,.46);background:#ffffff}
.atlas-user-message-copy-btn:focus-visible{outline:2px solid rgba(124,131,253,.52);outline-offset:2px}
.atlas-user-message-copy-btn.copied{color:#16a34a;border-color:rgba(22,163,74,.30);background:#ecfdf5}
.atlas-user-message-copy-icon{width:15px;height:15px;display:block}
.inner-message-container:hover>.atlas-user-message-copy-btn,.atlas-user-message-copy-btn:hover,.atlas-user-message-copy-btn:focus-visible,.atlas-user-message-copy-btn.copied{opacity:1;pointer-events:auto;transform:translateY(0) scale(1)}
@media (hover:none){.atlas-user-message-copy-btn{opacity:1;pointer-events:auto;transform:translateY(0) scale(1)}}
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
  bindObjectActionHandlers()
  setupSlashCapabilityPicker(element)
  setupUserMessageCopyActions(element)
  
  await activateSession(getSessionKey())
  setupUserMessageCopyActions(element)
  focusChatInput()

  console.log('[ChatUI] Initialized')
}

/**
 * Restore one chat session into the DeepChat view.
 *
 * Only the most recent activation may mutate the rendered history. Older
 * history requests can finish after a faster follow-up click, so they must be
 * ignored to keep the visible conversation aligned with the active sidebar row.
 */
export async function activateSession(sessionKey) {
  if (!chatElement) return false
  const requestedSessionKey = sessionKey || getSessionKey()
  const activationGeneration = ++sessionActivationGeneration
  currentSessionKey = requestedSessionKey || null
  if (currentSessionKey) {
    setSessionKey(currentSessionKey)
  }
  const hasHistory = await restoreSessionHistory(
    chatElement,
    currentSessionKey,
    activationGeneration
  )
  if (hasHistory === null) {
    return false
  }
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

function isCurrentSessionActivation(sessionKey, activationGeneration) {
  return activationGeneration === sessionActivationGeneration && currentSessionKey === sessionKey
}

async function restoreSessionHistory(element, sessionKey, activationGeneration) {
  if (!sessionKey) {
    if (!isCurrentSessionActivation(sessionKey, activationGeneration)) {
      return null
    }
    applyHistoryToElement(element, [])
    return false
  }

  try {
    const payload = await getSessionHistory(sessionKey)
    const history = (payload.messages || [])
      .map((message) => mapTranscriptMessageToHistory(message))
      .filter(Boolean)

    if (!isCurrentSessionActivation(sessionKey, activationGeneration)) {
      return null
    }
    applyHistoryToElement(element, history)
    bindObjectActionHandlers()
    return history.length > 0
  } catch (error) {
    if (!isCurrentSessionActivation(sessionKey, activationGeneration)) {
      return null
    }
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
    const workspaceDownloads = normalizeWorkspaceDownloadArtifacts(message.workspace_downloads)
    const objectActions = normalizeObjectActionReferences(message.object_actions)
    const rendered = buildMessageContent(
      [],
      '',
      message.content,
      null,
      false,
      false,
      true,
      0,
      workspaceDownloads,
      objectActions
    )
    return { role: 'ai', html: rendered.html }
  }
  return null
}

function configureHandler(element) {
  const handlerFn = async (body, signals) => {
    const rawMessageText = extractMessageFromBody(body)
    const slashMessage = prepareSlashCapabilityMessage(rawMessageText)
    const messageText = slashMessage.messageText
    const selectedCapability = slashMessage.selectedCapability
    if (!messageText && !selectedCapability) {
      signals.onClose()
      return
    }

    await runAgentMessage(messageText, selectedCapability, signals)
  }

  element.handler = handlerFn
  element.connect = { handler: handlerFn, stream: true }
}

async function runAgentMessage(messageText, selectedCapability, signals, options = {}) {
  let sessionKey = getSessionKey()
  if (!sessionKey) {
    sessionKey = await initSession()
    currentSessionKey = sessionKey
  }

  if (options.visibleUserTurn !== false) {
    notifyUserTurnStarted(sessionKey, messageText)
  }

  let runId
  try {
    const requestContext = {
      ui_locale: getCurrentLocale(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    }
    if (selectedCapability) {
      requestContext.selected_capability = selectedCapability
    }
    if (options.visibleUserTurn === false) {
      requestContext.visible_user_turn = false
    }
    const response = await fetch(buildApiUrl('/api/agent/run'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_key: sessionKey || '',
        message: messageText || '',
        timeout_seconds: 600,
        context: requestContext
      })
    })

    if (!response.ok) {
      let errorMessage = `Error: ${response.status} ${response.statusText}`
      try {
        const errorData = await response.json()
        if (errorData?.detail) {
          errorMessage = errorData.detail
        }
      } catch (_) {
        // Keep the HTTP status fallback when the response body is not JSON.
      }
      signals.onResponse({ html: `<p style="color: #d32f2f;">${escapeHtml(errorMessage)}</p>` })
      signals.onClose()
      return false
    }

    const data = await response.json()
    runId = data.run_id || data.runId || data.id
    if (!runId) {
      signals.onResponse({ html: `<p style="color: #d32f2f;">${escapeHtml(data.detail || 'Error: No run_id')}</p>` })
      signals.onClose()
      return false
    }
    if (typeof options.onRunCreated === 'function') {
      options.onRunCreated({ runId, sessionKey })
    }
  } catch (err) {
    console.error('[ChatUI] API call failed:', err)
    signals.onResponse({ html: `<p style="color: #d32f2f;">Error: ${escapeHtml(err.message)}</p>` })
    signals.onClose()
    return false
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

  const streamResult = await handleStreamWithSignals(runId, signals, { sessionKey, messageText })
  if (streamResult?.status === STREAM_RESULT_STATUS.completed && typeof options.onRunCompleted === 'function') {
    options.onRunCompleted({ runId, sessionKey })
  } else if (streamResult?.status === STREAM_RESULT_STATUS.failed && typeof options.onRunFailed === 'function') {
    options.onRunFailed({ runId, sessionKey, error: streamResult.error })
  }
  return true
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
          padding: '10px 18px',
          fontSize: '16px',
          lineHeight: '1.75',
          borderRadius: '24px'
        },
        outerContainer: {
          marginTop: '8px',
          marginBottom: '8px'
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
          paddingLeft: '10%',
          paddingRight: '10%'
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
          justifyContent: 'flex-start',
          paddingLeft: '10%',
          paddingRight: '10%'
        }
      }
    }
  }
  element.auxiliaryStyle = `
    :host { border: none !important; background: transparent !important; box-shadow: none !important; }
    #container, #chat-view, #messages, .messages, .messages-container { border: none !important; background: transparent !important; box-shadow: none !important; }
    #messages, .messages, .messages-container { box-sizing: border-box !important; padding-bottom: 112px !important; scroll-padding-bottom: 112px !important; }
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
        width: '100%',
        boxSizing: 'border-box',
        borderRadius: '32px',
        border: 'none',
        padding: '12px 20px',
        backgroundColor: '#ffffff',
        boxShadow: '0 22px 60px rgba(15, 23, 42, 0.08)'
      },
      text: {
        fontSize: '18px',
        lineHeight: '1.45',
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
  reasoning: ['chat.runtimeThinking', 'Thinking'],
  retrying: ['chat.runtimeRetrying', 'Retrying'],
  waiting_for_tool: ['chat.runtimeWaitingForTool', 'Waiting for tool'],
  tool_running: ['chat.runtimeToolRunning', 'Running tool'],
  controlled_path: ['chat.runtimeControlledPath', 'Controlled path'],
  failed: ['chat.runtimeFailed', 'Failed']
}

function getRuntimeStateLabel(state) {
  const config = RUNTIME_STATE_LABELS[state]
  if (!config) return state || 'Runtime'
  const [key, fallback] = config
  return translateIfExists(key) || fallback
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
  const caption = translateIfExists('chat.modelThinking') || 'Model thinking'
  return `<div class="thinking-body"><div class="thinking-caption">${escapeHtml(caption)}</div><div class="thinking-content-text">${escapeHtmlWithBreaks(thinkingContent)}</div></div>`
}

function formatRuntimeHeaderElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function getRuntimeDisplayEntries(runtimeEntries) {
  const entries = Array.isArray(runtimeEntries) ? runtimeEntries : []
  return entries.filter((entry) => (
    entry.state !== 'answered' &&
    entry.state !== 'answering' &&
    String(entry.message || '').trim() !== 'Reasoning phase completed.'
  ))
}

function buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs = null, isThinking = false, panelOpen = null, isComplete = false) {
  const entries = Array.isArray(runtimeEntries) ? runtimeEntries : []
  const hasThinkingText = !!(thinkingContent && thinkingContent.trim())
  if (!entries.length && !hasThinkingText) {
    return ''
  }
  const hasAnswered = !!isComplete
  const displayEntries = getRuntimeDisplayEntries(entries)
  const hasFailed = displayEntries.some((entry) => entry.state === 'failed')
  const chipEntries = displayEntries.filter((entry, index) => {
    if (index === 0) return true
    return entry.state !== displayEntries[index - 1].state
  })
  const chips = chipEntries.map((entry, index) => {
    const label = getRuntimeStateLabel(entry.state)
    const activeClass = index === chipEntries.length - 1 ? ' active' : ''
    return `<span class="runtime-chip ${entry.state || ''}${activeClass}">${escapeHtml(label)}</span>`
  }).join('')
  const logs = displayEntries.map((entry, index) => {
    const label = getRuntimeStateLabel(entry.state)
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
    ? '<span class="runtime-state-icon done">?</span>'
    : !hasFailed
    ? '<span class="thinking-dots thinking-title-dots"><span>.</span><span>.</span><span>.</span></span>'
    : ''
  const titleElapsed = formatRuntimeHeaderElapsed(elapsedMs)
  const titleElapsedHtml = titleElapsed
    ? `<span class="runtime-title-elapsed">${escapeHtml(titleElapsed)}</span>`
    : ''
  const shouldOpen = typeof panelOpen === 'boolean' ? panelOpen : false
  const detailsAttrs = shouldOpen ? ' open' : ''
  const title = translateIfExists('chat.runtimeThinking') || 'Thinking'
  return `<details class="runtime-panel"${detailsAttrs}><summary><div class="runtime-summary-left"><span class="runtime-title">${escapeHtml(title)}</span>${titleIcon}${titleElapsedHtml}</div><div class="runtime-summary-right"><span class="runtime-toggle">></span></div></summary><div class="runtime-body">${chips ? `<div class="runtime-statuses">${chips}</div>` : ''}${logs ? `<div class="runtime-log">${logs}</div>` : ''}${thinkingHtml}</div></details>`
}

function formatElapsed(elapsedMs) {
  if (typeof elapsedMs !== 'number' || Number.isNaN(elapsedMs) || elapsedMs < 0) return ''
  if (elapsedMs < 1000) {
    return `${Math.max(1, Math.round(elapsedMs))}ms`
  }
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function buildMessageContent(
  runtimeEntries,
  thinkingContent,
  responseContent,
  elapsedMs = null,
  isThinking = false,
  panelOpen = null,
  isComplete = false,
  renderRevision = 0,
  workspaceDownloadReferences = [],
  objectActionReferences = []
) {
  const runtimeHtml = buildRuntimePanel(runtimeEntries, thinkingContent, elapsedMs, isThinking, panelOpen, isComplete)
  const downloadHtml = buildGeneratedWorkspaceDownloadsHtml(workspaceDownloadReferences, responseContent)
  const objectActionContext = createObjectActionRenderContext(objectActionReferences)
  const markdownHtml = responseContent ? renderAssistantMarkdown(responseContent, objectActionContext) : ''
  const objectActionActionsHtml = buildGeneratedObjectActionActionsHtml(objectActionContext)
  const responseBodyHtml = `${markdownHtml}${downloadHtml}${objectActionActionsHtml}`
  const responseHtml = responseBodyHtml
    ? `<div class="response-content">${responseBodyHtml}</div>`
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

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

function decodeWorkspacePath(value) {
  try {
    return decodeURIComponent(value)
  } catch (_error) {
    return value
  }
}

function isSafeWorkspaceRelativePath(path) {
  const normalized = String(path || '').trim().replace(/\\/g, '/')
  if (!normalized || normalized.startsWith('/') || normalized.startsWith('~')) return false
  if (/^[a-z][a-z0-9+.-]*:/i.test(normalized)) return false
  if (/^[a-z]:\//i.test(normalized)) return false
  const normalizedLower = normalized.toLowerCase()
  if (normalizedLower === '.atlasclaw' || normalizedLower.startsWith('.atlasclaw/')) return false
  return normalized.split('/').every((part) => part && part !== '.' && part !== '..')
}

function normalizeWorkspaceDownloadReference(rawValue) {
  const decoded = decodeHtmlEntities(rawValue).trim().replace(/\\/g, '/')
  if (!decoded) return null

  if (/^workspace:\/\//i.test(decoded)) {
    let path = decodeWorkspacePath(decoded.replace(/^workspace:\/\//i, ''))
    if (isSafeWorkspaceRelativePath(path)) {
      return { path: path.replace(/\\/g, '/') }
    }
    return null
  }

  if (/^[a-z][a-z0-9+.-]*:/i.test(decoded)) return null
  return null
}

function getWorkspaceDownloadDisplayName(path) {
  return path.split('/').filter(Boolean).pop() || 'download'
}

function buildWorkspaceDownloadAnchor(labelHtml, path) {
  const href = escapeHtml(buildWorkspaceFileDownloadUrl(path))
  const name = getWorkspaceDownloadDisplayName(path)
  const ariaLabel = escapeHtml(`Download ${name}`)
  return `<a href="${href}" class="workspace-download-link" download aria-label="${ariaLabel}">${WORKSPACE_DOWNLOAD_ICON}<span class="workspace-download-text">${labelHtml}</span></a>`
}

function renderWorkspaceDownloadLink(labelHtml, rawReference, options = {}) {
  const reference = normalizeWorkspaceDownloadReference(rawReference, options)
  if (!reference) return null
  const effectiveLabel = escapeHtml(getWorkspaceDownloadDisplayName(reference.path))
  return buildWorkspaceDownloadAnchor(effectiveLabel, reference.path)
}

function workspaceDownloadReferenceKey(reference) {
  if (!reference) return ''
  return reference.path
}

function parseWorkspaceDownloadPayload(rawPayload) {
  if (!rawPayload) return null
  if (typeof rawPayload === 'string') {
    const trimmed = rawPayload.trim()
    if (!trimmed || !/^[{[]/.test(trimmed)) return null
    try {
      return JSON.parse(trimmed)
    } catch (_error) {
      return null
    }
  }
  if (typeof rawPayload === 'object') return rawPayload
  return null
}

function normalizeWorkspaceDownloadArtifact(item) {
  if (!item) return null
  if (typeof item === 'string') {
    const reference = normalizeWorkspaceDownloadReference(item)
    return reference ? { ...reference, label: getWorkspaceDownloadDisplayName(reference.path) } : null
  }
  if (typeof item !== 'object') return null

  const rawReference = item.href || item.url || item.reference
  if (rawReference) {
    const reference = normalizeWorkspaceDownloadReference(String(rawReference))
    return reference ? {
      ...reference,
      label: getWorkspaceDownloadDisplayName(reference.path)
    } : null
  }

  const path = String(item.path || item.relative_path || '').trim()
  if (!isSafeWorkspaceRelativePath(path)) return null
  return {
    path: path.replace(/\\/g, '/'),
    label: getWorkspaceDownloadDisplayName(path)
  }
}

function normalizeWorkspaceDownloadArtifacts(downloads) {
  if (!Array.isArray(downloads)) return []
  const references = []
  const seen = new Set()
  for (const item of downloads) {
    const reference = normalizeWorkspaceDownloadArtifact(item)
    if (!reference) continue
    const key = workspaceDownloadReferenceKey(reference)
    if (seen.has(key)) continue
    seen.add(key)
    references.push(reference)
  }
  return references
}

function extractWorkspaceDownloadArtifacts(rawPayload) {
  const payload = parseWorkspaceDownloadPayload(rawPayload)
  if (!payload || typeof payload !== 'object') return []
  return normalizeWorkspaceDownloadArtifacts(payload.workspace_downloads || payload.workspaceDownloads)
}

function responseContentHasWorkspaceDownloadReference(responseContent, reference) {
  if (!reference) return false
  const targetKey = workspaceDownloadReferenceKey(reference)
  const content = String(responseContent || '')
  const normalizedContent = content.replace(/\\/g, '/')
  if (normalizedContent.includes(`workspace://${reference.path}`)) {
    return true
  }

  const markdownLinkPattern = /\[([^\]]+)\]\(([^)]+)\)/g
  let linkMatch = null
  while ((linkMatch = markdownLinkPattern.exec(content)) !== null) {
    const linkedReference = normalizeWorkspaceDownloadReference(linkMatch[2])
    if (linkedReference && workspaceDownloadReferenceKey(linkedReference) === targetKey) {
      return true
    }
  }

  return false
}

function buildGeneratedWorkspaceDownloadsHtml(references, responseContent) {
  if (!Array.isArray(references) || !references.length) return ''
  const anchors = references
    .filter((reference) => !responseContentHasWorkspaceDownloadReference(responseContent, reference))
    .map((reference) => {
      const label = escapeHtml(reference.label || getWorkspaceDownloadDisplayName(reference.path))
      return buildWorkspaceDownloadAnchor(label, reference.path)
    })
    .join('')
  return anchors ? `<div class="workspace-generated-downloads">${anchors}</div>` : ''
}

function normalizeObjectActionUrl(rawHref) {
  const candidate = String(rawHref || '').trim()
  if (!candidate) return null
  if (!/^https?:\/\//i.test(candidate)) return null
  try {
    const url = new URL(candidate)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    if (!url.hostname) return null
    return url.href
  } catch (_error) {
    return null
  }
}

function normalizeLocalizedText(rawValue) {
  if (!rawValue || typeof rawValue !== 'object') return null
  const defaultValue = String(rawValue.default || '').trim()
  if (!defaultValue) return null

  const normalized = { default: defaultValue }
  if (rawValue.translations && typeof rawValue.translations === 'object') {
    const translations = {}
    for (const [locale, text] of Object.entries(rawValue.translations)) {
      const localeKey = String(locale || '').trim()
      const localizedText = String(text || '').trim()
      if (localeKey && localizedText) {
        translations[localeKey] = localizedText
      }
    }
    if (Object.keys(translations).length) {
      normalized.translations = translations
    }
  }
  return normalized
}

function objectActionLocaleCandidates() {
  const rawLocale = String(getCurrentLocale?.() || '').trim()
  if (!rawLocale) return []

  const normalizedLocale = rawLocale.replace(/_/g, '-')
  const candidates = [rawLocale, normalizedLocale]
  const baseLocale = normalizedLocale.split('-')[0]
  if (baseLocale && baseLocale !== normalizedLocale) {
    candidates.push(baseLocale)
  }
  return Array.from(new Set(candidates))
}

function resolveLocalizedText(value) {
  const localized = normalizeLocalizedText(value)
  if (!localized) return ''

  const translations = localized.translations || {}
  for (const candidate of objectActionLocaleCandidates()) {
    if (Object.prototype.hasOwnProperty.call(translations, candidate)) {
      return translations[candidate]
    }
    const underscoreCandidate = candidate.replace(/-/g, '_')
    if (Object.prototype.hasOwnProperty.call(translations, underscoreCandidate)) {
      return translations[underscoreCandidate]
    }
  }
  return localized.default
}

function escapeRegExp(text) {
  return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function hasOwnObjectField(value, fieldName) {
  return !!value &&
    typeof value === 'object' &&
    Object.prototype.hasOwnProperty.call(value, fieldName)
}

function normalizeObjectAction(rawAction) {
  if (!rawAction || typeof rawAction !== 'object') return null
  const actionId = String(rawAction.action_id || '').trim()
  const kind = String(rawAction.kind || '').trim()
  if (!actionId || !['open_url', 'agent_prompt'].includes(kind)) {
    return null
  }

  const action = { action_id: actionId, kind }
  const stringFields = [
    'href',
    'effect',
    'tone'
  ]
  for (const field of stringFields) {
    const value = rawAction[field]
    if (value === undefined || value === null) continue
    const normalized = String(value).trim()
    if (normalized) action[field] = normalized
  }
  const localizedFields = [
    'display_label',
    'agent_prompt',
    'agent_prompt_template',
    'confirmation_message'
  ]
  for (const field of localizedFields) {
    const localized = normalizeLocalizedText(rawAction[field])
    if (localized) {
      action[field] = localized
    }
  }

  if (kind === 'open_url') {
    const href = normalizeObjectActionUrl(rawAction.href)
    if (!href) return null
    action.href = href
  } else if (kind === 'agent_prompt') {
    const prompt = resolveLocalizedText(action.agent_prompt) ||
      resolveLocalizedText(action.agent_prompt_template)
    if (!prompt) return null
  }

  if (typeof rawAction.requires_confirmation === 'boolean') {
    action.requires_confirmation = rawAction.requires_confirmation
  }
  if (Array.isArray(rawAction.inputs)) {
    action.inputs = rawAction.inputs
      .filter((input) => input && typeof input === 'object' && String(input.name || '').trim())
      .map((input) => {
        const normalized = { name: String(input.name || '').trim() }
        for (const field of ['type']) {
          const value = input[field]
          if (value === undefined || value === null) continue
          const text = String(value).trim()
          if (text) normalized[field] = text
        }
        for (const field of ['display_label', 'placeholder']) {
          const localized = normalizeLocalizedText(input[field])
          if (localized) {
            normalized[field] = localized
          }
        }
        if (typeof input.required === 'boolean') {
          normalized.required = input.required
        }
        return normalized
      })
  }
  return action
}

function normalizeObjectActionReference(context = {}) {
  const rawActions = Array.isArray(context.object_actions) ? context.object_actions : []
  const actions = []
  const seen = new Set()
  for (const rawAction of rawActions) {
    const action = normalizeObjectAction(rawAction)
    if (!action) continue
    const key = objectActionKey(action)
    if (!key || seen.has(key)) continue
    seen.add(key)
    actions.push(action)
  }
  if (!actions.length) return null

  const reference = { object_actions: actions }
  for (const field of OBJECT_ACTION_CONTEXT_FIELDS) {
    if (!hasOwnObjectField(context, field)) continue
    const value = context[field]
    if (value === undefined || value === null) continue
    if (!String(value).trim()) continue
    reference[field] = value
  }
  return reference
}

function objectActionReferenceKey(reference) {
  if (!reference) return ''
  const objectId = reference.object_id
  const identity = [
    reference.object_type,
    objectId,
    objectId ? '' : reference.object_name,
    reference.index
  ].map((value) => value === undefined || value === null ? '' : String(value).trim())
  const actionKeys = (reference.object_actions || []).map((action) => objectActionKey(action))
  return JSON.stringify([identity, actionKeys])
}

function objectActionKey(action) {
  if (!action) return ''
  return JSON.stringify({
    action_id: action.action_id || '',
    kind: action.kind || '',
    href: action.href || '',
    agent_prompt: action.agent_prompt || '',
    agent_prompt_template: action.agent_prompt_template || ''
  })
}

function normalizeObjectActionReferences(references) {
  if (!Array.isArray(references)) return []
  const normalizedReferences = []
  const seen = new Set()
  for (const item of references) {
    const reference = item && typeof item === 'object'
      ? normalizeObjectActionReference(item)
      : null
    const key = objectActionReferenceKey(reference)
    if (!key || seen.has(key)) continue
    seen.add(key)
    normalizedReferences.push(reference)
  }
  return normalizedReferences
}

function coerceObjectActionPayload(value) {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed || !/^[{[]/.test(trimmed)) return value
  try {
    return JSON.parse(trimmed)
  } catch (_error) {
    return value
  }
}

function collectObjectActionReferences(value, references) {
  const payload = coerceObjectActionPayload(value)
  if (!payload) return
  if (Array.isArray(payload)) {
    for (const item of payload) {
      collectObjectActionReferences(item, references)
    }
    return
  }
  if (typeof payload !== 'object') return

  if (Array.isArray(payload.object_actions)) {
    const reference = normalizeObjectActionReference(payload)
    if (reference) {
      references.push(reference)
    } else {
      for (const item of payload.object_actions) {
        collectObjectActionReferences(item, references)
      }
    }
  }

  for (const [key, childValue] of Object.entries(payload)) {
    if (key === 'object_actions' && normalizeObjectActionReference(payload)) continue
    collectObjectActionReferences(childValue, references)
  }
}

function extractObjectActionReferences(rawPayload) {
  const payload = parseWorkspaceDownloadPayload(rawPayload)
  if (!payload || typeof payload !== 'object') return []
  const references = []
  collectObjectActionReferences(payload, references)
  return normalizeObjectActionReferences(references)
}

function getObjectActionDisplayLabel(reference) {
  if (!reference) return ''
  const label = reference.object_name ||
    reference.object_id
  if (label !== undefined && label !== null && String(label).trim()) {
    return String(label).trim()
  }
  if (reference.index !== undefined && reference.index !== null && String(reference.index).trim()) {
    return `#${String(reference.index).trim()}`
  }
  return ''
}

function getObjectActionAriaLabel(reference) {
  const actionLabel = getTranslatedChatLabel('chat.objectActions', 'Actions')
  const displayLabel = getObjectActionDisplayLabel(reference) || actionLabel
  const template = getTranslatedChatLabel('chat.objectActionsAria', 'Actions for {{label}}')
  return template.replace('{{label}}', displayLabel)
}

function getObjectActionLabel(action) {
  const label = resolveLocalizedText(action?.display_label)
  if (label) {
    return label
  }
  if (action?.kind === 'open_url') {
    return getTranslatedChatLabel('chat.openObject', 'Open')
  }
  return String(action?.action_id || '').trim() || getTranslatedChatLabel('chat.objectAction', 'Action')
}

function getObjectActionPrompt(action) {
  return resolveLocalizedText(action?.agent_prompt) ||
    resolveLocalizedText(action?.agent_prompt_template)
}

function objectActionButtonClass(action) {
  const tone = String(action?.tone || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
  const openClass = action?.kind === 'open_url' ? ' object-action-open-button' : ''
  return `object-action-button${openClass}${tone ? ` tone-${tone}` : ''}`
}

function buildObjectActionAnchor(action, reference) {
  const href = normalizeObjectActionUrl(action?.href)
  if (!href) return ''
  const text = getObjectActionLabel(action)
  return `<a href="${escapeHtml(href)}" class="object-action-link" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(`${text} ${getObjectActionDisplayLabel(reference)}`.trim())}">${OBJECT_ACTION_ICON}<span class="object-action-text">${escapeHtml(text)}</span></a>`
}

function buildObjectActionButton(action, reference) {
  const prompt = getObjectActionPrompt(action)
  if (action?.kind === 'open_url') {
    const href = normalizeObjectActionUrl(action?.href)
    if (!href) return ''
  } else if (!prompt) {
    return ''
  }
  const payload = encodeURIComponent(JSON.stringify({ action, object: actionReferencePublicContext(reference) }))
  const text = getObjectActionLabel(action)
  const icon = action?.kind === 'open_url' ? OBJECT_ACTION_ICON : ''
  return `<button type="button" class="${objectActionButtonClass(action)}" data-object-action-payload="${escapeHtml(payload)}" aria-label="${escapeHtml(`${text} ${getObjectActionDisplayLabel(reference)}`.trim())}">${icon}<span class="object-action-text">${escapeHtml(text)}</span></button>`
}

function actionReferencePublicContext(reference) {
  const context = {}
  for (const field of OBJECT_ACTION_CONTEXT_FIELDS) {
    const value = reference?.[field]
    if (value === undefined || value === null || !String(value).trim()) continue
    context[field] = value
  }
  return context
}

function buildObjectActionControls(reference) {
  const actions = Array.isArray(reference?.object_actions) ? reference.object_actions : []
  const controls = actions.map((action) => {
    if (action.kind === 'open_url') {
      return objectActionNeedsInlineInteraction(action)
        ? buildObjectActionButton(action, reference)
        : buildObjectActionAnchor(action, reference)
    }
    return buildObjectActionButton(action, reference)
  }).filter(Boolean).join('')
  if (!controls) return ''
  return `<div class="object-actions" aria-label="${escapeHtml(getObjectActionAriaLabel(reference))}">${controls}</div>`
}

function objectActionNeedsInlineInteraction(action) {
  return !!action?.requires_confirmation || !!action?.inputs?.length
}

function getObjectActionConfirmationMessage(action) {
  const label = getObjectActionLabel(action)
  const confirmationMessage = resolveLocalizedText(action?.confirmation_message)
  if (!confirmationMessage && !action?.requires_confirmation && action?.inputs?.length) {
    const inputTemplate = getTranslatedChatLabel(
      'chat.objectActionInputTitle',
      'Provide information for {{label}}'
    )
    return inputTemplate.replace('{{label}}', label)
  }
  const template = confirmationMessage ||
    getTranslatedChatLabel('chat.objectActionConfirm', 'Confirm {{label}}?')
  return template.replace('{{label}}', label)
}

function getObjectActionInputLabel(input) {
  return resolveLocalizedText(input?.display_label) || String(input?.name || '').trim()
}

function getObjectActionInputPlaceholder(input) {
  return resolveLocalizedText(input?.placeholder) || ''
}

function resolveObjectActionPrompt(action, inputValues = {}) {
  let prompt = getObjectActionPrompt(action)
  if (!prompt) return { prompt: '', error: '' }

  // Providers own the action wording; core only fills declared placeholders and enforces required fields.
  for (const input of action.inputs || []) {
    const name = String(input?.name || '').trim()
    if (!name) continue
    const value = String(inputValues[name] ?? '').trim()
    if (input.required && !String(value).trim()) {
      return {
        prompt: '',
        error: getTranslatedChatLabel('chat.objectActionRequiredInput', 'This action requires input.')
      }
    }
    prompt = prompt.replace(new RegExp(`{{\\s*${escapeRegExp(name)}\\s*}}`, 'g'), String(value).trim())
  }

  return { prompt, error: '' }
}

function validateObjectActionInputs(action, inputValues = {}) {
  for (const input of action.inputs || []) {
    const name = String(input?.name || '').trim()
    if (!name) continue
    const value = String(inputValues[name] ?? '').trim()
    if (input.required && !value) {
      return getTranslatedChatLabel('chat.objectActionRequiredInput', 'This action requires input.')
    }
  }
  return ''
}

function resolveObjectActionHref(action, inputValues = {}) {
  const inputError = validateObjectActionInputs(action, inputValues)
  if (inputError) return { href: '', error: inputError }

  const href = normalizeObjectActionUrl(action?.href)
  if (!href) {
    return {
      href: '',
      error: getTranslatedChatLabel('chat.objectActionOpenFailed', 'Unable to open this action.')
    }
  }
  return { href, error: '' }
}

function openObjectActionHref(href) {
  const normalizedHref = normalizeObjectActionUrl(href)
  if (!normalizedHref) return false
  // With `noopener`, `window.open()` can return null even when the browser opens the tab.
  // A real anchor keeps the same security attributes without turning that null into a false failure.
  const anchor = document.createElement('a')
  anchor.href = normalizedHref
  anchor.target = '_blank'
  anchor.rel = 'noopener noreferrer'
  anchor.style.position = 'fixed'
  anchor.style.left = '-9999px'
  anchor.style.top = '0'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  return true
}

function setObjectActionGroupConfirming(actionGroup, confirming) {
  if (!actionGroup) return
  actionGroup.classList.toggle('is-confirming', confirming)
  for (const button of actionGroup.querySelectorAll('button.object-action-button')) {
    button.disabled = confirming
  }
}

function removeObjectActionConfirmationCards(parent) {
  if (!parent) return
  for (const child of Array.from(parent.children)) {
    if (child.classList?.contains('object-action-confirmation-card')) {
      child.remove()
    }
  }
}

function setObjectActionCardError(card, message) {
  const error = card?.querySelector('.object-action-confirmation-error')
  if (!error) return
  error.textContent = message || ''
  card.classList.toggle('has-error', !!message)
}

function collectObjectActionCardInputs(card, action) {
  const values = {}
  for (const input of action.inputs || []) {
    const name = String(input?.name || '').trim()
    if (!name) continue
    const field = Array.from(card.querySelectorAll('[data-object-action-input-name]'))
      .find((node) => node.dataset.objectActionInputName === name)
    values[name] = field?.value || ''
  }
  return values
}

function buildObjectActionInputControl(input) {
  const name = String(input?.name || '').trim()
  if (!name) return null
  const label = document.createElement('label')
  label.className = 'object-action-input-label'
  const labelText = document.createElement('span')
  labelText.textContent = getObjectActionInputLabel(input)
  label.appendChild(labelText)

  const field = String(input?.type || '').toLowerCase() === 'textarea'
    ? document.createElement('textarea')
    : document.createElement('input')
  field.className = field.tagName === 'TEXTAREA'
    ? 'object-action-textarea'
    : 'object-action-input'
  if (field.tagName !== 'TEXTAREA') {
    field.type = 'text'
  }
  field.dataset.objectActionInputName = name
  field.placeholder = getObjectActionInputPlaceholder(input)
  if (input.required) {
    field.required = true
  }
  label.appendChild(field)
  return label
}

function buildObjectActionConfirmationCard(action, actionGroup) {
  const card = document.createElement('div')
  card.className = 'object-action-confirmation-card'
  card.setAttribute('role', 'group')
  card.setAttribute('aria-live', 'polite')

  const title = document.createElement('div')
  title.className = 'object-action-confirmation-title'
  title.textContent = getObjectActionConfirmationMessage(action)
  card.appendChild(title)

  if (!action.inputs?.length) {
    const help = document.createElement('div')
    help.className = 'object-action-confirmation-help'
    help.textContent = getTranslatedChatLabel(
      'chat.objectActionConfirmHelp',
      'The action will be submitted after confirmation.'
    )
    card.appendChild(help)
  }

  if (action.inputs?.length) {
    const inputs = document.createElement('div')
    inputs.className = 'object-action-confirmation-inputs'
    for (const input of action.inputs) {
      const control = buildObjectActionInputControl(input)
      if (control) inputs.appendChild(control)
    }
    card.appendChild(inputs)
  }

  const error = document.createElement('div')
  error.className = 'object-action-confirmation-error'
  card.appendChild(error)

  const buttons = document.createElement('div')
  buttons.className = 'object-action-confirmation-buttons'

  const cancelButton = document.createElement('button')
  cancelButton.type = 'button'
  cancelButton.className = 'object-action-button object-action-cancel-button'
  cancelButton.textContent = getTranslatedChatLabel('chat.objectActionCancel', 'Cancel')
  cancelButton.addEventListener('click', () => {
    card.remove()
    setObjectActionGroupConfirming(actionGroup, false)
  })
  buttons.appendChild(cancelButton)

  const submitButton = document.createElement('button')
  submitButton.type = 'button'
  submitButton.className = objectActionButtonClass(action)
  const label = getObjectActionLabel(action)
  const submitTemplate = action.inputs?.length
    ? getTranslatedChatLabel('chat.objectActionSubmitWithLabel', 'Submit {{label}}')
    : getTranslatedChatLabel('chat.objectActionConfirmWithLabel', 'Confirm {{label}}')
  submitButton.textContent = submitTemplate.replace('{{label}}', label)
  submitButton.addEventListener('click', () => {
    const inputValues = collectObjectActionCardInputs(card, action)
    const resolvedAction = action.kind === 'open_url'
      ? resolveObjectActionHref(action, inputValues)
      : resolveObjectActionPrompt(action, inputValues)
    if (resolvedAction.error || (!resolvedAction.prompt && !resolvedAction.href)) {
      setObjectActionCardError(card, resolvedAction.error)
      return
    }
    setObjectActionCardError(card, '')
    card.classList.add('is-submitting')
    submitButton.textContent = getTranslatedChatLabel('chat.objectActionSubmitting', 'Submitting...')

    const restoreSubmissionState = (message = '') => {
      card.classList.remove('is-submitting')
      submitButton.textContent = submitTemplate.replace('{{label}}', label)
      setObjectActionCardError(card, message)
    }
    const submitFailedMessage = () => getTranslatedChatLabel(
      'chat.objectActionSubmitFailed',
      'Unable to submit action. Please try again.'
    )

    if (action.kind === 'open_url') {
      if (openObjectActionHref(resolvedAction.href)) {
        card.remove()
        setObjectActionGroupConfirming(actionGroup, false)
        return
      }
      restoreSubmissionState(
        getTranslatedChatLabel('chat.objectActionOpenFailed', 'Unable to open this action.')
      )
      console.warn('[ChatUI] Failed to open object action URL')
      return
    }

    if (!submitObjectActionPrompt(resolvedAction.prompt, {
      onRunCompleted: () => {
        if (!card.isConnected) return
        card.remove()
        setObjectActionGroupConfirming(actionGroup, false)
      },
      onRunFailed: () => {
        if (!card.isConnected) return
        restoreSubmissionState(submitFailedMessage())
      },
      onRunCreationFailed: () => {
        restoreSubmissionState(submitFailedMessage())
      }
    })) {
      restoreSubmissionState(submitFailedMessage())
      console.warn('[ChatUI] Failed to submit object action prompt')
    }
  })
  buttons.appendChild(submitButton)
  card.appendChild(buttons)
  return card
}

function showObjectActionInlineInteraction(target, action) {
  const actionGroup = target.closest('.object-actions')
  const parent = actionGroup?.parentElement
  if (!actionGroup || !parent) return false
  removeObjectActionConfirmationCards(parent)
  setObjectActionGroupConfirming(actionGroup, true)
  const card = buildObjectActionConfirmationCard(action, actionGroup)
  actionGroup.insertAdjacentElement('afterend', card)
  const firstField = card.querySelector('.object-action-textarea,.object-action-input')
  const focusTarget = firstField || card.querySelector('button.object-action-button')
  window.setTimeout(() => focusTarget?.focus?.(), 0)
  return true
}

function canSubmitObjectActionDirectly(element) {
  return !!element &&
    typeof element.addMessage === 'function' &&
    typeof element.handler === 'function' &&
    !!element.shadowRoot?.querySelector('#container')
}

function createObjectActionSignals(element) {
  let aiMessageStarted = false
  return {
    onResponse: (payload = {}) => {
      const html = payload.html || ''
      if (!html) return
      element.addMessage({
        role: 'ai',
        html,
        overwrite: aiMessageStarted
      })
      aiMessageStarted = true
    },
    onClose: () => {},
    stopClicked: { listener: null }
  }
}

function submitObjectActionDirectly(message, callbacks = {}) {
  const element = chatElement || document.querySelector('deep-chat')
  if (!canSubmitObjectActionDirectly(element)) return false
  window.setTimeout(() => {
    // Object actions are follow-up commands, not new visible user turns in the conversation history.
    void runAgentMessage(message, null, createObjectActionSignals(element), {
      visibleUserTurn: false,
      onRunCreated: callbacks.onRunCreated,
      onRunCompleted: callbacks.onRunCompleted,
      onRunFailed: callbacks.onRunFailed
    }).then((created) => {
      if (created === false && typeof callbacks.onRunCreationFailed === 'function') {
        callbacks.onRunCreationFailed()
      }
    }).catch((error) => {
      console.warn('[ChatUI] Object action submit failed:', error)
      if (typeof callbacks.onRunCreationFailed === 'function') {
        callbacks.onRunCreationFailed()
      }
    })
  }, 0)
  return true
}

function submitObjectActionPrompt(prompt, callbacks = {}) {
  const message = String(prompt || '').trim()
  if (!message) return false
  return submitObjectActionDirectly(message, callbacks)
}

function bindObjectActionHandlers(element = chatElement) {
  if (!element?.shadowRoot) {
    scheduleObjectActionHandlers(element)
    return false
  }

  setupObjectActionRootObserver(element)
  const container = getMessageContainerForElement(element)
  if (!container) {
    scheduleObjectActionHandlers(element)
    return false
  }
  if (container._objectActionClickBound) {
    element._objectActionContainer = container
    return true
  }
  container._objectActionClickBound = true
  container.addEventListener('click', (event) => {
    const target = event.target instanceof Element
      ? event.target.closest('button[data-object-action-payload]')
      : null
    if (!(target instanceof HTMLButtonElement)) return
    event.preventDefault()
    const encoded = target.getAttribute('data-object-action-payload') || ''
    try {
      const payload = JSON.parse(decodeURIComponent(encoded))
      const action = payload.action || {}
      if (objectActionNeedsInlineInteraction(action)) {
        if (!showObjectActionInlineInteraction(target, action)) {
          console.warn('[ChatUI] Failed to render object action confirmation')
        }
        return
      }
      const { prompt } = resolveObjectActionPrompt(action)
      if (!prompt) return
      if (!submitObjectActionPrompt(prompt)) {
        console.warn('[ChatUI] Failed to submit object action prompt')
      }
    } catch (error) {
      console.warn('[ChatUI] Invalid object action payload:', error)
    }
  }, true)
  element._objectActionContainer = container
  return true
}

function createObjectActionRenderContext(references) {
  return {
    references: normalizeObjectActionReferences(references),
    usedObjectActions: new Set()
  }
}

function markObjectActionUsed(context, reference) {
  const key = objectActionReferenceKey(reference)
  if (!context || !key) return
  context.usedObjectActions.add(key)
}

function buildGeneratedObjectActionActionsHtml(context) {
  if (!context?.references?.length) return ''
  const unmatchedReferences = context.references
    .filter((reference) => !context.usedObjectActions.has(objectActionReferenceKey(reference)))
  if (unmatchedReferences.length !== 1) return ''
  return buildObjectActionControls(unmatchedReferences[0])
}

function linkifyBareWorkspaceReferences(html) {
  return html.replace(/(^|[\s(])workspace:\/\/[^\s<>)`]+/gi, (match, prefix, offset) => {
    if (prefix === '(' && offset > 0 && html[offset - 1] === ']') {
      return match
    }
    const rawReference = match.slice(prefix.length)
    const link = renderWorkspaceDownloadLink(
      escapeHtml(rawReference),
      rawReference,
      { labelFromPath: true },
    )
    return link ? `${prefix}${link}` : match
  })
}

function stripWrapperHeading(text) {
  let normalized = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/^[\uFEFF\u200B\u200C\u200D\s]+/, '')
  if (!normalized.trim()) return ''
  const wrapperPattern = /^(answer|result|response|??|??|??)\s*[:?-]?$/i

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
  html = linkifyBareWorkspaceReferences(html)
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label, url) => {
    const workspaceLink = renderWorkspaceDownloadLink(label, url)
    if (workspaceLink) return workspaceLink
    const safeUrl = sanitizeLinkUrl(url)
    return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  return html
}

function splitMarkdownTableRow(line) {
  const raw = normalizeMarkdownTableLine(line)
  if (!raw.includes('|')) return null

  let row = raw
  if (row.startsWith('|')) row = row.slice(1)
  if (row.endsWith('|')) row = row.slice(0, -1)

  const cells = []
  let current = ''
  let escaped = false
  for (const char of row) {
    if (escaped) {
      current += char
      escaped = false
      continue
    }
    if (char === '\\') {
      escaped = true
      continue
    }
    if (char === '|') {
      cells.push(current.trim())
      current = ''
      continue
    }
    current += char
  }
  cells.push(current.trim())

  return cells.length >= 2 ? cells : null
}

function normalizeMarkdownTableLine(line) {
  const raw = String(line || '').trim()
  if (!raw.includes('|')) return raw

  const listRowMatch = /^[-*]\s+(.+\|.*)$/.exec(raw)
  if (listRowMatch) return listRowMatch[1].trim()

  return raw
}

function isMarkdownTableSeparator(cells) {
  return Array.isArray(cells) &&
    cells.length >= 2 &&
    cells.every((cell) => /^:?-{3,}:?$/.test(String(cell || '').trim()))
}

function normalizeObjectActionHeaderKey(value) {
  return decodeHtmlEntities(value)
    .replace(/[*_`]/g, '')
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, '')
}

function normalizeObjectActionMatchValue(value) {
  return decodeHtmlEntities(value)
    .replace(/<[^>]*>/g, '')
    .trim()
    .toLowerCase()
}

function parseObjectActionIndexValue(value) {
  const normalized = normalizeObjectActionMatchValue(value)
  if (!/^-?\d+$/.test(normalized)) return null
  return Number.parseInt(normalized, 10)
}

function isObjectActionHeader(header) {
  return normalizeObjectActionHeaderKey(header) === 'objectactions'
}

function isObjectActionDetailField(fieldName) {
  return isObjectActionHeader(fieldName)
}

function shouldSuppressRawObjectActionFields(objectActionContext) {
  return !!objectActionContext?.references?.length
}

function getObjectActionTableColumns(headerCells, suppressRawObjectActions = false) {
  return headerCells.map((header, index) => {
    const key = normalizeObjectActionHeaderKey(header)
    return {
      index,
      hidden: suppressRawObjectActions && key === 'objectactions',
      isIndex: OBJECT_ACTION_INDEX_HEADER_KEYS.has(key),
      isId: OBJECT_ACTION_ID_HEADER_KEYS.has(key),
      isName: OBJECT_ACTION_NAME_HEADER_KEYS.has(key)
    }
  })
}

function isObjectActionListTable(bodyRows, columns) {
  if (bodyRows.length > 1) return true
  return columns.some((column) => column.isIndex && !column.hidden)
}

function getObjectActionReferenceIndexValues(reference) {
  const indexValue = parseObjectActionIndexValue(reference?.index)
  return indexValue === null ? [] : [indexValue]
}

function getObjectActionReferenceIdValues(reference) {
  return [reference?.object_id]
    .filter((value) => value !== undefined && value !== null && String(value).trim())
    .map((value) => normalizeObjectActionMatchValue(value))
}

function getObjectActionReferenceNameValues(reference) {
  return [reference?.object_name]
    .filter((value) => value !== undefined && value !== null && String(value).trim())
    .map((value) => normalizeObjectActionMatchValue(value))
}

function countObjectActionValues(values) {
  return values.reduce((counts, value) => {
    if (!value) return counts
    counts.set(value, (counts.get(value) || 0) + 1)
    return counts
  }, new Map())
}

function buildObjectActionNameMatchContext(bodyRows, columns, references) {
  const nameColumns = columns.filter((column) => column.isName && !column.hidden)
  const rowNameValues = []
  for (const row of bodyRows) {
    for (const column of nameColumns) {
      const value = normalizeObjectActionMatchValue(row[column.index] || '')
      if (value) rowNameValues.push(value)
    }
  }
  const referenceNameValues = references.flatMap((reference) => getObjectActionReferenceNameValues(reference))
  return {
    rowCounts: countObjectActionValues(rowNameValues),
    referenceCounts: countObjectActionValues(referenceNameValues)
  }
}

function findObjectActionByIndexColumn(row, columns, context) {
  const indexColumns = columns.filter((column) => column.isIndex && !column.hidden)
  for (const column of indexColumns) {
    const rowIndex = parseObjectActionIndexValue(row[column.index] || '')
    if (rowIndex === null) continue
    const reference = context.references.find((candidate) => {
      const key = objectActionReferenceKey(candidate)
      return key &&
        !context.usedObjectActions.has(key) &&
        getObjectActionReferenceIndexValues(candidate).includes(rowIndex)
    })
    if (reference) return reference
  }
  return null
}

function findObjectActionByIdColumn(row, columns, context) {
  const idColumns = columns.filter((column) => column.isId && !column.hidden)
  for (const column of idColumns) {
    const rowId = normalizeObjectActionMatchValue(row[column.index] || '')
    if (!rowId) continue
    const reference = context.references.find((candidate) => {
      const key = objectActionReferenceKey(candidate)
      return key &&
        !context.usedObjectActions.has(key) &&
        getObjectActionReferenceIdValues(candidate).includes(rowId)
    })
    if (reference) return reference
  }
  return null
}

function findObjectActionByUniqueNameColumn(row, columns, context, nameMatchContext) {
  const nameColumns = columns.filter((column) => column.isName && !column.hidden)
  for (const column of nameColumns) {
    const rowName = normalizeObjectActionMatchValue(row[column.index] || '')
    if (!rowName || nameMatchContext.rowCounts.get(rowName) !== 1) continue
    if (nameMatchContext.referenceCounts.get(rowName) !== 1) continue
    const reference = context.references.find((candidate) => {
      const key = objectActionReferenceKey(candidate)
      return key &&
        !context.usedObjectActions.has(key) &&
        getObjectActionReferenceNameValues(candidate).includes(rowName)
    })
    if (reference) return reference
  }
  return null
}

function findObjectActionForTableRow(row, columns, context, nameMatchContext) {
  if (!context?.references?.length) return null
  return findObjectActionByIndexColumn(row, columns, context) ||
    findObjectActionByIdColumn(row, columns, context) ||
    findObjectActionByUniqueNameColumn(row, columns, context, nameMatchContext)
}

function getMarkdownTableCellLength(value) {
  return String(value || '')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .length
}

function shouldRenderCompactMarkdownTable(headerCells, bodyRows, visibleColumns, shouldRenderObjectActions) {
  if (shouldRenderObjectActions) return false
  if (!bodyRows.length || !visibleColumns.length) return false
  if (visibleColumns.length > 3) return false
  if (bodyRows.length > 8) return false

  const rowsToMeasure = [headerCells, ...bodyRows]
  let totalLength = 0
  let maxCellLength = 0
  let maxRowLength = 0
  for (const row of rowsToMeasure) {
    let rowLength = 0
    for (const column of visibleColumns) {
      const cellLength = getMarkdownTableCellLength(row[column.index] || '')
      rowLength += cellLength
      maxCellLength = Math.max(maxCellLength, cellLength)
    }
    totalLength += rowLength
    maxRowLength = Math.max(maxRowLength, rowLength)
  }

  return maxCellLength <= 32 && maxRowLength <= 72 && totalLength <= 220
}

function renderMarkdownTable(headerCells, bodyRows, objectActionContext = null) {
  const columns = getObjectActionTableColumns(
    headerCells,
    shouldSuppressRawObjectActionFields(objectActionContext)
  )
  const visibleColumns = columns.filter((column) => !column.hidden)
  const canMatchObjectActions = !!objectActionContext?.references?.length &&
    isObjectActionListTable(bodyRows, columns)
  const matchingContext = canMatchObjectActions
    ? {
      references: objectActionContext.references,
      usedObjectActions: new Set(objectActionContext.usedObjectActions)
    }
    : null
  const nameMatchContext = canMatchObjectActions
    ? buildObjectActionNameMatchContext(bodyRows, columns, objectActionContext.references)
    : { rowCounts: new Map(), referenceCounts: new Map() }
  const rowObjectActions = canMatchObjectActions
    ? bodyRows.map((row) => {
      const matchedObjectAction = findObjectActionForTableRow(
        row,
        columns,
        matchingContext,
        nameMatchContext
      )
      if (matchedObjectAction) {
        markObjectActionUsed(matchingContext, matchedObjectAction)
      }
      return matchedObjectAction
    })
    : []
  const shouldRenderObjectActions = rowObjectActions.some(Boolean)
  const tableSizeClass = shouldRenderCompactMarkdownTable(
    headerCells,
    bodyRows,
    visibleColumns,
    shouldRenderObjectActions
  )
    ? 'response-table-wrap-compact'
    : 'response-table-wrap-wide'
  const actionHeader = shouldRenderObjectActions
    ? `<th class="response-table-action-header">${escapeHtml(getTranslatedChatLabel('chat.objectActions', 'Actions'))}</th>`
    : ''
  const headerHtml = visibleColumns
    .map((column) => `<th>${renderInlineMarkdown(headerCells[column.index] || '')}</th>`)
    .join('') + actionHeader
  const rowsHtml = bodyRows.map((row, rowIndex) => {
    const matchedObjectAction = rowObjectActions[rowIndex] || null
    if (matchedObjectAction) {
      markObjectActionUsed(objectActionContext, matchedObjectAction)
    }
    const cells = visibleColumns.map((column) => {
      const cell = row[column.index] || ''
      const numberClass = /^-?\d+(?:\.\d+)?$/.test(cell) ? ' class="response-table-number"' : ''
      return `<td${numberClass}>${renderInlineMarkdown(cell)}</td>`
    }).join('')
    const actionCell = shouldRenderObjectActions
      ? `<td class="response-table-action">${matchedObjectAction ? buildObjectActionControls(matchedObjectAction) : ''}</td>`
      : ''
    return `<tr>${cells}${actionCell}</tr>`
  }).join('')

  return `<div class="response-table-wrap ${tableSizeClass}"><table class="response-table"><thead><tr>${headerHtml}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`
}

function renderAssistantMarkdown(text, objectActionContext = null) {
  const cleaned = stripWrapperHeading(text || '')
  const escaped = escapeHtml(cleaned).replace(/\r\n/g, '\n')
  if (!escaped.trim()) return ''

  const suppressRawObjectActions = shouldSuppressRawObjectActionFields(objectActionContext)
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
    const headerCells = splitMarkdownTableRow(line)
    const separatorCells = splitMarkdownTableRow(nextLine)
    if (headerCells && isMarkdownTableSeparator(separatorCells)) {
      flushParagraph()
      flushList()
      const bodyRows = []
      let rowIndex = index + 2
      while (rowIndex < lines.length) {
        const candidateLine = (lines[rowIndex] || '').trim()
        if (!candidateLine) break
        const rowCells = splitMarkdownTableRow(candidateLine)
        if (!rowCells || isMarkdownTableSeparator(rowCells) || rowCells.length < headerCells.length) break
        bodyRows.push(rowCells)
        rowIndex += 1
      }
      if (bodyRows.length) {
        htmlParts.push(renderMarkdownTable(headerCells, bodyRows, objectActionContext))
        index = rowIndex - 1
        continue
      }
    }

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

    const pipeFieldMatch = /^\|\s*(.+?)\s*[:?]\s*(.+)$/.exec(line)
    if (pipeFieldMatch) {
      if (suppressRawObjectActions && isObjectActionDetailField(pipeFieldMatch[1])) {
        continue
      }
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

    const objectActionFieldMatch = /^([^:?|]+?)\s*[:?]\s*(.+)$/.exec(line)
    if (
      suppressRawObjectActions &&
      objectActionFieldMatch &&
      isObjectActionDetailField(objectActionFieldMatch[1])
    ) {
      flushParagraph()
      flushList()
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
  let runtimePanelSyncTimer = null
  let runtimePanelSuppressClickUntil = 0
  let runtimeEntries = [{ state: 'reasoning', message: 'Starting response analysis.' }]
  let finalAnswerReady = false
  let serverRuntimeSeen = false
  let localRuntimeSeedTimers = []
  let assistantUpdateTimer = null
  let thinkingScrollTimer = null
  let streamSettled = false
  let streamHandler = null
  let renderRevision = 0
  let lastRenderedMessageSnapshot = null
  let lastRenderedMessageSignature = null
  let workspaceDownloadReferences = []
  let workspaceDownloadReferenceKeys = new Set()
  let objectActionReferences = []
  let objectActionReferenceKeys = new Set()

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

  function clearPendingRenderTimers() {
    if (assistantUpdateTimer) {
      clearTimeout(assistantUpdateTimer)
      assistantUpdateTimer = null
    }
    if (thinkingScrollTimer) {
      clearTimeout(thinkingScrollTimer)
      thinkingScrollTimer = null
    }
    assistantUpdatePending = false
    thinkingScrollPending = false
  }

  function clearActiveStreamHandler() {
    if (currentStreamHandler === streamHandler) {
      currentStreamHandler = null
    }
  }

  function cleanupStreamTimers() {
    clearLocalRuntimeSeedTimers()
    cancelRuntimePanelStateSync()
    clearPendingRenderTimers()
    stopThinkingTimer({ render: false })
    stopRunTimer()
  }

  function scheduleLocalEarlyRuntimePhases() {
    clearLocalRuntimeSeedTimers()
    localRuntimeSeedTimers = EARLY_RUNTIME_PHASES.map((phase) => setTimeout(() => {
      if (streamSettled || serverRuntimeSeen || finalAnswerReady) return
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

  function recordWorkspaceDownloadArtifacts(rawPayload) {
    const references = extractWorkspaceDownloadArtifacts(rawPayload)
    if (!references.length) return false
    let changed = false
    for (const reference of references) {
      const key = workspaceDownloadReferenceKey(reference)
      if (!key || workspaceDownloadReferenceKeys.has(key)) continue
      workspaceDownloadReferenceKeys.add(key)
      workspaceDownloadReferences = [...workspaceDownloadReferences, reference]
      changed = true
    }
    return changed
  }

  function recordObjectActionReferences(rawPayload, { replace = false } = {}) {
    const references = extractObjectActionReferences(rawPayload)
    if (replace) {
      const changed = serializeVisibleMessageSnapshot({ objectActions: objectActionReferences }) !==
        serializeVisibleMessageSnapshot({ objectActions: references })
      objectActionReferences = references
      objectActionReferenceKeys = new Set(references.map(objectActionReferenceKey).filter(Boolean))
      return changed
    }
    if (!references.length) return false
    let changed = false
    for (const reference of references) {
      const key = objectActionReferenceKey(reference)
      if (!key || objectActionReferenceKeys.has(key)) continue
      objectActionReferenceKeys.add(key)
      objectActionReferences = [...objectActionReferences, reference]
      changed = true
    }
    return changed
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

  function buildVisibleMessageSnapshot(panelShouldOpen) {
    return {
      runtimeEntries: runtimeEntries.map((entry) => ({
        state: entry.state || '',
        message: entry.message || ''
      })),
      thinkingContent,
      aiMessageContent,
      thinkingFinalized,
      finalAnswerReady,
      panelOpen: !!panelShouldOpen,
      workspaceDownloads: workspaceDownloadReferences.map((reference) => ({
        path: reference.path || '',
        label: reference.label || ''
      })),
      objectActions: objectActionReferences.map((reference) => ({
        object_type: reference.object_type || '',
        object_id: reference.object_id || '',
        object_name: reference.object_name || '',
        index: reference.index ?? '',
        object_actions: reference.object_actions || []
      }))
    }
  }

  function serializeVisibleMessageSnapshot(snapshot) {
    return JSON.stringify(snapshot || {})
  }

  function currentPanelShouldOpen() {
    if (runtimePanelUserOverride && typeof runtimePanelOpen === 'boolean') {
      return runtimePanelOpen
    }
    return autoPanelShouldOpen()
  }

  function cancelRuntimePanelStateSync() {
    if (runtimePanelSyncTimer) {
      clearTimeout(runtimePanelSyncTimer)
      runtimePanelSyncTimer = null
    }
  }

  function scheduleRuntimePanelStateSync(shouldOpen) {
    cancelRuntimePanelStateSync()
    runtimePanelSyncTimer = setTimeout(() => {
      runtimePanelSyncTimer = null
      const container = getMessageContainer()
      if (!container) return
      const details = getLatestRuntimePanel(container)
      applyRuntimePanelState(details, shouldOpen)
    }, 0)
  }

  function captureRenderedRuntimePanelState() {
    const renderedPanelOpen = readRenderedRuntimePanelOpen()
    if (typeof renderedPanelOpen !== 'boolean') return
    if (runtimePanelUserOverride) return
    const autoPanelOpen = autoPanelShouldOpen()
    if (renderedPanelOpen !== autoPanelOpen) {
      runtimePanelUserOverride = true
      runtimePanelOpen = renderedPanelOpen
    }
  }

  function toggleRuntimePanel(details, nextOpen) {
    cancelRuntimePanelStateSync()
    runtimePanelUserOverride = true
    runtimePanelOpen = !!nextOpen
    details.open = !!nextOpen
    if (lastRenderedMessageSnapshot) {
      lastRenderedMessageSnapshot = {
        ...lastRenderedMessageSnapshot,
        panelOpen: runtimePanelOpen
      }
      lastRenderedMessageSignature = serializeVisibleMessageSnapshot(lastRenderedMessageSnapshot)
    }
  }

  function refreshRenderedElapsed(elapsedMs) {
    const container = getMessageContainer()
    if (!container) return
    const panel = getLatestRuntimePanel(container)
    if (!panel) return
    const wrapper = panel.closest('.message-wrapper')
    if (!wrapper || wrapper.getAttribute('data-render-revision') !== String(renderRevision)) return

    const titleElapsed = panel.querySelector('.runtime-title-elapsed')
    const nextTitleElapsed = formatRuntimeHeaderElapsed(elapsedMs)
    if (titleElapsed && nextTitleElapsed && titleElapsed.textContent !== nextTitleElapsed) {
      titleElapsed.textContent = nextTitleElapsed
    }

    const displayEntries = getRuntimeDisplayEntries(runtimeEntries)
    const hasFailed = displayEntries.some((entry) => entry.state === 'failed')
    const timeNodes = panel.querySelectorAll('.runtime-log-time')
    displayEntries.forEach((entry, index) => {
      const timeNode = timeNodes[index]
      if (!timeNode) return
      const isActiveEntry = !finalAnswerReady && !hasFailed && index === displayEntries.length - 1
      const effectiveElapsedMs = (
        isActiveEntry && typeof elapsedMs === 'number' && !Number.isNaN(elapsedMs)
      )
        ? Math.max(entry.elapsedMs || 0, elapsedMs)
        : entry.elapsedMs
      const nextTime = formatElapsed(effectiveElapsedMs)
      if (nextTime && timeNode.textContent !== nextTime) {
        timeNode.textContent = nextTime
      }
    })
  }

  function bindRuntimePanelToggle() {
    const container = getMessageContainer()
    if (!container) return
    const resolveRuntimePanelDetails = (event) => {
      if (!(event.target instanceof Element)) return null
      const summary = event.target.closest('summary')
      if (!(summary instanceof HTMLElement)) return null
      const details = summary.parentElement
      if (!(details instanceof HTMLElement) || !details.matches('details.runtime-panel')) return null
      return { summary, details }
    }
    if (!container._runtimeMouseDownBound) {
      container._runtimeMouseDownBound = true
      container.addEventListener('mousedown', (event) => {
        if (typeof event.button === 'number' && event.button !== 0) return
        const resolved = resolveRuntimePanelDetails(event)
        if (!resolved) return
        event.preventDefault()
        runtimePanelSuppressClickUntil = Date.now() + 300
        toggleRuntimePanel(resolved.details, !resolved.details.open)
        resolved.summary.focus?.({ preventScroll: true })
      }, true)
    }
    if (!container._runtimeClickBound) {
      container._runtimeClickBound = true
      container.addEventListener('click', (event) => {
        const resolved = resolveRuntimePanelDetails(event)
        if (!resolved) return
        event.preventDefault()
        if (runtimePanelSuppressClickUntil > Date.now()) {
          return
        }
        toggleRuntimePanel(resolved.details, !resolved.details.open)
      }, true)
    }
  }

  function updateUI() {
    try {
      captureRenderedRuntimePanelState()
      const panelShouldOpen = currentPanelShouldOpen()
      const elapsedMs = currentElapsedMs()
      const nextMessageSnapshot = buildVisibleMessageSnapshot(panelShouldOpen)
      const nextMessageSignature = serializeVisibleMessageSnapshot(nextMessageSnapshot)
      if (nextMessageSignature === lastRenderedMessageSignature) {
        refreshRenderedElapsed(elapsedMs)
        return
      }
      renderRevision += 1
      const content = buildMessageContent(
        runtimeEntries,
        thinkingContent,
        aiMessageContent,
        elapsedMs,
        !thinkingFinalized,
        panelShouldOpen,
        finalAnswerReady,
        renderRevision,
        workspaceDownloadReferences,
        objectActionReferences
      )
      if (content.html) {
        lastRenderedMessageSnapshot = nextMessageSnapshot
        lastRenderedMessageSignature = nextMessageSignature
        signals.onResponse({ html: content.html, overwrite: true })
        scheduleRuntimePanelStateSync(panelShouldOpen)
        bindRuntimePanelToggle()
        bindObjectActionHandlers()
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

  function stopThinkingTimer({ render = true } = {}) {
    if (thinkingTimerInterval) {
      clearInterval(thinkingTimerInterval)
      thinkingTimerInterval = null
    }
    if (thinkingStartTime) {
      const clientElapsed = Math.round((Date.now() - thinkingStartTime) / 100) / 10
      if (thinkingElapsedSeconds <= 0.1) {
        thinkingElapsedSeconds = clientElapsed
      }
      if (render) {
        updateUI()
      }
    }
  }

  function startRunTimer() {
    if (runTimerInterval) return
    runTimerInterval = setInterval(() => {
      if (streamSettled) {
        stopRunTimer()
        return
      }
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
    bindRuntimePanelToggle()
    const settleAbortedStream = () => {
      if (streamSettled) return
      streamSettled = true
      thinkingFinalized = true
      cleanupStreamTimers()
      clearActiveStreamHandler()
      signals.onClose()
      resolve({ status: STREAM_RESULT_STATUS.aborted })
    }

    streamHandler = createStreamHandler(runId, {
      onStart: () => {
        if (streamSettled) return
        updateUI()
      },
      onDelta: (data) => {
        if (streamSettled) return
        if (!data.content) return
        if (!thinkingFinalized) {
          thinkingFinalized = true
          stopThinkingTimer()
        }
        aiMessageContent += data.content
        hasRenderedDelta = true
        if (!assistantUpdatePending) {
          assistantUpdatePending = true
          assistantUpdateTimer = setTimeout(() => {
            assistantUpdateTimer = null
            assistantUpdatePending = false
            if (streamSettled) return
            updateUI()
          }, 100)
        }
      },
      onToolStart: (data) => {
        if (streamSettled) return
        pushRuntimeEntry('tool_running', `Running tool: ${data?.tool_name || 'tool'}`, { phase: 'running_tool' })
        updateUI()
      },
      onToolEnd: (data) => {
        if (streamSettled) return
        recordWorkspaceDownloadArtifacts(data?.result)
        recordObjectActionReferences(data?.result)
        pushRuntimeEntry('waiting_for_tool', `Tool completed: ${data?.tool_name || 'tool'}`, { phase: 'tool_completed' })
        updateUI()
      },
      onThinkingStart: () => {
        if (streamSettled) return
        hasThinkingContent = true
        thinkingFinalized = false
        startThinkingTimer()
        userHasScrolledUp = false
        pushRuntimeEntry('reasoning', 'Collecting model reasoning.', { phase: 'thinking' })
        updateUI()
      },
      onThinkingDelta: (data) => {
        if (streamSettled) return
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
          thinkingScrollTimer = setTimeout(() => {
            thinkingScrollTimer = null
            thinkingScrollPending = false
            if (streamSettled) return
            updateUI()
          }, 80)
        }
      },
      onThinkingEnd: (data) => {
        if (streamSettled) return
        thinkingFinalized = true
        if (data?.elapsed && data.elapsed > 0) {
          thinkingElapsedSeconds = data.elapsed
        }
        stopThinkingTimer()
        pushRuntimeEntry('reasoning', 'Reasoning phase completed.', { phase: 'completed' })
        updateUI()
      },
      onRuntime: (data) => {
        if (streamSettled) return
        serverRuntimeSeen = true
        clearLocalRuntimeSeedTimers()
        const hasWorkspaceDownloads = recordWorkspaceDownloadArtifacts(data?.metadata || data)
        const objectActionPayload = data?.metadata || data
        const hasObjectActions = recordObjectActionReferences(
          objectActionPayload,
          { replace: objectActionPayload?.phase === 'object_actions' }
        )
        if (data?.metadata?.phase === 'workspace_downloads') {
          updateUI()
          return
        }
        pushRuntimeEntry(data.state, data.message, data.metadata || {})
        if (hasWorkspaceDownloads || hasObjectActions) {
          updateUI()
          return
        }
        updateUI()
      },
      onHeartbeat: () => {
        if (streamSettled) return
        refreshActiveRuntimeEntry()
        updateUI()
      },
      onEnd: () => {
        const doFinalRender = async () => {
          if (streamSettled) return
          streamSettled = true
          thinkingFinalized = true
          cleanupStreamTimers()
          if (!runtimeEntries.some((entry) => entry.state === STREAM_RESULT_STATUS.failed)) {
            if (aiMessageContent.trim() || workspaceDownloadReferences.length || objectActionReferences.length) {
              finalAnswerReady = true
            } else {
              pushRuntimeEntry('failed', 'Run ended without a usable answer.', { phase: 'completed' })
            }
          }
          const finalStatus = runtimeEntries.some((entry) => entry.state === STREAM_RESULT_STATUS.failed)
            ? STREAM_RESULT_STATUS.failed
            : STREAM_RESULT_STATUS.completed
          updateUI()
          await notifyRunCompleted(context.sessionKey)
          signals.onClose()
          clearActiveStreamHandler()
          resolve({ status: finalStatus })
        }
        setTimeout(() => {
          void doFinalRender()
        }, 200)
      },
      onAbort: () => {
        settleAbortedStream()
      },
      onError: async (error) => {
        if (streamSettled) return
        streamSettled = true
        thinkingFinalized = true
        cleanupStreamTimers()
        pushRuntimeEntry('failed', error?.message || 'Unknown error', { phase: 'error' })
        updateUI()
        await notifyRunCompleted(context.sessionKey)
        signals.onClose()
        clearActiveStreamHandler()
        resolve({ status: STREAM_RESULT_STATUS.failed, error })
      }
    })

    currentStreamHandler = streamHandler
    streamHandler.start()
  })
}

/**
 * Abort the active SSE stream and let the owning handler release timers before a session switch.
 */
export function abortCurrentStream() {
  if (currentStreamHandler) {
    const streamHandlerToAbort = currentStreamHandler
    currentStreamHandler = null
    streamHandlerToAbort.abort()
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
  focusChatInput,
  cancelChatInputFocusRetry,
  configureI18nAttributes
}
