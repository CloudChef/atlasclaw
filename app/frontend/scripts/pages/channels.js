/**
 * channels.js - Channels Page Module
 *
 * SPA page module for channel connection management.
 * Migrated from channels.html + channel-manager.js
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { t, updatePageTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'

// ========== Module State ==========
let mounted = false
let pageContainer = null

// Channel state
let currentChannelType = null
let currentSchema = null
let allChannels = []
let editingConnectionId = null
let pendingDeleteId = null

// Event handler references for cleanup
let popstateHandler = null

// ========== Page Template ==========
const PAGE_TEMPLATE = `
  <div class="channel-content">
    <!-- Channel List View (default) -->
    <div id="channelListView">
      <section class="channel-section">
        <h2 class="section-title" data-i18n="channel.available">Available Channels</h2>
        <p class="section-desc" data-i18n="channel.selectToConfig">Select a channel type to configure</p>
        <div class="channel-types" id="channelTypes">
          <div class="channel-loading" data-i18n="channel.loading">Loading...</div>
        </div>
      </section>
    </div>
    
    <!-- Channel Detail View (shown when ?type=xxx) -->
    <div id="channelDetailView" style="display: none;">
      <!-- Content will be rendered dynamically -->
    </div>
  </div>

  <!-- Delete Confirm Dialog -->
  <div id="deleteDialog" class="confirm-dialog hidden">
    <div class="confirm-content">
      <h3 data-i18n="channel.deleteConfirmTitle">Confirm Delete</h3>
      <p id="deleteMessage" data-i18n="channel.deleteConfirm">Are you sure you want to delete this connection?</p>
      <div class="confirm-buttons">
        <button class="btn-cancel" id="btnCancelDelete" data-i18n="channel.cancel">Cancel</button>
        <button class="btn-confirm" id="btnConfirmDelete" data-i18n="channel.delete">Delete</button>
      </div>
    </div>
  </div>
`

// ========== Lifecycle Functions ==========

/**
 * Mount channels page into container
 * @param {HTMLElement} container - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(container, { params, route } = {}) {
  console.log('[ChannelsPage] Mounting...')

  pageContainer = container

  // Render page HTML
  container.innerHTML = PAGE_TEMPLATE

  // Bind delete dialog events
  bindDeleteDialogEvents()

  // Handle popstate for browser back/forward
  popstateHandler = () => handleRouteChange()
  window.addEventListener('popstate', popstateHandler)

  // Load channel types
  const channels = await fetchChannelTypes()
  renderChannelTypes(channels)

  // Handle initial route (check URL params)
  await handleRouteChange()

  // Update translations
  updatePageTranslations()

  mounted = true
  console.log('[ChannelsPage] Mounted')
}

/**
 * Unmount channels page - cleanup
 */
export async function unmount() {
  console.log('[ChannelsPage] Unmounting...')

  // Remove popstate listener
  if (popstateHandler) {
    window.removeEventListener('popstate', popstateHandler)
    popstateHandler = null
  }

  // Reset all module state
  mounted = false
  pageContainer = null
  currentChannelType = null
  currentSchema = null
  allChannels = []
  editingConnectionId = null
  pendingDeleteId = null

  console.log('[ChannelsPage] Unmounted')
}

// ========== Helper: Query within page container ==========

/**
 * Query element within page container
 */
function $(selector) {
  return pageContainer?.querySelector(selector)
}

/**
 * Query all elements within page container
 */
function $$(selector) {
  return pageContainer?.querySelectorAll(selector) || []
}

// ========== Localization Helpers ==========

/**
 * Get localized channel name
 */
function getChannelName(channel) {
  const translatedName = t(`channel.name_${channel.type}`)
  // If translation exists (not returning the key), use it
  if (translatedName && !translatedName.startsWith('channel.name_')) {
    return translatedName
  }
  return channel.name || channel.type
}

/**
 * Get localized field text (title, description, placeholder)
 */
