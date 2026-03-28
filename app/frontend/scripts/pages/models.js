/**
 * models.js - Models Page Module
 *
 * Model management page for SPA architecture.
 * Migrated from models.html inline scripts.
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { t, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'

// ========== Module State ==========
let mounted = false
let containerRef = null

// Model configs state
let modelConfigs = []
let editingModelId = null
let pendingDeleteId = null

// Provider data loaded from backend
let PROVIDER_PRESETS = {}
let PROVIDER_MODELS = {}

// Providers that don't require API Key (local/self-hosted)
const NO_API_KEY_PROVIDERS = ['ollama', 'vllm', 'custom']

// Debounce timer for API key input
let fetchDebounceTimer = null

// Event listener references for cleanup
let eventListeners = []

// ========== HTML Templates ==========

const PAGE_HTML = `
<div class="channel-content">
    <!-- Model Configs Table -->
    <section class="channel-section">
        <p class="section-desc" data-i18n="model.pageDescription">Manage your AI model configurations</p>
        <div style="margin: 16px 0;">
            <button class="btn-primary" id="btnCreateModel">
                <span data-i18n="model.newButton">+ New Model Config</span>
            </button>
        </div>
        <div class="connections-list" id="modelConfigsList">
            <div class="channel-loading" data-i18n="model.loading">Loading...</div>
        </div>
    </section>
</div>

<!-- Create/Edit Modal -->
<div id="modelModal" class="confirm-dialog hidden">
    <div class="confirm-content">
        <h3 id="modalTitle" style="color: #333;" data-i18n="model.createTitle">Create Model Config</h3>
        <form id="modelForm" class="config-form">
            <!-- Basic Information (always visible) -->
            <div class="form-group">
                <label><span data-i18n="model.name">Name</span> <span class="required">*</span></label>
                <input type="text" name="name" required data-i18n-placeholder="model.namePlaceholder" placeholder="e.g. gpt-4-main">
            </div>
            <div class="form-group">
                <label><span data-i18n="model.provider">Provider</span> <span class="required">*</span></label>
                <select name="provider" required class="form-select">
                    <option value="" disabled selected data-i18n="model.providerPlaceholder">Select provider</option>
                    <option value="anthropic" data-i18n="model.providers.anthropic">Anthropic</option>
                    <option value="baichuan" data-i18n="model.providers.baichuan">Baichuan</option>
                    <option value="cohere" data-i18n="model.providers.cohere">Cohere</option>
                    <option value="deepseek" data-i18n="model.providers.deepseek">DeepSeek</option>
                    <option value="doubao" data-i18n="model.providers.doubao">Doubao</option>
                    <option value="google" data-i18n="model.providers.google">Google Gemini</option>
                    <option value="groq" data-i18n="model.providers.groq">Groq</option>
                    <option value="hunyuan" data-i18n="model.providers.hunyuan">Hunyuan</option>
                    <option value="minimax" data-i18n="model.providers.minimax">MiniMax</option>
                    <option value="mistral" data-i18n="model.providers.mistral">Mistral AI</option>
                    <option value="moonshot" data-i18n="model.providers.moonshot">Moonshot</option>
                    <option value="ollama" data-i18n="model.providers.ollama">Ollama</option>
                    <option value="openai" data-i18n="model.providers.openai">OpenAI</option>
                    <option value="qwen" data-i18n="model.providers.qwen">Qwen</option>
                    <option value="siliconflow" data-i18n="model.providers.siliconflow">SiliconFlow</option>
                    <option value="spark" data-i18n="model.providers.spark">Spark</option>
                    <option value="stepfun" data-i18n="model.providers.stepfun">StepFun</option>
                    <option value="vllm" data-i18n="model.providers.vllm">vLLM</option>
                    <option value="yi" data-i18n="model.providers.yi">Yi</option>
                    <option value="zhipu" data-i18n="model.providers.zhipu">ZhipuAI</option>
                    <option value="custom" data-i18n="model.providers.custom">Custom</option>
                </select>
            </div>
            <div class="form-group">
                <label><span data-i18n="model.modelId">Model ID</span> <span class="required">*</span></label>
                <div style="display: flex; flex-direction: column; gap: 8px; flex: 1;">
                    <select name="model_id" id="modelIdSelect" required class="form-select">
                        <option value="" disabled selected data-i18n="model.modelIdPlaceholder">Select or enter model ID</option>
                    </select>
                    <input type="text" name="model_id_custom" id="modelIdCustom" style="display: none;" data-i18n-placeholder="model.modelIdCustomPlaceholder" placeholder="Enter custom model ID">
                </div>
            </div>
            <div class="form-group">
                <label><span data-i18n="model.apiKey">API Key</span> <span class="required" id="apiKeyRequired" style="display: none;">*</span></label>
                <div style="position: relative; flex: 1;">
                    <input type="password" name="api_key" id="apiKeyInput" data-i18n-placeholder="model.apiKeyPlaceholder" placeholder="Enter API key" style="padding-right: 40px; width: 100%;">
                    <button type="button" id="toggleApiKey" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; cursor: pointer; color: #666;">
                        <svg id="eyeIcon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                        </svg>
                    </button>
                </div>
            </div>

            <!-- Advanced Settings Toggle -->
            <div class="advanced-toggle" id="advancedToggle">
                <span id="advancedArrow">▶</span> <span data-i18n="model.advancedSettings">Advanced Settings</span>
            </div>

            <!-- Advanced Settings (collapsed by default) -->
            <div id="advancedFields" style="display: none;">
                <div class="form-group">
                    <label data-i18n="model.displayName">Display Name</label>
                    <input type="text" name="display_name" data-i18n-placeholder="model.displayNamePlaceholder" placeholder="e.g. GPT-4 Main">
                </div>
                <div class="form-group">
                    <label data-i18n="model.baseUrl">Base URL</label>
                    <input type="text" name="base_url" data-i18n-placeholder="model.baseUrlPlaceholder" placeholder="e.g. https://api.openai.com/v1">
                </div>
                <div class="form-group">
                    <label data-i18n="model.apiType">API Type</label>
                    <select name="api_type" class="form-select">
                        <option value="openai">OpenAI</option>
                        <option value="anthropic">Anthropic</option>
                        <option value="google">Google</option>
                    </select>
                </div>
                <div class="form-group">
                    <label data-i18n="model.contextWindow">Context Window</label>
                    <input type="number" name="context_window" value="128000" placeholder="128000">
                </div>
                <div class="form-group">
                    <label data-i18n="model.maxTokens">Max Tokens</label>
                    <input type="number" name="max_tokens" value="4096" placeholder="4096">
                </div>
                <div class="form-group">
                    <label data-i18n="model.temperature">Temperature</label>
                    <input type="number" name="temperature" step="0.1" value="0.7" placeholder="0.7">
                </div>
                <div class="form-group">
                    <label data-i18n="model.priority">Priority</label>
                    <div style="flex: 1;">
                        <input type="number" name="priority" value="0" placeholder="0" style="width: 100%;">
                        <span class="hint" data-i18n="model.priorityHint">Higher priority models are preferred</span>
                    </div>
                </div>
                <div class="form-group">
                    <label data-i18n="model.weight">Weight</label>
                    <div style="flex: 1;">
                        <input type="number" name="weight" value="100" placeholder="100" style="width: 100%;">
                        <span class="hint" data-i18n="model.weightHint">Used for load balancing</span>
                    </div>
                </div>
                <div class="form-group">
                    <label data-i18n="model.description">Description</label>
                    <textarea name="description" rows="3" data-i18n-placeholder="model.descriptionPlaceholder" placeholder="Optional description"></textarea>
                </div>
                <div class="form-group" style="padding-left: 132px;">
                    <label style="display: flex; align-items: center; gap: 8px; width: auto; min-width: 0; text-align: left; padding-top: 0;">
                        <input type="checkbox" name="is_active" checked style="width: auto;">
                        <span data-i18n="model.isActive">Is Active</span>
                    </label>
                </div>
            </div>
        </form>
        <div class="confirm-buttons">
            <button class="btn-cancel" id="btnCancelModal" data-i18n="model.cancel">Cancel</button>
            <button class="btn-primary" id="btnSaveModel" data-i18n="model.save">Save</button>
        </div>
    </div>
</div>

<!-- Delete Confirm Dialog -->
<div id="deleteDialog" class="confirm-dialog hidden">
    <div class="confirm-content">
        <h3 data-i18n="model.deleteConfirmTitle">Confirm Delete</h3>
        <p id="deleteMessage" data-i18n="model.deleteConfirmMessage">Are you sure you want to delete this model config?</p>
        <div class="confirm-buttons">
            <button class="btn-cancel" id="btnCancelDelete" data-i18n="model.cancel">Cancel</button>
            <button class="btn-confirm" id="btnConfirmDelete" data-i18n="model.delete">Delete</button>
        </div>
    </div>
</div>
`

// ========== API Functions ==========

async function fetchModelConfigs() {
  try {
    const res = await fetch('/api/model-configs')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    return Array.isArray(data) ? data : (data.configs || data.model_configs || [])
  } catch (error) {
    console.error('[ModelsPage] Failed to fetch:', error)
    return []
  }
}

async function createModelConfig(data) {
  const res = await fetch('/api/model-configs', {
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

async function updateModelConfig(id, data) {
  const res = await fetch(`/api/model-configs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

async function deleteModelConfigApi(id) {
  const res = await fetch(`/api/model-configs/${id}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return true
}

// ========== Provider Data ==========

async function loadProviderData() {
  try {
    const res = await fetch('/api/providers')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()

    // Build PROVIDER_PRESETS and PROVIDER_MODELS from API response
    for (const [name, info] of Object.entries(data)) {
      PROVIDER_PRESETS[name] = {
        base_url: info.base_url || '',
        api_type: info.api_type || 'openai',
      }
      PROVIDER_MODELS[name] = info.models || []
    }
    console.log('[ModelsPage] Provider data loaded:', Object.keys(data).length, 'providers')
  } catch (error) {
    console.error('[ModelsPage] Failed to load provider data:', error)
    // Fallback: leave empty, static options will work from select
  }

  // Restore cached models from localStorage (with 24h TTL)
  try {
    const cachedJson =
      localStorage.getItem('xuanwu_fetched_models') ||
      localStorage.getItem('atlasclaw_fetched_models') ||
      '{}'
    const cached = JSON.parse(cachedJson)
    const now = Date.now()
    const TTL = 24 * 60 * 60 * 1000 // 24 hours
    for (const [prov, entry] of Object.entries(cached)) {
      if (entry.timestamp && (now - entry.timestamp) < TTL && entry.models?.length) {
        // Merge cached models with static preset
        const staticModels = PROVIDER_MODELS[prov] || []
        PROVIDER_MODELS[prov] = [...new Set([...entry.models, ...staticModels])].sort((a, b) => a.localeCompare(b))
      }
    }
    console.log('[ModelsPage] Restored cached models from localStorage')
  } catch (e) {
    // Ignore storage errors
  }
}

// ========== Fetch Models from Provider API ==========

async function fetchModelsFromProvider(silent = false) {
  const form = containerRef?.querySelector('#modelForm')
  if (!form) return

  const provider = form.querySelector('[name="provider"]')?.value
  const apiKey = form.querySelector('[name="api_key"]')?.value?.trim()
  const baseUrl = form.querySelector('[name="base_url"]')?.value?.trim()

  if (!provider || provider === 'custom') {
    if (!silent) showToast(t('model.providerRequired'), 'error')
    return
  }

  // Show loading state on model select
  const modelSelect = containerRef?.querySelector('#modelIdSelect')
  if (modelSelect) {
    modelSelect.disabled = true
    // Add loading option at the beginning
    const loadingOption = document.createElement('option')
    loadingOption.value = '__loading__'
    loadingOption.textContent = t('model.loadingModels')
    loadingOption.disabled = true
    modelSelect.insertBefore(loadingOption, modelSelect.firstChild)
    modelSelect.value = '__loading__'
  }

  try {
    const res = await fetch('/api/providers/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, base_url: baseUrl, api_key: apiKey })
    })

    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()

    // Get current model ID to preserve selection
    const currentModelId = containerRef?.querySelector('#modelIdCustom')?.value?.trim() ||
      (modelSelect?.value && modelSelect.value !== '__loading__' && modelSelect.value !== '__custom__' ? modelSelect.value : '')

    if (data.models && data.models.length > 0) {
      // MERGE with static preset list
      const staticModels = PROVIDER_MODELS[provider] || []
      const allModels = [...new Set([...data.models, ...staticModels])]
      allModels.sort((a, b) => a.localeCompare(b))

      // Update cache
      PROVIDER_MODELS[provider] = allModels

      // Save to localStorage
      try {
        const cachedJson =
          localStorage.getItem('xuanwu_fetched_models') ||
          localStorage.getItem('atlasclaw_fetched_models') ||
          '{}'
        const cached = JSON.parse(cachedJson)
        cached[provider] = { models: allModels, timestamp: Date.now() }
        localStorage.setItem('xuanwu_fetched_models', JSON.stringify(cached))
        localStorage.removeItem('atlasclaw_fetched_models')
      } catch (e) { /* ignore storage errors */ }

      // Rebuild dropdown
      updateModelIdOptions(provider, currentModelId)

      if (!silent) {
        showToast(t('model.fetchSuccess'), 'success')
      }
    } else if (!silent) {
      showToast(t('model.noModelsFound'), 'warning')
    }
  } catch (error) {
    console.error('[ModelsPage] Failed to fetch models:', error)
    if (!silent) {
      showToast(error.message, 'error')
    }
    // Silent mode: just restore the dropdown without fetched models
    const currentModelId = containerRef?.querySelector('#modelIdCustom')?.value?.trim() || ''
    updateModelIdOptions(provider, currentModelId)
  } finally {
    if (modelSelect) {
      modelSelect.disabled = false
      // Remove loading option if still present
      const loadingOption = modelSelect.querySelector('option[value="__loading__"]')
      if (loadingOption) loadingOption.remove()
    }
  }
}

