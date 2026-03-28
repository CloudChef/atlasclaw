/**
 * header.js component tests
 */

jest.mock('../../app/frontend/scripts/auth.js', () => ({
  logout: jest.fn(() => Promise.resolve())
}))

describe('header.js', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="header"></div>'
    document.title = 'Xuanwu'
    jest.resetModules()
  })

  test('updateHeaderTitleText uses literal agent name for header and document title', async () => {
    const { renderHeader, updateHeaderTitleText } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container)
    updateHeaderTitleText('Enterprise Assistant')

    expect(document.getElementById('page-title').textContent).toBe('Enterprise Assistant')
    expect(document.getElementById('page-title').hasAttribute('data-i18n')).toBe(false)
    expect(document.title).toBe('Enterprise Assistant')
  })
})
