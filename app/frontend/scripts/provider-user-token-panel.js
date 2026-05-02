/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { translateIfExists } from './i18n.js'
import { showToast } from './components/toast.js'

const USER_TOKEN_AUTH_TYPE = 'user_token'
const USER_TOKEN_FIELD = {
  name: 'user_token',
  type: 'password',
  label: 'User Token',
  label_i18n_key: 'provider.userToken',
  placeholder: 'Enter user token',
  placeholder_i18n_key: 'provider.userTokenPlaceholder',
  required: true,
  sensitive: true,
  auth_types: [USER_TOKEN_AUTH_TYPE]
}

const DEFAULT_TEXT_KEYS = {
  loadingTitle: ['account.providerTokensLoadingTitle', 'Loading provider tokens'],
  loadingDescription: ['account.providerTokensLoadingDescription', 'Checking provider instances that allow personal user tokens.'],
  errorTitle: ['account.providerTokensErrorTitle', 'Unable to load provider tokens'],
  emptyTitle: ['account.providerTokensEmptyTitle', 'No personal token providers available'],
  emptyDescription: ['account.providerTokensEmptyDescription', 'No provider instances currently allow user-owned user_token configuration.'],
  providerColumn: ['account.providerTokensProvider', 'Provider'],
  instanceColumn: ['account.providerTokensInstance', 'Instance'],
  statusColumn: ['account.providerTokensStatus', 'Personal Token'],
  updatedColumn: ['account.providerTokensUpdated', 'Updated'],
  modalTitle: ['account.providerTokenModalTitle', 'Set {{provider}} User Token'],
  modalDescription: ['account.providerTokenModalDescription', 'Set the personal token AtlasClaw should use for this provider instance.'],
  saving: ['account.providerTokensSaving', 'Saving...'],
  saved: ['account.providerTokenSaved', 'Provider token saved successfully'],
  saveFailed: ['account.providerTokenSaveFailed', 'Unable to save provider token']
}

let activeSave = false

function createProviderTokenState() {
  return {
    serviceProviders: [],
    providerDefinitions: {},
    userProviderConfigs: {},
    loading: false,
    error: '',
    modal: null
  }
}

