import { translateIfExists } from '../i18n.js'

let containerRef = null
let state = createInitialState()

function createInitialState() {
  return {
    loading: false,
    error: '',
    instances: [],
    providerDefinitions: {},
    userProviderConfigs: {},
    selectedKey: '',
    formValues: {},
    baselineValues: {},
    visibleFields: [],
    hiddenFields: [],
    saveError: '',
    secretVisibility: {}
  }
}

function tr(key, fallback, params = {}) {
  return translateIfExists(key, params) || fallback
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function indexDefinitions(definitions) {
  return Object.fromEntries(
    (Array.isArray(definitions) ? definitions : [])
      .filter((entry) => entry?.provider_type)
      .map((entry) => [entry.provider_type, entry])
  )
}

function getProviderName(providerType) {
  const definition = state.providerDefinitions[providerType]
  return tr(definition?.name_i18n_key || '', definition?.display_name || providerType)
}

function getCredentialFields(providerType, values = {}) {
  const fields = state.providerDefinitions[providerType]?.schema?.fields
  if (!Array.isArray(fields)) {
    return []
  }

  const authTypeField = fields.find((field) => field?.name === 'auth_type')
  const authType = String(values.auth_type || authTypeField?.default || '').trim().toLowerCase()

  return fields.filter((field) => {
    if (field?.name === 'base_url') {
      return false
    }

    const authTypes = Array.isArray(field?.auth_types) ? field.auth_types : []
    return !authTypes.length || authTypes.includes(authType)
  })
}

function buildInitialValues(instance) {
  const savedConfig = state.userProviderConfigs?.[instance.providerType]?.[instance.instanceName]?.config || {}

  return Object.fromEntries(
    getCredentialFields(instance.providerType, savedConfig).map((field) => {
      const currentValue = savedConfig[field.name]
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

function selectInstance(instanceKey) {
  state.selectedKey = instanceKey
  const instance = state.instances.find((entry) => entry.key === instanceKey) || null

  if (!instance) {
    state.formValues = {}
    state.baselineValues = {}
    state.visibleFields = []
    state.hiddenFields = []
    return
  }

  const nextValues = buildInitialValues(instance)
  const allFields = getCredentialFields(instance.providerType, nextValues)

  state.formValues = { ...nextValues }
  state.baselineValues = { ...nextValues }
  state.hiddenFields = allFields.filter((field) => field.type === 'hidden')
  state.visibleFields = allFields.filter((field) => field.type !== 'hidden')
  state.secretVisibility = {}
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
    throw new Error(`Request failed: ${response.status}`)
  }

  return response.status === 204 ? {} : response.json()
}

function render() {
  if (!containerRef) {
    return
  }

  const selected = state.instances.find((entry) => entry.key === state.selectedKey) || null
  const optionsMarkup = state.instances
    .map(
      (instance) => `
    <option value="${escapeHtml(instance.key)}" ${instance.key === state.selectedKey ? 'selected' : ''}>
      ${escapeHtml(instance.label)}
    </option>`
    )
    .join('')

  const visibleFieldsMarkup = state.visibleFields
    .map(
      (field) => `
      <label class="account-field">
        <span>${escapeHtml(field.label || field.name)}</span>
        <input
          class="account-auth-config-input"
          type="${escapeHtml(field.type || 'text')}"
          name="${escapeHtml(field.name)}"
          value="${escapeHtml(state.formValues[field.name] || '')}"
        >
      </label>`
    )
    .join('')

  containerRef.innerHTML = `
    <form class="account-auth-config-shell" id="accountAuthConfigForm">
      <label class="account-field">
        <span>${escapeHtml(tr('provider.instanceName', 'Instance'))}</span>
        <select id="accountAuthConfigInstanceSelect" class="account-auth-config-select" name="instance_key">
          ${optionsMarkup}
        </select>
      </label>

      <div class="account-auth-config-meta-grid">
        <div class="account-auth-config-meta" data-auth-config-meta="provider">
          <span>${escapeHtml(tr('provider.providerType', 'Provider'))}</span>
          <strong>${escapeHtml(selected ? getProviderName(selected.providerType) : '--')}</strong>
        </div>
        <div class="account-auth-config-meta" data-auth-config-meta="base-url">
          <span>${escapeHtml(tr('provider.baseUrl', 'Base URL'))}</span>
          <strong>${escapeHtml(selected?.baseUrl || '--')}</strong>
        </div>
      </div>

      <div class="account-auth-config-fields">
        ${visibleFieldsMarkup}
      </div>
    </form>
  `
}

async function refreshData() {
  state.loading = true
  render()

  const [serviceData, definitionData, userProviderData] = await Promise.all([
    requestJson('/api/service-providers/available-instances'),
    requestJson('/api/service-providers/definitions'),
    requestJson('/api/users/me/provider-settings')
  ])

  state.providerDefinitions = indexDefinitions(definitionData?.providers)
  state.userProviderConfigs = userProviderData?.providers || {}
  state.instances = (Array.isArray(serviceData?.providers) ? serviceData.providers : []).map((entry) => ({
    key: `${entry.provider_type}::${entry.instance_name}`,
    providerType: String(entry.provider_type || ''),
    instanceName: String(entry.instance_name || ''),
    baseUrl: String(entry.base_url || ''),
    label: `${getProviderName(entry.provider_type)} / ${entry.instance_name}`
  }))

  selectInstance(state.instances[0]?.key || '')
  state.loading = false
  render()
}

export async function mount(container) {
  containerRef = container
  await refreshData()
}

export async function unmount() {
  containerRef = null
  state = createInitialState()
}

export default { mount, unmount }