// ========== Model ID Dropdown Logic ==========

function updateModelIdOptions(provider, currentValue) {
  const select = containerRef?.querySelector('#modelIdSelect')
  const customInput = containerRef?.querySelector('#modelIdCustom')
  if (!select) return

  const models = PROVIDER_MODELS[provider] || []

  // Build options
  let html = `<option value="" disabled>${t('model.modelIdPlaceholder')}</option>`
  models.forEach(m => {
    html += `<option value="${m}"${m === currentValue ? ' selected' : ''}>${m}</option>`
  })
  html += `<option value="__custom__">${t('model.customModelId')}</option>`
  select.innerHTML = html

  // If currentValue exists but not in the list, select custom and show input
  if (currentValue && !models.includes(currentValue) && currentValue !== '__custom__') {
    select.value = '__custom__'
    customInput.style.display = 'block'
    customInput.value = currentValue
  } else if (currentValue && models.includes(currentValue)) {
    select.value = currentValue
    customInput.style.display = 'none'
    customInput.value = ''
  } else {
    select.value = ''
    customInput.style.display = 'none'
    customInput.value = ''
  }
}

// ========== API Key Required ==========

function updateApiKeyRequired(provider) {
  const required = provider && !NO_API_KEY_PROVIDERS.includes(provider)
  const asterisk = containerRef?.querySelector('#apiKeyRequired')
  if (asterisk) {
    asterisk.style.display = required ? 'inline' : 'none'
  }
}

