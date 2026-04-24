/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * providers.js - Service Provider Authentication Configuration Page
 *
 * Personal provider tokens are managed against fixed provider instances.
 * Ordinary users only see provider instances that allow user-owned user_token.
 */

import { showToast } from '../components/toast.js'
import { translateIfExists } from '../i18n.js'

const PROVIDER_ORDER = ['smartcmp', 'dingtalk']
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

let pageContainer = null
let clickHandler = null
let changeHandler = null
let submitHandler = null
let state = createInitialState()
let isSaving = false

function createInitialState() {
  return {
    serviceProviders: [],
    providerDefinitions: {},
    userProviderConfigs: {},
    loading: false,
    error: '',
    modal: null
  }
}

function tr(key, fallback, params = {}) {
  return translateIfExists(key, params) || fallback
}

export async function mount(container) {
  pageContainer = container
  bindEvents()
  await refreshData()
}

export async function unmount() {
  if (pageContainer && clickHandler) {
    pageContainer.removeEventListener('click', clickHandler)
    pageContainer.removeEventListener('change', changeHandler)
    pageContainer.removeEventListener('submit', submitHandler)
  }

  pageContainer = null
  clickHandler = null
  changeHandler = null
  submitHandler = null
  state = createInitialState()
  isSaving = false
}

function bindEvents() {
  clickHandler = async (event) => {
    const configureButton = event.target.closest('[data-configure-template]')
    if (configureButton) {
      openConfigureModal(
        configureButton.dataset.providerType || '',
        configureButton.dataset.instanceName || ''
      )
      return
    }

    if (event.target.closest('[data-close-modal]')) {
      closeModal()
      return
    }

    const overlay = event.target.closest('#providerModal')
    if (overlay && event.target === overlay) {
      closeModal()
    }
  }

  changeHandler = (event) => {
    if (!state.modal || !event.target.matches('#providerModalForm select[name="instance_name"]')) {
      return
    }

    state.modal.instanceName = event.target.value
    state.modal.values = getInitialCredentialValues(state.modal.providerType, state.modal.instanceName)
    state.modal.error = ''
    render()
  }

  submitHandler = async (event) => {
    if (!event.target.matches('#providerModalForm')) {
      return
    }

    event.preventDefault()
    await saveModal()
  }

  pageContainer.addEventListener('click', clickHandler)
  pageContainer.addEventListener('change', changeHandler)
  pageContainer.addEventListener('submit', submitHandler)
}

async function refreshData() {
  state.loading = true
  state.error = ''
  render()

  try {
    const [serviceData, definitionData, userProviderData] = await Promise.all([
      requestJson('/api/service-providers/available-instances'),
      requestJson('/api/service-providers/definitions'),
      requestJson('/api/users/me/provider-settings')
    ])

    state.serviceProviders = Array.isArray(serviceData?.providers) ? serviceData.providers : []
    state.providerDefinitions = indexProviderDefinitions(definitionData?.providers)
    state.userProviderConfigs = typeof userProviderData?.providers === 'object' && userProviderData.providers
      ? userProviderData.providers
      : {}
  } catch (error) {
    state.error = error?.message || tr('provider.loadError', 'Failed to load providers')
  } finally {
    state.loading = false
    render()
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
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        message = payload.detail
      } else if (typeof payload?.message === 'string' && payload.message.trim()) {
        message = payload.message
      } else if (typeof payload?.error === 'string' && payload.error.trim()) {
        message = payload.error
      }
    } catch (_error) {
      // Keep status fallback for non-JSON responses.
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return {}
  }

  return response.json()
}

function render() {
  if (!pageContainer) {
    return
  }

  pageContainer.innerHTML = `
    <div class="pv-page">
      <div class="pv-page-header">
        <h1 data-i18n="provider.pageTitle">${tr('provider.pageTitle', 'Authentication Configuration')}</h1>
        <p data-i18n="provider.subtitle">${tr('provider.subtitle', 'Set your personal provider tokens for workspace-managed instances.')}</p>
      </div>
      ${renderProviderSections()}
      ${renderModal()}
    </div>
  `
}

function renderProviderSections() {
  const providerTypes = getProviderTypes()

  if (state.loading) {
    return `
      <div class="pv-empty">
        <strong data-i18n="provider.loadingTitle">${tr('provider.loadingTitle', 'Loading provider inventory')}</strong>
        <span data-i18n="provider.loadingDescription">${tr('provider.loadingDescription', 'Syncing provider instances and personal credential status.')}</span>
      </div>
    `
  }

  if (!providerTypes.length) {
    return `
      <div class="pv-empty">
        <strong data-i18n="provider.emptyTitle">${tr('provider.emptyTitle', 'No personal token providers available')}</strong>
        <span data-i18n="provider.emptyDescription">${tr('provider.emptyDescription', 'No provider instances currently allow user-owned user_token configuration.')}</span>
      </div>
    `
  }

  return `
    <div class="pv-sections">
      ${providerTypes.map((providerType) => renderProviderSection(providerType)).join('')}
      ${state.error ? `<div class="pv-inline-note"><span class="pv-inline-note-label" data-i18n="provider.errorLabel">${tr('provider.errorLabel', 'Error')}</span>${escapeHtml(state.error)}</div>` : ''}
    </div>
  `
}

