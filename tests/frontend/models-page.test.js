/* Copyright 2026 Qianyun, Inc. */
const { readFileSync } = require('node:fs')

let page, root, payload, usageHandler
const response = data => ({ ok: true, json: async () => data })
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve() }
const item = {
  id: 'call-1', created_at: '2026-11-26T10:20:30Z', model: 'openai:gpt-4.1',
  provider: 'openai', token_id: 'primary', key_label: 'sk-…1234', action: '<img src=x onerror=alert(1)>',
  input_tokens: 1200, output_tokens: 300, total_tokens: 1500, source: 'reported',
  status: 'completed', duration_ms: 500, run_id: 'run-1', session_key: 'session-1'
}

beforeEach(async () => {
  jest.resetModules()
  document.head.innerHTML = ''
  document.body.innerHTML = '<div id="root"></div>'
  root = document.querySelector('#root')
  payload = { items: [item], total: 21, summary: {
    input_tokens: 1200, output_tokens: 300, total_tokens: 1500, calls: 2, requests: 1,
    unknown_calls: 1, estimated_calls: 0, average_duration_ms: 500
  } }
  usageHandler = () => Promise.resolve(response(payload))
  global.fetch = jest.fn(url => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response([{ id: 'config-1', name: 'legacy-name', display_name: 'Legacy Display Name', model_id: 'gpt-4.1', provider: 'openai', api_key_masked: 'sk-…1234', is_active: true }]))
    return Promise.resolve(response({}))
  })
  const { loadLocale } = await import('../../app/frontend/scripts/i18n.js')
  await loadLocale('en-US')
  page = await import('../../app/frontend/scripts/pages/models.js')
})

afterEach(async () => { await page.unmount() })

test('renders real token usage, timestamp first and escaped action details without billing', async () => {
  await page.mount(root)
  await flush()
  const table = root.querySelector('#modelUsageTable')
  expect(table).not.toBeNull()
  expect(table.querySelector('th').textContent).toBe('Time')
  expect(table.textContent).toContain('1,500')
  expect(table.textContent).toContain('sk-…1234')
  expect(table.querySelector('thead').textContent).toContain('Cached tokens')
  expect(root.querySelector('[data-usage-summary="total_tokens"]').textContent).toBe('1,500')
  table.querySelector('button').click()
  expect(table.querySelector('.usage-details').hidden).toBe(false)
  expect(table.querySelector('.usage-details').textContent).toContain(item.action)
  expect(table.querySelector('img')).toBeNull()
  expect(root.textContent).not.toMatch(/12,450|80%|Billing|cheapest|MONTHLY/)
})

test('keeps unavailable tokens distinct from zero and labels estimates', async () => {
  payload.items = [
    { ...item, input_tokens: null, output_tokens: null, total_tokens: null, source: 'unavailable' },
    { ...item, id: 'call-2', input_tokens: 0, total_tokens: 0, output_tokens: 0, source: 'estimated' }
  ]
  await page.mount(root)
  await flush()
  const rows = root.querySelectorAll('#modelUsageTable .usage-row')
  expect(rows[0].textContent).not.toContain('Unavailable')
  expect(rows[0].querySelectorAll('td')[3].textContent).toBe('—')
  expect(rows[1].textContent).toContain('Estimated')
  expect(rows[1].querySelectorAll('td')[3].textContent).toBe('0')
  rows[0].querySelector('[data-usage-detail]').click()
  expect(root.querySelector('.usage-details').textContent).toContain('Unavailable')
})

test('shows cached token counts as zero, unavailable, or a formatted value', async () => {
  payload.items = [
    { ...item, cache_read_tokens: 0 },
    { ...item, id: 'call-2', cache_read_tokens: null },
    { ...item, id: 'call-3', cache_read_tokens: 2048 }
  ]
  await page.mount(root)
  await flush()

  const rows = [...root.querySelectorAll('#modelUsageTable .usage-row')]
  expect(rows.map(row => row.querySelectorAll('td')[6].textContent)).toEqual(['0', '—', '2,048'])
  const detailId = rows[2].querySelector('[data-usage-detail]').getAttribute('aria-controls')
  rows[2].querySelector('[data-usage-detail]').click()
  expect(root.querySelector(`#${detailId}`).textContent).toContain('2,048')
})

test('filters by model/key, paginates, and resets page when filtering', async () => {
  await page.mount(root)
  await flush()
  root.querySelector('#usageNext').click()
  await flush()
  expect(fetch.mock.calls.some(([url]) => String(url).includes('page=2'))).toBe(true)
  root.querySelector('#usageModelFilter').value = 'openai:gpt-4.1'
  root.querySelector('#usageKeyFilter').value = 'primary'
  root.querySelector('#usageFilters').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flush()
  const urls = fetch.mock.calls.map(([url]) => String(url)).filter(url => url.includes('/api/model-usage'))
  const query = new URL(urls.at(-1), 'http://localhost').searchParams
  expect(query.get('model')).toBe('openai:gpt-4.1')
  expect(query.get('token_id')).toBe('primary')
  expect(query.get('page')).toBe('1')
})