// ========== UI Rendering ==========

function renderModelConfigs(configs) {
  modelConfigs = configs || []
  const container = containerRef?.querySelector('#modelConfigsList')
  if (!container) return

  if (!configs || configs.length === 0) {
    container.innerHTML = `<div class="connections-empty">${t('model.noModels')}</div>`
    return
  }

  container.innerHTML = configs.map(config => `
    <div class="connection-item" data-id="${config.id}">
      <div class="connection-status ${config.is_active ? 'connected' : 'disconnected'}"></div>
      <div class="connection-info" style="flex: 2;">
        <div class="connection-name">${config.name || config.id}</div>
        <div class="connection-detail">${config.display_name || ''}</div>
      </div>
      <div class="connection-detail" style="flex: 1; padding: 0 12px;">${config.provider || '-'}</div>
      <div class="connection-detail" style="flex: 1.5; padding: 0 12px;">${config.model_id || '-'}</div>
      <div class="connection-detail" style="flex: 0.8; padding: 0 12px;">${config.api_type || 'openai'}</div>
      <div class="connection-detail" style="flex: 0.8; padding: 0 12px;">${config.context_window || '-'}</div>
      <div class="connection-detail" style="flex: 0.6; padding: 0 12px;">${config.max_tokens || '-'}</div>
      <div class="connection-status-text" style="flex: 0.6;">${config.is_active ? t('model.active') : t('model.inactive')}</div>
      <div class="connection-actions">
        <button class="btn-small btn-edit" data-action="edit">${t('model.edit')}</button>
        <button class="btn-small btn-toggle" data-action="toggle" data-active="${config.is_active}">${config.is_active ? t('model.disable') : t('model.enable')}</button>
        <button class="btn-small btn-delete" data-action="delete">${t('model.delete')}</button>
      </div>
    </div>
  `).join('')

  // Bind events
  container.querySelectorAll('.connection-item').forEach(item => {
    item.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        const action = btn.dataset.action
        const configId = item.dataset.id
        handleModelAction(action, configId, btn.dataset.active === 'true')
      })
    })
  })
}

