const api = {
  listProviderManagementProviders: jest.fn(),
  getProviderManagementProvider: jest.fn(),
  saveProviderManagementProvider: jest.fn(),
  setProviderManagementActivation: jest.fn(),
  listProviderManagementStandaloneSkills: jest.fn(),
  saveStandaloneSkillModelPolicy: jest.fn(),
  listProviderManagementModelOptions: jest.fn()
}
const currentAuthInfo = { permissions: { provider_management: { view: true, enable_disable: false, configure_models: false } } }
const modelOption = (number) => ({ ref: `ref-${number}`, model_id: `model-${number}`, provider: 'Local', source: 'saved config', capabilities: { text: true } })
const tick = () => new Promise(resolve => setTimeout(resolve, 0))

jest.unstable_mockModule('../../app/frontend/scripts/app.js', () => ({ getAuthInfo: () => currentAuthInfo }))
jest.unstable_mockModule('../../app/frontend/scripts/auth.js', () => ({ checkAuth: jest.fn() }))
jest.unstable_mockModule('../../app/frontend/scripts/config.js', () => ({ buildAssetUrl: path => path }))
jest.unstable_mockModule('../../app/frontend/scripts/i18n.js', () => ({ translateIfExists: key => key }))
jest.unstable_mockModule('../../app/frontend/scripts/components/toast.js', () => ({ showToast: jest.fn() }))
jest.unstable_mockModule('../../app/frontend/scripts/api-client.js', () => api)

let page
beforeAll(async () => { page = await import('../../app/frontend/scripts/pages/provider-management.js') })

beforeEach(() => {
  currentAuthInfo.permissions.provider_management = { view: true, enable_disable: false, configure_models: false }
  document.body.innerHTML = '<div id="page"></div>'
  api.listProviderManagementProviders.mockResolvedValue({ providers: [{
    provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'explicit', allowed_model_refs: ['ref-text'], default_model_ref: 'ref-text',
      allowed_models: [{ ref: 'ref-text', model_id: 'text-only', provider: 'Local', capabilities: { text: true } }] },
    skills: [{ skill_id: 'audit:report', name: 'Report', requires_vision: false, revision: 1,
      model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
  }] })
  api.getProviderManagementProvider.mockResolvedValue({
    provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'explicit', allowed_model_refs: ['ref-text'], default_model_ref: 'ref-text',
      allowed_models: [{ ref: 'ref-text', model_id: 'text-only', provider: 'Local', capabilities: { text: true } }] },
    skills: [{ skill_id: 'audit:report', name: 'Report', requires_vision: false, revision: 1,
      model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
  })
  api.listProviderManagementStandaloneSkills.mockResolvedValue({ skills: [{
    skill_id: 'notes:summary', name: 'Summary', revision: 5, requires_vision: false,
    model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null }
  }] })
})

afterEach(() => page.unmount())

test('renders independent tabs and keeps activation and model controls permission-specific', async () => {
  const host = document.getElementById('page')
  await page.mount(host)
  expect(host.querySelector('[data-action="show-skills"]')).toBeTruthy()
  expect(host.querySelector('[data-action="open-provider"]')).toBeTruthy()

  host.querySelector('[data-action="open-provider"]').click()
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(host.textContent).toContain('text-only · Local')
  expect(host.querySelector('[data-action="activation"]').disabled).toBe(true)
  expect(host.querySelector('[data-action="save-provider"]').disabled).toBe(true)
  expect(host.querySelector('[data-action="edit-skill"]').disabled).toBe(true)

  host.querySelector('[data-action="show-skills"]').click()
  expect(host.textContent).toContain('notes:summary')
  expect(host.querySelector('[data-action="open-standalone"]').disabled).toBe(true)
})

test('sends only writable policy fields and reports stale revisions on save', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  api.saveProviderManagementProvider.mockRejectedValue({ status: 409, message: 'revision conflict' })
  const host = document.getElementById('page')
  await page.mount(host)
  host.querySelector('[data-action="open-provider"]').click()
  await new Promise(resolve => setTimeout(resolve, 0))
  host.querySelector('[data-action="save-provider"]').click()
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(api.saveProviderManagementProvider).toHaveBeenCalledWith('audit', 'main', {
    revision: 2,
    enabled: true,
    model_policy: { mode: 'explicit', allowed_model_refs: ['ref-text'], default_model_ref: 'ref-text' },
    skills: [{ skill_id: 'audit:report', model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
  })
  expect(host.textContent).toContain('providerManagement.staleRevision')
})

test('paginates the complete child Provider draft scope and filters cached options by current search', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  const savedModels = Array.from({ length: 25 }, (_, index) => modelOption(index + 1))
  const pendingModel = modelOption(26)
  const provider = {
    provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'explicit', allowed_model_refs: savedModels.map(model => model.ref), default_model_ref: 'ref-1', allowed_models: savedModels },
    skills: [{ skill_id: 'audit:report', name: 'Report', requires_vision: false, revision: 1,
      model_policy: { mode: 'explicit', allowed_model_refs: [], default_model_ref: null } }]
  }
  api.listProviderManagementProviders.mockResolvedValue({ providers: [provider] })
  api.getProviderManagementProvider.mockResolvedValue(provider)
  api.listProviderManagementModelOptions.mockImplementation(async params => params.target_kind === 'provider'
    ? { models: [pendingModel], total: 1, page: params.page, page_size: params.page_size }
    : { models: [], total: 0, page: params.page, page_size: params.page_size })
  const host = document.getElementById('page')
  await page.mount(host)
  const tabs = host.querySelectorAll('[role="tab"]')
  expect(tabs[0].getAttribute('aria-controls')).toBe('pm-panel-providers')
  expect(host.querySelector('#pm-panel-providers').getAttribute('role')).toBe('tabpanel')
  host.querySelector('[data-action="open-provider"]').click()
  await tick()
  host.querySelector('[data-action="open-picker"][data-editor="provider"]').click()
  await tick()
  host.querySelector('[data-action="toggle-pending"][data-ref="ref-26"]').click()
  host.querySelector('[data-action="add-selected"]').click()
  host.querySelector('[data-action="edit-skill"]').click()
  host.querySelector('[data-action="open-picker"][data-editor="skill:audit:report"]').click()
  await tick()
  expect(host.querySelectorAll('.pm-option')).toHaveLength(20)
  expect(host.querySelector('[data-action="picker-page"][data-page="2"]').disabled).toBe(false)
  host.querySelector('[data-action="picker-page"][data-page="2"]').click()
  await tick()
  expect(host.querySelectorAll('.pm-option')).toHaveLength(6)
  expect(host.textContent).toContain('model-26 · Local')
  const search = host.querySelector('[data-action="search-models"]')
  search.value = 'model-2'
  search.dispatchEvent(new Event('input', { bubbles: true }))
  await new Promise(resolve => setTimeout(resolve, 300))
  expect(host.querySelectorAll('.pm-option').length).toBeLessThanOrEqual(20)
  expect([...host.querySelectorAll('.pm-option strong')].every(node => node.textContent.toLowerCase().includes('model-2'))).toBe(true)
})

