/**
 * providers.js - Service Provider Management Page
 *
 * Inventory view for backend-defined service providers and DB-managed instances.
 */

import { showToast } from '../components/toast.js'
import { translateIfExists } from '../i18n.js'

const SENSITIVE_KEYS = new Set([
  'cookie',
  'password',
  'secret',
  'app_secret',
  'api_key',
  'token',
  'access_token',
  'credential'
])

const PROVIDER_ORDER = ['smartcmp', 'dingtalk']

const ACTION_ICONS = {
  view: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>`,
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true">
    <path d="M12 20h9"></path>
    <path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"></path>
  </svg>`,
  delete: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true">
    <path d="M3 6h18"></path>
    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>
    <path d="M10 11v6"></path>
    <path d="M14 11v6"></path>
  </svg>`,
  activate: `<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16" aria-hidden="true">
    <path d="M8 5v14l11-7z"></path>
  </svg>`,
  disable: `<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16" aria-hidden="true">
    <path d="M6 5h4v14H6zm8 0h4v14h-4z"></path>
  </svg>`,
  eye: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>`,
  eyeOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
    <line x1="1" y1="1" x2="23" y2="23"></line>
  </svg>`
}

let pageContainer = null
let clickHandler = null
let changeHandler = null
let submitHandler = null
let state = createInitialState()

function createInitialState() {
  return {
    serviceProviders: [],
    providerDefinitions: {},
    managedConfigs: [],
    selectedProviderType: '',
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
}

function bindEvents() {
  clickHandler = async (event) => {
    const providerCard = event.target.closest('[data-provider-card]')
    if (providerCard) {
      state.selectedProviderType = providerCard.dataset.type || ''
      render()
      return
    }

    if (event.target.closest('[data-open-create]')) {
      openCreateModal(state.selectedProviderType)
      return
    }

    if (event.target.closest('[data-close-modal]')) {
      closeModal()
      return
    }

    const secretToggle = event.target.closest('[data-toggle-secret]')
    if (secretToggle) {
      toggleSecretField(secretToggle.dataset.toggleSecret || '', secretToggle)
      return
    }

    const overlay = event.target.closest('#providerModal')
    if (overlay && event.target === overlay) {
      closeModal()
      return
    }

    const viewButton = event.target.closest('[data-view-config]')
    if (viewButton) {
      openViewModal(viewButton.dataset.viewConfig || '')
      return
    }

    const editButton = event.target.closest('[data-edit-config]')
    if (editButton) {
      openEditModal(editButton.dataset.editConfig || '')
      return
    }

    const toggleButton = event.target.closest('[data-toggle-config]')
    if (toggleButton) {
      await toggleManagedConfig(toggleButton.dataset.toggleConfig || '')
      return
    }

    const deleteButton = event.target.closest('[data-delete-config]')
    if (deleteButton) {
      await deleteManagedConfig(deleteButton.dataset.deleteConfig || '')
    }
  }

  changeHandler = () => {}

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
    const [serviceData, definitionData, managedData] = await Promise.all([
      requestJson('/api/service-providers/available-instances'),
      requestJson('/api/service-providers/definitions'),
      requestJson('/api/provider-configs?page=1&page_size=100')
    ])

    state.serviceProviders = Array.isArray(serviceData?.providers) ? serviceData.providers : []
    state.providerDefinitions = indexProviderDefinitions(definitionData?.providers)
    state.managedConfigs = Array.isArray(managedData?.provider_configs) ? managedData.provider_configs : []

    const availableTypes = getProviderTypes()
    if (!availableTypes.includes(state.selectedProviderType)) {
      state.selectedProviderType = availableTypes[0] || ''
    }
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
        <h1 data-i18n="provider.pageTitle">${tr('provider.pageTitle', 'Provider Management')}</h1>
        <p data-i18n="provider.subtitle">${tr('provider.subtitle', 'Manage service provider instances from configuration files and database-managed overrides in one operational view.')}</p>
      </div>
      ${renderProviderBand()}
      ${renderSelectedProvider()}
      ${renderModal()}
    </div>
  `
}