// ========== Modal Functions ==========

function openCreateModal() {
  editingModelId = null
  const modalTitle = containerRef?.querySelector('#modalTitle')
  const form = containerRef?.querySelector('#modelForm')
  if (!modalTitle || !form) return

  modalTitle.textContent = t('model.createTitle')
  form.reset()
  form.querySelector('[name="is_active"]').checked = true

  // Collapse advanced settings for new model
  const advFields = containerRef?.querySelector('#advancedFields')
  const advArrow = containerRef?.querySelector('#advancedArrow')
  if (advFields) { advFields.style.display = 'none' }
  if (advArrow) { advArrow.textContent = '▶' }

  // Reset auto-fill tracking
  const baseUrlInput = form.querySelector('[name="base_url"]')
  if (baseUrlInput) { baseUrlInput.dataset.autoFilled = 'false' }

  // Reset model ID dropdown
  const modelSelect = containerRef?.querySelector('#modelIdSelect')
  if (modelSelect) {
    modelSelect.innerHTML = `<option value="" disabled selected>${t('model.modelIdPlaceholder')}</option>`
  }
  const customInput = containerRef?.querySelector('#modelIdCustom')
  if (customInput) customInput.style.display = 'none'

  // Hide API Key required indicator for new model
  updateApiKeyRequired('')

  containerRef?.querySelector('#modelModal')?.classList.remove('hidden')
}