function renderProviderSection(providerType) {
  const meta = getProviderMeta(providerType)
  const rows = getTemplateRows(providerType)

  return `
    <section class="pv-panel pv-provider-section" data-provider-section data-provider-type="${escapeHtml(providerType)}">
      <div class="pv-panel-header compact">
        <div>
          <h2 class="pv-panel-title">${escapeHtml(meta.name)}</h2>
          <p>${escapeHtml(meta.description)}</p>
        </div>
      </div>
      ${renderTemplatesTable(rows)}
    </section>
  `
}

function renderTemplatesTable(rows) {
  if (!rows.length) {
    return `
      <div class="pv-empty compact">
        <strong data-i18n="provider.noInstancesTitle">${tr('provider.noInstancesTitle', 'No instances for this provider')}</strong>
        ${tr('provider.noInstancesDescription', 'Add a provider instance in')} <code>atlasclaw.json</code>.
      </div>
    `
  }

  return `
    <div class="pv-table-wrap">
      <table class="pv-table">
        <thead>
          <tr>
            <th>${tr('provider.tableInstance', 'Instance')}</th>
            <th>${tr('provider.tableToken', 'Personal Token')}</th>
            <th>${tr('provider.tableUpdated', 'Updated')}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(renderTemplateRow).join('')}
        </tbody>
      </table>
    </div>
  `
}

function renderTemplateRow(row) {
  const actionLabel = tr('provider.configureCredentialsShort', 'Configure')

  return `
    <tr>
      <td>
        <div class="pv-instance-cell">
          <div class="pv-instance-heading">
            <strong>${escapeHtml(row.instanceName)}</strong>
          </div>
        </div>
      </td>
      <td>${renderUserTokenStatus(row.configured)}</td>
      <td><span class="${row.updatedLabel === '--' ? 'pv-cell-muted' : ''}">${escapeHtml(row.updatedLabel)}</span></td>
      <td class="pv-table-action-cell">
        <div class="pv-actions">
          <button
            class="btn-small pv-row-action-btn"
            type="button"
            data-configure-template
            data-provider-type="${escapeHtml(row.providerType)}"
            data-instance-name="${escapeHtml(row.instanceName)}"
          >${escapeHtml(actionLabel)}</button>
        </div>
      </td>
    </tr>
  `
}

function renderUserTokenStatus(isConfigured) {
  const label = isConfigured
    ? tr('provider.statusConfigured', 'Configured')
    : tr('provider.notConfigured', 'Not configured')
  const className = isConfigured ? 'is-configured' : 'is-missing'
  return `<span class="pv-token-status ${className}">${escapeHtml(label)}</span>`
}

function renderModal() {
  if (!state.modal?.open) {
    return ''
  }

  const modal = state.modal
  const meta = getProviderMeta(modal.providerType)
  const credentialFields = getProviderCredentialFields(modal.providerType, modal.values, modal.instanceName)
  const modalTitle = tr('provider.modalConfigureTitleSpecific', `Set ${meta.name} User Token`, { provider: meta.name })
  const modalDescription = tr(
    'provider.modalConfigureDescription',
    'Set the personal token AtlasClaw should use for this provider instance.'
  )

  return `
    <div class="pv-modal-overlay" id="providerModal">
      <div class="pv-modal">
        <div class="pv-modal-header">
          <div>
            <span class="pv-modal-kicker">${tr('provider.modalKicker', 'Authentication Configuration')}</span>
            <h2>${escapeHtml(modalTitle)}</h2>
            <p class="pv-modal-description">${escapeHtml(modalDescription)}</p>
          </div>
          <button class="pv-modal-close" type="button" data-close-modal aria-label="${tr('provider.close', 'Close')}">x</button>
        </div>
        <form id="providerModalForm">
          <div class="pv-modal-body">
            <div class="pv-modal-primary-fields">
              <span class="pv-modal-instance-label">${tr('provider.instanceName', 'Instance')}</span>
              <strong class="pv-modal-instance-name">${escapeHtml(modal.instanceName)}</strong>
            </div>
            <div class="pv-modal-stack">${credentialFields.map((field) => renderSchemaField(field, modal.values[field.name] || '', modal)).join('')}</div>
            ${modal.error ? `<div class="pv-inline-note is-error"><span class="pv-inline-note-label">${tr('provider.saveFailedLabel', 'Save failed')}</span>${escapeHtml(modal.error)}</div>` : ''}
          </div>
          <div class="pv-modal-footer">
            <button class="pv-btn-secondary" type="button" data-close-modal>${tr('provider.cancel', 'Cancel')}</button>
            <button class="pv-btn-primary" type="submit">${tr('provider.saveCredentials', 'Save')}</button>
          </div>
        </form>
      </div>
    </div>
  `
}