function renderProviderBand() {
  const providerTypes = getProviderTypes()

  if (!providerTypes.length) {
    return `
      <div class="pv-empty">
        <strong data-i18n="provider.emptyTitle">${tr('provider.emptyTitle', 'No service providers found')}</strong>
        ${tr('provider.emptyDescription', 'Add provider instances in')} <code>atlasclaw.json</code> ${tr('provider.emptyDescriptionSuffix', 'or create a managed provider config.')}
      </div>
    `
  }

  return `
    <div class="pv-type-band-shell">
      <div class="pv-type-band">
        ${providerTypes.map(renderProviderCard).join('')}
      </div>
    </div>
  `
}

function renderProviderCard(providerType) {
  const meta = getProviderMeta(providerType)
  const summary = getProviderSummary(providerType)
  const selectedClass = providerType === state.selectedProviderType ? 'selected' : ''

  return `
    <button
      type="button"
      class="pv-type-card ${selectedClass}"
      data-provider-card
      data-type="${escapeHtml(providerType)}"
      style="--pv-accent: ${escapeHtml(meta.accent)}"
    >
      <div class="pv-card-header">
        <span class="pv-card-icon">${escapeHtml(meta.icon)}</span>
        <div class="pv-card-title-group">
          <strong>${escapeHtml(meta.name)}</strong>
          <span class="pv-card-badge">${escapeHtml(meta.badge)}</span>
        </div>
      </div>
      <p class="pv-card-copy">${escapeHtml(meta.description)}</p>
      <div class="pv-card-stats">
        <div>
          <span>${tr('provider.statInstances', 'Instances')}</span>
          <strong>${summary.totalInstances}</strong>
        </div>
        <div>
          <span>${tr('provider.statConfigFile', 'Config file')}</span>
          <strong>${summary.configFileCount}</strong>
        </div>
        <div>
          <span>${tr('provider.statManaged', 'Managed')}</span>
          <strong>${summary.managedCount}</strong>
        </div>
      </div>
    </button>
  `
}

function renderSelectedProvider() {
  const providerType = state.selectedProviderType
  if (!providerType) {
    return ''
  }

  const meta = getProviderMeta(providerType)
  const rows = getMergedRows(providerType)

  return `
    <section class="pv-panel">
      <div class="pv-panel-header compact">
        <div>
          <div class="pv-eyebrow" data-i18n="provider.inventoryEyebrow">${tr('provider.inventoryEyebrow', 'Instance Inventory')}</div>
          <h2 class="pv-panel-title">${tr('provider.inventoryTitle', `${meta.name} Instances`, { provider: meta.name })}</h2>
          <p data-i18n="provider.inventoryDescription">${tr('provider.inventoryDescription', 'Operational instances merged from atlasclaw.json and database-managed provider configs.')}</p>
        </div>
        <div class="pv-panel-actions">
          <span class="pv-counter">${tr('provider.totalCounter', '{{count}} total', { count: rows.length })}</span>
          <button class="pv-btn-primary" id="btnCreateProviderInstance" type="button" data-open-create data-i18n="provider.newInstance">${tr('provider.newInstance', '+ New Instance')}</button>
        </div>
      </div>
      ${renderInstancesTable(rows)}
      ${state.error ? `<div class="pv-inline-note"><span class="pv-inline-note-label" data-i18n="provider.errorLabel">${tr('provider.errorLabel', 'Error')}</span>${escapeHtml(state.error)}</div>` : ''}
    </section>
  `
}