test('loads selected config usage and clears sidebar for a new config', async () => {
  await page.mount(root)
  await flush()
  root.querySelector('[data-action="edit"]').click()
  await flush()
  expect(fetch.mock.calls.some(([url]) => String(url).includes('model_config_id=config-1'))).toBe(true)
  expect(root.querySelector('#modelConfigUsage').textContent).toContain('1,500')
  root.querySelector('#btnFormCancel').click()
  await flush()
  root.querySelector('#btnCreateModelInPage').click()
  expect(root.querySelector('#modelConfigUsage').textContent).toContain('Save this configuration to view its usage.')
})

test('shows retry on errors without false zero totals', async () => {
  usageHandler = () => Promise.resolve({ ok: false, status: 500 })
  await page.mount(root)
  await flush()
  expect(root.querySelector('#modelUsageContent').textContent).toContain('Unable to load usage')
  expect(root.querySelector('[data-usage-summary="total_tokens"]').textContent).toBe('—')
  usageHandler = () => Promise.resolve(response(payload))
  root.querySelector('#usageRetry').click()
  await flush()
  expect(root.querySelector('#modelUsageTable').textContent).toContain('1,500')
})

test('ignores stale filter responses and responses after unmount', async () => {
  await page.mount(root)
  await flush()
  let finishOld
  usageHandler = () => new Promise(resolve => { finishOld = resolve })
  root.querySelector('#usageNext').click()
  await flush()
  usageHandler = () => Promise.resolve(response({ ...payload, items: [], total: 0 }))
  root.querySelector('#usageFilters').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flush()
  finishOld(response(payload))
  await flush()
  expect(root.querySelector('#modelUsageContent').textContent).toContain('No usage recorded')
  usageHandler = () => new Promise(resolve => { finishOld = resolve })
  root.querySelector('#usageFilters').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flush()
  await page.unmount()
  root.innerHTML = 'Another page'
  finishOld(response(payload))
  await flush()
  expect(root.textContent).toBe('Another page')
})

test('keeps historical usage accessible when no model configs remain', async () => {
  const originalFetch = global.fetch
  global.fetch = jest.fn((url, options) => String(url).endsWith('/api/model-configs')
    ? Promise.resolve(response([])) : originalFetch(url, options))
  await page.mount(root)
  await flush()
  expect(root.querySelector('#modelUsageTable').textContent).toContain('1,500')
  expect(root.querySelector('#btnCreateModelInPage')).not.toBeNull()
})

test('shows failed call status in timestamp details', async () => {
  payload.items = [{ ...item, status: 'failed' }]
  await page.mount(root)
  await flush()
  root.querySelector('#modelUsageTable button').click()
  expect(root.querySelector('.usage-details').textContent).toContain('Failed')
})

test('ignores a pending config response after opening a new config', async () => {
  await page.mount(root)
  await flush()
  let finishConfig
  usageHandler = url => url.includes('model_config_id')
    ? new Promise(resolve => { finishConfig = resolve }) : Promise.resolve(response(payload))
  root.querySelector('[data-action="edit"]').click()
  await flush()
  root.querySelector('#btnFormCancel').click()
  await flush()
  root.querySelector('#btnCreateModelInPage').click()
  finishConfig(response(payload))
  await flush()
  expect(root.querySelector('#modelConfigUsage').textContent).toBe('Save this configuration to view its usage.')
})

test('shows canonical model and provider names in the list and form without editable names', async () => {
  await page.mount(root)
  await flush()

  expect(root.querySelector('.model-identity-name').textContent).toBe('gpt-4.1 (openai)')
  root.querySelector('[data-action="edit"]').click()

  expect(root.querySelector('#modelName')).toBeNull()
  expect(root.querySelector('#modelDisplayName')).toBeNull()
  const identityRow = root.querySelector('.model-form-layout .form-row-2col')
  expect(identityRow.querySelector('#modelProvider')).not.toBeNull()
  expect(identityRow.querySelector('#modelModelId')).not.toBeNull()
  expect(identityRow.querySelector('label[for="modelProvider"]')).not.toBeNull()
  expect(identityRow.querySelector('label[for="modelModelId"]')).not.toBeNull()
  expect(root.querySelector('#modelBaseUrl').closest('.form-row')).not.toBe(identityRow)
  expect(root.querySelector('#modelApiKey').closest('.form-row')).not.toBe(identityRow)
})