function renderSchemaField(field, value, modalContext = null) {
  const fieldName = String(field?.name || '')
  if (!fieldName) {
    return ''
  }

  if (field.type === 'hidden') {
    return `<input type="hidden" name="${escapeHtml(fieldName)}" value="${escapeHtml(value)}">`
  }

  const label = getSchemaFieldLabel(field)
  const hasStoredSensitiveValue = Boolean(
    modalContext
    && (field?.sensitive || field?.type === 'password')
    && getUserProviderEntry(modalContext.providerType, modalContext.instanceName)?.configured
  )
  const placeholder = hasStoredSensitiveValue
    ? tr('provider.secretUpdatePlaceholder', 'Enter a new value to update')
    : getSchemaFieldPlaceholder(field)
  const required = field.required && !hasStoredSensitiveValue ? 'required' : ''

  if (field.type === 'password') {
    return `
      <label class="pv-form-field">
        <span>${escapeHtml(label)}</span>
        <input class="pv-form-input" type="password" name="${escapeHtml(fieldName)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off" ${required}>
      </label>
    `
  }

  return `
    <label class="pv-form-field">
      <span>${escapeHtml(label)}</span>
      <input class="pv-form-input" type="${escapeHtml(field.type || 'text')}" name="${escapeHtml(fieldName)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" ${required}>
    </label>
  `
}

function openConfigureModal(providerType, instanceName = '') {
  const targetType = providerType || getProviderTypes()[0] || ''
  const targetInstance = instanceName || getConfigFileTemplates(targetType)[0]?.instanceName || ''

  state.modal = {
    open: true,
    providerType: targetType,
    instanceName: targetInstance,
    values: getInitialCredentialValues(targetType, targetInstance),
    error: ''
  }

  render()
}

function closeModal() {
  state.modal = null
  render()
}

function syncModalStateFromDOM() {
  if (!state.modal || !pageContainer) {
    return
  }

  const form = pageContainer.querySelector('#providerModalForm')
  if (!form) {
    return
  }

  const formData = new FormData(form)
  state.modal.values = Object.fromEntries(
    getProviderCredentialFields(state.modal.providerType, state.modal.values, state.modal.instanceName).map((field) => [
      field.name,
      String(formData.get(field.name) || '')
    ])
  )
}

async function saveModal() {
  if (isSaving || !state.modal) {
    return
  }

  isSaving = true
  try {
    syncModalStateFromDOM()

    const instanceName = state.modal.instanceName.trim()
    if (!instanceName) {
      state.modal.error = tr('provider.requiredFields', 'Please choose a provider instance.')
      render()
      return
    }

    const existingEntry = getUserProviderEntry(state.modal.providerType, state.modal.instanceName)
    const userToken = String(state.modal.values?.user_token ?? '').trim()
    if (!existingEntry?.configured && !userToken) {
      state.modal.error = tr('provider.requiredFields', 'User token is required.')
      render()
      return
    }

    const config = {}
    if (userToken) {
      config.user_token = userToken
    }

    const payload = {
      provider_type: state.modal.providerType,
      instance_name: instanceName,
      config
    }

    try {
      await requestJson('/api/users/me/provider-settings', {
        method: 'PUT',
        body: JSON.stringify(payload)
      })

      state.modal = null
      await refreshData()
      showToast(
        tr('provider.saveCredentialsSuccess', 'Authentication configuration saved successfully'),
        'success'
      )
    } catch (error) {
      state.modal.error = error?.message || tr('provider.saveError', 'Unable to save authentication configuration')
      showToast(state.modal.error, 'error')
      render()
    }
  } finally {
    isSaving = false
  }
}

function getProviderTypes() {
  const typeSet = new Set()

  for (const item of state.serviceProviders) {
    if (item?.provider_type && authChainIncludesUserToken(item.auth_type)) {
      typeSet.add(item.provider_type)
    }
  }

  return [...typeSet].sort((left, right) => {
    const leftRank = PROVIDER_ORDER.indexOf(left)
    const rightRank = PROVIDER_ORDER.indexOf(right)
    if (leftRank !== -1 || rightRank !== -1) {
      return (leftRank === -1 ? 999 : leftRank) - (rightRank === -1 ? 999 : rightRank)
    }
    return left.localeCompare(right)
  })
}