function getFieldText(key, textType, fallback) {
  const translated = t(`channel.field.${key}.${textType}`)
  // If translation exists (not returning the key), use it
  if (translated && !translated.startsWith('channel.field.')) {
    return translated
  }
  return fallback || ''
}

// ========== URL Routing ==========

/**
 * Get channel type from URL query parameter
 */
function getChannelTypeFromURL() {
  const params = new URLSearchParams(window.location.search)
  return params.get('type')
}

/**
 * Navigate to a channel type (update URL)
 */
function navigateToChannel(type) {
  const url = new URL(window.location.href)
  if (type) {
    url.searchParams.set('type', type)
  } else {
    url.searchParams.delete('type')
  }
  window.history.pushState({}, '', url)
  handleRouteChange()
}

// ========== API Functions ==========

/**
 * Fetch all available channel types
 */
async function fetchChannelTypes() {
  try {
    const res = await fetch('/api/channels')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (error) {
    console.error('[ChannelsPage] Failed to fetch channel types:', error)
    return []
  }
}

/**
 * Fetch channel configuration schema
 */
async function fetchChannelSchema(type) {
  try {
    const res = await fetch(`/api/channels/${type}/schema`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (error) {
    console.error(`[ChannelsPage] Failed to fetch schema for ${type}:`, error)
    return null
  }
}

/**
 * Fetch connections for a channel type
 */
async function fetchConnections(type) {
  try {
    const res = await fetch(`/api/channels/${type}/connections`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } catch (error) {
    console.error(`[ChannelsPage] Failed to fetch connections for ${type}:`, error)
    return { connections: [] }
  }
}

/**
 * Create a new connection
 */
async function createConnection(type, data) {
  const res = await fetch(`/api/channels/${type}/connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

/**
 * Update an existing connection
 */
async function updateConnection(type, id, data) {
  const res = await fetch(`/api/channels/${type}/connections/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

/**
 * Delete a connection
 */
async function deleteConnection(type, id) {
  const res = await fetch(`/api/channels/${type}/connections/${id}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return true
}

/**
 * Toggle connection enabled state
 */
async function toggleConnection(type, id, enable) {
  const action = enable ? 'enable' : 'disable'
  const res = await fetch(`/api/channels/${type}/connections/${id}/${action}`, {
    method: 'POST'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

/**
 * Verify connection configuration
 */
async function verifyConnection(type, id) {
  const res = await fetch(`/api/channels/${type}/connections/${id}/verify`, {
    method: 'POST'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

// ========== UI Rendering ==========

/**
 * Render channel type cards (main grid)
 */
function renderChannelTypes(channels) {
  allChannels = channels || []
  const container = $('#channelTypes')

  if (!channels || channels.length === 0) {
    if (container) {
      container.innerHTML = `<div class="channel-empty" data-i18n="channel.noChannels">${t('channel.noChannels')}</div>`
    }
    return
  }

  // Render main grid
  if (container) {
    container.innerHTML = channels.map(channel => `
      <div class="channel-type-card ${currentChannelType === channel.type ? 'active' : ''}" data-type="${channel.type}">
        <div class="channel-name">${getChannelName(channel)}</div>
        <div class="channel-status">${channel.connection_count > 0 
          ? `${channel.connection_count} ${t('channel.connectionsCount')}` 
          : t('channel.notConfigured')}</div>
      </div>
    `).join('')

    // Bind click events - use navigateToChannel for URL routing
    container.querySelectorAll('.channel-type-card').forEach(card => {
      card.addEventListener('click', () => navigateToChannel(card.dataset.type))
    })
  }
}

/**
 * Handle route change - render appropriate view based on URL
 */
async function handleRouteChange() {
  if (!mounted && !pageContainer) return

  const type = getChannelTypeFromURL()
  currentChannelType = type

  // Update active states in UI
  $$('.channel-type-card').forEach(card => {
    card.classList.toggle('active', card.dataset.type === type)
  })

  const channelListView = $('#channelListView')
  const channelDetailView = $('#channelDetailView')

  if (type) {
    // Show channel detail/config view
    if (channelListView) channelListView.style.display = 'none'
    if (channelDetailView) channelDetailView.style.display = 'block'
    await renderChannelDetailView(type)
  } else {
    // Show channel list view
    if (channelListView) channelListView.style.display = 'block'
    if (channelDetailView) channelDetailView.style.display = 'none'
  }
}

/**
 * Render channel detail/config view
 */
async function renderChannelDetailView(type) {
  const container = $('#channelDetailView')
  if (!container) return

  // Find channel info
  const channelInfo = allChannels.find(c => c.type === type) || { type, name: type }

  // Fetch schema and connections in parallel
  const [schema, connectionsData] = await Promise.all([
    fetchChannelSchema(type),
    fetchConnections(type)
  ])

  currentSchema = schema
  const connections = connectionsData.connections || []

  // Build the detail view HTML
  container.innerHTML = `
    <div class="channel-detail-header">
      <button class="btn-back" id="btnBackToList">← ${t('channel.backToList')}</button>
      <div class="channel-detail-title">
        <h2>${getChannelName(channelInfo)}</h2>
      </div>
      <button class="btn-primary" id="btnAddConnection">
        <span>+</span> ${t('channel.newConnection')}
      </button>
    </div>
    
    <div class="channel-detail-body">
      <!-- Existing Connections -->
      <div class="connections-section">
        <h3>${t('channel.existingConnections')}</h3>
        <div class="connections-list" id="connectionsList">
          ${connections.length === 0 
            ? `<div class="connections-empty">${t('channel.noConnections')}</div>`
            : connections.map(conn => renderConnectionItem(conn)).join('')
          }
        </div>
      </div>
      
      <!-- Config Form (hidden by default) -->
      <div class="config-form-section" id="configFormSection" style="display: none;">
        <h3 id="configFormTitle">${t('channel.newConnection')}</h3>
        <div class="config-form" id="configForm">
          ${renderConfigForm(schema)}
        </div>
        <div class="form-actions">
          <button class="btn-secondary" id="btnCancelForm">${t('channel.cancel')}</button>
          <button class="btn-secondary" id="btnVerifyConfig">${t('channel.verify')}</button>
          <button class="btn-primary" id="btnSaveConfig">${t('channel.save')}</button>
        </div>
      </div>
    </div>
  `

  // Bind events
  bindDetailViewEvents()
}

/**
 * Render a single connection item
 */
function renderConnectionItem(conn) {
  const statusClass = conn.enabled ? 'connected' : 'disconnected'
  const statusText = conn.enabled ? t('channel.connected') : t('channel.disconnected')
  const toggleText = conn.enabled ? t('channel.disable') : t('channel.enable')

  // Show first config field as preview
  const configPreview = conn.config 
    ? Object.entries(conn.config).slice(0, 1).map(([k, v]) => `${k}: ${String(v).slice(0, 16)}...`).join('')
    : ''

  return `
    <div class="connection-item" data-id="${conn.id}">
      <div class="connection-status ${statusClass}"></div>
      <div class="connection-info">
        <div class="connection-name">${conn.name || conn.id}</div>
        <div class="connection-detail">${configPreview}</div>
      </div>
      <div class="connection-status-text">${statusText}</div>
      <div class="connection-actions">
        <button class="btn-small btn-edit" data-action="edit">${t('channel.edit')}</button>
        <button class="btn-small btn-toggle" data-action="toggle" data-enabled="${conn.enabled}">${toggleText}</button>
        <button class="btn-small btn-delete" data-action="delete">${t('channel.delete')}</button>
      </div>
    </div>
  `
}

/**
 * Render config form from JSON Schema
 */
function renderConfigForm(schema, values = {}) {
  if (!schema || !schema.properties) {
    return `<div class="form-error">${t('channel.schemaLoadFailed')}</div>`
  }

  const properties = schema.properties || {}
  const required = schema.required || []
  const requiredByMode = schema.required_by_mode || {}

  // Get localized placeholder for connection name
  const namePlaceholder = t('channel.connectionNamePlaceholder')

  // Connection name field first
  let html = `
    <div class="form-group">
      <label>${t('channel.connectionName')} <span class="required">*</span></label>
      <input type="text" name="_name" value="${values.name || ''}" 
             placeholder="${namePlaceholder}" required>
    </div>
  `

  // Get current connection_mode value for conditional rendering
  const connectionModeValue = values.config?.connection_mode
  const currentMode = (connectionModeValue !== undefined && connectionModeValue !== '') 
                      ? connectionModeValue 
                      : (properties.connection_mode?.default || '')

  // Generate fields from schema properties
  for (const [key, prop] of Object.entries(properties)) {
    const rawValue = values.config?.[key]
    const value = (rawValue !== undefined && rawValue !== '') ? rawValue : (prop.default || '')

    // Handle enum fields (render as select dropdown)
    if (prop.enum && Array.isArray(prop.enum)) {
      const title = getFieldText(key, 'title', prop.title || key)
      const description = getFieldText(key, 'description', prop.description || '')
      const enumLabels = prop.enumLabels || {}

      html += `
        <div class="form-group">
          <label>${title} <span class="required">*</span></label>
          <select name="${key}" class="form-select" data-mode-selector="${key === 'connection_mode'}">
            ${prop.enum.map(opt => {
              const label = getFieldText(`${key}.${opt}`, 'label', enumLabels[opt] || opt)
              const selected = value === opt ? 'selected' : ''
              return `<option value="${opt}" ${selected}>${label}</option>`
            }).join('')}
          </select>
          ${description ? `<span class="hint">${description}</span>` : ''}
        </div>
      `
      continue
    }

    // Check if field has showWhen condition
    const showWhen = prop.showWhen
    let shouldShow = true
    let showWhenAttr = ''

    if (showWhen && typeof showWhen === 'object') {
      showWhenAttr = `data-show-when='${JSON.stringify(showWhen)}'`
      // Check if current mode matches the condition
      for (const [condKey, condValue] of Object.entries(showWhen)) {
        const currentCondValue = values.config?.[condKey] || properties[condKey]?.default || ''
        if (currentCondValue !== condValue) {
          shouldShow = false
          break
        }
      }
    }

    // Determine if field is required based on current mode
    let isRequired = required.includes(key)
    if (requiredByMode && currentMode) {
      const modeRequired = requiredByMode[currentMode] || []
      if (modeRequired.includes(key)) {
        isRequired = true
      }
    }

    const inputType = prop.type === 'string' && (key.toLowerCase().includes('secret') || key.toLowerCase().includes('password')) ? 'password' : 'text'

    // Get localized text with fallback to schema values
    const title = getFieldText(key, 'title', prop.title || key)
    const description = getFieldText(key, 'description', prop.description || '')
    const placeholder = getFieldText(key, 'placeholder', prop.placeholder || prop.description || '')

    html += `
      <div class="form-group" ${showWhenAttr} style="${shouldShow ? '' : 'display: none;'}">
        <label>${title} ${isRequired ? '<span class="required">*</span>' : ''}</label>
        <input type="${inputType}" name="${key}" value="${value}" 
               placeholder="${placeholder}" ${isRequired ? 'data-conditional-required="true"' : ''}>
        ${description ? `<span class="hint">${description}</span>` : ''}
      </div>
    `
  }

  return html
}

/**
 * Handle connection mode change - show/hide fields dynamically
 */
function handleModeChange(selectElement, schema) {
  const selectedMode = selectElement.value
  const form = selectElement.closest('.config-form') || selectElement.closest('#configForm')
  if (!form) return

  const requiredByMode = schema?.required_by_mode || {}
  const modeRequired = requiredByMode[selectedMode] || []

  // Find all fields with showWhen conditions
  form.querySelectorAll('[data-show-when]').forEach(group => {
    const showWhen = JSON.parse(group.dataset.showWhen)
    let shouldShow = true

    for (const [condKey, condValue] of Object.entries(showWhen)) {
      const condInput = form.querySelector(`[name="${condKey}"]`)
      if (condInput && condInput.value !== condValue) {
        shouldShow = false
        break
      }
    }

    group.style.display = shouldShow ? '' : 'none'

    // Update required state for inputs
    const input = group.querySelector('input, select')
    if (input && input.name) {
      const isRequired = modeRequired.includes(input.name)
      if (isRequired) {
        input.setAttribute('data-conditional-required', 'true')
      } else {
        input.removeAttribute('data-conditional-required')
      }
      // Update required asterisk
      const label = group.querySelector('label')
      if (label) {
        const existingRequired = label.querySelector('.required')
        if (isRequired && !existingRequired) {
          label.innerHTML += ' <span class="required">*</span>'
        } else if (!isRequired && existingRequired) {
          existingRequired.remove()
        }
      }
    }
  })
}

/**
 * Bind mode change events after form render
 */
function bindModeChangeEvents(schema) {
  const form = $('#configForm')
  if (!form) return

  form.querySelectorAll('select[data-mode-selector="true"]').forEach(select => {
    select.addEventListener('change', () => handleModeChange(select, schema))
  })
}

// ========== Event Binding ==========

/**
 * Bind delete dialog events
 */
function bindDeleteDialogEvents() {
  $('#btnCancelDelete')?.addEventListener('click', hideDeleteDialog)
  $('#btnConfirmDelete')?.addEventListener('click', handleDelete)
}

/**
 * Bind events for detail view
 */
function bindDetailViewEvents() {
  // Back button
  $('#btnBackToList')?.addEventListener('click', () => navigateToChannel(null))

  // Add connection button
  $('#btnAddConnection')?.addEventListener('click', showNewConnectionForm)

  // Form buttons
  $('#btnCancelForm')?.addEventListener('click', hideConfigForm)
  $('#btnVerifyConfig')?.addEventListener('click', handleVerify)
  $('#btnSaveConfig')?.addEventListener('click', handleSave)

  // Connection item actions
  $$('.connection-item').forEach(item => {
    item.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        const action = btn.dataset.action
        const connId = item.dataset.id
        handleConnectionAction(action, connId, btn.dataset.enabled === 'true')
      })
    })
  })
}

// ========== Form Actions ==========

/**
 * Show form for new connection
 */
function showNewConnectionForm() {
  editingConnectionId = null
  const formSection = $('#configFormSection')
  const formTitle = $('#configFormTitle')
  const form = $('#configForm')

  if (formTitle) formTitle.textContent = t('channel.newConnection')
  if (form) form.innerHTML = renderConfigForm(currentSchema)
  if (formSection) formSection.style.display = 'block'

  // Bind mode change events for dynamic field display
  bindModeChangeEvents(currentSchema)

  // Re-bind form events
  $('#btnCancelForm')?.addEventListener('click', hideConfigForm)
  $('#btnVerifyConfig')?.addEventListener('click', handleVerify)
  $('#btnSaveConfig')?.addEventListener('click', handleSave)
}

/**
 * Show form for editing connection
 */
async function showEditConnectionForm(connectionId) {
  editingConnectionId = connectionId

  // Fetch current connection data
  const data = await fetchConnections(currentChannelType)
  const connection = (data.connections || []).find(c => c.id === connectionId)

  if (!connection) {
    showToast(t('channel.connectionNotFound'), 'error')
    return
  }

  const formSection = $('#configFormSection')
  const formTitle = $('#configFormTitle')
  const form = $('#configForm')

  if (formTitle) formTitle.textContent = t('channel.editConnection')
  if (form) form.innerHTML = renderConfigForm(currentSchema, connection)
  if (formSection) formSection.style.display = 'block'

  // Bind mode change events for dynamic field display
  bindModeChangeEvents(currentSchema)
}

/**
 * Hide config form
 */
function hideConfigForm() {
  const formSection = $('#configFormSection')
  if (formSection) formSection.style.display = 'none'
  editingConnectionId = null
}

/**
 * Handle connection actions (edit/toggle/delete)
 */
async function handleConnectionAction(action, connectionId, isEnabled) {
  switch (action) {
    case 'edit':
      await showEditConnectionForm(connectionId)
      break
    case 'toggle':
      await handleToggle(connectionId, !isEnabled)
      break
    case 'delete':
      showDeleteConfirm(connectionId)
      break
  }
}

/**
 * Handle form save
 */
async function handleSave() {
  const form = $('#configForm')
  if (!form) return

  const inputs = form.querySelectorAll('input, select')
  const config = {}
  let name = ''
  let hasValidationError = false

  // Validate all required fields (only visible ones with conditional-required)
  for (const input of inputs) {
    const formGroup = input.closest('.form-group')
    const isVisible = formGroup && formGroup.style.display !== 'none'
    const isConditionalRequired = input.hasAttribute('data-conditional-required')
    const isRequired = input.required || (isVisible && isConditionalRequired)

    if (isRequired && isVisible && !input.value.trim()) {
      input.classList.add('input-error')
      hasValidationError = true
    } else {
      input.classList.remove('input-error')
    }

    if (input.name === '_name') {
      name = input.value.trim()
    } else if (input.value.trim()) {
      config[input.name] = input.value.trim()
    }
  }

  if (hasValidationError) {
    showToast(t('channel.requiredFieldsMissing') || 'Please fill in all required fields', 'error')
    return
  }

  if (!name) {
    showToast(t('channel.nameRequired'), 'error')
    return
  }

  try {
    if (editingConnectionId) {
      await updateConnection(currentChannelType, editingConnectionId, { name, config })
      showToast(t('channel.updateSuccess'), 'success')
    } else {
      await createConnection(currentChannelType, { name, config })
      showToast(t('channel.createSuccess'), 'success')
    }

    hideConfigForm()
    // Refresh the detail view
    await renderChannelDetailView(currentChannelType)
    await refreshChannelTypes()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Handle verify button
 */
async function handleVerify() {
  if (!editingConnectionId) {
    showToast(t('channel.saveFirst'), 'warning')
    return
  }

  try {
    const result = await verifyConnection(currentChannelType, editingConnectionId)
    if (result.valid) {
      showToast(t('channel.verifySuccess'), 'success')
    } else {
      showToast(result.errors?.join(', ') || t('channel.verifyFailed'), 'error')
    }
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Handle toggle enable/disable
 */
async function handleToggle(connectionId, enable) {
  try {
    await toggleConnection(currentChannelType, connectionId, enable)
    showToast(enable 
      ? t('channel.enableSuccess') 
      : t('channel.disableSuccess'), 'success')
    // Refresh the detail view
    await renderChannelDetailView(currentChannelType)
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Handle delete confirmation
 */
async function handleDelete() {
  if (!pendingDeleteId) return

  try {
    await deleteConnection(currentChannelType, pendingDeleteId)
    showToast(t('channel.deleteSuccess'), 'success')
    hideDeleteDialog()
    // Refresh the detail view
    await renderChannelDetailView(currentChannelType)
    await refreshChannelTypes()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Refresh channel types list
 */
async function refreshChannelTypes() {
  const channels = await fetchChannelTypes()
  renderChannelTypes(channels)
}

// ========== Delete Dialog ==========

/**
 * Show delete confirmation dialog
 */
function showDeleteConfirm(connectionId) {
  pendingDeleteId = connectionId
  const dialog = $('#deleteDialog')
  if (dialog) dialog.classList.remove('hidden')
}

/**
 * Hide delete dialog
 */
function hideDeleteDialog() {
  pendingDeleteId = null
  const dialog = $('#deleteDialog')
  if (dialog) dialog.classList.add('hidden')
}

export default { mount, unmount }