test('creates duplicate model/provider configs with distinct internal names and the same visible label', async () => {
  const savedConfigs = []
  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    if (target.endsWith('/api/model-configs') && options.method === 'POST') {
      const saved = JSON.parse(options.body)
      savedConfigs.push({ id: `config-${savedConfigs.length + 1}`, ...saved })
      return Promise.resolve(response(savedConfigs.at(-1)))
    }
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response(savedConfigs))
    return Promise.resolve(response({}))
  })
  await page.mount(root)
  await flush()

  const createConfig = async () => {
    root.querySelector('#btnCreateModelInPage').click()
    root.querySelector('#modelProvider').value = 'openai'
    root.querySelector('#modelProvider').dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    const modelSelect = root.querySelector('#modelModelId')
    modelSelect.value = '__custom__'
    modelSelect.dispatchEvent(new Event('change', { bubbles: true }))
    root.querySelector('#modelModelIdCustom').value = 'gpt-4.1'
    root.querySelector('#modelApiKey').value = 'secret'
    root.querySelector('#btnFormSave').click()
    await flush()
  }

  await createConfig()
  const firstVisibleName = root.querySelector('.model-identity-name').textContent
  await createConfig()

  expect(savedConfigs).toHaveLength(2)
  expect(savedConfigs[0].name).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  expect(savedConfigs[1].name).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  expect(savedConfigs[1].name).not.toBe(savedConfigs[0].name)
  expect(savedConfigs[0]).not.toHaveProperty('display_name')
  expect(savedConfigs[1]).not.toHaveProperty('display_name')
  expect(firstVisibleName).toBe('gpt-4.1 (openai)')
  expect(root.querySelectorAll('.model-identity-name')[1].textContent).toBe(firstVisibleName)
})

test('keeps config identity and blank API key semantics when saving an edit', async () => {
  let saved
  const originalFetch = global.fetch
  global.fetch = jest.fn((url, options = {}) => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    if (target.endsWith('/api/model-configs/config-1') && options.method === 'PUT') {
      saved = JSON.parse(options.body)
      return Promise.resolve(response({ id: 'config-1', ...saved }))
    }
    return originalFetch(url, options)
  })
  await page.mount(root)
  await flush()
  root.querySelector('[data-action="edit"]').click()
  await flush()
  root.querySelector('#modelApiKey').value = ''
  root.querySelector('#btnFormSave').click()
  await flush()

  expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/api/model-configs/config-1'))).toBe(true)
  expect(saved.name).toBeUndefined()
  expect(saved.display_name).toBeUndefined()
  expect(saved.api_key).toBeNull()
})

test('resets noncustom endpoints from provider presets and only exposes a URL editor for Custom', async () => {
  const config = {
    id: 'config-1', name: 'legacy-name', display_name: 'Legacy Display Name',
    model_id: 'gpt-4.1', provider: 'openai', base_url: 'https://proxy.example/v1',
    api_type: 'anthropic', api_key_masked: 'sk-…1234', is_active: true
  }
  global.fetch = jest.fn(url => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.endsWith('/api/providers')) return Promise.resolve(response({
      openai: { base_url: 'https://api.openai.test/v1', api_type: 'openai', models: ['gpt-4.1'] },
      anthropic: { base_url: 'https://api.anthropic.test', api_type: 'anthropic', models: ['claude-test'] },
      google: { base_url: '', api_type: 'google', models: ['gemini-test'] },
      custom: { base_url: 'https://must-not-leak.test', api_type: 'openai', models: [] }
    }))
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response([config]))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    return Promise.resolve(response({}))
  })
  await page.mount(root)
  await flush()
  root.querySelector('[data-action="edit"]').click()

  const baseUrl = root.querySelector('#modelBaseUrl')
  const apiType = root.querySelector('#modelApiType')
  const baseUrlRow = baseUrl.closest('.form-row')
  expect(baseUrl.value).toBe('https://proxy.example/v1')
  expect(baseUrl.readOnly).toBe(true)
  expect(baseUrlRow.hidden).toBe(false)

  const provider = root.querySelector('#modelProvider')
  provider.value = 'anthropic'
  provider.dispatchEvent(new Event('change', { bubbles: true }))
  expect(baseUrl.value).toBe('https://api.anthropic.test')
  expect(apiType.value).toBe('anthropic')
  expect(baseUrl.readOnly).toBe(true)

  provider.value = 'custom'
  provider.dispatchEvent(new Event('change', { bubbles: true }))
  expect(baseUrl.value).toBe('')
  expect(apiType.value).toBe('openai')
  expect(baseUrl.readOnly).toBe(false)
  expect(baseUrlRow.hidden).toBe(false)

  provider.value = 'google'
  provider.dispatchEvent(new Event('change', { bubbles: true }))
  expect(baseUrl.value).toBe('')
  expect(apiType.value).toBe('google')
  expect(baseUrl.readOnly).toBe(true)
  expect(baseUrlRow.hidden).toBe(true)
})

