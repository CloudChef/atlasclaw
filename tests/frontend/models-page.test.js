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
    if (target.endsWith('/api/model-configs')) return Promise.resolve(response([{ id: 'config-1', name: 'primary', display_name: 'Primary', model_id: 'openai:gpt-4.1', provider: 'openai', is_active: true }]))
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
  expect(rows[0].textContent).toContain('Unavailable')
  expect(rows[0].querySelectorAll('td')[3].textContent).toBe('—')
  expect(rows[1].textContent).toContain('Estimated')
  expect(rows[1].querySelectorAll('td')[3].textContent).toBe('0')
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