test('keeps picker search focus and caret after asynchronous results for continued typing', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  const provider = { provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'explicit', allowed_model_refs: [], default_model_ref: null, allowed_models: [] }, skills: [] }
  api.listProviderManagementProviders.mockResolvedValue({ providers: [provider] })
  api.getProviderManagementProvider.mockResolvedValue(provider)
  api.listProviderManagementModelOptions.mockImplementation(async params => ({
    models: params.search ? [modelOption(27)] : [modelOption(1)], total: 1, page: params.page, page_size: params.page_size
  }))
  const host = document.getElementById('page')
  await page.mount(host)
  host.querySelector('[data-action="open-provider"]').click()
  await tick()
  host.querySelector('[data-action="open-picker"][data-editor="provider"]').click()
  await tick()
  let search = host.querySelector('[data-action="search-models"]')
  search.focus()
  search.value = 'v'
  search.setSelectionRange(1, 1)
  search.dispatchEvent(new Event('input', { bubbles: true }))
  await new Promise(resolve => setTimeout(resolve, 300))
  search = host.querySelector('[data-action="search-models"]')
  expect(document.activeElement).toBe(search)
  expect(search.selectionStart).toBe(1)
  search.value = 'vision'
  search.setSelectionRange(6, 6)
  search.dispatchEvent(new Event('input', { bubbles: true }))
  await new Promise(resolve => setTimeout(resolve, 300))
  search = host.querySelector('[data-action="search-models"]')
  expect(document.activeElement).toBe(search)
  expect(search.value).toBe('vision')
  expect(search.selectionStart).toBe(6)
})

test('uses safe Skill-scoped candidates when the Provider inherits platform defaults', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  const provider = { provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null, allowed_models: [] },
    skills: [{ skill_id: 'audit:image', name: 'Image review', requires_vision: true, revision: 1,
      model_policy: { mode: 'explicit', allowed_model_refs: [], default_model_ref: null } }]
  }
  const visionModel = { ...modelOption(28), capabilities: { text: true, vision: true } }
  api.listProviderManagementProviders.mockResolvedValue({ providers: [provider] })
  api.getProviderManagementProvider.mockResolvedValue(provider)
  api.listProviderManagementModelOptions.mockImplementation(async params => params.target_kind === 'provider_skill'
    ? { models: [visionModel], total: 1, page: params.page, page_size: params.page_size }
    : { models: [], total: 0, page: params.page, page_size: params.page_size })
  const host = document.getElementById('page')
  await page.mount(host)
  host.querySelector('[data-action="open-provider"]').click()
  await tick()
  host.querySelector('[data-action="edit-skill"]').click()
  host.querySelector('[data-action="open-picker"]').click()
  await tick()
  expect(host.querySelectorAll('.pm-option')).toHaveLength(1)
  expect(host.querySelector('.pm-option strong').textContent).toContain('model-28')
})