test.each([null, ''])('fills the provider endpoint when editing a config with base_url=%p', async baseUrlValue => {
  const config = {
    id: 'config-1', name: 'legacy-name', model_id: 'claude-test', provider: 'anthropic',
    base_url: baseUrlValue, api_type: null, api_key_masked: 'sk-…1234', is_active: true
  }
  global.fetch = jest.fn(url => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.endsWith('/api/providers')) return Promise.resolve(response({
      anthropic: { base_url: 'https://api.anthropic.test', api_type: 'anthropic', models: ['claude-test'] }
    }))
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response([config]))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    return Promise.resolve(response({}))
  })
  await page.mount(root)
  await flush()
  root.querySelector('[data-action="edit"]').click()

  expect(root.querySelector('#modelBaseUrl').value).toBe('https://api.anthropic.test')
  expect(root.querySelector('#modelApiType').value).toBe('anthropic')
  expect(root.querySelector('#modelBaseUrl').readOnly).toBe(true)
})

test('uses light card classes for list summaries and form usage panels', async () => {
  await page.mount(root)
  await flush()

  expect(root.querySelector('.stats-card-clusters')).toBeNull()
  expect(root.querySelector('.panel-card-dark')).toBeNull()
  root.querySelector('[data-action="edit"]').click()
  expect(root.querySelector('.sidebar-card-dark')).toBeNull()
  expect(root.querySelector('.model-form-sidebar .sidebar-card')).not.toBeNull()
})

test('shows file models before database models with translated sources, null limits, and readonly actions', async () => {
  global.fetch = jest.fn(url => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response({
      model_configs: [{
        id: 'db-id', name: 'db-internal', model_id: 'db-model', provider: 'openai',
        context_window: 4096, max_tokens: 1024, is_active: true, source: 'database', read_only: false
      }],
      total: 1,
      file_models: [{
        id: 'file:0', name: 'openrouter', model_id: '<img src=x onerror=alert(1)>', provider: 'openrouter',
        context_window: null, max_tokens: null, is_active: true, source: 'config_file', read_only: true
      }]
    }))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    return Promise.resolve(response({}))
  })
  await page.mount(root)
  await flush()

  const rows = [...root.querySelectorAll('.model-table tbody tr.model-row')]
  expect(rows.map(row => row.dataset.id)).toEqual(['file:0', 'db-id'])
  expect(root.querySelector('.ecosystem-badges').textContent).toContain('Total: 2 Models')
  expect(rows[0].textContent).toContain('openrouter')
  expect(rows[0].querySelector('img')).toBeNull()
  expect(rows[0].querySelector('.model-identity-name').textContent).toBe('<img src=x onerror=alert(1)> (openrouter)')
  expect([...rows[0].querySelectorAll('td')].map(cell => cell.textContent.trim())).toContain('—')
  expect(rows[0].textContent).toContain('Configuration file')
  expect(rows[0].querySelector('.model-actions-cell').textContent).toContain('Read only')
  expect(rows[0].querySelector('[data-action]')).toBeNull()
  expect(rows[1].textContent).toContain('Database')
  expect(rows[1].querySelector('[data-action="edit"]')).not.toBeNull()
})

test('ignores delegated edit and delete actions on readonly file model rows', async () => {
  global.fetch = jest.fn(url => {
    const target = String(url)
    if (target.includes('/locales/')) return Promise.resolve(response(JSON.parse(readFileSync('locales/en-US.json', 'utf8'))))
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response({
      model_configs: [], total: 0,
      file_models: [{ id: 'file:0', name: 'openrouter', provider: 'openrouter', model_id: 'model-x', read_only: true, is_active: true }]
    }))
    if (target.includes('/api/model-usage')) return usageHandler(target)
    return Promise.resolve(response({}))
  })
  await page.mount(root)
  await flush()

  const row = root.querySelector('.model-row')
  for (const action of ['edit', 'delete']) {
    const button = document.createElement('button')
    button.dataset.action = action
    button.dataset.id = 'file:0'
    row.querySelector('.model-actions-cell').append(button)
    button.click()
    button.remove()
  }

  expect(root.querySelector('.model-form-page').style.display).toBe('none')
  expect(root.querySelector('#deleteDialog').classList.contains('hidden')).toBe(true)
  expect(fetch.mock.calls.some(([url, options]) => String(url).includes('/api/model-configs/file%3A0') && options?.method === 'DELETE')).toBe(false)
})
