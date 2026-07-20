/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { resolveEmbedContext } from '../api-client.js?v=27'
import { EmbedContextBridge } from './context-bridge.js'
import { EmbedContextStore } from './context-store.js?v=27'
import { renderObjectContextBar } from './components/object-context-bar.js'
import { renderObjectActionReferences } from '../chat-ui.js?v=28'
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
   * @param {HTMLElement|null} options.openButton - Expand control.
   * @param {HTMLElement|null} options.closeButton - Collapse control.
   */
  constructor({ surface, contextSlot, actionSlot, openButton = null, closeButton = null }) {
    this.surface = surface
    this.contextSlot = contextSlot
    this.actionSlot = actionSlot
    this.store = new EmbedContextStore()
    this.bridge = new EmbedContextBridge({
      integrationId: surface.integrationId,
      hostOrigin: surface.hostOrigin,
      nonce: surface.nonce,
      onPageChanged: (page) => this._handlePageChanged(page)
    })
    this.unsubscribe = this.store.subscribe((context, status) => this._render(context, status))
    this.openHandler = () => this.bridge.openFullAgent()
    this.closeHandler = () => this.bridge.closeFloatingAssistant()
    openButton?.addEventListener('click', this.openHandler)
    closeButton?.addEventListener('click', this.closeHandler)
    this.openButton = openButton
    this.closeButton = closeButton
  }

  /** Start the message bridge after bootstrap validates Origin and nonce. */
  start() {
    return this.bridge.start()
  }

  /** Release bridge, DOM and subscription resources on page unmount. */
  destroy() {
    this.bridge.stop()
    this.unsubscribe?.()
    this.openButton?.removeEventListener('click', this.openHandler)
    this.closeButton?.removeEventListener('click', this.closeHandler)
    renderObjectContextBar(this.contextSlot, null)
    renderObjectActionReferences(this.actionSlot, [])
    setSlashCapabilityPageScopeActive(false)
  }

  /** @returns {Promise<object|null>} Send-time minimal Agent Turn context. */
  async getTurnContext() {
    const context = await this.store.getTurnContext(500)
    return context ? {
      ...context,
      integration_id: this.surface.integrationId
    } : null
  }

  async _handlePageChanged({ generation, path }) {
    const pending = this.store.beginResolve(generation)
    if (!pending) return
    try {
      const payload = await resolveEmbedContext({
        integrationId: this.surface.integrationId,
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
    setSlashCapabilityPageScopeActive(
      status === 'pending' || status === 'resolved' || status === 'unavailable',
      this.contextSlot?.ownerDocument?.getElementById('chat') || null
    )
    renderObjectContextBar(this.contextSlot, context?.object || null)
    const turnContext = context ? Object.freeze({
      embed_context_id: context.contextId,
      context_generation: context.generation,
      integration_id: this.surface.integrationId
    }) : null
    renderObjectActionReferences(this.actionSlot, context?.objectActions || [], {
      turnContext,
      isContextCurrent: (candidate) => (
        candidate?.integration_id === this.surface.integrationId &&
        this.store.isCurrent(candidate)
      ),
      exclusiveSubmission: true,
      onRunCreationError: (message) => {
        if (turnContext && this.store.isCurrent(turnContext)) {
          this._showActionError(message)
        }
      }
    })
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
