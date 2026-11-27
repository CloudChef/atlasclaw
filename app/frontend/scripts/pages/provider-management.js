import { checkAuth } from '../auth.js'
import { getAuthInfo } from '../app.js'
import { buildAssetUrl } from '../config.js'
import { translateIfExists } from '../i18n.js'
import { showToast } from '../components/toast.js'
import {
  getProviderManagementActions,
  createProviderDraft,
  getAddedModels,
  addModelToPolicy,
  removeModelFromPolicy,
  setPolicyDefault,
  policyPayload,
  modelMeetsRequirements,
  filterModelsBySearch,
  filterChildOptionsByDraft
} from '../provider-management-state.js'
import {
  listProviderManagementProviders,
  getProviderManagementProvider,
  saveProviderManagementProvider,
  setProviderManagementActivation,
  listProviderManagementStandaloneSkills,
  saveStandaloneSkillModelPolicy,
  listProviderManagementModelOptions
} from '../api-client.js'

let host
let authInfo
let actions
let providers = []
let standaloneSkills = []
let draft
let activeTab = 'providers'
let selectedSkillId
let modalState
let childReturnSkillId
let statusMessage = ''
let sequence = 0

const copy = (key, fallback) => translateIfExists(key) || fallback
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char])
const providerKey = p => `${encodeURIComponent(p.provider_type)}/${encodeURIComponent(p.instance_name)}`
const modeOf = policy => policy?.mode === 'inherit' ? 'inherit' : 'explicit'
const policyClone = policy => ({
  mode: modeOf(policy),
  allowed_model_refs: [...(policy?.allowed_model_refs || [])],
  default_model_ref: policy?.default_model_ref || null,
  ...(Array.isArray(policy?.allowed_models) ? { allowed_models: [...policy.allowed_models] } : {})
})
const displayModel = model => `${model.model_id} · ${model.provider}`
const supportsVision = model => model.capabilities?.vision === true
const skillRequirements = (skill, responseRequirements) => responseRequirements || skill?.required_capabilities || (skill?.requires_vision ? { vision: true } : {})
const modelRefName = (ref, models) => displayModel(models.find(model => model.ref === ref) || { ref, model_id: ref, provider: '' })

function addPageStyles() {
  if (document.getElementById('provider-management-page-css')) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = buildAssetUrl('/styles/provider-management.css')
  link.id = 'provider-management-page-css'
  document.head.appendChild(link)
}

function render() {
  if (!host) return
  host.innerHTML = `
    <main class="pm-page">
      <header class="pm-heading">
        <div><p class="pm-eyebrow">${esc(copy('providerManagement.breadcrumb', 'Management'))}</p>
          <h2>${esc(copy('providerManagement.title', 'Provider Management'))}</h2>
          <p>${esc(copy('providerManagement.description', 'Manage provider availability and model routing for this workspace.'))}</p>
        </div>
      </header>
      ${statusMessage ? `<div class="pm-status" role="status">${esc(statusMessage)} <button type="button" data-action="dismiss-status" aria-label="${esc(copy('providerManagement.dismiss', 'Dismiss'))}">×</button></div>` : ''}
      <div class="pm-tabs" role="tablist" aria-label="${esc(copy('providerManagement.tabsLabel', 'Configuration type'))}">
        <button role="tab" id="pm-tab-providers" aria-controls="pm-panel-providers" aria-selected="${activeTab === 'providers'}" tabindex="${activeTab === 'providers' ? '0' : '-1'}" data-action="show-providers">${esc(copy('providerManagement.providersTab', 'Providers'))}</button>
        <button role="tab" id="pm-tab-skills" aria-controls="pm-panel-skills" aria-selected="${activeTab === 'skills'}" tabindex="${activeTab === 'skills' ? '0' : '-1'}" data-action="show-skills">${esc(copy('providerManagement.skillsTab', 'Standalone Skills'))}</button>
      </div>
      <div role="tabpanel" id="${activeTab === 'skills' ? 'pm-panel-skills' : 'pm-panel-providers'}" aria-labelledby="${activeTab === 'skills' ? 'pm-tab-skills' : 'pm-tab-providers'}" tabindex="0">
        ${activeTab === 'skills' ? renderStandalonePanel() : renderProvidersPanel()}
      </div>
      ${renderPicker()}
    </main>`
  bindEvents()
}