function renderInstancesTable(rows) {
  if (state.loading) {
    return `
      <div class="pv-empty compact">
        <strong data-i18n="provider.loadingTitle">${tr('provider.loadingTitle', 'Loading provider inventory')}</strong>
        <span data-i18n="provider.loadingDescription">${tr('provider.loadingDescription', 'Syncing service provider instances from backend APIs.')}</span>
      </div>
    `
  }

  if (!rows.length) {
    return `
      <div class="pv-empty compact">
        <strong data-i18n="provider.noInstancesTitle">${tr('provider.noInstancesTitle', 'No instances for this provider')}</strong>
        ${tr('provider.noInstancesDescription', 'Create a managed instance or add one in')} <code>atlasclaw.json</code>.
      </div>
    `
  }

  return `
    <div class="pv-table-wrap">
      <table class="pv-table">
        <thead>
          <tr>
            <th>${tr('provider.tableInstance', 'Instance')}</th>
            <th>${tr('provider.tableSource', 'Source')}</th>
            <th>${tr('provider.tableBaseUrl', 'Base URL')}</th>
            <th>${tr('provider.tableStatus', 'Status')}</th>
            <th>${tr('provider.tableUpdated', 'Updated')}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(renderInstanceRow).join('')}
        </tbody>
      </table>
    </div>
  `
}

function renderInstanceRow(row) {
  const statusLabel = row.isActive
    ? tr('provider.statusActive', 'Active')
    : tr('provider.statusInactive', 'Inactive')
  const statusClass = row.isActive ? 'is-active' : 'is-inactive'
  const sourceLabel = getRowSourceLabel(row)

  return `
    <tr>
      <td>
        <div class="pv-instance-cell">
          <div class="pv-instance-heading">
            <strong>${escapeHtml(row.instanceName)}</strong>
            ${row.source === 'config_file'
              ? `<span class="pv-instance-readonly-badge">${tr('provider.readOnly', 'Read only')}</span>`
              : ''}
          </div>
          <span>${escapeHtml(getProviderMeta(row.providerType).name)}</span>
        </div>
      </td>
      <td><span class="pv-source-badge ${row.source === 'managed' ? 'is-managed' : ''}">${escapeHtml(sourceLabel)}</span></td>
      <td>${escapeHtml(row.baseUrl || tr('provider.notConfigured', 'Not configured'))}</td>
      <td><span class="pv-status ${statusClass}">${escapeHtml(statusLabel)}</span></td>
      <td><span class="${row.updatedLabel === '--' ? 'pv-cell-muted' : ''}">${escapeHtml(row.updatedLabel)}</span></td>
      <td class="pv-table-action-cell">
        ${row.source === 'managed'
          ? `
            <div class="pv-actions">
              <button class="btn-small pv-row-action-btn" type="button" data-edit-config="${escapeHtml(row.id)}">${tr('provider.edit', 'Edit')}</button>
              <button class="btn-small pv-row-action-btn" type="button" data-toggle-config="${escapeHtml(row.id)}">${row.isActive ? tr('provider.disable', 'Disable') : tr('provider.activate', 'Activate')}</button>
              <button class="btn-small btn-delete pv-row-action-btn" type="button" data-delete-config="${escapeHtml(row.id)}">${tr('provider.delete', 'Delete')}</button>
            </div>
          `
          : `
            <div class="pv-actions pv-actions-readonly">
              <button class="btn-small pv-row-action-btn" type="button" data-view-config="${escapeHtml(row.rowKey)}">${tr('provider.view', 'View')}</button>
            </div>
          `}
      </td>
    </tr>
  `
}

