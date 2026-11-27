const models = [
  { ref: 'm1', model_id: 'text-small', provider: 'OpenRouter', capabilities: { text: true } },
  { ref: 'm2', model_id: 'vision-pro', provider: 'OpenRouter', capabilities: { text: true, vision: true } },
  { ref: 'm3', model_id: 'text-large', provider: 'Local', capabilities: { text: true } }
]

let state
beforeAll(async () => {
  state = await import('../../app/frontend/scripts/provider-management-state.js')
})

describe('provider management access', () => {
  test('hides the route and menu without view permission', () => {
    expect(state.canAccessProviderManagement({ permissions: { provider_management: { view: false } } })).toBe(false)
    expect(state.canAccessProviderManagement({ permissions: {} })).toBe(false)
  })

  test('separates activation controls from model configuration controls', () => {
    const actions = state.getProviderManagementActions({
      permissions: { provider_management: { view: true, enable_disable: true, configure_models: false } }
    })
    expect(actions).toEqual({ canView: true, canEnableDisable: true, canConfigureModels: false })
  })

  test('allows delegated permission managers to open Role Management', async () => {
    const permissions = await import('../../app/frontend/scripts/permissions.js')
    const authInfo = { permissions: { provider_management: { view: false, enable_disable: false, configure_models: false, manage_permissions: true } } }
    expect(permissions.canAccessRoleManagement(authInfo)).toBe(true)
    expect(permissions.canManagePermissionModule(authInfo, 'provider_management')).toBe(true)
  })
})

describe('Provider and Skill model drafts', () => {
  test('shows only added models and exposes a pending Provider addition to child Skills', () => {
    const provider = state.createProviderDraft({
      revision: 8,
      enabled: true,
      model_policy: { mode: 'explicit', allowed_model_refs: ['m1'], default_model_ref: 'm1' },
      skills: [{ skill_id: 'review:report', requires_vision: true, revision: 3,
        model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
    })
    expect(state.getAddedModels(provider.model_policy, models).map(model => model.ref)).toEqual(['m1'])
    state.addModelToPolicy(provider.model_policy, 'm2')
    expect(state.getProviderCandidates(provider, models).map(model => model.ref)).toEqual(['m3'])
    expect(state.getSkillCandidates(provider, provider.skills[0], models).map(model => model.ref)).toEqual(['m2'])
    expect(state.getAddedModels(provider.model_policy, models).map(model => model.ref)).toEqual(['m1', 'm2'])
  })

  test('removing a model clears its default and defaults can only target added models', () => {
    const policy = { mode: 'explicit', allowed_model_refs: ['m1', 'm2'], default_model_ref: 'm1' }
    expect(state.setPolicyDefault(policy, 'm3')).toBe(false)
    expect(state.setPolicyDefault(policy, 'm2')).toBe(true)
    state.removeModelFromPolicy(policy, 'm2')
    expect(policy).toEqual({ mode: 'explicit', allowed_model_refs: ['m1'], default_model_ref: null })
  })

  test('keeps missing model references visible and removable', () => {
    const policy = { mode: 'explicit', allowed_model_refs: ['gone'], default_model_ref: 'gone', allowed_models: [
      { ref: 'gone', model_id: 'gone', provider: '', capabilities: {}, missing: true, is_active: false, source: 'missing' }
    ] }
    expect(state.getAddedModels(policy, [])).toEqual(policy.allowed_models)
    state.removeModelFromPolicy(policy, 'gone')
    expect(policy.allowed_model_refs).toEqual([])
    expect(policy.default_model_ref).toBeNull()
  })

  test('serializes the full revisioned Provider draft payload with child Skill policies', () => {
    const provider = state.createProviderDraft({
      revision: 8,
      enabled: true,
      model_policy: { mode: 'explicit', allowed_model_refs: ['m1'], default_model_ref: 'm1', allowed_models: [{ ref: 'm1', model_id: 'text-small' }] },
      skills: [{ skill_id: 'review:report', requires_vision: false, revision: 3,
        model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null, allowed_models: [] } }]
    })
    expect(state.policyPayload(provider)).toEqual({
      revision: 8,
      enabled: true,
      model_policy: { mode: 'explicit', allowed_model_refs: ['m1'], default_model_ref: 'm1' },
      skills: [{ skill_id: 'review:report', model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } }]
    })
  })
})

describe('provider management API', () => {
  let api
  beforeAll(async () => {
    api = await import('../../app/frontend/scripts/api-client.js')
  })
  beforeEach(() => { global.fetch = jest.fn() })

  test('encodes provider identity, serializes revisioned saves, and preserves 409 status', async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ provider_type: 'audit', instance_name: 'primary' }) })
    await api.getProviderManagementProvider('audit report', 'primary/one')
    expect(global.fetch.mock.calls[0][0]).toContain('/api/provider-management/providers/audit%20report/primary%2Fone')

    const payload = { revision: 4, enabled: true, model_policy: { mode: 'inherit', allowed_model_refs: [], default_model_ref: null }, skills: [] }
    global.fetch.mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: 'stale revision' }) })
    await expect(api.saveProviderManagementProvider('audit', 'primary', payload)).rejects.toMatchObject({ status: 409, message: 'stale revision' })
    expect(global.fetch.mock.calls[1][1]).toMatchObject({ method: 'PUT', body: JSON.stringify(payload), credentials: 'include' })
  })

  test('sends child model candidate scope and server pagination values', async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ models: [], total: 0, page: 3, page_size: 10 }) })
    await api.listProviderManagementModelOptions({ target_kind: 'provider', provider_type: 'audit', instance_name: 'primary', search: 'vision', page: 3, page_size: 10 })
    expect(global.fetch.mock.calls[0][0]).toContain('target_kind=provider')
    expect(global.fetch.mock.calls[0][0]).toContain('page=3')
  })
})