function renderProvidersPanel() {
  if (draft) return renderProviderDetail()
  return `<section class="pm-panel"><div class="pm-panel-heading"><div><h3>${esc(copy('providerManagement.providersHeading', 'Providers'))}</h3><p>${esc(copy('providerManagement.providersHint', 'Provider activation is global. Model configuration applies to this instance and its Skills.'))}</p></div></div>
    ${providers.length ? `<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>${esc(copy('providerManagement.providerColumn', 'Provider'))}</th><th>${esc(copy('providerManagement.stateColumn', 'Status'))}</th><th>${esc(copy('providerManagement.modelsColumn', 'Allowed models'))}</th><th>${esc(copy('providerManagement.skillsColumn', 'Skills'))}</th><th></th></tr></thead><tbody>${providers.map(provider => {
      const policy = provider.model_policy || {}
      const modelByRef = new Map((policy.allowed_models || []).map(model => [model.ref, model]))
      const modelNames = (policy.allowed_model_refs || []).map(ref => {
        const model = modelByRef.get(ref)
        return esc(model && !model.missing && model.is_active !== false ? `${model.model_id} · ${model.provider}` : ref)
      }).join(', ')
      return `<tr><th><strong>${esc(provider.name || provider.provider_type)}</strong><small>${esc(provider.provider_type)} / ${esc(provider.instance_name)}</small></th><td><span class="pm-badge ${provider.enabled ? 'is-active' : 'is-inactive'}">${esc(provider.enabled ? copy('providerManagement.enabled', 'Enabled') : copy('providerManagement.disabled', 'Disabled'))}</span></td><td>${modelNames || `<span class="pm-muted">${esc(policy.mode === 'inherit' ? copy('providerManagement.platformDefault', 'Platform defaults') : copy('providerManagement.noneAdded', 'No models added'))}</span>`}</td><td>${Number(provider.skills?.length || 0)}</td><td><button class="pm-link-button" type="button" data-action="open-provider" data-key="${esc(providerKey(provider))}">${esc(copy('providerManagement.configure', 'Configure'))}</button></td></tr>`
    }).join('')}</tbody></table></div>` : `<div class="pm-empty">${esc(copy('providerManagement.noProviders', 'No registered Providers were returned.'))}</div>`}
  </section>`
}

