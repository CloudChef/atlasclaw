/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { resolveEmbedContext } from '../api-client.js?v=32'
import { EmbedContextBridge } from './context-bridge.js?v=36'
import { EmbedContextStore } from './context-store.js?v=35'
import { renderObjectContextBar } from './components/object-context-bar.js?v=2'
import { renderContextObjectActions } from '../chat-ui.js?v=45'

export const EMBED_CONTEXT_RESOLVE_DEBOUNCE_MS = 500

/**
 * Coordinate PAGE_CHANGED resolution and the optional floating Context UI.
 */
export class EmbedContextController {
  /**
   * @param {object} options - Bootstrap-approved surface and DOM slots.
   * @param {object} options.surface - Parsed floating surface.
   * @param {HTMLElement} options.contextSlot - Object context slot.
   * @param {HTMLElement} options.actionSlot - Optional object actions slot.
   * @param {HTMLElement|null} options.closeButton - Collapse control.
   */
  constructor({ surface, contextSlot, actionSlot, closeButton = null }) {
    this.surface = surface
    this.contextSlot = contextSlot
    this.actionSlot = actionSlot
    this.store = new EmbedContextStore()
    this.bridge = new EmbedContextBridge({
      hostOrigin: surface.hostOrigin,
      nonce: surface.nonce,
      onPageChanged: (page) => this._handlePageChanged(page)
    })
    this.unsubscribe = this.store.subscribe((context, status) => this._render(context, status))
    this.closeHandler = () => this.bridge.closeFloatingAssistant()
    closeButton?.addEventListener('click', this.closeHandler)
    this.closeButton = closeButton
    this.resolveTimer = null
    this.resolveAbortController = null
    this.destroyed = false
  }

  /** Start the origin-checking message bridge after bootstrap validates the surface nonce. */
  start() {
    return this.bridge.start()
  }

  /** Release bridge, DOM and subscription resources on page unmount. */
  destroy() {
    this.destroyed = true
    this._cancelScheduledResolve()
    this._abortActiveResolve()
    this.bridge.stop()
    this.unsubscribe?.()
    this.closeButton?.removeEventListener('click', this.closeHandler)
    renderObjectContextBar(this.contextSlot, null, null)
    renderContextObjectActions(this.actionSlot, null)
  }

  /** @returns {object|null} Current optional Agent Turn context. */
  getTurnContext() {
    return this.store.getTurnContext()
  }

  /** Clear transient confirmation/submission UI while preserving current page actions. */
  resetActionInteraction() {
    this._render(this.store.current, this.store.status)
  }

  _handlePageChanged({ generation, path }) {
    if (this.destroyed) return false
    // beginResolve clears the visible object synchronously; an older async
    // response can therefore never repopulate actions after navigation.
    const pending = this.store.beginResolve(generation)
    if (!pending) return false

    this._cancelScheduledResolve()
    this._abortActiveResolve()
    this.resolveTimer = setTimeout(() => {
      this.resolveTimer = null
      void this._resolvePageContext({ generation, path })
    }, EMBED_CONTEXT_RESOLVE_DEBOUNCE_MS)
    return true
  }

  async _resolvePageContext({ generation, path }) {
    if (this.destroyed || generation !== this.store.latestGeneration) return

    const abortController = new AbortController()
    this.resolveAbortController = abortController

    try {
      const payload = await resolveEmbedContext({
        surfaceId: this.surface.nonce,
        generation,
        path
      }, {
        signal: abortController.signal
      })
      this.store.completeResolve(generation, payload)
    } catch (error) {
      if (!abortController.signal.aborted) {
        console.warn('[EmbedContext] Context resolution failed:', error)
      }
      this.store.completeResolve(generation, null)
    } finally {
      if (this.resolveAbortController === abortController) {
        this.resolveAbortController = null
      }
    }
  }

  _cancelScheduledResolve() {
    if (this.resolveTimer === null) return
    clearTimeout(this.resolveTimer)
    this.resolveTimer = null
  }

  _abortActiveResolve() {
    this.resolveAbortController?.abort()
    this.resolveAbortController = null
  }

  _render(context, status) {
    renderObjectContextBar(
      this.contextSlot,
      context?.object || null,
      context?.skill || null,
      status
    )
    const turnContext = context ? Object.freeze({
      embed_context_id: context.contextId,
      context_generation: context.generation
    }) : null
    renderContextObjectActions(
      this.actionSlot,
      context
        ? {
            object_type: context.object?.type,
            object_id: context.object?.id,
            object_name: context.object?.name,
            object_actions: context.objectActions
          }
        : null,
      {
        turnContext,
        isContextCurrent: (candidate) => this.store.isCurrent(candidate),
        onRunCreationError: (message) => {
          if (turnContext && this.store.isCurrent(turnContext)) {
            this._showActionError(message)
          }
        }
      }
    )
  }

  _showActionError(message) {
    if (!this.actionSlot) return
    let alert = this.actionSlot.querySelector('.object-action-submit-alert')
    if (!alert) {
      alert = this.actionSlot.ownerDocument.createElement('div')
      alert.className = 'object-action-submit-alert'
      alert.setAttribute('role', 'alert')
      this.actionSlot.appendChild(alert)
    }
    alert.textContent = String(message || 'Unable to submit action. Please try again.')
  }
}