export function createProviderUserTokenController(options = {}) {
  const config = {
    container: options.container,
    panelSelector: options.panelSelector,
    modalHostSelector: options.modalHostSelector,
    idPrefix: options.idPrefix || 'provider',
    configureAttribute: options.configureAttribute || 'data-provider-user-token-configure',
    closeAttribute: options.closeAttribute || 'data-provider-user-token-close',
    modalId: options.modalId || `${options.idPrefix || 'provider'}ProviderTokenModal`,
    formId: options.formId || `${options.idPrefix || 'provider'}ProviderTokenForm`,
    inputId: options.inputId || `${options.idPrefix || 'provider'}ProviderTokenInput`,
    saveButtonId: options.saveButtonId || `${options.idPrefix || 'provider'}SaveProviderTokenBtn`,
    textKeys: {
      ...DEFAULT_TEXT_KEYS,
      ...(options.textKeys || {})
    }
  }

  let state = createProviderTokenState()
  let saving = false
  let abortController = null
  let requestAbortController = null
  let loadGeneration = 0
  let disposed = false

  function getContainer() {
    return typeof config.container === 'function' ? config.container() : config.container
  }

  function text(name, params = {}) {
    const entry = config.textKeys[name] || DEFAULT_TEXT_KEYS[name]
    const [key, fallback] = Array.isArray(entry) ? entry : [entry, '']
    if (key) {
      const translated = translateIfExists(key, params)
      if (translated) {
        return translated
      }
    }
    return interpolate(fallback, params)
  }

  async function load() {
    const generation = ++loadGeneration
    requestAbortController?.abort()
    requestAbortController = new AbortController()

    state.loading = true
    state.error = ''
    render()

    try {
      const [serviceData, definitionData, userProviderData] = await Promise.all([
        requestJson('/api/service-providers/available-instances', {
          signal: requestAbortController.signal
        }),
        requestJson('/api/service-providers/definitions', {
          signal: requestAbortController.signal
        }),
        requestJson('/api/users/me/provider-settings', {
          signal: requestAbortController.signal
        })
      ])

      if (!isActiveLoad(generation)) {
        return
      }

      state.serviceProviders = Array.isArray(serviceData?.providers) ? serviceData.providers : []
      state.providerDefinitions = indexProviderDefinitions(definitionData?.providers)
      state.userProviderConfigs = typeof userProviderData?.providers === 'object' && userProviderData.providers
        ? userProviderData.providers
        : {}
    } catch (error) {
      if (!isActiveLoad(generation)) {
        return
      }
      state.error = error?.message || translateIfExists('provider.loadError') || 'Failed to load providers'
    } finally {
      if (!isActiveLoad(generation)) {
        return
      }
      requestAbortController = null
      state.loading = false
      render()
    }
  }

  function render() {
    const container = getContainer()
    if (!container) return

    const panel = container.querySelector(config.panelSelector)
    const modalHost = container.querySelector(config.modalHostSelector)
    if (panel) {
      panel.innerHTML = renderPanel()
    }
    if (modalHost) {
      modalHost.innerHTML = renderModal()
    }
  }

  function renderPanel() {
    if (state.loading) {
      return `
        <div class="account-provider-token-empty">
          <strong>${escapeHtml(text('loadingTitle'))}</strong>
          <span>${escapeHtml(text('loadingDescription'))}</span>
        </div>
      `
    }

    if (state.error) {
      return `
        <div class="account-provider-token-empty is-error">
          <strong>${escapeHtml(text('errorTitle'))}</strong>
          <span>${escapeHtml(state.error)}</span>
        </div>
      `
    }

    const rows = getRows()
    if (!rows.length) {
      return `
        <div class="account-provider-token-empty">
          <strong>${escapeHtml(text('emptyTitle'))}</strong>
          <span>${escapeHtml(text('emptyDescription'))}</span>
        </div>
      `
    }

    return `
      <div class="account-provider-token-table-wrap">
        <table class="account-provider-token-table">
          <thead>
            <tr>
              <th>${escapeHtml(text('providerColumn'))}</th>
              <th>${escapeHtml(text('instanceColumn'))}</th>
              <th>${escapeHtml(text('statusColumn'))}</th>
              <th>${escapeHtml(text('updatedColumn'))}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(renderRow).join('')}
          </tbody>
        </table>
      </div>
    `
  }

  function renderRow(row) {
    const statusLabel = row.configured
      ? translateIfExists('provider.statusConfigured') || 'Configured'
      : translateIfExists('provider.notConfigured') || 'Not configured'
    const statusClass = row.configured ? 'is-configured' : 'is-missing'
    const actionLabel = row.configured
      ? translateIfExists('provider.updateCredentialsShort') || 'Update'
      : translateIfExists('provider.configureCredentialsShort') || 'Configure'

    return `
      <tr>
        <td>
          <strong>${escapeHtml(row.providerName)}</strong>
        </td>
        <td>${escapeHtml(row.instanceName)}</td>
        <td><span class="account-provider-token-status ${statusClass}">${escapeHtml(statusLabel)}</span></td>
        <td><span class="${row.updatedLabel === '--' ? 'account-muted-cell' : ''}">${escapeHtml(row.updatedLabel)}</span></td>
        <td class="account-provider-token-action-cell">
          <button
            type="button"
            class="account-provider-token-configure-btn"
            ${config.configureAttribute}
            data-provider-type="${escapeHtml(row.providerType)}"
            data-instance-name="${escapeHtml(row.instanceName)}"
          >${escapeHtml(actionLabel)}</button>
        </td>
      </tr>
    `
  }

  function renderModal() {
    if (!state.modal?.open) {
      return ''
    }

    const modal = state.modal
    const meta = getProviderMeta(modal.providerType)
    const field = getProviderUserTokenField(modal.providerType)
    const hasStoredToken = Boolean(getUserProviderEntry(modal.providerType, modal.instanceName)?.configured)
    const placeholder = hasStoredToken
      ? translateIfExists('provider.secretUpdatePlaceholder') || 'Enter a new value to update'
      : getSchemaFieldPlaceholder(field)
    const required = field.required && !hasStoredToken ? 'required' : ''
    const title = text('modalTitle', { provider: meta.name })

    return `
      <div id="${escapeHtml(config.modalId)}" class="modal-overlay">
        <div class="modal account-provider-token-modal">
          <div class="modal-header">
            <div>
              <h2>${escapeHtml(title)}</h2>
              <p class="modal-description">${escapeHtml(text('modalDescription'))}</p>
            </div>
            <button type="button" class="modal-close" ${config.closeAttribute} aria-label="${escapeHtml(translateIfExists('provider.close') || 'Close')}">&times;</button>
          </div>
          <form id="${escapeHtml(config.formId)}" novalidate>
            <div class="modal-body">
              <div class="account-provider-token-modal-context">
                <span>${escapeHtml(text('providerColumn'))}</span>
                <strong>${escapeHtml(meta.name)}</strong>
                <span>${escapeHtml(text('instanceColumn'))}</span>
                <strong>${escapeHtml(modal.instanceName)}</strong>
              </div>
              <label class="account-field">
                <span>${escapeHtml(getSchemaFieldLabel(field))}</span>
                <input id="${escapeHtml(config.inputId)}" type="password" name="user_token" value="" placeholder="${escapeHtml(placeholder)}" autocomplete="off" ${required}>
              </label>
              ${modal.error ? `<p class="account-provider-token-error">${escapeHtml(modal.error)}</p>` : ''}
            </div>
            <div class="modal-footer">
              <button type="button" class="btn-secondary" ${config.closeAttribute}>${escapeHtml(translateIfExists('provider.cancel') || 'Cancel')}</button>
              <button type="submit" class="btn-primary" id="${escapeHtml(config.saveButtonId)}">${escapeHtml(translateIfExists('provider.saveCredentials') || 'Save')}</button>
            </div>
          </form>
        </div>
      </div>
    `
  }

  function openModal(providerType, instanceName) {
    state.modal = {
      open: true,
      providerType,
      instanceName,
      error: ''
    }
    render()
    getContainer()?.querySelector(`#${cssEscape(config.inputId)}`)?.focus()
  }

  function closeModal() {
    state.modal = null
    render()
  }

  async function saveModal() {
    if (saving || activeSave || disposed || !state.modal) {
      return
    }

    const container = getContainer()
    const modal = state.modal
    const input = container?.querySelector(`#${cssEscape(config.inputId)}`)
    const saveBtn = container?.querySelector(`#${cssEscape(config.saveButtonId)}`)
    const userToken = String(input?.value || '').trim()
    const existingEntry = getUserProviderEntry(modal.providerType, modal.instanceName)

    if (!existingEntry?.configured && !userToken) {
      state.modal.error = translateIfExists('provider.requiredFields') || 'User token is required.'
      render()
      return
    }

    saving = true
    activeSave = true
    if (saveBtn) {
      saveBtn.disabled = true
      saveBtn.textContent = text('saving')
    }

    try {
      await requestJson('/api/users/me/provider-settings', {
        method: 'PUT',
        body: JSON.stringify({
          provider_type: modal.providerType,
          instance_name: modal.instanceName,
          config: userToken ? { user_token: userToken } : {}
        })
      })

      if (disposed) {
        return
      }

      state.modal = null
      await load()
      if (disposed) {
        return
      }
      showToast(text('saved'), 'success')
    } catch (error) {
      if (disposed) {
        return
      }
      state.modal = {
        ...modal,
        error: error?.message || text('saveFailed')
      }
      showToast(state.modal.error, 'error')
      render()
    } finally {
      saving = false
      activeSave = false
    }
  }

  function handleClick(event) {
    const configureButton = event.target.closest(`[${config.configureAttribute}]`)
    if (configureButton) {
      openModal(
        configureButton.dataset.providerType || '',
        configureButton.dataset.instanceName || ''
      )
      return
    }

    if (event.target.closest(`[${config.closeAttribute}]`)) {
      closeModal()
      return
    }

    const overlay = event.target.closest(`#${cssEscape(config.modalId)}`)
    if (overlay && event.target === overlay) {
      closeModal()
    }
  }

  async function handleSubmit(event) {
    if (!event.target.matches(`#${cssEscape(config.formId)}`)) {
      return
    }

    event.preventDefault()
    await saveModal()
  }

  function handleKeydown(event) {
    if (event.key === 'Escape') {
      closeModal()
    }
  }

  function bind() {
    const container = getContainer()
    if (!container || abortController) return

    disposed = false
    abortController = new AbortController()
    const { signal } = abortController
    container.addEventListener('click', handleClick, { signal })
    container.addEventListener('submit', handleSubmit, { signal })
    document.addEventListener('keydown', handleKeydown, { signal })
  }

  function destroy() {
    disposed = true
    loadGeneration += 1
    requestAbortController?.abort()
    requestAbortController = null
    abortController?.abort()
    abortController = null
    state = createProviderTokenState()
    saving = false
  }

  function isActiveLoad(generation) {
    return !disposed && generation === loadGeneration
  }

  function getRows() {
    return state.serviceProviders
      .filter((entry) => entry?.provider_type && authChainIncludesUserToken(entry.auth_type))
      .map((entry) => {
        const providerType = String(entry.provider_type)
        const instanceName = String(entry.instance_name || '')
        const userEntry = getUserProviderEntry(providerType, instanceName)
        const meta = getProviderMeta(providerType)
        return {
          providerType,
          providerName: meta.name,
          instanceName,
          configured: Boolean(userEntry?.configured),
          updatedLabel: formatProviderTimestamp(userEntry?.updated_at)
        }
      })
      .sort((left, right) => {
        const nameSort = left.providerName.localeCompare(right.providerName)
        if (nameSort) return nameSort
        const typeSort = left.providerType.localeCompare(right.providerType)
        if (typeSort) return typeSort
        return left.instanceName.localeCompare(right.instanceName)
      })
  }

  function getProviderMeta(providerType) {
    const fallbackName = String(providerType || 'provider')
      .replace(/[-_]+/g, ' ')
      .replace(/\b\w/g, (segment) => segment.toUpperCase())

    const definition = state.providerDefinitions[providerType]
    if (!definition) {
      return { name: fallbackName }
    }

    return {
      name: translateProviderToken(definition.name_i18n_key || '', definition.display_name || fallbackName)
    }
  }

  function getUserProviderEntry(providerType, instanceName) {
    const providerBucket = state.userProviderConfigs?.[providerType]
    if (!providerBucket || typeof providerBucket !== 'object') {
      return null
    }

    const entry = providerBucket[instanceName]
    return entry && typeof entry === 'object' ? entry : null
  }

  function getProviderSchemaFields(providerType) {
    const fields = state.providerDefinitions[providerType]?.schema?.fields
    return Array.isArray(fields) ? fields : []
  }

  function getProviderUserTokenField(providerType) {
    return getProviderSchemaFields(providerType).find((field) => {
      if (field?.name !== USER_TOKEN_FIELD.name) {
        return false
      }
      const authTypes = normalizeAuthTypeChain(field?.auth_types || [])
      return !authTypes.length || authTypes.includes(USER_TOKEN_AUTH_TYPE)
    }) || USER_TOKEN_FIELD
  }

  function getSchemaFieldLabel(field) {
    return translateProviderToken(field.label_i18n_key || '', field.label || field.name || '')
  }

  function getSchemaFieldPlaceholder(field) {
    return translateProviderToken(field.placeholder_i18n_key || '', field.placeholder || '')
  }

  return {
    bind,
    closeModal,
    destroy,
    getRows,
    load,
    render
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })

  if (!response.ok) {
    let message = `Request failed: ${response.status}`
    try {
      const payload = await response.json()
      message = payload.detail || payload.message || payload.error || message
    } catch {
      // Keep status fallback for non-JSON responses.
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return {}
  }

  return response.json()
}

function indexProviderDefinitions(definitions) {
  if (!Array.isArray(definitions)) {
    return {}
  }

  return Object.fromEntries(
    definitions
      .filter((item) => item?.provider_type)
      .map((item) => [item.provider_type, item])
  )
}

function normalizeAuthTypeChain(value) {
  const rawValues = Array.isArray(value) ? value : [value]
  const chain = []
  for (const item of rawValues) {
    const normalized = String(item || '').trim().toLowerCase()
    if (normalized && !chain.includes(normalized)) {
      chain.push(normalized)
    }
  }
  return chain
}

function authChainIncludesUserToken(value) {
  return normalizeAuthTypeChain(value).includes(USER_TOKEN_AUTH_TYPE)
}

function translateProviderToken(key, fallback, params = {}) {
  return translateIfExists(key, params) || interpolate(fallback, params)
}

function formatProviderTimestamp(value) {
  if (!value) {
    return '--'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value)
  }

  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}`
}

function interpolate(value, params = {}) {
  return String(value || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => {
    return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : ''
  })
}

function cssEscape(value) {
  if (window.CSS?.escape) {
    return window.CSS.escape(value)
  }
  return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&')
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}