function getProviderMeta(providerType) {
  const fallbackName = String(providerType || 'provider')
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (segment) => segment.toUpperCase())

  const definition = state.providerDefinitions[providerType]
  if (definition) {
    return {
      name: tr(definition.name_i18n_key || '', definition.display_name || fallbackName),
      badge: definition.badge || 'SERVICE',
      icon: definition.icon || fallbackName.slice(0, 2).toUpperCase(),
      accent: definition.accent || '#475569',
      description: tr(
        definition.description_i18n_key || '',
        definition.description || tr('provider.catalog.custom.description', 'Backend-defined service provider with instance-bound personal credentials.')
      )
    }
  }

  return {
    name: fallbackName,
    badge: 'SERVICE',
    icon: fallbackName.slice(0, 2).toUpperCase(),
    accent: '#475569',
    description: tr('provider.catalog.custom.description', 'Backend-defined service provider with instance-bound personal credentials.')
  }
}

function getConfigFileTemplates(providerType) {
  return state.serviceProviders
    .filter((entry) => entry.provider_type === providerType)
    .map((entry) => {
      const authTypes = normalizeAuthTypeChain(entry.auth_type)
      return {
        providerType,
        instanceName: String(entry.instance_name || ''),
        baseUrl: String(entry.base_url || ''),
        authType: authTypes[0] || '',
        authTypes,
        configKeys: Array.isArray(entry.config_keys) ? entry.config_keys : []
      }
    })
    .filter((entry) => entry.authTypes.includes(USER_TOKEN_AUTH_TYPE))
}

function getTemplateByInstance(providerType, instanceName) {
  return getConfigFileTemplates(providerType).find((entry) => entry.instanceName === instanceName) || null
}

function getUserProviderEntry(providerType, instanceName) {
  const providerBucket = state.userProviderConfigs?.[providerType]
  if (!providerBucket || typeof providerBucket !== 'object') {
    return null
  }
  const entry = providerBucket[instanceName]
  return entry && typeof entry === 'object' ? entry : null
}

function getTemplateRows(providerType) {
  return getConfigFileTemplates(providerType).map((template) => {
    const userEntry = getUserProviderEntry(providerType, template.instanceName)
    return {
      providerType,
      instanceName: template.instanceName,
      configured: Boolean(userEntry?.configured),
      updatedLabel: formatTimestamp(userEntry?.updated_at),
      config: userEntry?.config || {}
    }
  })
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

function getProviderSchemaFields(providerType) {
  const fields = state.providerDefinitions[providerType]?.schema?.fields
  if (!Array.isArray(fields) || !fields.length) {
    return []
  }

  return fields
}

function getProviderCredentialFields(providerType, _values = {}, _instanceName = '') {
  const fields = getProviderSchemaFields(providerType)
  const userTokenField = fields.find((field) => {
    if (field?.name !== USER_TOKEN_FIELD.name) {
      return false
    }
    const authTypes = normalizeAuthTypeChain(field?.auth_types || [])
    return !authTypes.length || authTypes.includes(USER_TOKEN_AUTH_TYPE)
  })

  return [userTokenField || USER_TOKEN_FIELD]
}

function getInitialCredentialValues(providerType, instanceName) {
  const savedConfig = getUserProviderEntry(providerType, instanceName)?.config || {}

  return Object.fromEntries(
    getProviderCredentialFields(providerType, savedConfig, instanceName).map((field) => {
      const currentValue = savedConfig?.[field.name]
      if (currentValue !== undefined && currentValue !== null && String(currentValue) !== '') {
        if (field?.sensitive || field?.type === 'password') {
          return [field.name, '']
        }
        return [field.name, String(currentValue)]
      }
      if (field.default != null) {
        return [field.name, String(field.default)]
      }
      return [field.name, '']
    })
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

function getSchemaFieldLabel(field) {
  return tr(field.label_i18n_key || '', field.label || field.name || '')
}

function getSchemaFieldPlaceholder(field) {
  return tr(field.placeholder_i18n_key || '', field.placeholder || '')
}

function formatConfigValue(field, rawValue, isConfigured = false) {
  const normalizedValue = String(rawValue ?? '').trim()

  if (field?.sensitive || field?.type === 'password') {
    if (isConfigured) {
      return tr('provider.statusConfigured', 'Configured')
    }
    if (!normalizedValue) {
      return tr('provider.notConfigured', 'Not configured')
    }
    return tr('provider.statusConfigured', 'Configured')
  }

  if (!normalizedValue) {
    return tr('provider.notConfigured', 'Not configured')
  }

  return normalizedValue
}

function formatTimestamp(value) {
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

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
