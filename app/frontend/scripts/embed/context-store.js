/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Hold the current floating page snapshot and prevent stale resolve responses
 * from repopulating context after a newer Host generation arrives.
 */
export class EmbedContextStore {
  constructor() {
    this.latestGeneration = -1
    this.current = null
    this.pending = null
    this.status = 'idle'
    this.listeners = new Set()
  }

  /**
   * Start resolving one newer Host generation and synchronously clear old UI.
   *
   * @param {number} generation - Monotonically increasing Host generation.
   * @returns {{generation: number, resolve: Function}|null} Pending handle.
   */
  beginResolve(generation) {
    if (!Number.isSafeInteger(generation) || generation < 0 || generation <= this.latestGeneration) {
      return null
    }
    this.latestGeneration = generation
    this.current = null
    this.status = 'pending'
    const pending = { generation }
    this.pending = pending
    this._notify()
    return pending
  }

  /**
   * Apply a resolve response only while it still belongs to the latest generation.
   *
   * @param {number} generation - Echoed response generation.
   * @param {object|null} payload - Validated API payload or null degradation.
   * @returns {boolean} Whether the response was applied.
   */
  completeResolve(generation, payload) {
    const pending = this.pending
    if (!pending || pending.generation !== generation || generation !== this.latestGeneration) {
      return false
    }
    if (payload?.generation !== generation) {
      this.pending = null
      this.status = 'unavailable'
      this._notify()
      return false
    }
    const responseStatus = payload?.status
    const object = payload?.object || null
    const contextId = payload?.context_id || null
    const resolved = responseStatus === 'resolved' && object && contextId
    // Freeze the resolved view because action callbacks may retain it while a
    // later PAGE_CHANGED event is already clearing the current generation.
    this.current = resolved
      ? Object.freeze({
          generation,
          contextId,
          expiresAt: payload.expires_at || null,
          object: Object.freeze({ ...object }),
          skill: payload.skill && typeof payload.skill === 'object'
            ? Object.freeze({ ...payload.skill })
            : null,
          objectActions: Object.freeze(
            Array.isArray(payload.object_actions)
              ? payload.object_actions.map((action) => Object.freeze({ ...action }))
              : []
          )
        })
      : null
    this.status = resolved
      ? 'resolved'
      : (responseStatus === 'unsupported' ? 'unsupported' : 'unavailable')
    this.pending = null
    this._notify()
    return true
  }

  /**
   * Return the current resolved context without delaying or controlling Chat.
   *
   * @returns {object|null} Minimal Agent Run context fields.
   */
  getTurnContext() {
    return this.current?.generation === this.latestGeneration
      ? toTurnContext(this.current)
      : null
  }

  /** @returns {object|null} Current immutable snapshot view. */
  getCurrent() {
    return this.current
  }

  /** @returns {string} Current resolution status for UI scope coordination. */
  getStatus() {
    return this.status
  }

  /**
   * Check whether an action still targets the exact visible Host generation.
   *
   * @param {object|null} turnContext - Captured Embed context identity.
   * @returns {boolean} Whether the captured context is still current.
   */
  isCurrent(turnContext) {
    return !!this.current &&
      this.current.contextId === turnContext?.embed_context_id &&
      this.current.generation === turnContext?.context_generation
  }

  /**
   * Subscribe to context changes.
   * @param {(state: object|null) => void} listener - Change callback.
   * @returns {Function} Unsubscribe callback.
   */
  subscribe(listener) {
    this.listeners.add(listener)
    listener(this.current)
    return () => this.listeners.delete(listener)
  }

  _notify() {
    this.listeners.forEach((listener) => listener(this.current, this.status))
  }

}

function toTurnContext(context) {
  return {
    embed_context_id: context.contextId,
    context_generation: context.generation
  }
}
