/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * header.js component tests
 */

jest.mock('../../app/frontend/scripts/auth.js', () => ({
  logout: jest.fn(() => Promise.resolve())
}))

describe('header.js', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="header"></div>'
    document.title = 'AtlasClaw'
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

  test('renderHeader shows account settings link for regular users', async () => {
    const { renderHeader } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container, { authInfo: { username: 'alice', is_admin: false } })

    expect(container.querySelector('a[href="/account"]')).not.toBeNull()
    expect(container.querySelector('a[href="/admin/users"]')).toBeNull()
  })

  test('renderHeader shows role management link for admins', async () => {
    const { renderHeader } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container, {
      authInfo: {
        username: 'admin',
        is_admin: true,
        permissions: {
          users: { view: true },
          roles: { view: true }
        }
      }
    })

    expect(container.querySelector('a[href="/admin/users"]')).not.toBeNull()
    expect(container.querySelector('a[href="/admin/roles"]')).not.toBeNull()
  })

  test('renderHeader shows RBAC surfaces for non-admin users with effective permissions', async () => {
    const { renderHeader } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container, {
      authInfo: {
        username: 'auditor',
        is_admin: false,
        permissions: {
          roles: { view: true },
          users: { view: true },
          channels: { view: false },
          model_configs: { view: false }
        }
      }
    })

    expect(container.querySelector('a[href="/admin/users"]')).not.toBeNull()
    expect(container.querySelector('a[href="/admin/roles"]')).not.toBeNull()
    expect(container.querySelector('a[href="/channels"]')).toBeNull()
  })

  test('renderHeader hides provider management link even when provider permissions are granted', async () => {
    const { renderHeader } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container, {
      authInfo: {
        username: 'ops-admin',
        is_admin: false,
        permissions: {
          provider_configs: { view: true }
        }
      }
    })

    expect(container.querySelector('a[href="/providers"]')).toBeNull()
    expect(container.querySelector('a[href="/models"]')).toBeNull()
  })

  test('header avatar updates when profile change event is dispatched', async () => {
    const { renderHeader } = await import('../../app/frontend/scripts/components/header.js')

    const container = document.getElementById('header')
    renderHeader(container, { authInfo: { username: 'alice', display_name: 'Alice', is_admin: false } })

    document.dispatchEvent(new CustomEvent('atlasclaw:user-profile-updated', {
      detail: {
        username: 'alice',
        display_name: 'Alice Chen',
        avatar_url: '/user-content/avatars/alice.png',
        is_admin: false
      }
    }))

    const avatarImage = container.querySelector('#userAvatarBtn img')
    expect(avatarImage).not.toBeNull()
    expect(avatarImage.getAttribute('src')).toBe('/user-content/avatars/alice.png')
    expect(container.querySelector('.dropdown-username').textContent).toBe('Alice Chen')
  })
})
