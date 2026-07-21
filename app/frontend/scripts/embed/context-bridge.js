/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

const PROTOCOL = 'atlasclaw-embed/v1'

/**
 * Floating iframe message bridge. Authentication still comes from the shared
 * Host Cookie; the URL-declared exact Host origin and nonce bind only this
 * iframe message channel.
 */
export class EmbedContextBridge {
  /**
   * @param {object} options - Validated bootstrap and window dependencies.
   * @param {Window} options.windowRef - iframe window.
   * @param {string} options.hostOrigin - Exact Host origin parsed from the iframe URL.
   * @param {string} options.nonce - Host-generated iframe nonce.
   * @param {(page: {generation: number, path: string}) => void} options.onPageChanged - Page callback.
   */
  constructor({ windowRef = window, hostOrigin, nonce, onPageChanged }) {
    this.windowRef = windowRef
    this.hostOrigin = hostOrigin
    this.nonce = nonce
    this.onPageChanged = onPageChanged
    this.boundMessage = (event) => this._handleMessage(event)
    this.started = false
  }

  /** Start listening and notify the exact parent origin that the bridge is ready. */
  start() {
    if (this.started || !this._isConfigured()) return false
    this.started = true
    this.windowRef.addEventListener('message', this.boundMessage)
    this._post('ATLASCLAW_READY')
    return true
  }

  /** Stop bridge listeners. */
  stop() {
    if (!this.started) return
    this.windowRef.removeEventListener('message', this.boundMessage)
    this.started = false
  }

  /** Request the Host to collapse the floating assistant. */
  closeFloatingAssistant() {
    this._post('CLOSE_FLOATING_ASSISTANT')
  }

  _handleMessage(event) {
    if (!this.started || event.source !== this.windowRef.parent || event.origin !== this.hostOrigin) return
    const data = event.data
    if (!isPlainObject(data) || data.protocol !== PROTOCOL) return
    if (data.type !== 'PAGE_CHANGED') return
    if (data.nonce !== this.nonce || !Number.isSafeInteger(data.generation) || data.generation < 0) return
    if (!isNormalizedHostPath(data.path)) return
    // Reject legacy or injected fields instead of silently extending the Host
    // protocol with client-controlled identity, session, or object metadata.
    if (Object.keys(data).some((key) => ![
      'protocol', 'type', 'nonce', 'generation', 'path'
    ].includes(key))) return
    this.onPageChanged?.({ generation: data.generation, path: data.path })
  }

  _post(type) {
    if (!this._isConfigured()) return
    this.windowRef.parent.postMessage({
      protocol: PROTOCOL,
      type,
      nonce: this.nonce
    }, this.hostOrigin)
  }

  _isConfigured() {
    return !!this.hostOrigin && !!this.nonce &&
      this.windowRef.parent && this.windowRef.parent !== this.windowRef
  }
}

export { PROTOCOL as EMBED_PROTOCOL }

function isNormalizedHostPath(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') ||
    path.includes('//') || /[?#\\]/.test(path) || /[\u0000-\u001f]/.test(path)) return false
  return path.split('/').slice(1).every((segment) => {
    try {
      const decoded = decodeURIComponent(segment)
      return decoded !== '.' && decoded !== '..' && !/[\\/]/.test(decoded) &&
        !/[\u0000-\u001f]/.test(decoded) && !/%[0-9a-f]{2}/i.test(decoded)
    } catch {
      return false
    }
  })
}

function isPlainObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}