async function openEditModal(id) {
  editingModelId = id
  const config = modelConfigs.find(c => c.id === id)
  if (!config) {
    showToast(t('model.notFound'), 'error')
    return
  }

  const modalTitle = containerRef?.querySelector('#modalTitle')
  const form = containerRef?.querySelector('#modelForm')
  if (!modalTitle || !form) return

  modalTitle.textContent = t('model.editTitle')

  form.querySelector('[name="name"]').value = config.name || ''
  form.querySelector('[name="display_name"]').value = config.display_name || ''
  form.querySelector('[name="provider"]').value = config.provider || ''

  // Update API Key required indicator based on provider
  updateApiKeyRequired(config.provider || '')

  // Update model ID dropdown based on provider, then set value
  updateModelIdOptions(config.provider || '', config.model_id || '')

  form.querySelector('[name="base_url"]').value = config.base_url || ''
  form.querySelector('[name="api_key"]').value = config.api_key || ''
  form.querySelector('[name="api_type"]').value = config.api_type || 'openai'
  form.querySelector('[name="context_window"]').value = config.context_window || 128000
  form.querySelector('[name="max_tokens"]').value = config.max_tokens || 4096
  form.querySelector('[name="temperature"]').value = config.temperature || 0.7
  form.querySelector('[name="priority"]').value = config.priority || 0
  form.querySelector('[name="weight"]').value = config.weight || 100
  form.querySelector('[name="description"]').value = config.description || ''
  form.querySelector('[name="is_active"]').checked = config.is_active !== false

  // Keep advanced settings collapsed when editing (user can expand if needed)
  const advFields = containerRef?.querySelector('#advancedFields')
  const advArrow = containerRef?.querySelector('#advancedArrow')
  if (advFields) { advFields.style.display = 'none' }
  if (advArrow) { advArrow.textContent = '▶' }

  // Mark base_url as not auto-filled when editing existing config
  const baseUrlInput = form.querySelector('[name="base_url"]')
  if (baseUrlInput) { baseUrlInput.dataset.autoFilled = 'false' }

  containerRef?.querySelector('#modelModal')?.classList.remove('hidden')
}

