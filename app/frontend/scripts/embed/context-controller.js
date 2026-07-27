/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { resolveEmbedContext } from '../api-client.js?v=31'
import { EmbedContextBridge } from './context-bridge.js?v=36'
import { EmbedContextStore } from './context-store.js?v=33'
import { renderObjectContextBar } from './components/object-context-bar.js'
import { renderContextObjectActions } from '../chat-ui.js?v=43'
import { setSlashCapabilityPageScopeActive } from '../slash-picker.js?v=27'

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
  }

  /** Start the origin-checking message bridge after bootstrap validates the surface nonce. */
  start() {
    return this.bridge.start()
  }

  /** Release bridge, DOM and subscription resources on page unmount. */
  destroy() {
    this.bridge.stop()
    this.unsubscribe?.()
    this.closeButton?.removeEventListener('click', this.closeHandler)
    renderObjectContextBar(this.contextSlot, null)
    renderContextObjectActions(this.actionSlot, null)
    setSlashCapabilityPageScopeActive(false)
  }

  /** @returns {Promise<object|null>} Send-time minimal Agent Turn context. */
  async getTurnContext() {
    return this.store.getTurnContext(500)
  }

  /** Clear transient confirmation/submission UI while preserving current page actions. */
  resetActionInteraction() {
    this._render(this.store.current, this.store.status)
  }

  async _handlePageChanged({ generation, path }) {
    // beginResolve clears the visible object synchronously; an older async
    // response can therefore never repopulate actions after navigation.
    const pending = this.store.beginResolve(generation)
    if (!pending) return
    try {
      const payload = await resolveEmbedContext({
        surfaceId: this.surface.nonce,
        generation,
        path
      })
      this.store.completeResolve(generation, payload)
    } catch (error) {
      console.warn('[EmbedContext] Context resolution failed:', error)
      this.store.completeResolve(generation, null)
    }
  }

  _render(context, status) {
    // A matched-but-unavailable page remains page-scoped and fail-closed.
    // Only an unsupported page returns to unrestricted ordinary Chat behavior.
    setSlashCapabilityPageScopeActive(
      status === 'pending' || status === 'resolved' || status === 'unavailable',
      this.contextSlot?.ownerDocument?.getElementById('chat') || null
    )
    renderObjectContextBar(this.contextSlot, context?.object || null)
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
