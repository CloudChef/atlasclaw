/* Copyright 2026 Qianyun, Inc. */
const { readFileSync } = require('node:fs')
const fresh = JSON.parse(readFileSync('locales/en-US.json', 'utf8'))
const flush = async () => { for (let index = 0; index < 30; index += 1) await Promise.resolve() }

test.each(['en-US', 'zh-CN'])('all Models UI translation keys exist in %s', locale => {
  const translations = JSON.parse(readFileSync(`locales/${locale}.json`, 'utf8'))
  const source = readFileSync('scripts/pages/models.js', 'utf8')
  const keys = [...source.matchAll(/(?:data-i18n(?:-placeholder|-title)?=["']|\bt\(['"])(model\.[\w.]+)/g)]
    .map(match => match[1]).filter(key => !key.endsWith('.'))
  for (const key of new Set(keys)) {
    const value = key.split('.').reduce((parent, part) => parent?.[part], translations)
    expect(typeof value === 'string' && value.length > 0 ? null : key).toBeNull()
  }
  expect(Object.keys(translations.model.usage).sort()).toEqual(Object.keys(fresh.model.usage).sort())
})

test('refreshes an old in-memory English locale before rendering list, errors and configuration', async () => {
  jest.resetModules()
  document.body.innerHTML = '<div id="root"></div>'
  const outdated = JSON.parse(JSON.stringify(fresh))
  delete outdated.model.usage
  outdated.model.configTip = 'Old configuration tip'
  let localeRequests = 0
  global.fetch = jest.fn(async url => {
    const target = String(url)
    if (target.includes('/locales/')) {
      localeRequests += 1
      return { ok: true, json: async () => localeRequests === 1 ? outdated : fresh }
    }
    if (target.includes('/api/model-usage')) return { ok: false, status: 500 }
    return { ok: true, json: async () => ({}) }
  })
  const { loadLocale } = await import('../../app/frontend/scripts/i18n.js')
  await loadLocale('en-US')
  const page = await import('../../app/frontend/scripts/pages/models.js')
  const root = document.getElementById('root')
  try {
    await page.mount(root)
    await flush()
    expect(localeRequests).toBe(2)
    expect(root.textContent).not.toMatch(/model\.usage\./)
    expect(root.querySelector('#modelUsageContent').textContent).toContain(fresh.model.usage.error)
    root.querySelector('#btnCreateModelInPage').click()
    await flush()
    expect(root.querySelector('#modelConfigUsage').textContent).toBe(fresh.model.usage.saveFirst)
    expect(root.querySelector('[data-i18n="model.configTip"]').textContent).toBe(fresh.model.configTip)
  } finally {
    await page.unmount()
  }
})