function renderProviderDetail() {
  const provider = draft
  const canToggle = actions.canEnableDisable
  const canConfigure = actions.canConfigureModels
  return `<section class="pm-panel">
    <div class="pm-detail-heading"><button type="button" class="pm-back" data-action="back">← ${esc(copy('providerManagement.back', 'All Providers'))}</button><div class="pm-detail-title"><div><h3>${esc(provider.name || provider.provider_type)}</h3><p>${esc(provider.provider_type)} / ${esc(provider.instance_name)}</p></div><span class="pm-badge ${provider.enabled ? 'is-active' : 'is-inactive'}">${esc(provider.enabled ? copy('providerManagement.enabled', 'Enabled') : copy('providerManagement.disabled', 'Disabled'))}</span></div></div>
    <div class="pm-activation-row"><div><strong>${esc(copy('providerManagement.activationLabel', 'Provider activation'))}</strong><p>${esc(copy('providerManagement.activationHint', 'Disabling prevents new tasks from starting through this Provider.'))}</p></div><label class="pm-switch"><input type="checkbox" data-action="activation" ${provider.enabled ? 'checked' : ''} ${canToggle ? '' : 'disabled'} aria-label="${esc(copy('providerManagement.activationLabel', 'Provider activation'))}"><span></span></label></div>
    <div class="pm-section-heading"><div><h4>${esc(copy('providerManagement.modelPolicyTitle', 'Provider model policy'))}</h4><p>${esc(copy('providerManagement.addedOnlyHint', 'Only models added to this policy are available for routing.'))}</p></div></div>
    <label class="pm-check-row pm-inherit-row"><input type="checkbox" data-action="provider-inherit" ${modeOf(provider.model_policy) === 'inherit' ? 'checked' : ''} ${canConfigure ? '' : 'disabled'}><span>${esc(copy('providerManagement.platformInherit', 'Use platform default policy'))}</span></label>
    ${modeOf(provider.model_policy) === 'inherit' ? `<p class="pm-dialog-hint">${esc(copy('providerManagement.providerInheritHint', 'This Provider uses the platform default model policy.'))}</p>` : renderPolicyEditor(provider.model_policy, 'provider', canConfigure, {})}
    <div class="pm-section-heading"><div><h4>${esc(copy('providerManagement.childSkillsTitle', 'Provider Skills'))}</h4><p>${esc(copy('providerManagement.childSkillsHint', 'A Skill can inherit the Provider policy or use an explicit subset.'))}</p></div></div>
    ${provider.skills?.length ? `<div class="pm-table-wrap"><table class="pm-table pm-skill-table"><thead><tr><th>${esc(copy('providerManagement.skillColumn', 'Skill'))}</th><th>${esc(copy('providerManagement.requirementColumn', 'Requirement'))}</th><th>${esc(copy('providerManagement.policyColumn', 'Policy'))}</th><th></th></tr></thead><tbody>${provider.skills.map(skill => `<tr><th><strong>${esc(skill.name || skill.skill_id)}</strong><small>${esc(skill.skill_id)}</small></th><td>${esc(skill.requires_vision ? copy('providerManagement.vision', 'Text + image') : copy('providerManagement.textOnly', 'Text'))}</td><td>${esc(skill.model_policy?.mode === 'inherit' ? copy('providerManagement.inheritProvider', 'Inherit Provider') : `${(skill.model_policy?.allowed_model_refs || []).length} ${copy('providerManagement.modelsAdded', 'models added')}`)}</td><td><button class="pm-link-button" type="button" data-action="edit-skill" data-skill="${esc(skill.skill_id)}" ${canConfigure ? '' : 'disabled'}>${esc(copy('providerManagement.configureModels', 'Configure models'))}</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="pm-empty">${esc(copy('providerManagement.noChildSkills', 'No Skills are registered for this Provider.'))}</div>`}
    <div class="pm-footer"><div class="pm-error" role="alert" ${provider.validationError ? '' : 'hidden'}>${esc(provider.validationError || '')}</div><div class="pm-buttons"><button type="button" class="pm-button" data-action="back">${esc(copy('providerManagement.cancel', 'Back'))}</button><button type="button" class="pm-button pm-primary" data-action="save-provider" ${canConfigure ? '' : 'disabled'}>${esc(copy('providerManagement.save', 'Save model configuration'))}</button></div></div>
    ${provider.skillEditor ? renderChildEditor(provider.skillEditor, canConfigure) : ''}
  </section>`
}

function renderPolicyEditor(policy, editorId, canConfigure, requirements) {
  const isInherited = modeOf(policy) === 'inherit'
  const models = modalState?.allModels || []
  const added = getAddedModels(policy, models)
  const compatible = added.filter(model => model.missing !== true && model.is_active !== false && modelMeetsRequirements(model, requirements))
  const defaultRef = policy.default_model_ref || ''
  return `<div class="pm-policy-editor" data-editor="${editorId}">
    <div class="pm-added-list">${added.length ? added.map(model => `<div class="pm-added-model ${model.missing || model.is_active === false ? 'is-missing' : ''}"><div><strong>${esc(displayModel(model))}</strong><small>${esc(model.missing || model.is_active === false ? copy('providerManagement.missingModel', 'Model is no longer available') : model.source || '')}</small></div><span class="pm-capability">${esc(supportsVision(model) ? copy('providerManagement.textVision', 'Text + vision') : copy('providerManagement.textCapability', 'Text'))}</span><button type="button" class="pm-remove" data-action="remove-model" data-editor="${editorId}" data-ref="${esc(model.ref)}" ${canConfigure ? '' : 'disabled'}>${esc(copy('providerManagement.remove', 'Remove'))}</button></div>`).join('') : `<div class="pm-empty">${esc(copy('providerManagement.noneAdded', 'No models added'))}</div>`}</div>
    <button type="button" class="pm-button pm-add" data-action="open-picker" data-editor="${editorId}" ${canConfigure ? '' : 'disabled'}>＋ ${esc(copy('providerManagement.addModels', 'Add models'))}</button>
    <label class="pm-field"><span>${esc(copy('providerManagement.defaultModel', 'Default model'))}</span><select data-action="set-default" data-editor="${editorId}" ${canConfigure ? '' : 'disabled'}><option value="">${esc(copy('providerManagement.chooseDefault', 'Choose a default model'))}</option>${compatible.map(model => `<option value="${esc(model.ref)}" ${defaultRef === model.ref ? 'selected' : ''}>${esc(displayModel(model))}</option>`).join('')}</select></label>
    ${Object.keys(requirements || {}).length && !isInherited && added.some(model => !modelMeetsRequirements(model, requirements)) ? `<p class="pm-warning">${esc(copy('providerManagement.capabilityFilter', 'Some added models do not meet this Skill’s required capabilities. Remove them or choose compatible models.'))}</p>` : ''}
  </div>`
}

function renderChildEditor(editor, canConfigure) {
  const skill = editor.skill
  const inherited = modeOf(editor.policy) === 'inherit'
  return `<div class="pm-editor-overlay" data-editor-kind="skill" role="presentation" data-action="close-child-overlay"><section class="pm-dialog" role="dialog" aria-modal="true" aria-labelledby="pm-child-title"><header><div><p>${esc(draft.provider_type)} / ${esc(skill.skill_id)}</p><h4 id="pm-child-title">${esc(skill.name || skill.skill_id)}</h4></div><button type="button" data-action="close-child" aria-label="${esc(copy('providerManagement.close', 'Close'))}">×</button></header>
    <label class="pm-check-row"><input type="checkbox" data-action="skill-inherit" ${inherited ? 'checked' : ''} ${canConfigure ? '' : 'disabled'}><span>${esc(copy('providerManagement.inheritToggle', 'Inherit the Provider model policy'))}</span></label>
    ${inherited ? `<p class="pm-dialog-hint">${esc(copy('providerManagement.inheritHint', 'This Skill follows the Provider allowed models and default, filtered by its capability requirements.'))}</p>` : renderPolicyEditor(editor.policy, `skill:${skill.skill_id}`, canConfigure, skillRequirements(skill))}
    <div class="pm-error" role="alert" ${editor.error ? '' : 'hidden'}>${esc(editor.error || '')}</div><footer><button type="button" class="pm-button" data-action="close-child">${esc(copy('providerManagement.cancel', 'Cancel'))}</button><button type="button" class="pm-button pm-primary" data-action="save-child" ${canConfigure ? '' : 'disabled'}>${esc(copy('providerManagement.applySkill', 'Apply Skill configuration'))}</button></footer></section></div>`
}

function renderStandalonePanel() {
  const skill = standaloneSkills.find(item => item.skill_id === selectedSkillId)
  const list = `<section class="pm-panel"><div class="pm-panel-heading"><div><h3>${esc(copy('providerManagement.standaloneHeading', 'Standalone Skills'))}</h3><p>${esc(copy('providerManagement.standaloneHint', 'These Skills use platform defaults or their own model policy, independent of Provider assignments.'))}</p></div></div>
    ${standaloneSkills.length ? `<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>${esc(copy('providerManagement.skillColumn', 'Skill'))}</th><th>${esc(copy('providerManagement.requirementColumn', 'Requirement'))}</th><th>${esc(copy('providerManagement.policyColumn', 'Policy'))}</th><th></th></tr></thead><tbody>${standaloneSkills.map(item => `<tr><th><strong>${esc(item.name || item.skill_id)}</strong><small>${esc(item.skill_id)}</small></th><td>${esc(item.requires_vision ? copy('providerManagement.vision', 'Text + image') : copy('providerManagement.textOnly', 'Text'))}</td><td>${esc(item.model_policy?.mode === 'inherit' ? copy('providerManagement.platformDefault', 'Platform defaults') : `${(item.model_policy?.allowed_model_refs || []).length} ${copy('providerManagement.modelsAdded', 'models added')}`)}</td><td><button type="button" class="pm-link-button" data-action="open-standalone" data-skill="${esc(item.skill_id)}" ${actions.canConfigureModels ? '' : 'disabled'}>${esc(copy('providerManagement.configureModels', 'Configure models'))}</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="pm-empty">${esc(copy('providerManagement.noStandaloneSkills', 'No standalone Skills were returned.'))}</div>`}
    </section>`
  if (!skill || !skill.editor) return list
  const policy = skill.editor
  const inherited = modeOf(policy) === 'inherit'
  return `${list}<div class="pm-editor-overlay" data-editor-kind="skill" role="presentation" data-action="close-standalone-overlay"><section class="pm-dialog pm-standalone-editor" role="dialog" aria-modal="true" aria-labelledby="pm-standalone-title"><header><div><p>${esc(copy('providerManagement.standaloneHeading', 'Standalone Skills'))} / ${esc(skill.skill_id)}</p><h3 id="pm-standalone-title">${esc(skill.name || skill.skill_id)}</h3></div><button type="button" data-action="back-skills" aria-label="${esc(copy('providerManagement.close', 'Close'))}">×</button></header><p>${esc(skill.skill_id)} · ${esc(skill.requires_vision ? copy('providerManagement.vision', 'Text + image') : copy('providerManagement.textOnly', 'Text'))}</p>
    <label class="pm-check-row"><input type="checkbox" data-action="standalone-inherit" ${inherited ? 'checked' : ''} ${actions.canConfigureModels ? '' : 'disabled'}><span>${esc(copy('providerManagement.platformInherit', 'Use platform default policy'))}</span></label>
    ${inherited ? `<p class="pm-dialog-hint">${esc(copy('providerManagement.platformInheritHint', 'This Skill does not inherit a Provider policy.'))}</p>` : renderPolicyEditor(policy, `standalone:${skill.skill_id}`, actions.canConfigureModels, skillRequirements(skill))}
    <div class="pm-error" role="alert" ${skill.error ? '' : 'hidden'}>${esc(skill.error || '')}</div><footer><button type="button" class="pm-button" data-action="back-skills">${esc(copy('providerManagement.cancel', 'Cancel'))}</button><button type="button" class="pm-button pm-primary" data-action="save-standalone" ${actions.canConfigureModels ? '' : 'disabled'}>${esc(copy('providerManagement.save', 'Save model configuration'))}</button></footer>
    </section></div>`
}

function renderPicker() {
  if (!modalState?.open) return ''
  const { models = [], search = '', page = 1, pageSize = 20, total = 0, loading = false, pending = new Set(), editorId } = modalState
  return `<div class="pm-picker-overlay" role="presentation" data-action="close-picker-overlay"><section class="pm-picker" role="dialog" aria-modal="true" aria-labelledby="pm-picker-title"><header><div><p>${esc(copy('providerManagement.pickerEyebrow', 'Add from configured models'))}</p><h3 id="pm-picker-title">${esc(copy('providerManagement.pickerTitle', 'Add available models'))}</h3></div><button type="button" data-action="close-picker" aria-label="${esc(copy('providerManagement.close', 'Close'))}">×</button></header>
    <label class="pm-search"><span>${esc(copy('providerManagement.searchModels', 'Search models'))}</span><input type="search" data-action="search-models" value="${esc(search)}" placeholder="${esc(copy('providerManagement.searchPlaceholder', 'Model or provider name'))}"></label>
    <div class="pm-picker-results" aria-live="polite">${loading ? `<p class="pm-empty">${esc(copy('providerManagement.loading', 'Loading models…'))}</p>` : modalState.error ? `<p class="pm-error">${esc(modalState.error)}</p>` : models.length ? models.map(model => `<label class="pm-option ${pending.has(model.ref) ? 'is-selected' : ''}"><input type="checkbox" data-action="toggle-pending" data-ref="${esc(model.ref)}" ${pending.has(model.ref) ? 'checked' : ''}><span><strong>${esc(displayModel(model))}</strong><small>${esc(model.source || '')}</small></span><span class="pm-capability">${esc(supportsVision(model) ? copy('providerManagement.textVision', 'Text + vision') : copy('providerManagement.textCapability', 'Text'))}</span></label>`).join('') : `<p class="pm-empty">${esc(copy('providerManagement.noMatches', search ? 'No matching models.' : 'No available models.'))}</p>`}</div>
    <div class="pm-pagination"><span>${esc(copy('providerManagement.page', 'Page'))} ${page} / ${Math.max(1, Math.ceil(total / pageSize))}</span><div><button type="button" data-action="picker-page" data-page="${page - 1}" ${page <= 1 || loading ? 'disabled' : ''}>${esc(copy('providerManagement.previous', 'Previous'))}</button><button type="button" data-action="picker-page" data-page="${page + 1}" ${page * pageSize >= total || loading ? 'disabled' : ''}>${esc(copy('providerManagement.next', 'Next'))}</button></div></div>
    <footer><button type="button" class="pm-button" data-action="close-picker">${esc(copy('providerManagement.cancel', 'Cancel'))}</button><button type="button" class="pm-button pm-primary" data-action="add-selected" ${pending.size ? '' : 'disabled'}>${esc(copy('providerManagement.addSelected', `Add selected (${pending.size})`))}</button></footer></section></div>`
}

function modelPolicyFor(editorId) {
  if (editorId === 'provider') return draft.model_policy
  if (editorId.startsWith('skill:')) return draft.skillEditor?.policy
  if (editorId.startsWith('standalone:')) return standaloneSkills.find(item => item.skill_id === selectedSkillId)?.editor
  return null
}

function selectedModels(editorId) {
  const policy = modelPolicyFor(editorId)
  return policy?.allowed_model_refs || []
}

function pickerScope(editorId) {
  if (editorId === 'provider') return { target_kind: 'provider', provider_type: draft.provider_type, instance_name: draft.instance_name }
  if (editorId.startsWith('skill:')) return { target_kind: 'provider_skill', provider_type: draft.provider_type, instance_name: draft.instance_name, skill_id: editorId.slice(6) }
  return { target_kind: 'standalone_skill', skill_id: editorId.slice('standalone:'.length) }
}

async function loadPickerPage({ focusSearch = false } = {}) {
  if (!modalState?.open) return
  const requestId = ++sequence
  const searchFocused = focusSearch || document.activeElement?.matches?.('[data-action="search-models"]') === true
  const searchStart = document.activeElement?.matches?.('[data-action="search-models"]') ? document.activeElement.selectionStart : null
  const searchEnd = document.activeElement?.matches?.('[data-action="search-models"]') ? document.activeElement.selectionEnd : null
  const restoreSearch = () => {
    if (!searchFocused || !host) return
    const input = host.querySelector('[data-action="search-models"]')
    input?.focus()
    if (searchStart !== null && searchEnd !== null) input?.setSelectionRange(searchStart, searchEnd)
  }
  modalState.loading = true
  modalState.error = ''
  render()
  restoreSearch()
  try {
    const { editorId, search, page, pageSize } = modalState
    const scope = pickerScope(editorId)
    let response
    if (editorId.startsWith('skill:')) {
      const skill = draft.skills.find(item => item.skill_id === editorId.slice(6))
      if (modeOf(draft.model_policy) === 'inherit') {
        response = await listProviderManagementModelOptions({ ...scope, search, page, page_size: pageSize })
        const current = new Set(selectedModels(editorId))
        const requirements = skillRequirements(skill, response.required_capabilities)
        response.models = (response.models || []).filter(model => !current.has(model.ref) && model.missing !== true && model.is_active !== false && modelMeetsRequirements(model, requirements))
      } else {
      const metadata = new Map([...(modalState.allModels || []), ...(draft.model_policy.allowed_models || [])].map(model => [model.ref, model]))
      const allowedRefs = new Set(draft.model_policy.allowed_model_refs || [])
      const scoped = [...allowedRefs].map(ref => metadata.get(ref)).filter(model => model && model.missing !== true && model.is_active !== false)
      const localCandidates = filterModelsBySearch(filterChildOptionsByDraft(scoped, draft, skill), search)
      const current = new Set(selectedModels(editorId))
      const candidates = localCandidates.filter(model => !current.has(model.ref))
      const offset = (page - 1) * pageSize
      response = { models: candidates.slice(offset, offset + pageSize), total: candidates.length, page, page_size: pageSize }
      }
    } else {
      response = await listProviderManagementModelOptions({ ...scope, search, page, page_size: pageSize })
      const current = new Set(selectedModels(editorId))
      response.models = (response.models || []).filter(model => !current.has(model.ref))
      if (editorId.startsWith('standalone:')) {
        const skill = standaloneSkills.find(item => item.skill_id === editorId.slice('standalone:'.length))
        const requirements = skillRequirements(skill, response.required_capabilities)
        response.models = response.models.filter(model => modelMeetsRequirements(model, requirements) && model.missing !== true && model.is_active !== false)
      }
    }
    if (requestId !== sequence || !modalState?.open) return
    modalState.models = response.models || []
    modalState.total = response.total || 0
    modalState.page = response.page || page
    modalState.pageSize = response.page_size || pageSize
    modalState.allModels = [...(modalState.allModels || []), ...modalState.models].reduce((all, model) => {
      if (!all.some(item => item.ref === model.ref)) all.push(model)
      return all
    }, [])
    modalState.loading = false
  } catch (error) {
    if (requestId !== sequence) return
    modalState.loading = false
    modalState.error = error.message
  }
  render()
  restoreSearch()
}

function policyError(policy, skill, providerScope) {
  if (modeOf(policy) === 'inherit') return ''
  const allowed = policy.allowed_model_refs || []
  if (!allowed.length) return copy('providerManagement.errorEmpty', 'Add at least one model or use inherited configuration.')
  if (providerScope && modeOf(draft.model_policy) !== 'inherit' && allowed.some(ref => !(draft.model_policy.allowed_model_refs || []).includes(ref))) return copy('providerManagement.errorOutOfScope', 'A Skill model must be included in the Provider allowed models.')
  const metadata = [...(policy.allowed_models || []), ...(modalState?.allModels || [])]
  if (allowed.some(ref => metadata.some(model => model.ref === ref && (model.missing === true || model.is_active === false)))) return copy('providerManagement.errorMissingModel', 'Remove unavailable model references before saving.')
  if (skill) {
    const models = [...(policy.allowed_models || []), ...(modalState?.allModels || [])]
    const requirements = skillRequirements(skill)
    if (allowed.some(ref => models.some(model => model.ref === ref && !modelMeetsRequirements(model, requirements)))) return copy('providerManagement.errorCapability', 'This Skill requires models with compatible capabilities.')
  }
  if (!allowed.includes(policy.default_model_ref)) return copy('providerManagement.errorDefault', 'Choose a default from the added models.')
  return ''
}

function validateProvider() {
  const error = policyError(draft.model_policy, null, false)
  if (error) return error
  for (const skill of draft.skills || []) {
    const error = policyError(skill.model_policy, skill, true)
    if (error) return `${skill.name || skill.skill_id}: ${error}`
  }
  return ''
}

async function refreshProviders() {
  const result = await listProviderManagementProviders()
  providers = result.providers || []
}

async function refreshStandaloneSkills() {
  const result = await listProviderManagementStandaloneSkills()
  standaloneSkills = result.skills || []
}

async function openProvider(key) {
  const provider = providers.find(item => providerKey(item) === key)
  if (!provider) return
  try {
    draft = createProviderDraft(await getProviderManagementProvider(provider.provider_type, provider.instance_name))
    draft.model_policy = policyClone(draft.model_policy)
    draft.skills = (draft.skills || []).map(skill => ({ ...skill, model_policy: policyClone(skill.model_policy) }))
    draft.validationError = ''
    modalState = { allModels: [] }
    render()
  } catch (error) { showError(error) }
}

async function saveProvider() {
  const error = validateProvider()
  if (error) { draft.validationError = error; render(); return }
  try {
    const saved = await saveProviderManagementProvider(draft.provider_type, draft.instance_name, policyPayload(draft))
    draft = null
    statusMessage = copy('providerManagement.saved', 'Provider model configuration saved.')
    await refreshProviders()
    render()
  } catch (error) { reportSaveError(error, draft) }
}

async function toggleActivation(enabled) {
  if (!draft || !actions.canEnableDisable) return
  const expected = draft.enabled
  render()
  try {
    const saved = await setProviderManagementActivation(draft.provider_type, draft.instance_name, { revision: draft.revision, enabled })
    draft.enabled = saved.enabled
    draft.revision = saved.revision
    await refreshProviders()
    render()
  } catch (error) {
    draft.enabled = expected
    reportSaveError(error, draft)
  }
}

function reportSaveError(error, target) {
  const message = error.status === 409
    ? copy('providerManagement.staleRevision', 'This configuration changed elsewhere. Reload the latest version before saving again.')
    : error.message || copy('providerManagement.saveFailed', 'Unable to save this configuration.')
  if (target === draft) target.validationError = message
  else target.error = message
  render()
}

function showError(error) {
  statusMessage = error.message || copy('providerManagement.loadFailed', 'Unable to load Provider Management.')
  render()
}

function policyForEditor(editorId) {
  const policy = modelPolicyFor(editorId)
  if (!policy) return
  if (policy.mode === 'inherit') {
    policy.mode = 'explicit'
    policy.allowed_model_refs = []
    policy.default_model_ref = null
  }
  return policy
}

function focusPickerReturn(editorId) {
  if (!editorId || !host) return
  const button = [...host.querySelectorAll('[data-action="open-picker"]')]
    .find(element => element.getAttribute('data-editor') === editorId)
  button?.focus()
}

function closePicker() {
  if (!modalState?.open) return
  const editorId = modalState.editorId
  modalState.open = false
  sequence += 1
  if (modalState.searchTimer) clearTimeout(modalState.searchTimer)
  render()
  focusPickerReturn(editorId)
}

function beginPicker(editorId) {
  const policy = policyForEditor(editorId)
  if (!policy) return
  modalState = { ...(modalState || {}), open: true, editorId, search: '', page: 1, pageSize: 20, total: 0, models: [], allModels: modalState?.allModels || [], pending: new Set(), loading: false, error: '' }
  loadPickerPage({ focusSearch: true })
}

function applyPolicyChange(action, editorId, ref, value) {
  const policy = modelPolicyFor(editorId)
  if (!policy) return
  if (action === 'remove-model') removeModelFromPolicy(policy, ref)
  if (action === 'set-default') setPolicyDefault(policy, value)
  render()
}

function openChildEditor(skillId) {
  const skill = draft.skills.find(item => item.skill_id === skillId)
  if (!skill) return
  childReturnSkillId = skillId
  draft.skillEditor = { skill, policy: policyClone(skill.model_policy), error: '' }
  modalState = { ...(modalState || {}), allModels: modalState?.allModels || [] }
  render()
  host.querySelector('[data-action="close-child"]')?.focus()
}

function closeChildEditor(save = false) {
  if (!draft?.skillEditor) return
  const editor = draft.skillEditor
  if (save) {
    const error = policyError(editor.policy, editor.skill, true)
    if (error) { editor.error = error; render(); host.querySelector('[data-action="close-child"]')?.focus(); return }
    const skill = draft.skills.find(item => item.skill_id === editor.skill.skill_id)
    skill.model_policy = policyClone(editor.policy)
  }
  delete draft.skillEditor
  render()
  const trigger = [...host.querySelectorAll('[data-action="edit-skill"]')]
    .find(element => element.getAttribute('data-skill') === childReturnSkillId)
  trigger?.focus()
}

function startStandaloneEditor(skillId) {
  const skill = standaloneSkills.find(item => item.skill_id === skillId)
  if (!skill) return
  selectedSkillId = skillId
  skill.editor = policyClone(skill.model_policy)
  skill.error = ''
  modalState = { ...(modalState || {}), allModels: [] }
  render()
  host.querySelector('.pm-standalone-editor [data-action="back-skills"]')?.focus()
}

function closeStandaloneEditor() {
  if (!selectedSkillId) return
  const closingId = selectedSkillId
  selectedSkillId = null
  render()
  const trigger = [...host.querySelectorAll('[data-action="open-standalone"]')]
    .find(element => element.getAttribute('data-skill') === closingId)
  trigger?.focus()
}

async function saveStandalone() {
  const skill = standaloneSkills.find(item => item.skill_id === selectedSkillId)
  if (!skill) return
  const error = policyError(skill.editor, skill, false)
  if (error) { skill.error = error; render(); return }
  try {
    const saved = await saveStandaloneSkillModelPolicy(skill.skill_id, { revision: skill.revision, model_policy: { mode: skill.editor.mode, allowed_model_refs: skill.editor.allowed_model_refs, default_model_ref: skill.editor.default_model_ref } })
    Object.assign(skill, saved)
    delete skill.editor
    const closingId = selectedSkillId
    selectedSkillId = null
    statusMessage = copy('providerManagement.saved', 'Skill model configuration saved.')
    render()
    host.querySelectorAll('[data-action="open-standalone"]').forEach(trigger => {
      if (trigger.getAttribute('data-skill') === closingId) trigger.focus()
    })
  } catch (error) { reportSaveError(error, skill) }
}

async function addPendingModels() {
  if (!modalState?.pending?.size) return
  const editorId = modalState.editorId
  const current = modelPolicyFor(editorId)
  if (!current) return
  const pending = [...modalState.pending]
  const policy = policyForEditor(editorId)
  pending.forEach(ref => addModelToPolicy(policy, ref))
  modalState.allModels = [...(modalState.allModels || []), ...modalState.models].reduce((all, model) => {
    if (!all.some(item => item.ref === model.ref)) all.push(model)
    return all
  }, [])
  modalState.open = false
  if (editorId.startsWith('skill:')) draft.skillEditor.error = ''
  render()
  focusPickerReturn(editorId)
}

function focusableIn(container) {
  return [...container.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]')]
    .filter(element => element.getAttribute('aria-hidden') !== 'true' && !element.hidden)
}

function handleDialogKeydown(dialog, event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    if (dialog.classList.contains('pm-picker')) closePicker()
    else if (draft?.skillEditor) closeChildEditor(false)
    else if (selectedSkillId) closeStandaloneEditor()
    return
  }
  if (event.key !== 'Tab') return
  const items = focusableIn(dialog)
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault()
    first.focus()
  }
}

