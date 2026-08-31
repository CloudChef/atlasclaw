/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { getCurrentLocale, translateIfExists } from './i18n.js'
import { showToast } from './components/toast.js'

function text(key, fallback) {
  return translateIfExists(`account.${key}`) || fallback
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function formatDate(value) {
  if (!value) return text('accessTokenNeverUsed', 'Never')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString(getCurrentLocale())
}

function tokenMetadata(token) {
  const { token: plaintext, ...metadata } = token || {}
  return metadata
}

async function responseJson(response, fallback) {
  let payload = null
  try {
    payload = await response.json()
  } catch {
    // Keep the stable fallback when an intermediary returns a non-JSON error page.
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || fallback)
  }
  return payload
}

/**
 * Manage long-lived opaque API tokens owned by the current Agent administrator.
 * Plaintext values are held only in memory after creation and are
 * never restored by the metadata list endpoint.
 *
 * @param {{container: HTMLElement, panelSelector: string}} options panel host configuration
 * @returns {{bind: Function, load: Function, destroy: Function}} lifecycle controller
 */
export function createAccessTokenController({ container, panelSelector }) {
  let tokens = []
  let oneTimeToken = ''
  let loading = true
  let error = ''
  let disposed = false
  let submitting = false
  let pendingTokenId = ''

  const panel = () => container?.querySelector(panelSelector)

  function renderRows() {
    if (!tokens.length) {
      return `
        <div class="account-provider-token-empty">
          <strong>${escapeHtml(text('accessTokensEmptyTitle', 'No access tokens'))}</strong>
          <span>${escapeHtml(text('accessTokensEmptyDescription', 'Create a token for administrative API access.'))}</span>
        </div>
      `
    }

    return `
      <div class="account-provider-token-table-wrap">
        <table class="account-provider-token-table account-access-token-table">
          <thead>
            <tr>
              <th>${escapeHtml(text('accessTokenName', 'Name'))}</th>
              <th>${escapeHtml(text('accessTokenHint', 'Token hint'))}</th>
              <th>${escapeHtml(text('accessTokenLastUsed', 'Last used'))}</th>
              <th>${escapeHtml(text('accessTokenCreated', 'Created'))}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${tokens.map(token => {
              const revoked = Boolean(token.revoked_at)
              return `
              <tr>
                <td>
                  <strong>${escapeHtml(token.name)}</strong>
                  ${revoked ? `<span class="account-access-token-revoked">${escapeHtml(text('accessTokenRevoked', 'Revoked'))}</span>` : ''}
                </td>
                <td><code>${escapeHtml(token.token_hint)}</code></td>
                <td>${escapeHtml(formatDate(token.last_used_at))}</td>
                <td>${escapeHtml(formatDate(token.created_at))}</td>
                <td class="account-provider-token-action-cell">
                  ${revoked
                    ? `<span class="account-muted-cell">${escapeHtml(formatDate(token.revoked_at))}</span>`
                    : `<button type="button" class="account-access-token-action is-danger" data-access-token-revoke="${escapeHtml(token.id)}" ${pendingTokenId ? 'disabled' : ''}>${escapeHtml(text('accessTokenRevoke', 'Revoke'))}</button>`}
                </td>
              </tr>
            `
            }).join('')}
          </tbody>
        </table>
      </div>
    `
  }

  function render() {
    const host = panel()
    if (!host || disposed) return

    const oneTimePanel = oneTimeToken
      ? `
        <div class="account-access-token-secret">
          <div>
            <strong>${escapeHtml(text('accessTokenCopyTitle', 'Copy this token now'))}</strong>
            <span>${escapeHtml(text('accessTokenCopyDescription', 'It will not be shown again.'))}</span>
          </div>
          <code id="accountAccessTokenPlaintext">${escapeHtml(oneTimeToken)}</code>
          <button type="button" class="btn-secondary" data-access-token-copy>${escapeHtml(text('accessTokenCopy', 'Copy'))}</button>
          <button type="button" class="account-access-token-dismiss" data-access-token-dismiss aria-label="${escapeHtml(text('accessTokenDismiss', 'Dismiss'))}">&times;</button>
        </div>
      `
      : ''

    const content = loading
      ? `<div class="account-provider-token-empty"><strong>${escapeHtml(text('accessTokensLoading', 'Loading access tokens'))}</strong></div>`
      : error
        ? `<div class="account-provider-token-empty is-error"><strong>${escapeHtml(text('accessTokensLoadFailed', 'Unable to load access tokens'))}</strong><span>${escapeHtml(error)}</span></div>`
        : renderRows()

    host.innerHTML = `
      <form id="accountAccessTokenCreateForm" class="account-access-token-create" novalidate>
        <label class="account-field">
          <span>${escapeHtml(text('accessTokenName', 'Name'))}</span>
          <input id="accountAccessTokenName" type="text" maxlength="100" required placeholder="${escapeHtml(text('accessTokenNamePlaceholder', 'For example, automation client'))}">
        </label>
        <button type="submit" class="btn-primary" ${submitting ? 'disabled' : ''}>${escapeHtml(submitting ? text('accessTokenCreating', 'Creating...') : text('accessTokenCreate', 'Create token'))}</button>
      </form>
      ${oneTimePanel}
      ${content}
    `
  }

  async function load() {
    loading = true
    error = ''
    render()
    try {
      const response = await fetch('/api/access-tokens')
      const payload = await responseJson(response, text('accessTokensLoadFailed', 'Unable to load access tokens'))
      tokens = Array.isArray(payload?.tokens) ? payload.tokens.map(tokenMetadata) : []
    } catch (loadError) {
      error = loadError.message
    } finally {
      loading = false
      render()
    }
  }

  async function createToken() {
    const name = panel()?.querySelector('#accountAccessTokenName')?.value.trim()
    if (!name || submitting) return
    submitting = true
    render()
    try {
      const response = await fetch('/api/access-tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
      const created = await responseJson(response, text('accessTokenCreateFailed', 'Unable to create access token'))
      oneTimeToken = created.token
      tokens = [tokenMetadata(created), ...tokens.filter(token => token.id !== created.id)]
      showToast(text('accessTokenCreatedToast', 'Access token created'), 'success')
    } catch (createError) {
      showToast(createError.message, 'error')
    } finally {
      submitting = false
      render()
    }
  }

  async function revokeToken(tokenId) {
    if (pendingTokenId || !window.confirm(text('accessTokenRevokeConfirm', 'Revoke this token? It will stop working immediately.'))) return
    pendingTokenId = tokenId
    render()
    try {
      const response = await fetch(`/api/access-tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' })
      const revoked = await responseJson(response, text('accessTokenRevokeFailed', 'Unable to revoke access token'))
      tokens = tokens.map(token => token.id === tokenId ? revoked : token)
    } catch (revokeError) {
      showToast(revokeError.message, 'error')
    } finally {
      pendingTokenId = ''
      render()
    }
  }

  async function handleClick(event) {
    const copyButton = event.target.closest('[data-access-token-copy]')
    if (copyButton && oneTimeToken) {
      try {
        await navigator.clipboard.writeText(oneTimeToken)
        showToast(text('accessTokenCopiedToast', 'Access token copied'), 'success')
      } catch {
        showToast(text('accessTokenCopyFailed', 'Unable to copy automatically'), 'error')
      }
      return
    }
    if (event.target.closest('[data-access-token-dismiss]')) {
      oneTimeToken = ''
      render()
      return
    }
    const revokeButton = event.target.closest('[data-access-token-revoke]')
    if (revokeButton) {
      await revokeToken(revokeButton.dataset.accessTokenRevoke)
    }
  }

  function handleSubmit(event) {
    if (event.target.id !== 'accountAccessTokenCreateForm') return
    event.preventDefault()
    createToken()
  }

  function bind() {
    panel()?.addEventListener('click', handleClick)
    panel()?.addEventListener('submit', handleSubmit)
  }

  function destroy() {
    panel()?.removeEventListener('click', handleClick)
    panel()?.removeEventListener('submit', handleSubmit)
    disposed = true
    oneTimeToken = ''
  }

  return { bind, load, destroy }
}