test('filters text-only options out of a standalone vision Skill picker', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  api.listProviderManagementModelOptions.mockResolvedValue({
    models: [modelOption(31), { ...modelOption(32), capabilities: { text: true, vision: true } }],
    total: 2, page: 1, page_size: 20
  })
  api.listProviderManagementStandaloneSkills.mockResolvedValue({ skills: [{
    skill_id: 'images:inspect', name: 'Image inspection', revision: 3, requires_vision: true,
    model_policy: { mode: 'explicit', allowed_model_refs: [], default_model_ref: null }
  }] })
  const host = document.getElementById('page')
  await page.mount(host)
  host.querySelector('[data-action="show-skills"]').click()
  host.querySelector('[data-action="open-standalone"]').click()
  host.querySelector('[data-action="open-picker"]').click()
  await tick()
  expect(host.querySelectorAll('.pm-option')).toHaveLength(1)
  expect(host.querySelector('.pm-option strong').textContent).toContain('model-32')
  expect(host.textContent).not.toContain('model-31')
})

test('keeps the standalone Skills list beneath its right-side configuration drawer', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  const host = document.getElementById('page')
  await page.mount(host)
  host.querySelector('[data-action="show-skills"]').click()
  host.querySelector('[data-action="open-standalone"]').click()

  const panel = host.querySelector('#pm-panel-skills')
  const list = panel.querySelector('.pm-panel')
  const drawer = panel.querySelector('.pm-editor-overlay')
  expect(list).not.toBeNull()
  expect(list.querySelector('[data-action="open-standalone"]').getAttribute('data-skill')).toBe('notes:summary')
  expect(drawer).not.toBeNull()
  expect(drawer.querySelector('.pm-standalone-editor')).not.toBeNull()
  expect(drawer.querySelector('[role="dialog"][aria-modal="true"]')).not.toBeNull()

  drawer.querySelector('.pm-standalone-editor').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  expect(host.querySelector('#pm-panel-skills .pm-editor-overlay')).toBeNull()
  expect(host.querySelector('#pm-panel-skills .pm-panel')).not.toBeNull()
})

test('uses model metadata in the Provider list and traps focus while Escape closes only the top dialog', async () => {
  currentAuthInfo.permissions.provider_management.configure_models = true
  currentAuthInfo.permissions.provider_management.view = true
  const provider = { provider_type: 'audit', instance_name: 'main', name: 'Audit', enabled: true, revision: 2,
    model_policy: { mode: 'explicit', allowed_model_refs: ['ref-text', 'missing-ref'], default_model_ref: 'ref-text', allowed_models: [
      { ...modelOption(1), ref: 'ref-text', model_id: 'text-only' },
      { ref: 'missing-ref', model_id: 'missing-ref', provider: '', source: 'missing', missing: true, is_active: false, capabilities: {} }
    ] },
    skills: [{ skill_id: 'audit:report', name: 'Report', requires_vision: false, revision: 1,
      model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
  }
  api.listProviderManagementProviders.mockResolvedValue({ providers: [provider] })
  api.getProviderManagementProvider.mockResolvedValue(provider)
  const host = document.getElementById('page')
  await page.mount(host)
  expect(host.textContent).toContain('text-only · Local')
  expect(host.textContent).toContain('missing-ref')
  expect(host.textContent).not.toContain('ref-text, missing-ref')
  host.querySelector('[data-action="open-provider"]').click()
  await tick()
  host.querySelector('[data-action="edit-skill"]').click()
  host.querySelector('[data-action="skill-inherit"]').click()
  const dialog = host.querySelector('.pm-dialog')
  const dialogButtons = [...dialog.querySelectorAll('button,input,select')].filter(element => !element.disabled)
  dialogButtons.at(-1).focus()
  dialogButtons.at(-1).dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
  expect(document.activeElement).toBe(dialogButtons[0])
  host.querySelector('[data-action="open-picker"]').click()
  await tick()
  host.querySelector('.pm-picker').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  expect(host.querySelector('.pm-picker')).toBeNull()
  expect(host.querySelector('.pm-dialog')).not.toBeNull()
  expect(document.activeElement.getAttribute('data-action')).toBe('open-picker')
})