function closeModal() {
  containerRef?.querySelector('#modelModal')?.classList.add('hidden')
  editingModelId = null
}

async function saveModelConfig() {
  const form = containerRef?.querySelector('#modelForm')
  if (!form) return

  const formData = new FormData(form)
  const modelIdSelect = containerRef?.querySelector('#modelIdSelect')
  const modelIdCustom = containerRef?.querySelector('#modelIdCustom')

  const data = {
    name: formData.get('name')?.trim(),
    display_name: formData.get('display_name')?.trim() || null,
    provider: formData.get('provider')?.trim(),
    model_id: (modelIdSelect?.value === '__custom__'
      ? modelIdCustom?.value?.trim()
      : modelIdSelect?.value?.trim()),
    base_url: formData.get('base_url')?.trim() || null,
    api_key: formData.get('api_key')?.trim() || null,
    api_type: formData.get('api_type') || 'openai',
    context_window: parseInt(formData.get('context_window')) || 128000,
    max_tokens: parseInt(formData.get('max_tokens')) || 4096,
    temperature: parseFloat(formData.get('temperature')) || 0.7,
    priority: parseInt(formData.get('priority')) || 0,
    weight: parseInt(formData.get('weight')) || 100,
    description: formData.get('description')?.trim() || null,
    is_active: form.querySelector('[name="is_active"]').checked
  }

  // Validation
  if (!data.name) {
    showToast(t('model.nameRequired'), 'error')
    return
  }
  if (!data.provider) {
    showToast(t('model.providerRequired'), 'error')
    return
  }
  if (!data.model_id || data.model_id === '__custom__') {
    showToast(t('model.modelIdRequired'), 'error')
    return
  }
  // API Key validation - required for cloud providers
  if (!NO_API_KEY_PROVIDERS.includes(data.provider) && !data.api_key) {
    showToast(t('model.apiKeyRequired') || 'API Key is required for this provider', 'error')
    return
  }

  try {
    if (editingModelId) {
      await updateModelConfig(editingModelId, data)
      showToast(t('model.updateSuccess'), 'success')
    } else {
      await createModelConfig(data)
      showToast(t('model.createSuccess'), 'success')
    }
    closeModal()
    await loadModelConfigs()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== Delete Dialog ==========

function showDeleteConfirm(id) {
  pendingDeleteId = id
  containerRef?.querySelector('#deleteDialog')?.classList.remove('hidden')
}

function hideDeleteDialog() {
  pendingDeleteId = null
  containerRef?.querySelector('#deleteDialog')?.classList.add('hidden')
}

async function confirmDelete() {
  if (!pendingDeleteId) return

  try {
    await deleteModelConfigApi(pendingDeleteId)
    showToast(t('model.deleteSuccess'), 'success')
    hideDeleteDialog()
    await loadModelConfigs()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== Action Handlers ==========

async function handleModelAction(action, configId, isActive) {
  switch (action) {
    case 'edit':
      await openEditModal(configId)
      break
    case 'toggle':
      await toggleModelStatus(configId, isActive)
      break
    case 'delete':
      showDeleteConfirm(configId)
      break
  }
}

async function toggleModelStatus(id, currentStatus) {
  try {
    const config = modelConfigs.find(c => c.id === id)
    if (!config) return

    await updateModelConfig(id, { ...config, is_active: !currentStatus })
    showToast(!currentStatus ? t('model.activated') : t('model.deactivated'), 'success')
    await loadModelConfigs()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== API Key Toggle ==========

function setupApiKeyToggle() {
  const toggleBtn = containerRef?.querySelector('#toggleApiKey')
  const apiKeyInput = containerRef?.querySelector('#apiKeyInput')
  const eyeIcon = containerRef?.querySelector('#eyeIcon')

  if (toggleBtn && apiKeyInput) {
    const handler = () => {
      const isPassword = apiKeyInput.type === 'password'
      apiKeyInput.type = isPassword ? 'text' : 'password'
      eyeIcon.innerHTML = isPassword
        ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>'
        : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>'
    }
    toggleBtn.addEventListener('click', handler)
    eventListeners.push({ el: toggleBtn, type: 'click', handler })
  }
}

// ========== Load Data ==========

async function loadModelConfigs() {
  const configs = await fetchModelConfigs()
  renderModelConfigs(configs)
}

// ========== Event Binding ==========

function bindEvents() {
  // Create button
  const btnCreate = containerRef?.querySelector('#btnCreateModel')
  if (btnCreate) {
    const handler = () => openCreateModal()
    btnCreate.addEventListener('click', handler)
    eventListeners.push({ el: btnCreate, type: 'click', handler })
  }

  // Cancel modal button
  const btnCancel = containerRef?.querySelector('#btnCancelModal')
  if (btnCancel) {
    const handler = () => closeModal()
    btnCancel.addEventListener('click', handler)
    eventListeners.push({ el: btnCancel, type: 'click', handler })
  }

  // Save button
  const btnSave = containerRef?.querySelector('#btnSaveModel')
  if (btnSave) {
    const handler = () => saveModelConfig()
    btnSave.addEventListener('click', handler)
    eventListeners.push({ el: btnSave, type: 'click', handler })
  }

  // Delete dialog buttons
  const btnCancelDelete = containerRef?.querySelector('#btnCancelDelete')
  if (btnCancelDelete) {
    const handler = () => hideDeleteDialog()
    btnCancelDelete.addEventListener('click', handler)
    eventListeners.push({ el: btnCancelDelete, type: 'click', handler })
  }

  const btnConfirmDelete = containerRef?.querySelector('#btnConfirmDelete')
  if (btnConfirmDelete) {
    const handler = () => confirmDelete()
    btnConfirmDelete.addEventListener('click', handler)
    eventListeners.push({ el: btnConfirmDelete, type: 'click', handler })
  }

  // Setup API key toggle
  setupApiKeyToggle()

  // Advanced settings toggle
  const advToggle = containerRef?.querySelector('#advancedToggle')
  if (advToggle) {
    const handler = () => {
      const fields = containerRef?.querySelector('#advancedFields')
      const arrow = containerRef?.querySelector('#advancedArrow')
      if (fields.style.display === 'none') {
        fields.style.display = 'block'
        arrow.textContent = '▼'
      } else {
        fields.style.display = 'none'
        arrow.textContent = '▶'
      }
    }
    advToggle.addEventListener('click', handler)
    eventListeners.push({ el: advToggle, type: 'click', handler })
  }

  // Provider change handler
  const providerSelect = containerRef?.querySelector('[name="provider"]')
  if (providerSelect) {
    const handler = (e) => {
      // Update API Key required indicator
      updateApiKeyRequired(e.target.value)

      const preset = PROVIDER_PRESETS[e.target.value]
      if (preset) {
        const form = containerRef?.querySelector('#modelForm')
        const baseUrlInput = form?.querySelector('[name="base_url"]')
        const apiTypeSelect = form?.querySelector('[name="api_type"]')
        // Only auto-fill if the field is empty or user hasn't manually edited
        if (!baseUrlInput.value || baseUrlInput.dataset.autoFilled === 'true') {
          baseUrlInput.value = preset.base_url
          baseUrlInput.dataset.autoFilled = 'true'
        }
        if (apiTypeSelect) {
          apiTypeSelect.value = preset.api_type
        }
      }
      // Update model ID options for the selected provider
      updateModelIdOptions(e.target.value, '')

      // Auto-fetch models if API key already exists
      const provider = e.target.value
      const apiKey = containerRef?.querySelector('#apiKeyInput')?.value?.trim()
      if (provider && provider !== 'custom' && apiKey && apiKey.length >= 8) {
        // Auto-fetch with slight delay to let UI update first
        setTimeout(() => fetchModelsFromProvider(true), 200)
      }
    }
    providerSelect.addEventListener('change', handler)
    eventListeners.push({ el: providerSelect, type: 'change', handler })
  }

  // API key input with debounced fetch
  const apiKeyInput = containerRef?.querySelector('#apiKeyInput')
  if (apiKeyInput) {
    const handler = function() {
      clearTimeout(fetchDebounceTimer)
      const provider = containerRef?.querySelector('[name="provider"]')?.value
      const apiKey = this.value?.trim()
      // Only auto-fetch for non-custom providers with a valid-looking API key
      if (provider && provider !== 'custom' && apiKey && apiKey.length >= 8) {
        fetchDebounceTimer = setTimeout(() => {
          fetchModelsFromProvider(true) // silent mode - no toast on success
        }, 800) // 800ms debounce
      }
    }
    apiKeyInput.addEventListener('input', handler)
    eventListeners.push({ el: apiKeyInput, type: 'input', handler })
  }

  // Model ID select change
  const modelIdSelect = containerRef?.querySelector('#modelIdSelect')
  if (modelIdSelect) {
    const handler = (e) => {
      const customInput = containerRef?.querySelector('#modelIdCustom')
      if (e.target.value === '__custom__') {
        customInput.style.display = 'block'
        customInput.focus()
      } else {
        customInput.style.display = 'none'
        customInput.value = ''
      }
    }
    modelIdSelect.addEventListener('change', handler)
    eventListeners.push({ el: modelIdSelect, type: 'change', handler })
  }

  // Mark base_url as manually edited when user types
  const baseUrlInput = containerRef?.querySelector('[name="base_url"]')
  if (baseUrlInput) {
    const handler = (e) => {
      e.target.dataset.autoFilled = 'false'
    }
    baseUrlInput.addEventListener('input', handler)
    eventListeners.push({ el: baseUrlInput, type: 'input', handler })
  }

  // Close modal on backdrop click
  const modelModal = containerRef?.querySelector('#modelModal')
  if (modelModal) {
    const handler = (e) => {
      if (e.target.id === 'modelModal') closeModal()
    }
    modelModal.addEventListener('click', handler)
    eventListeners.push({ el: modelModal, type: 'click', handler })
  }

  // Close delete dialog on backdrop click
  const deleteDialog = containerRef?.querySelector('#deleteDialog')
  if (deleteDialog) {
    const handler = (e) => {
      if (e.target.id === 'deleteDialog') hideDeleteDialog()
    }
    deleteDialog.addEventListener('click', handler)
    eventListeners.push({ el: deleteDialog, type: 'click', handler })
  }
}

// ========== CSS Loading ==========

function loadPageCSS() {
  // Check if CSS is already loaded
  if (document.getElementById('models-page-css')) return

  const cssLink = document.createElement('link')
  cssLink.rel = 'stylesheet'
  cssLink.href = '/styles/models.css'
  cssLink.id = 'models-page-css'
  document.head.appendChild(cssLink)
}

function unloadPageCSS() {
  document.getElementById('models-page-css')?.remove()
}

// ========== Mount / Unmount ==========

/**
 * Mount models page into container
 * @param {HTMLElement} container - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(container, { params, route } = {}) {
  console.log('[ModelsPage] Mounting...')

  containerRef = container

  // Load page-specific CSS
  loadPageCSS()

  // Render HTML
  container.innerHTML = PAGE_HTML

  // Update i18n translations
  updateContainerTranslations(container)

  // Load provider data from backend
  await loadProviderData()

  // Load model configs
  await loadModelConfigs()

  // Bind all events
  bindEvents()

  mounted = true
  console.log('[ModelsPage] Mounted')
}

/**
 * Unmount models page - cleanup
 */
export async function unmount() {
  console.log('[ModelsPage] Unmounting...')

  // Clear debounce timer
  if (fetchDebounceTimer) {
    clearTimeout(fetchDebounceTimer)
    fetchDebounceTimer = null
  }

  // Remove event listeners
  eventListeners.forEach(({ el, type, handler }) => {
    el?.removeEventListener(type, handler)
  })
  eventListeners = []

  // Unload page CSS
  unloadPageCSS()

  // Reset state
  modelConfigs = []
  editingModelId = null
  pendingDeleteId = null
  PROVIDER_PRESETS = {}
  PROVIDER_MODELS = {}
  containerRef = null
  mounted = false

  console.log('[ModelsPage] Unmounted')
}

export default { mount, unmount }
