jest.mock('../../app/frontend/scripts/components/toast.js', () => ({
  showToast: jest.fn()
}))

const smartcmpDefinition = {
  provider_type: 'smartcmp',
  name_i18n_key: 'provider.catalog.smartcmp.name',
  display_name: 'SmartCMP',
  schema: {
    fields: [
      {
        name: 'base_url',
        label: 'Base URL',
        type: 'text',
        default: 'https://console.smartcmp.cloud'
      },
      {
        name: 'auth_type',
        type: 'hidden',
        default: 'user_token'
      },
      {
        name: 'user_token',
        label: 'User Token',
        placeholder: 'Enter user token',
        type: 'password',
        required: true,
        sensitive: true,
        auth_types: ['user_token']
      }
    ]
  }
}

const dingtalkDefinition = {
  provider_type: 'dingtalk',
  name_i18n_key: 'provider.catalog.dingtalk.name',
  display_name: 'DingTalk',
  schema: {
    fields: [
      {
        name: 'base_url',
        label: 'Base URL',
        type: 'text',
        default: 'https://oapi.dingtalk.com'
      },
      {
        name: 'auth_type',
        type: 'hidden',
        default: 'app_credentials'
      },
      {
        name: 'app_key',
        label: 'App Key',
        type: 'text',
        required: true
      }
    ]
  }
}

function createFetchMock({
  availableInstances = [
    {
      provider_type: 'smartcmp',
      instance_name: 'default',
      base_url: 'https://console.smartcmp.cloud',
      auth_type: 'user_token',
      config_keys: []
    },
    {
      provider_type: 'dingtalk',
      instance_name: 'ops',
      base_url: 'https://oapi.dingtalk.com',
      auth_type: 'app_credentials',
      config_keys: []
    }
  ],
  definitions = [smartcmpDefinition, dingtalkDefinition],
  providerSettings = {
    smartcmp: {
      default: {
        configured: true,
        config: {
          auth_type: 'user_token'
        },
        updated_at: '2026-04-21T09:00:00Z'
      }
    }
  }
} = {}) {
  return jest.fn((url, options = {}) => {
    const target = String(url)
    const method = String(options.method || 'GET').toUpperCase()

    if (target === '/api/service-providers/available-instances') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ providers: availableInstances })
      })
    }

    if (target === '/api/service-providers/definitions') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ providers: definitions })
      })
    }

    if (target === '/api/users/me/provider-settings' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ providers: providerSettings })
      })
    }

    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({})
    })
  })
}

async function mountModule(overrides = {}) {
  document.body.innerHTML = '<div id="page-root"></div>'
  global.fetch = createFetchMock(overrides)

  const page = await import('../../app/frontend/scripts/pages/account-auth-config.js')
  const container = document.getElementById('page-root')
  await page.mount(container)

  return { page, container }
}

afterEach(async () => {
  jest.resetModules()
})

test('mount renders an instance selector with provider and base-url context', async () => {
  const { container } = await mountModule()

  const selector = container.querySelector('#accountAuthConfigInstanceSelect')
  const options = [...selector.options].map((option) => option.textContent.trim())

  expect(options).toEqual(['SmartCMP / default', 'DingTalk / ops'])
  expect(container.querySelector('[data-auth-config-meta="provider"]')).not.toBeNull()
  expect(container.querySelector('[data-auth-config-meta="base-url"]')).not.toBeNull()
  expect(container.querySelector('input[name="user_token"]')).not.toBeNull()
  expect(container.querySelector('input[name="user_token"]').value).toBe('')
  expect(container.querySelector('.pv-type-card')).toBeNull()
  expect(container.querySelector('.pv-table')).toBeNull()
  expect(container.querySelector('#providerModal')).toBeNull()
})
