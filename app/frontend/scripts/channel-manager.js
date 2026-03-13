/**
 * Channel Manager Module
 * Handles channel connection management UI with URL-based routing
 */

import { initI18n, t, updatePageTranslations } from './i18n.js';

// State
let currentChannelType = null;
let currentSchema = null;
let allChannels = [];
let editingConnectionId = null;
let pendingDeleteId = null;

/**
 * Get localized channel name
 */
function getChannelName(channel) {
    const translatedName = t(`channel.name_${channel.type}`);
    // If translation exists (not returning the key), use it
    if (translatedName && !translatedName.startsWith('channel.name_')) {
        return translatedName;
    }
    return channel.name || channel.type;
}

// ========== URL Routing ==========

/**
 * Get channel type from URL query parameter
 */
function getChannelTypeFromURL() {
    const params = new URLSearchParams(window.location.search);
    return params.get('type');
}

/**
 * Navigate to a channel type (update URL)
 */
function navigateToChannel(type) {
    const url = new URL(window.location.href);
    if (type) {
        url.searchParams.set('type', type);
    } else {
        url.searchParams.delete('type');
    }
    window.history.pushState({}, '', url);
    handleRouteChange();
}

/**
 * Handle browser back/forward navigation
 */
window.addEventListener('popstate', handleRouteChange);

// ========== API Functions ==========

/**
 * Fetch all available channel types
 */