function renderModal() {
  if (!state.modal?.open) {
    return ''
  }

  if (state.modal.mode === 'view') {
    return renderViewModal(state.modal)
  }

  const modal = state.modal
  const meta = getProviderMeta(modal.providerType)
  const configFields = getProviderConfigFields(modal.providerType, modal.values)
  const hiddenFields = configFields.filter((field) => field.type === 'hidden')
  const visibleFields = configFields.filter((field) => field.type !== 'hidden')
  const modeTitle = modal.mode === 'edit'
    ? tr('provider.modalEditTitle', 'Edit Provider Instance')
    : tr('provider.modalCreateTitle', 'New Provider Instance')

  return `
    <div class="pv-modal-overlay" id="providerModal">
      <div class="pv-modal">
        <div class="pv-modal-header">
          <div>
            <div class="pv-eyebrow">${modal.mode === 'edit' ? tr('provider.modalEditEyebrow', 'Managed Instance') : tr('provider.modalCreateEyebrow', 'Create Instance')}</div>
            <h2>${modeTitle}</h2>
            <p class="pv-modal-description">${escapeHtml(meta.description)}</p>
          </div>
          <button class="pv-modal-close" type="button" data-close-modal aria-label="${tr('provider.close', 'Close')}">×</button>
        </div>
        <form id="providerModalForm">
          <div class="pv-modal-body">
            ${hiddenFields.map((field) => renderSchemaField(field, modal.values[field.name] || '')).join('')}
            <div class="pv-modal-grid">
              ${renderReadonlyField(tr('provider.providerType', 'Provider Type'), getProviderMeta(modal.providerType).name)}
              ${renderInputField(tr('provider.instanceName', 'Instance Name'), 'instance_name', modal.instanceName, { placeholder: tr('provider.instanceNamePlaceholder', 'e.g. prod-cn') })}
            </div>
            <div class="pv-form-section-title">${tr('provider.connectionParameters', 'Connection Parameters')}</div>
            ${visibleFields.length
              ? `<div class="pv-modal-stack">${visibleFields.map((field) => renderSchemaField(field, modal.values[field.name] || '')).join('')}</div>`
              : `<div class="pv-inline-note"><span class="pv-inline-note-label">${tr('provider.noExtraSecret', 'No extra secret required')}</span>${tr('provider.noExtraSecretDescription', 'This auth mode relies on platform session or upstream token context.')}</div>`}
            <label class="pv-switch">
              <input type="checkbox" name="is_active" ${modal.isActive ? 'checked' : ''}>
              <span>${tr('provider.enableAfterSave', 'Enable this managed instance after save')}</span>
            </label>
            ${modal.error ? `<div class="pv-inline-note is-error"><span class="pv-inline-note-label">${tr('provider.saveFailedLabel', 'Save failed')}</span>${escapeHtml(modal.error)}</div>` : ''}
          </div>
          <div class="pv-modal-footer">
            <button class="pv-btn-secondary" type="button" data-close-modal>${tr('provider.cancel', 'Cancel')}</button>
            <button class="pv-btn-primary" type="submit">${modal.mode === 'edit' ? tr('provider.saveChanges', 'Save Changes') : tr('provider.createInstance', 'Create Instance')}</button>
          </div>
        </form>
      </div>
    </div>
  `
}

function renderViewModal(modal) {
  const meta = getProviderMeta(modal.providerType)
  const configKeys = Array.isArray(modal.configKeys) ? modal.configKeys : []

  return `
    <div class="pv-modal-overlay" id="providerModal">
      <div class="pv-modal pv-modal-view">
        <div class="pv-modal-header">
          <div>
            <div class="pv-eyebrow">${tr('provider.readOnly', 'Read only')}</div>
            <h2>${tr('provider.modalViewTitle', 'View Provider Instance')}</h2>
            <p class="pv-modal-description">${escapeHtml(meta.description)}</p>
          </div>
          <button class="pv-modal-close" type="button" data-close-modal aria-label="${tr('provider.close', 'Close')}">×</button>
        </div>
        <div class="pv-modal-body">
          <div class="pv-modal-grid">
            ${renderReadonlyField(tr('provider.providerType', 'Provider Type'), meta.name)}
            ${renderReadonlyField(tr('provider.instanceName', 'Instance Name'), modal.instanceName)}
            ${renderReadonlyField(tr('provider.baseUrl', 'Base URL'), modal.baseUrl || tr('provider.notConfigured', 'Not configured'))}
            ${renderReadonlyField(tr('provider.tableSource', 'Source'), modal.sourceLabel || tr('provider.sourceConfigFile', 'Config file'))}
          </div>
          ${configKeys.length
            ? `
              <div class="pv-form-section-title">${tr('provider.configKeySummary', 'Available Parameters')}</div>
              <div class="pv-key-list">
                ${configKeys.map((key) => `<span class="pv-key-chip">${escapeHtml(key)}</span>`).join('')}
              </div>
            `
            : ''}
          <div class="pv-inline-note">
            <span class="pv-inline-note-label">${tr('provider.readOnly', 'Read only')}</span>
            ${tr('provider.configValuesProtected', 'Config-file values are protected here and can only be changed in atlasclaw.json.')}
          </div>
        </div>
        <div class="pv-modal-footer">
          <button class="pv-btn-secondary" type="button" data-close-modal>${tr('provider.close', 'Close')}</button>
        </div>
      </div>
    </div>
  `
}