function bindEvents() {
  host.querySelectorAll('[data-action]').forEach(element => element.addEventListener('click', async event => {
    const target = event.currentTarget
    const action = target.getAttribute('data-action')
    if (action === 'dismiss-status') { statusMessage = ''; render() }
    else if (action === 'show-providers') { activeTab = 'providers'; draft = null; selectedSkillId = null; render(); host.querySelector('#pm-tab-providers')?.focus() }
    else if (action === 'show-skills') { activeTab = 'skills'; draft = null; selectedSkillId = null; render(); host.querySelector('#pm-tab-skills')?.focus() }
    else if (action === 'open-provider') openProvider(target.getAttribute('data-key'))
    else if (action === 'back') { draft = null; render() }
    else if (action === 'save-provider') saveProvider()
    else if (action === 'edit-skill') openChildEditor(target.getAttribute('data-skill'))
    else if (action === 'close-child') closeChildEditor(false)
    else if (action === 'save-child') closeChildEditor(true)
    else if (action === 'open-standalone') startStandaloneEditor(target.getAttribute('data-skill'))
    else if (action === 'back-skills') closeStandaloneEditor()
    else if (action === 'save-standalone') saveStandalone()
    else if (action === 'open-picker') beginPicker(target.getAttribute('data-editor'))
    else if (action === 'remove-model') applyPolicyChange(action, target.getAttribute('data-editor'), target.getAttribute('data-ref'))
    else if (action === 'close-picker') closePicker()
    else if (action === 'close-child-overlay' && event.target === target) closeChildEditor(false)
    else if (action === 'close-standalone-overlay' && event.target === target) closeStandaloneEditor()
    else if (action === 'add-selected') addPendingModels()
    else if (action === 'picker-page') { modalState.page = Number(target.getAttribute('data-page')); loadPickerPage() }
  }))
  host.querySelectorAll('[data-action="activation"]').forEach(element => element.addEventListener('change', event => toggleActivation(event.target.checked)))
  host.querySelectorAll('[role="tab"]').forEach(element => element.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const nextTab = event.key === 'Home' ? 'providers' : event.key === 'End' ? 'skills' : (event.key === 'ArrowRight' ? 'skills' : 'providers')
    activeTab = nextTab
    draft = null
    selectedSkillId = null
    render()
    host.querySelector(`#pm-tab-${nextTab}`)?.focus()
  }))
  host.querySelectorAll('[data-action="provider-inherit"]').forEach(element => element.addEventListener('change', event => {
    draft.model_policy = event.target.checked ? { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } : { mode: 'explicit', allowed_model_refs: [], default_model_ref: null }
    render()
  }))
  host.querySelectorAll('[data-action="set-default"]').forEach(element => element.addEventListener('change', event => applyPolicyChange('set-default', element.getAttribute('data-editor'), null, event.target.value)))
  host.querySelectorAll('[data-action="skill-inherit"]').forEach(element => element.addEventListener('change', event => {
    draft.skillEditor.policy = event.target.checked ? { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } : { mode: 'explicit', allowed_model_refs: [], default_model_ref: null }
    render()
  }))
  host.querySelectorAll('[data-action="standalone-inherit"]').forEach(element => element.addEventListener('change', event => {
    const skill = standaloneSkills.find(item => item.skill_id === selectedSkillId)
    skill.editor = event.target.checked ? { mode: 'inherit', allowed_model_refs: [], default_model_ref: null } : { mode: 'explicit', allowed_model_refs: [], default_model_ref: null }
    render()
  }))
  host.querySelectorAll('[data-action="search-models"]').forEach(element => element.addEventListener('input', event => {
    modalState.search = event.target.value
    modalState.page = 1
    clearTimeout(modalState.searchTimer)
    modalState.searchTimer = setTimeout(loadPickerPage, 250)
  }))
  host.querySelectorAll('[data-action="toggle-pending"]').forEach(element => element.addEventListener('change', event => {
    const ref = event.target.getAttribute('data-ref')
    if (event.target.checked) modalState.pending.add(ref)
    else modalState.pending.delete(ref)
    render()
    const replacement = Array.from(host.querySelectorAll('[data-action="toggle-pending"]')).find(item => item.getAttribute('data-ref') === ref)
    replacement?.focus()
  }))
  host.querySelectorAll('[data-action="close-picker-overlay"]').forEach(element => element.addEventListener('click', event => {
    if (event.target === element) closePicker()
  }))
  host.querySelectorAll('.pm-dialog, .pm-standalone-editor, .pm-picker').forEach(element => element.addEventListener('keydown', event => {
    handleDialogKeydown(element, event)
  }))
}

export async function mount(container) {
  host = container
  addPageStyles()
  const user = getAuthInfo() || await checkAuth({ redirect: true })
  if (!user) return
  authInfo = user
  actions = getProviderManagementActions(authInfo)
  if (!actions.canView) {
    showToast(copy('providerManagement.accessDenied', 'You do not have permission to view Provider Management.'), 'error')
    return
  }
  providers = []
  standaloneSkills = []
  draft = null
  selectedSkillId = null
  activeTab = 'providers'
  modalState = { allModels: [] }
  statusMessage = ''
  render()
  try {
    await Promise.all([refreshProviders(), refreshStandaloneSkills()])
    render()
  } catch (error) { showError(error) }
}

export function unmount() {
  sequence += 1
  if (modalState?.searchTimer) clearTimeout(modalState.searchTimer)
  host = null
  authInfo = null
  actions = null
  providers = []
  standaloneSkills = []
  draft = null
  selectedSkillId = null
  childReturnSkillId = null
  modalState = null
}

export default { mount, unmount }