async function fetchChannelTypes() {
    try {
        const res = await fetch('/api/channels');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (error) {
        console.error('[ChannelManager] Failed to fetch channel types:', error);
        return [];
    }
}

/**
 * Fetch channel configuration schema
 */
async function fetchChannelSchema(type) {
    try {
        const res = await fetch(`/api/channels/${type}/schema`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (error) {
        console.error(`[ChannelManager] Failed to fetch schema for ${type}:`, error);
        return null;
    }
}

/**
 * Fetch connections for a channel type
 */
async function fetchConnections(type) {
    try {
        const res = await fetch(`/api/channels/${type}/connections`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (error) {
        console.error(`[ChannelManager] Failed to fetch connections for ${type}:`, error);
        return { connections: [] };
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
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
}

/**
 * Update an existing connection
 */
async function updateConnection(type, id, data) {
    const res = await fetch(`/api/channels/${type}/connections/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
}

/**
 * Delete a connection
 */
async function deleteConnection(type, id) {
    const res = await fetch(`/api/channels/${type}/connections/${id}`, {
        method: 'DELETE'
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return true;
}

/**
 * Toggle connection enabled state
 */
async function toggleConnection(type, id, enable) {
    const action = enable ? 'enable' : 'disable';
    const res = await fetch(`/api/channels/${type}/connections/${id}/${action}`, {
        method: 'POST'
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
}

/**
 * Verify connection configuration
 */
async function verifyConnection(type, id) {
    const res = await fetch(`/api/channels/${type}/connections/${id}/verify`, {
        method: 'POST'
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
}

// ========== UI Rendering ==========

/**
 * Render channel type cards (sidebar + main grid)
 */
function renderChannelTypes(channels) {
    allChannels = channels || [];
    const container = document.getElementById('channelTypes');
    const sidebarContainer = document.getElementById('sidebarChannelList');
    
    if (!channels || channels.length === 0) {
        if (container) {
            container.innerHTML = `<div class="channel-empty" data-i18n="channel.noChannels">${t('channel.noChannels')}</div>`;
        }
        return;
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
        `).join('');
        
        // Bind click events - use navigateToChannel for URL routing
        container.querySelectorAll('.channel-type-card').forEach(card => {
            card.addEventListener('click', () => navigateToChannel(card.dataset.type));
        });
    }
    
    // Render sidebar list
    if (sidebarContainer) {
        sidebarContainer.innerHTML = channels.map(channel => `
            <div class="sidebar-channel-item ${currentChannelType === channel.type ? 'active' : ''}" 
                 data-type="${channel.type}">
                <span class="channel-name">${getChannelName(channel)}</span>
            </div>
        `).join('');
        
        // Bind click events - use navigateToChannel for URL routing
        sidebarContainer.querySelectorAll('.sidebar-channel-item').forEach(item => {
            item.addEventListener('click', () => navigateToChannel(item.dataset.type));
        });
    }
}

/**
 * Handle route change - render appropriate view based on URL
 */
async function handleRouteChange() {
    const type = getChannelTypeFromURL();
    currentChannelType = type;
    
    // Update active states in UI
    document.querySelectorAll('.channel-type-card').forEach(card => {
        card.classList.toggle('active', card.dataset.type === type);
    });
    document.querySelectorAll('.sidebar-channel-item').forEach(item => {
        item.classList.toggle('active', item.dataset.type === type);
    });
    
    const channelListView = document.getElementById('channelListView');
    const channelDetailView = document.getElementById('channelDetailView');
    
    if (type) {
        // Show channel detail/config view
        if (channelListView) channelListView.style.display = 'none';
        if (channelDetailView) channelDetailView.style.display = 'block';
        await renderChannelDetailView(type);
    } else {
        // Show channel list view
        if (channelListView) channelListView.style.display = 'block';
        if (channelDetailView) channelDetailView.style.display = 'none';
    }
}

/**
 * Render channel detail/config view
 */
async function renderChannelDetailView(type) {
    const container = document.getElementById('channelDetailView');
    if (!container) return;
    
    // Find channel info
    const channelInfo = allChannels.find(c => c.type === type) || { type, name: type };
    
    // Fetch schema and connections in parallel
    const [schema, connectionsData] = await Promise.all([
        fetchChannelSchema(type),
        fetchConnections(type)
    ]);
    
    currentSchema = schema;
    const connections = connectionsData.connections || [];
    
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
    `;
    
    // Bind events
    bindDetailViewEvents();
}

/**
 * Render a single connection item
 */
function renderConnectionItem(conn) {
    const statusClass = conn.enabled ? 'connected' : 'disconnected';
    const statusText = conn.enabled ? t('channel.connected') : t('channel.disconnected');
    const toggleText = conn.enabled ? t('channel.disable') : t('channel.enable');
    
    // Show first config field as preview
    const configPreview = conn.config 
        ? Object.entries(conn.config).slice(0, 1).map(([k, v]) => `${k}: ${String(v).slice(0, 16)}...`).join('')
        : '';
    
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
    `;
}

/**
 * Get localized field text (title, description, placeholder)
 */
function getFieldText(key, textType, fallback) {
    const translated = t(`channel.field.${key}.${textType}`);
    // If translation exists (not returning the key), use it
    if (translated && !translated.startsWith('channel.field.')) {
        return translated;
    }
    return fallback || '';
}

/**
 * Render config form from JSON Schema
 */
function renderConfigForm(schema, values = {}) {
    if (!schema || !schema.properties) {
        return `<div class="form-error">${t('channel.schemaLoadFailed')}</div>`;
    }
    
    const properties = schema.properties || {};
    const required = schema.required || [];
    
    // Get localized placeholder for connection name
    const namePlaceholder = t('channel.connectionNamePlaceholder');
    
    // Connection name field first
    let html = `
        <div class="form-group">
            <label>${t('channel.connectionName')} <span class="required">*</span></label>
            <input type="text" name="_name" value="${values.name || ''}" 
                   placeholder="${namePlaceholder}" required>
        </div>
    `;
    
    // Generate fields from schema properties
    for (const [key, prop] of Object.entries(properties)) {
        const isRequired = required.includes(key);
        const value = values.config?.[key] || '';
        const inputType = prop.type === 'string' && (key.toLowerCase().includes('secret') || key.toLowerCase().includes('password')) ? 'password' : 'text';
        
        // Get localized text with fallback to schema values
        const title = getFieldText(key, 'title', prop.title || key);
        const description = getFieldText(key, 'description', prop.description || '');
        const placeholder = getFieldText(key, 'placeholder', prop.placeholder || prop.description || '');
        
        html += `
            <div class="form-group">
                <label>${title} ${isRequired ? '<span class="required">*</span>' : ''}</label>
                <input type="${inputType}" name="${key}" value="${value}" 
                       placeholder="${placeholder}" ${isRequired ? 'required' : ''}>
                ${description ? `<span class="hint">${description}</span>` : ''}
            </div>
        `;
    }
    
    return html;
}

/**
 * Bind events for detail view
 */
function bindDetailViewEvents() {
    // Back button
    document.getElementById('btnBackToList')?.addEventListener('click', () => navigateToChannel(null));
    
    // Add connection button
    document.getElementById('btnAddConnection')?.addEventListener('click', showNewConnectionForm);
    
    // Form buttons
    document.getElementById('btnCancelForm')?.addEventListener('click', hideConfigForm);
    document.getElementById('btnVerifyConfig')?.addEventListener('click', handleVerify);
    document.getElementById('btnSaveConfig')?.addEventListener('click', handleSave);
    
    // Connection item actions
    document.querySelectorAll('.connection-item').forEach(item => {
        item.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const action = btn.dataset.action;
                const connId = item.dataset.id;
                handleConnectionAction(action, connId, btn.dataset.enabled === 'true');
            });
        });
    });
}

/**
 * Show form for new connection
 */
function showNewConnectionForm() {
    editingConnectionId = null;
    const formSection = document.getElementById('configFormSection');
    const formTitle = document.getElementById('configFormTitle');
    const form = document.getElementById('configForm');
    
    if (formTitle) formTitle.textContent = t('channel.newConnection');
    if (form) form.innerHTML = renderConfigForm(currentSchema);
    if (formSection) formSection.style.display = 'block';
    
    // Re-bind form events
    document.getElementById('btnCancelForm')?.addEventListener('click', hideConfigForm);
    document.getElementById('btnVerifyConfig')?.addEventListener('click', handleVerify);
    document.getElementById('btnSaveConfig')?.addEventListener('click', handleSave);
}

/**
 * Show form for editing connection
 */
async function showEditConnectionForm(connectionId) {
    editingConnectionId = connectionId;
    
    // Fetch current connection data
    const data = await fetchConnections(currentChannelType);
    const connection = (data.connections || []).find(c => c.id === connectionId);
    
    if (!connection) {
        showToast(t('channel.connectionNotFound'), 'error');
        return;
    }
    
    const formSection = document.getElementById('configFormSection');
    const formTitle = document.getElementById('configFormTitle');
    const form = document.getElementById('configForm');
    
    if (formTitle) formTitle.textContent = t('channel.editConnection');
    if (form) form.innerHTML = renderConfigForm(currentSchema, connection);
    if (formSection) formSection.style.display = 'block';
}

/**
 * Hide config form
 */
function hideConfigForm() {
    const formSection = document.getElementById('configFormSection');
    if (formSection) formSection.style.display = 'none';
    editingConnectionId = null;
}

// ========== Legacy compatibility - redirect old selectChannelType calls ==========

async function selectChannelType(type) {
    navigateToChannel(type);
}

/**
 * Handle connection actions (edit/toggle/delete)
 */
async function handleConnectionAction(action, connectionId, isEnabled) {
    switch (action) {
        case 'edit':
            await showEditConnectionForm(connectionId);
            break;
        case 'toggle':
            await handleToggle(connectionId, !isEnabled);
            break;
        case 'delete':
            showDeleteConfirm(connectionId);
            break;
    }
}

/**
 * Show form for new connection
 */
function showNewForm() {
    editingConnectionId = null;
    const titleEl = document.getElementById('formTitle');
    if (titleEl) titleEl.textContent = t('channel.newConnection');
    
    renderFormFromSchema(currentSchema);
    
    const wrapper = document.getElementById('connectionFormWrapper');
    if (wrapper) wrapper.style.display = 'block';
}

/**
 * Show form for editing connection
 */
async function showEditForm(connectionId) {
    editingConnectionId = connectionId;
    const titleEl = document.getElementById('formTitle');
    if (titleEl) titleEl.textContent = t('channel.editConnection');
    
    // Fetch current connection data
    const data = await fetchConnections(currentChannelType);
    const connection = (data.connections || []).find(c => c.id === connectionId);
    
    if (connection) {
        renderFormFromSchema(currentSchema, connection);
    }
    
    const wrapper = document.getElementById('connectionFormWrapper');
    if (wrapper) wrapper.style.display = 'block';
}

/**
 * Hide form
 */
function hideForm() {
    const wrapper = document.getElementById('connectionFormWrapper');
    if (wrapper) wrapper.style.display = 'none';
    editingConnectionId = null;
}

/**
 * Handle form save
 */
async function handleSave() {
    const form = document.getElementById('configForm');
    if (!form) return;
    
    const inputs = form.querySelectorAll('input');
    const config = {};
    let name = '';
    let hasValidationError = false;
    
    // Validate all required fields
    for (const input of inputs) {
        if (input.required && !input.value.trim()) {
            input.classList.add('input-error');
            hasValidationError = true;
        } else {
            input.classList.remove('input-error');
        }
        
        if (input.name === '_name') {
            name = input.value.trim();
        } else if (input.value.trim()) {
            config[input.name] = input.value.trim();
        }
    }
    
    if (hasValidationError) {
        showToast(t('channel.requiredFieldsMissing') || 'Please fill in all required fields', 'error');
        return;
    }
    
    if (!name) {
        showToast(t('channel.nameRequired'), 'error');
        return;
    }
    
    try {
        if (editingConnectionId) {
            await updateConnection(currentChannelType, editingConnectionId, { name, config });
            showToast(t('channel.updateSuccess'), 'success');
        } else {
            await createConnection(currentChannelType, { name, config });
            showToast(t('channel.createSuccess'), 'success');
        }
        
        hideConfigForm();
        // Refresh the detail view
        await renderChannelDetailView(currentChannelType);
        await refreshChannelTypes();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Handle verify button
 */
async function handleVerify() {
    if (!editingConnectionId) {
        showToast(t('channel.saveFirst'), 'warning');
        return;
    }
    
    try {
        const result = await verifyConnection(currentChannelType, editingConnectionId);
        if (result.valid) {
            showToast(t('channel.verifySuccess'), 'success');
        } else {
            showToast(result.errors?.join(', ') || t('channel.verifyFailed'), 'error');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Handle toggle enable/disable
 */
async function handleToggle(connectionId, enable) {
    try {
        await toggleConnection(currentChannelType, connectionId, enable);
        showToast(enable 
            ? t('channel.enableSuccess') 
            : t('channel.disableSuccess'), 'success');
        // Refresh the detail view
        await renderChannelDetailView(currentChannelType);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Handle delete confirmation
 */
async function handleDelete() {
    if (!pendingDeleteId) return;
    
    try {
        await deleteConnection(currentChannelType, pendingDeleteId);
        showToast(t('channel.deleteSuccess'), 'success');
        hideDeleteDialog();
        // Refresh the detail view
        await renderChannelDetailView(currentChannelType);
        await refreshChannelTypes();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Refresh channel types list
 */
async function refreshChannelTypes() {
    const channels = await fetchChannelTypes();
    renderChannelTypes(channels);
}

/**
 * Show delete confirmation dialog
 */
function showDeleteConfirm(connectionId) {
    pendingDeleteId = connectionId;
    const dialog = document.getElementById('deleteDialog');
    if (dialog) dialog.classList.remove('hidden');
}

/**
 * Hide delete dialog
 */
function hideDeleteDialog() {
    pendingDeleteId = null;
    const dialog = document.getElementById('deleteDialog');
    if (dialog) dialog.classList.add('hidden');
}

// ========== Toast Notification ==========

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    
    toast.textContent = message;
    toast.className = `toast ${type}`;
    
    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3000);
}

// ========== Initialization ==========

async function init() {
    console.log('[ChannelManager] Initializing...');
    
    try {
        // Initialize i18n
        await initI18n();
        updatePageTranslations();
        
        // Load channel types
        const channels = await fetchChannelTypes();
        renderChannelTypes(channels);
        
        // Delete dialog buttons
        document.getElementById('btnCancelDelete')?.addEventListener('click', hideDeleteDialog);
        document.getElementById('btnConfirmDelete')?.addEventListener('click', handleDelete);
        
        // Handle initial route (check URL params)
        await handleRouteChange();
        
        console.log('[ChannelManager] Initialized successfully');
    } catch (error) {
        console.error('[ChannelManager] Initialization failed:', error);
    }
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