function renderReadonlyField(label, value) {
  return `
    <label class="pv-form-field">
      <span>${escapeHtml(label)}</span>
      <div class="pv-readonly-pill">${escapeHtml(value)}</div>
    </label>
  `
}

function renderInputField(label, name, value, { placeholder = '', type = 'text' } = {}) {
  return `
    <label class="pv-form-field">
      <span>${escapeHtml(label)}</span>
      <input class="pv-form-input" type="${escapeHtml(type)}" name="${escapeHtml(name)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
    </label>
  `
}

function renderSchemaField(field, value) {
  const fieldName = String(field?.name || '')
  if (!fieldName) {
    return ''
  }

  if (field.type === 'hidden') {
    return `<input type="hidden" name="${escapeHtml(fieldName)}" value="${escapeHtml(value)}">`
  }

  const label = getSchemaFieldLabel(field)
  const placeholder = getSchemaFieldPlaceholder(field)
  const required = field.required ? 'required' : ''

  if (field.type === 'password') {
    return `
      <label class="pv-form-field">
        <span>${escapeHtml(label)}</span>
        <div class="pv-secret-field">
          <input class="pv-form-input" type="password" name="${escapeHtml(fieldName)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" ${required}>
          <button class="pv-secret-toggle" type="button" data-toggle-secret="${escapeHtml(fieldName)}" aria-label="${tr('provider.toggleSecretVisibility', 'Show or hide secret')}">
            ${ACTION_ICONS.eye}
          </button>
        </div>
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

function openCreateModal(providerType) {
  const targetType = providerType || getProviderTypes()[0] || ''

  state.modal = {
    open: true,
    mode: 'create',
    configId: '',
    providerType: targetType,
    instanceName: '',
    isActive: true,
    values: getInitialConfigValues(targetType),
    error: ''
  }

  render()
}

function openEditModal(configId) {
  const current = state.managedConfigs.find((item) => item.id === configId)
  if (!current) {
    return
  }

  const providerType = current.provider_type

  state.modal = {
    open: true,
    mode: 'edit',
    configId,
    providerType,
    instanceName: current.instance_name || '',
    isActive: current.is_active !== false,
    values: getInitialConfigValues(providerType, current.config || {}),
    error: ''
  }

  render()
}

function openViewModal(rowKey) {
  const row = findMergedRowByKey(rowKey)
  if (!row) {
    return
  }

  state.modal = {
    open: true,
    mode: 'view',
    providerType: row.providerType,
    instanceName: row.instanceName,
    baseUrl: row.baseUrl,
    sourceLabel: getRowSourceLabel(row),
    configKeys: row.configKeys || [],
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
  state.modal.instanceName = String(formData.get('instance_name') || '')
  state.modal.isActive = formData.get('is_active') === 'on'
  state.modal.values = Object.fromEntries(
    getProviderConfigFields(state.modal.providerType, state.modal.values).map((field) => [
      field.name,
      String(formData.get(field.name) || '')
    ])
  )
}

async function saveModal() {
  if (!state.modal) {
    return
  }

  syncModalStateFromDOM()

  const instanceName = state.modal.instanceName.trim()
  if (!instanceName) {
    state.modal.error = tr('provider.requiredFields', 'Instance name and base URL are required.')
    render()
    return
  }

  const configFields = getProviderConfigFields(state.modal.providerType, state.modal.values)
  const config = {}
  for (const field of configFields) {
    const normalizedValue = String(state.modal.values?.[field.name] ?? '').trim()
    if (normalizedValue || field.default != null) {
      config[field.name] = normalizedValue || String(field.default)
    }
  }

  const payload = {
    provider_type: state.modal.providerType,
    instance_name: instanceName,
    config,
    is_active: state.modal.isActive
  }

  try {
    const modalMode = state.modal.mode
    if (state.modal.mode === 'edit') {
      await requestJson(`/api/provider-configs/${state.modal.configId}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      })
    } else {
      await requestJson('/api/provider-configs', {
        method: 'POST',
        body: JSON.stringify(payload)
      })
    }

    state.modal = null
    await refreshData()
    showToast(
      tr(
        modalMode === 'edit' ? 'provider.updateSuccess' : 'provider.createSuccess',
        modalMode === 'edit'
          ? 'Provider instance updated successfully'
          : 'Provider instance created successfully'
      ),
      'success'
    )
  } catch (error) {
    state.modal.error = error?.message || tr('provider.saveError', 'Unable to save provider config')
    showToast(state.modal.error, 'error')
    render()
  }
}

