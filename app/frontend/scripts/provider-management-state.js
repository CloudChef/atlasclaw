import { hasPermission } from './permissions.js'

export function canAccessProviderManagement(authInfo) {
  return hasPermission(authInfo, 'provider_management.view')
}

export function getProviderManagementActions(authInfo) {
  return {
    canView: canAccessProviderManagement(authInfo),
    canEnableDisable: hasPermission(authInfo, 'provider_management.enable_disable'),
    canConfigureModels: hasPermission(authInfo, 'provider_management.configure_models')
  }
}

const clone = value => JSON.parse(JSON.stringify(value))

export function createProviderDraft(provider) {
  return clone(provider)
}

export function getAddedModels(policy, models) {
  const allowed = new Set(policy?.allowed_model_refs || [])
  const metadata = new Map([...models, ...(policy?.allowed_models || [])].map(model => [model.ref, model]))
  return [...allowed].map(ref => metadata.get(ref) || { ref, model_id: ref, provider: '', capabilities: {} })
}

export function getProviderCandidates(provider, models) {
  const added = new Set(provider?.model_policy?.allowed_model_refs || [])
  return models.filter(model => !added.has(model.ref))
}

export function getSkillCandidates(provider, skill, models) {
  const providerAllowed = new Set(provider?.model_policy?.allowed_model_refs || [])
  const requirements = skill?.required_capabilities || (skill?.requires_vision ? { vision: true } : {})
  return models.filter(model => providerAllowed.has(model.ref) && modelMeetsRequirements(model, requirements))
}

export function modelMeetsRequirements(model, requirements = {}) {
  if (Array.isArray(requirements)) return requirements.every(capability => model.capabilities?.[capability] === true)
  return Object.entries(requirements || {}).every(([capability, required]) => required !== true || model.capabilities?.[capability] === true)
}

export function addModelToPolicy(policy, ref) {
  if (!policy.allowed_model_refs.includes(ref)) policy.allowed_model_refs.push(ref)
}

export function removeModelFromPolicy(policy, ref) {
  policy.allowed_model_refs = policy.allowed_model_refs.filter(item => item !== ref)
  if (policy.default_model_ref === ref) policy.default_model_ref = null
}

export function setPolicyDefault(policy, ref) {
  if (ref && !policy.allowed_model_refs.includes(ref)) return false
  policy.default_model_ref = ref || null
  return true
}

export function policyPayload(provider) {
  const writePolicy = policy => ({
    mode: policy?.mode === 'inherit' ? 'inherit' : 'explicit',
    allowed_model_refs: [...(policy?.allowed_model_refs || [])],
    default_model_ref: policy?.default_model_ref || null
  })
  return {
    revision: provider.revision,
    enabled: provider.enabled,
    model_policy: writePolicy(provider.model_policy),
    skills: (provider.skills || []).map(skill => ({ skill_id: skill.skill_id, model_policy: writePolicy(skill.model_policy) }))
  }
}

export function filterModelsBySearch(models, query) {
  const search = String(query || '').trim().toLocaleLowerCase()
  if (!search) return models
  return models.filter(model => `${model.provider} ${model.model_id}`.toLocaleLowerCase().includes(search))
}

export function filterChildOptionsByDraft(models, provider, skill) {
  return getSkillCandidates(provider, skill, models)
}
