import { getEmbedModeValue, getEmbedParentOrigin, isEmbedMode } from './embed-mode.js'

const HOST_SOURCE = 'cmp-atlasclaw-host'
const IFRAME_SOURCE = 'atlasclaw'

let bridgeEnabled = false
let allowedOrigin = '*'
let stateProvider = null
let handlers = {}

function postToHost(type, payload = {}) {
  if (!bridgeEnabled || window.parent === window) {
    return
  }

  window.parent.postMessage(
    {
      source: IFRAME_SOURCE,
      type,
      payload
    },
    allowedOrigin
  )
}

function handleHostMessage(event) {
  if (!bridgeEnabled) {
    return
  }

  if (allowedOrigin !== '*' && event.origin !== allowedOrigin) {
    return
  }

  const data = event.data || {}
  if (data.source !== HOST_SOURCE) {
    return
  }

  const payload = data.payload || {}

  switch (data.type) {
    case 'cmp:request-state':
      publishEmbedState()
      break
    case 'cmp:new-session':
      handlers.startNewSession?.()
      break
    case 'cmp:activate-session':
      handlers.activateSession?.(payload.sessionKey || '')
      break
    case 'cmp:delete-session':
      handlers.deleteSession?.(payload.sessionKey || '')
      break
    default:
      break
  }
}

export function initEmbedBridge({ getState, nextHandlers = {} } = {}) {
  if (!isEmbedMode() || window.parent === window) {
    return () => {}
  }

  stateProvider = getState || null
  handlers = nextHandlers
  allowedOrigin = getEmbedParentOrigin()

  if (!bridgeEnabled) {
    bridgeEnabled = true
    window.addEventListener('message', handleHostMessage)
  }

  postToHost('atlasclaw:ready', { mode: getEmbedModeValue() })
  publishEmbedState()

  return destroyEmbedBridge
}

export function publishEmbedState(nextState = null) {
  if (!bridgeEnabled) {
    return
  }

  const payload = nextState || (typeof stateProvider === 'function' ? stateProvider() : {})
  postToHost('atlasclaw:state', payload || {})
}

export function destroyEmbedBridge() {
  if (!bridgeEnabled) {
    return
  }

  window.removeEventListener('message', handleHostMessage)
  bridgeEnabled = false
  allowedOrigin = '*'
  stateProvider = null
  handlers = {}
}