async function toggleManagedConfig(configId) {
  const current = state.managedConfigs.find((item) => item.id === configId)
  if (!current) {
    return
  }

  const payload = {
    provider_type: current.provider_type,
    instance_name: current.instance_name,
    config: current.config || {},
    is_active: !current.is_active
  }

  try {
    await requestJson(`/api/provider-configs/${configId}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    })
    await refreshData()
    showToast(
      !current.is_active
        ? tr('provider.activated', 'Provider instance activated')
        : tr('provider.deactivated', 'Provider instance deactivated'),
      'success'
    )
  } catch (error) {
    showToast(error?.message || tr('provider.saveError', 'Unable to save provider config'), 'error')
  }
}

async function deleteManagedConfig(configId) {
  const current = state.managedConfigs.find((item) => item.id === configId)
  if (!current || !window.confirm(tr('provider.deleteConfirm', 'Delete managed instance {{name}}?', { name: current.instance_name }))) {
    return
  }

  try {
    await requestJson(`/api/provider-configs/${configId}`, { method: 'DELETE' })
    await refreshData()
    showToast(tr('provider.deleteSuccess', 'Provider instance deleted successfully'), 'success')
  } catch (error) {
    showToast(error?.message || tr('provider.saveError', 'Unable to save provider config'), 'error')
  }
}

function getProviderTypes() {
  const typeSet = new Set()

  for (const item of state.serviceProviders) {
    if (item?.provider_type) {
      typeSet.add(item.provider_type)
    }
  }

  for (const item of state.managedConfigs) {
    if (item?.provider_type) {
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
        definition.description || tr('provider.catalog.custom.description', 'Backend-defined service provider with managed connection instances.')
      )
    }
  }

  return {
    name: fallbackName,
    badge: 'SERVICE',
    icon: fallbackName.slice(0, 2).toUpperCase(),
    accent: '#475569',
    description: tr('provider.catalog.custom.description', 'Backend-defined service provider with managed connection instances.')
  }
}

function getProviderSummary(providerType) {
  const rows = getMergedRows(providerType)
  const primaryEndpointRaw = rows.find((row) => row.baseUrl)?.baseUrl || ''

  return {
    totalInstances: rows.length,
    configFileCount: rows.filter((row) => row.source === 'config_file').length,
    managedCount: rows.filter((row) => row.source === 'managed').length,
    activeCount: rows.filter((row) => row.source === 'config_file' || row.isActive).length,
    primaryEndpoint: primaryEndpointRaw || tr('provider.notConfigured', 'Not configured'),
    primaryEndpointRaw
  }
}

function getMergedRows(providerType) {
  const rowsByKey = new Map()

  for (const item of state.serviceProviders.filter((entry) => entry.provider_type === providerType)) {
    const rowKey = `${providerType}:${item.instance_name}`
    rowsByKey.set(rowKey, {
      id: '',
      rowKey,
      providerType,
      instanceName: item.instance_name,
      baseUrl: String(item.base_url || ''),
      configKeys: Array.isArray(item.config_keys) ? item.config_keys : [],
      source: 'config_file',
      override: false,
      isActive: true,
      updatedLabel: '--'
    })
  }

  for (const item of state.managedConfigs.filter((entry) => entry.provider_type === providerType)) {
    const rowKey = `${providerType}:${item.instance_name}`
    const config = item.config || {}
    rowsByKey.set(rowKey, {
      id: item.id,
      rowKey,
      providerType,
      instanceName: item.instance_name,
      baseUrl: String(config.base_url || ''),
      configKeys: Object.keys(config)
        .filter((key) => key !== 'base_url' && key !== 'auth_type')
        .sort(),
      source: 'managed',
      override: rowsByKey.has(rowKey),
      isActive: item.is_active !== false,
      updatedLabel: formatTimestamp(item.updated_at)
    })
  }

  return [...rowsByKey.values()].sort((left, right) => left.instanceName.localeCompare(right.instanceName))
}

function findMergedRowByKey(rowKey) {
  const normalizedKey = String(rowKey || '').trim()
  if (!normalizedKey) {
    return null
  }

  const providerType = normalizedKey.split(':', 1)[0]
  if (providerType) {
    return getMergedRows(providerType).find((row) => row.rowKey === normalizedKey) || null
  }

  for (const type of getProviderTypes()) {
    const row = getMergedRows(type).find((entry) => entry.rowKey === normalizedKey)
    if (row) {
      return row
    }
  }

  return null
}

function getRowSourceLabel(row) {
  if (row.override) {
    return tr('provider.sourceManagedOverride', 'Managed Override')
  }
  return row.source === 'managed'
    ? tr('provider.sourceManaged', 'Managed')
    : tr('provider.sourceConfigFile', 'Config file')
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

function getProviderConfigFields(providerType, values = {}) {
  const fields = state.providerDefinitions[providerType]?.schema?.fields
  if (Array.isArray(fields) && fields.length) {
    const authTypeDefault = fields.find((field) => field?.name === 'auth_type')?.default || ''
    const authType = String(values?.auth_type || authTypeDefault || '').trim().toLowerCase()
    return fields.filter((field) => {
      const authTypes = Array.isArray(field?.auth_types) ? field.auth_types : []
      return !authTypes.length || authTypes.includes(authType)
    })
  }

  return [{
    name: 'base_url',
    type: 'text',
    required: true,
    label: 'Base URL',
    label_i18n_key: 'provider.baseUrl',
    placeholder: 'https://example.com',
    placeholder_i18n_key: 'provider.baseUrlPlaceholder',
    sensitive: false
  }]
}

function getInitialConfigValues(providerType, values = {}) {
  return Object.fromEntries(
    getProviderConfigFields(providerType).map((field) => {
      const currentValue = values?.[field.name]
      if (currentValue !== undefined && currentValue !== null && String(currentValue) !== '') {
        return [field.name, String(currentValue)]
      }
      if (field.default != null) {
        return [field.name, String(field.default)]
      }
      return [field.name, '']
    })
  )
}

function getSchemaFieldLabel(field) {
  return tr(field.label_i18n_key || '', field.label || field.name || '')
}

function getSchemaFieldPlaceholder(field) {
  return tr(field.placeholder_i18n_key || '', field.placeholder || '')
}

function toggleSecretField(fieldName, button) {
  if (!pageContainer || !fieldName) {
    return
  }

  const input = pageContainer.querySelector(`#providerModalForm input[name="${fieldName}"]`)
  if (!input) {
    return
  }

  const isPassword = input.type === 'password'
  input.type = isPassword ? 'text' : 'password'
  button.innerHTML = isPassword ? ACTION_ICONS.eyeOff : ACTION_ICONS.eye
}

function formatTimestamp(value) {
  if (!value) {
    return tr('provider.justNow', 'Just now')
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
