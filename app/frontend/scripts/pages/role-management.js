/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { translateIfExists, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { checkAuth } from '../auth.js'
import { getAuthInfo } from '../app.js'
import { buildAssetUrl, buildAppUrl } from '../config.js'
import { restoreInputFocus } from '../dom-utils.js'
import {
  canAccessRoleManagement,
  canManagePermissionModule,
  hasPermission,
} from '../permissions.js'

const MODULES = [
  ['skills', 'skills', 'roles.modules.skills', 'Skills', 'roles.modules.skillsDescription', 'Manage which skills this role can enable and use.'],
  ['providers', 'provider', 'roles.modules.providers', 'Providers', 'roles.modules.providersDescription', 'Manage which provider instances this role can access at runtime.'],
  ['channels', 'channels', 'roles.modules.channels', 'Channels', 'roles.modules.channelsDescription', 'Manage access to connection configuration and lifecycle actions.'],
  ['model_configs', 'model', 'roles.modules.modelConfigs', 'Model Configs', 'roles.modules.modelConfigsDescription', 'Control model catalog visibility and maintenance rights.'],
  ['users', 'users', 'roles.modules.users', 'Users', 'roles.modules.usersDescription', 'Define how this role may browse and administer workspace users.'],
  ['roles', 'roles', 'roles.modules.roles', 'Roles', 'roles.modules.rolesDescription', 'Set whether this role can create, edit, and retire other roles.']
].map(([id, icon, titleKey, fallbackTitle, descriptionKey, fallbackDescription]) => ({
  id,
  icon,
  titleKey,
  fallbackTitle,
  descriptionKey,
  fallbackDescription
}))

const MODULE_PERMISSION_DEFINITIONS = {
  skills: [
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.skillPermissions.managePermissionsDescription', 'Edit which skill permissions roles may receive.']
  ],
  providers: [
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.providerPermissions.managePermissionsDescription', 'Edit which provider instances roles may access at runtime.']
  ],
  channels: [
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.channelPermissions.managePermissionsDescription', 'Edit which channel types roles may access at runtime.']
  ],
  tokens: [
    ['view', 'roles.permissions.view', 'View', 'roles.tokenPermissions.viewDescription', 'Inspect token names, providers, and current health status.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.tokenPermissions.createDescription', 'Register new provider endpoints or API key entries.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.tokenPermissions.editDescription', 'Rotate API keys and update model routing metadata.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.tokenPermissions.deleteDescription', 'Retire tokens that should no longer be used by the workspace.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.tokenPermissions.managePermissionsDescription', 'Edit the token permission model available to other roles.']
  ],
  agent_configs: [
    ['view', 'roles.permissions.view', 'View', 'roles.agentConfigPermissions.viewDescription', 'Inspect reusable agent templates, persona blocks, and activation status.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.agentConfigPermissions.createDescription', 'Create new agent templates for assistants and workflow specialists.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.agentConfigPermissions.editDescription', 'Update agent identity, memory presets, and runtime defaults.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.agentConfigPermissions.deleteDescription', 'Remove agent templates that should no longer be available.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.agentConfigPermissions.managePermissionsDescription', 'Edit the agent-template permission model available to other roles.']
  ],
  provider_configs: [
    ['view', 'roles.permissions.view', 'View', 'roles.providerConfigPermissions.viewDescription', 'Inspect registered provider instances and their ownership.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.providerConfigPermissions.createDescription', 'Register new provider instances and baseline configuration.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.providerConfigPermissions.editDescription', 'Update provider configuration, credentials, and runtime options.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.providerConfigPermissions.deleteDescription', 'Remove provider instances that should no longer be used.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.providerConfigPermissions.managePermissionsDescription', 'Edit the provider instance permission model available to other roles.']
  ],
  model_configs: [
    ['view', 'roles.permissions.view', 'View', 'roles.modelConfigPermissions.viewDescription', 'Inspect model catalog entries and routing metadata.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.modelConfigPermissions.createDescription', 'Register new model definitions for supported providers.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.modelConfigPermissions.editDescription', 'Update model routing, capability metadata, and runtime defaults.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.modelConfigPermissions.deleteDescription', 'Retire model definitions that should no longer be selectable.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.modelConfigPermissions.managePermissionsDescription', 'Edit the model configuration permission model available to other roles.']
  ],
  users: [
    ['view', 'roles.permissions.view', 'View', 'roles.userPermissions.viewDescription', 'Browse users, status, and the roles currently assigned to them.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.userPermissions.createDescription', 'Invite or create new users directly from the admin workspace.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.userPermissions.editDescription', 'Change profiles, assigned roles, and authentication options.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.userPermissions.deleteDescription', 'Remove user accounts that no longer need workspace access.'],
    ['assign_roles', 'roles.permissions.assignRoles', 'Assign Roles', 'roles.userPermissions.assignRolesDescription', 'Assign or remove roles for workspace users.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.userPermissions.managePermissionsDescription', 'Edit the user permission model available to other roles.']
  ],
  roles: [
    ['view', 'roles.permissions.view', 'View', 'roles.rolePermissions.viewDescription', 'Inspect existing roles, summaries, and saved permission sets.'],
    ['create', 'roles.permissions.create', 'Create', 'roles.rolePermissions.createDescription', 'Create new custom roles for teams and projects.'],
    ['edit', 'roles.permissions.edit', 'Edit', 'roles.rolePermissions.editDescription', 'Adjust permission bundles and module defaults.'],
    ['delete', 'roles.permissions.delete', 'Delete', 'roles.rolePermissions.deleteDescription', 'Retire roles that are no longer assigned anywhere.'],
    ['manage_permissions', 'roles.permissions.managePermissions', 'Manage Permissions', 'roles.rolePermissions.managePermissionsDescription', 'Edit permission models and role governance boundaries across modules.']
  ]
}

const ACCESS_ALL_PERMISSION_DEFINITIONS = {
  skills: [
    'roles.accessAll.skills.label',
    'Access All',
    'roles.accessAll.skills.description',
    'Includes every usable skill available now and every future skill added later. Individual skill selections below are ignored while this is enabled.'
  ],
  providers: [
    'roles.accessAll.providers.label',
    'Access All',
    'roles.accessAll.providers.description',
    'Includes every provider instance available now and every future provider instance added later. Individual provider selections below are ignored while this is enabled.'
  ],
  channels: [
    'roles.accessAll.channels.label',
    'Access All',
    'roles.accessAll.channels.description',
    'Includes every channel type available now and every future channel type added later. Individual channel selections below are ignored while this is enabled.'
  ]
}

const ICONS = {
  governance: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 6 5.5V11c0 4.4 2.7 7.6 6 8.5 3.3-.9 6-4.1 6-8.5V5.5L12 3Z"></path><path d="M9.5 12.2 11 13.7l3.6-3.7"></path></svg>',
  skills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m6 18 8.8-8.8"></path><path d="m13.2 6.2 1.1-1.1a2.1 2.1 0 1 1 3 3l-1.1 1.1"></path><path d="m5 19 3-1-2-2-1 3Z"></path><path d="M6.5 5.5v2"></path><path d="M3.5 8.5h2"></path><path d="m8.4 8.4-1.4-1.4"></path></svg>',
  channels: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="12" r="2"></circle><circle cx="18" cy="6" r="2"></circle><circle cx="18" cy="18" r="2"></circle><path d="M8 12h4"></path><path d="m14.2 10.8 2.1-2.1"></path><path d="m14.2 13.2 2.1 2.1"></path></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="12" r="3.5"></circle><path d="M11.5 12H20"></path><path d="M17 12v-2.5"></path><path d="M14.5 12v2"></path></svg>',
  agent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="7" width="12" height="10" rx="3"></rect><path d="M12 4v3"></path><circle cx="10" cy="11" r="1"></circle><circle cx="14" cy="11" r="1"></circle><path d="M9.5 14.5h5"></path></svg>',
  provider: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4.5" width="14" height="5" rx="1.5"></rect><rect x="5" y="14.5" width="14" height="5" rx="1.5"></rect><path d="M8 7h.01"></path><path d="M8 17h.01"></path><path d="M11 7h5"></path><path d="M11 17h5"></path></svg>',
  model: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12 4 6 3.2-6 3.2-6-3.2L12 4Z"></path><path d="M6 7.2v5.6L12 16l6-3.2V7.2"></path><path d="M6 12.8 12 16l6-3.2"></path></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="3.2"></rect><circle cx="9" cy="10.2" r="2.35"></circle><path d="M5.9 16.2c.9-1.75 2.25-2.6 4.1-2.6 1.8 0 3.15.85 4 2.6"></path><path d="M16.3 8.9h2.4"></path><path d="M16.3 12.2h2.4"></path></svg>',
  roles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.8" cy="8.2" r="2.8"></circle><path d="M6.2 18.4c1-2.25 2.65-3.35 4.6-3.35 2.1 0 3.8 1.1 4.8 3.35"></path><path d="m17.25 4.85 1.15.8 1.4-.2-.45 1.34.8 1.08-1.35.3-.8 1.1-.88-.98-.9-.26.44-1.24-.44-1.24 1.43.2Z"></path></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="m19 6-1 14H6L5 6"></path></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>'
}

let container = null
let rolesListEl = null
let editorEl = null
let deleteModal = null
let roles = []
let skills = []
let providerSkills = []
let providerInstances = []
let channelTypes = []
let selectedRoleId = null
let roleSearch = ''
let skillSearch = ''
let providerSearch = ''
let channelSearch = ''
let activeModuleId = 'skills'
let draftRoleState = null
let identifierTouched = false
let eventCleanupFns = []
let currentUserAuthInfo = null

const PAGE_HTML = `
<div class="role-management-page">
  <div class="role-management-shell">
    <header class="role-management-page-header">
      <div>
        <span class="role-management-eyebrow" data-i18n="roles.eyebrow">Access Design</span>
        <h1 data-i18n="roles.title">Role Management</h1>
        <p data-i18n="roles.description">Design reusable permission bundles for channels, users, tokens, and skills from one editor.</p>
      </div>
      <button type="button" class="btn-primary role-management-create-btn" id="createRoleBtn">
        ${ICONS.plus}
        <span data-i18n="roles.createButton">Create Role</span>
      </button>
    </header>
    <div class="role-management-workspace">
      <aside class="role-management-sidebar">
        <label class="role-search-shell" for="roleSearchInput">
          ${ICONS.search}
          <input type="text" id="roleSearchInput" data-i18n-placeholder="roles.searchPlaceholder" placeholder="Search roles...">
        </label>
        <div class="role-management-list" id="roleList"></div>
      </aside>
      <section class="role-management-editor-shell" id="roleEditor"></section>
    </div>
  </div>
</div>
<div id="deleteRoleModal" class="modal-overlay hidden">
  <div class="modal role-delete-modal">
    <div class="modal-header">
      <div>
        <h2 data-i18n="roles.deleteTitle">Delete Role</h2>
        <p class="modal-description" data-i18n="roles.deleteDescription">This role will be removed permanently if it is no longer assigned to any user.</p>
      </div>
      <button type="button" class="modal-close" id="deleteRoleClose">&times;</button>
    </div>
    <div class="modal-body">
      <p class="confirm-message"><span data-i18n="roles.deleteConfirm">Are you sure you want to delete this role?</span></p>
      <strong class="delete-target-name" id="deleteRoleName"></strong>
      <input type="hidden" id="deleteRoleId" value="">
    </div>
    <div class="modal-footer">
      <button type="button" class="btn-secondary" id="deleteRoleCancel" data-i18n="roles.cancel">Cancel</button>
      <button type="button" class="btn-danger" id="deleteRoleConfirm" data-i18n="roles.deleteButton">Delete Role</button>
    </div>
  </div>
</div>
`

function translateOrFallback(key, fallback) {
  return translateIfExists(key) || fallback
}

function getBuiltinRoleTranslation(role, field) {
  if (!role?.is_builtin || !role?.identifier) return null
  return translateIfExists(`roles.builtinRoleCatalog.${role.identifier}.${field}`)
}

function getRoleDisplayName(role) {
  return getBuiltinRoleTranslation(role, 'name') || role?.name || ''
}

function getRoleDisplayDescription(role) {
  return getBuiltinRoleTranslation(role, 'description') || role?.description || ''
}

function getSkillTranslation(skill, field) {
  const skillName = skill?.skill_id || skill?.skill_name || skill?.name
  if (!skillName) return null
  return (
    translateIfExists(`roles.skillCatalog.${skillName}.${field}`)
    || translateIfExists(`roles.skillCatalog.${skillName.split(':').pop()}.${field}`)
  )
}

function getSkillDisplayName(skill) {
  return getSkillTranslation(skill, 'name') || skill?.skill_name || skill?.name || ''
}

function getSkillDisplayDescription(skill) {
  return (
    getSkillTranslation(skill, 'description')
    || skill?.description
    || translateOrFallback('roles.defaultSkillDescription', 'Workspace skill available to AtlasClaw agents.')
  )
}

function addTrackedListener(element, event, handler, options) {
  if (!element) return
  element.addEventListener(event, handler, options)
  eventCleanupFns.push(() => element.removeEventListener(event, handler, options))
}

function cloneData(value) {
  return JSON.parse(JSON.stringify(value))
}

function escapeHtml(str) {
  if (!str) return ''
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

function slugifyIdentifier(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function buildDefaultPermissions() {
  return {
    skills: { module_permissions: { view: false, enable_disable: false, manage_permissions: false }, allow_all: false, skill_permissions: [] },
    providers: { module_permissions: { manage_permissions: false }, allow_all: false, provider_permissions: [] },
    channels: { module_permissions: { manage_permissions: false }, allow_all: false, channel_permissions: [] },
    tokens: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
    agent_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
    provider_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
    model_configs: { view: false, create: false, edit: false, delete: false, manage_permissions: false },
    users: { view: false, create: false, edit: false, delete: false, assign_roles: false, manage_permissions: false },
    roles: { view: false, create: false, edit: false, delete: false, manage_permissions: false }
  }
}

function buildAllEnabledPermissions() {
  const permissions = buildDefaultPermissions()
  Object.entries(permissions).forEach(([moduleId, config]) => {
    if (moduleId === 'skills') {
      config.module_permissions.view = true
      config.module_permissions.enable_disable = true
      config.module_permissions.manage_permissions = true
      config.allow_all = true
      config.skill_permissions = buildSkillPermissionsFromCatalog(true)
      return
    }
    if (moduleId === 'providers') {
      config.module_permissions.manage_permissions = true
      config.allow_all = true
      config.provider_permissions = buildProviderPermissionsFromCatalog(true)
      return
    }
    if (moduleId === 'channels') {
      config.module_permissions.manage_permissions = true
      config.allow_all = true
      config.channel_permissions = buildChannelPermissionsFromCatalog(true)
      return
    }
    Object.keys(config).forEach(key => {
      config[key] = true
    })
  })
  return permissions
}

function getRoleTemplate(identifier = '') {
  if (identifier === 'admin') return buildAllEnabledPermissions()
  if (identifier === 'user') {
    const permissions = buildDefaultPermissions()
    permissions.channels.allow_all = true
    return permissions
  }
  if (identifier === 'viewer') {
    const permissions = buildDefaultPermissions()
    permissions.skills.module_permissions.view = true
    permissions.channels.channel_permissions = buildChannelPermissionsFromCatalog(true)
    permissions.tokens.view = true
    permissions.users.view = true
    permissions.roles.view = true
    return permissions
  }
  return buildDefaultPermissions()
}

function providerPermissionKey(providerType, instanceName) {
  return `${String(providerType || '').trim()}::${String(instanceName || '').trim()}`
}

function channelPermissionKey(channelType) {
  return String(channelType || '').trim()
}

function getProviderDisplayName(provider) {
  return provider?.display_name || provider?.provider_type || ''
}

function getProviderDisplayDescription(provider) {
  return provider?.base_url || translateOrFallback('roles.defaultProviderDescription', 'Registered service provider instance.')
}

function buildSkillPermissionsFromCatalog(defaultEnabled = true) {
  return skills.map(skill => {
    const skillName = String(skill?.name || '').trim()
    const runtimeEnabled = skill?.runtime_enabled !== false
    return {
      skill_id: skillName,
      skill_name: skillName,
      description: String(skill?.description || ''),
      runtime_enabled: runtimeEnabled,
      authorized: runtimeEnabled && defaultEnabled,
      enabled: runtimeEnabled && defaultEnabled
    }
  }).filter(skill => skill.skill_id)
}

function buildProviderPermissionsFromCatalog(defaultAllowed = true) {
  return providerInstances.map(provider => {
    const providerType = String(provider?.provider_type || '').trim()
    const instanceName = String(provider?.instance_name || '').trim()
    return {
      provider_type: providerType,
      display_name: String(provider?.display_name || providerType),
      instance_name: instanceName,
      base_url: String(provider?.base_url || ''),
      auth_type: provider?.auth_type || '',
      allowed: defaultAllowed
    }
  }).filter(provider => provider.provider_type && provider.instance_name)
}

function getChannelDisplayName(channel) {
  return channel?.channel_name || channel?.name || channel?.type || channel?.channel_type || ''
}

function getChannelDisplayDescription(channel) {
  const mode = channel?.mode ? String(channel.mode) : ''
  return mode
    ? translateOrFallback('roles.defaultChannelDescriptionWithMode', `Channel type using ${mode} mode.`).replace('{{mode}}', mode)
    : translateOrFallback('roles.defaultChannelDescription', 'Registered channel type available to workspace users.')
}

function buildChannelPermissionsFromCatalog(defaultAllowed = true) {
  return channelTypes.map(channel => {
    const channelType = String(channel?.type || channel?.channel_type || '').trim()
    return {
      channel_type: channelType,
      channel_name: String(channel?.name || channel?.channel_name || channelType),
      mode: channel?.mode || '',
      allowed: defaultAllowed
    }
  }).filter(channel => channel.channel_type)
}

function normalizeProviderPermissions(storedPermissions = [], providerCatalog = []) {
  const storedByKey = new Map()
  for (const entry of Array.isArray(storedPermissions) ? storedPermissions : []) {
    const providerType = String(entry?.provider_type || '').trim()
    const instanceName = String(entry?.instance_name || '').trim()
    if (!providerType || !instanceName) continue
    storedByKey.set(providerPermissionKey(providerType, instanceName), {
      provider_type: providerType,
      display_name: String(entry?.display_name || providerType),
      instance_name: instanceName,
      base_url: String(entry?.base_url || ''),
      auth_type: entry?.auth_type || '',
      allowed: entry.allowed === true
    })
  }

  const normalized = []
  for (const provider of providerCatalog) {
    const providerType = String(provider?.provider_type || '').trim()
    const instanceName = String(provider?.instance_name || '').trim()
    if (!providerType || !instanceName) continue
    const key = providerPermissionKey(providerType, instanceName)
    const existing = storedByKey.get(key)
    normalized.push({
      provider_type: providerType,
      display_name: String(provider?.display_name || existing?.display_name || providerType),
      instance_name: instanceName,
      base_url: String(provider?.base_url || existing?.base_url || ''),
      auth_type: provider?.auth_type || existing?.auth_type || '',
      allowed: existing ? existing.allowed === true : false
    })
  }

  return normalized
}

function normalizeChannelPermissions(storedPermissions = [], channelCatalog = []) {
  const storedByType = new Map()
  for (const entry of Array.isArray(storedPermissions) ? storedPermissions : []) {
    const channelType = String(entry?.channel_type || '').trim()
    if (!channelType) continue
    storedByType.set(channelPermissionKey(channelType), {
      channel_type: channelType,
      channel_name: String(entry?.channel_name || channelType),
      mode: entry?.mode || '',
      allowed: entry.allowed === true
    })
  }

  const normalized = []
  for (const channel of channelCatalog) {
    const channelType = String(channel?.type || channel?.channel_type || '').trim()
    if (!channelType) continue
    const key = channelPermissionKey(channelType)
    const existing = storedByType.get(key)
    normalized.push({
      channel_type: channelType,
      channel_name: String(channel?.name || existing?.channel_name || channelType),
      mode: channel?.mode || existing?.mode || '',
      allowed: existing ? existing.allowed === true : false
    })
  }

  return normalized
}

function buildPermissionsPayload(role) {
  const permissions = cloneData(role?.permissions || buildDefaultPermissions())
  delete permissions.rbac
  if (permissions.users && typeof permissions.users === 'object') {
    delete permissions.users.reset_password
  }
  if (permissions.providers && typeof permissions.providers === 'object') {
    permissions.providers.allow_all = permissions.providers.allow_all === true
    permissions.providers.provider_permissions = Array.isArray(permissions.providers.provider_permissions)
      ? permissions.providers.provider_permissions
        .filter(provider => !role?.isNew || provider?.allowed === true)
        .filter(provider => provider?.provider_type && provider?.instance_name)
        .map(provider => ({
          provider_type: provider.provider_type,
          instance_name: provider.instance_name,
          allowed: provider.allowed === true
        }))
      : []
  }
  if (permissions.channels && typeof permissions.channels === 'object') {
    permissions.channels.allow_all = permissions.channels.allow_all === true
    const channelPermissions = Array.isArray(permissions.channels.channel_permissions)
      ? permissions.channels.channel_permissions
      : []
    permissions.channels.channel_permissions = Array.isArray(permissions.channels.channel_permissions)
      ? channelPermissions
        .filter(channel => !role?.isNew || channel?.allowed === true)
        .filter(channel => channel?.channel_type)
        .map(channel => ({
          channel_type: channel.channel_type,
          channel_name: channel.channel_name || channel.channel_type,
          allowed: channel.allowed === true
        }))
      : []
  }
  if (permissions.skills && typeof permissions.skills === 'object') {
    permissions.skills.allow_all = permissions.skills.allow_all === true
  }

  return permissions
}

function normalizeRole(role, skillCatalog = []) {
  const normalized = {
    id: role?.id || '',
    name: role?.name || '',
    identifier: role?.identifier || '',
    description: role?.description || '',
    is_active: role?.is_active !== false,
    is_builtin: role?.is_builtin === true,
    isNew: role?.isNew === true,
    permissions: cloneData(role?.permissions || getRoleTemplate(role?.identifier))
  }
  const basePermissions = buildDefaultPermissions()
  normalized.permissions = {
    ...basePermissions,
    ...normalized.permissions,
    skills: {
      ...basePermissions.skills,
      ...(normalized.permissions.skills || {}),
      module_permissions: {
        ...basePermissions.skills.module_permissions,
        ...(normalized.permissions.skills?.module_permissions || {})
      },
      allow_all: normalized.permissions.skills?.allow_all === true,
      skill_permissions: Array.isArray(normalized.permissions.skills?.skill_permissions)
        ? normalized.permissions.skills.skill_permissions
        : []
    },
    providers: {
      ...basePermissions.providers,
      ...(normalized.permissions.providers || {}),
      module_permissions: {
        ...basePermissions.providers.module_permissions,
        ...(normalized.permissions.providers?.module_permissions || {})
      },
      allow_all: normalized.permissions.providers?.allow_all === true,
      provider_permissions: normalizeProviderPermissions(
        normalized.permissions.providers?.provider_permissions,
        providerInstances
      )
    },
    channels: {
      ...basePermissions.channels,
      ...(normalized.permissions.channels || {}),
      module_permissions: {
        ...basePermissions.channels.module_permissions,
        ...(normalized.permissions.channels?.module_permissions || {})
      },
      allow_all: normalized.permissions.channels?.allow_all === true,
      channel_permissions: normalizeChannelPermissions(
        normalized.permissions.channels?.channel_permissions,
        channelTypes
      )
    }
  }
  const storedSkillPermissions = new Map(
    normalized.permissions.skills.skill_permissions.map(skill => [skill.skill_id || skill.skill_name, skill])
  )
  normalized.permissions.skills.skill_permissions = skillCatalog.map(skill => {
    const existing = storedSkillPermissions.get(skill.name) || {}
    const runtimeEnabled = skill.runtime_enabled !== false
    return {
      skill_id: skill.name,
      skill_name: skill.name,
      description: skill.description || '',
      runtime_enabled: runtimeEnabled,
      authorized: runtimeEnabled && existing.authorized === true,
      enabled: runtimeEnabled && existing.enabled === true
    }
  })
  // Auto-include provider skills as always authorized+enabled (they auto-load from provider config)
  for (const ps of providerSkills) {
    normalized.permissions.skills.skill_permissions.push({
      skill_id: ps.name,
      skill_name: ps.name,
      description: ps.description || '',
      runtime_enabled: ps.runtime_enabled !== false,
      is_provider_skill: true,
      authorized: true,
      enabled: true
    })
  }
  return normalized
}

function createEmptyRole() {
  return normalizeRole({
    id: 'new-role',
    name: '',
    identifier: '',
    description: '',
    is_active: true,
    is_builtin: false,
    isNew: true,
    permissions: getRoleTemplate()
  }, skills)
}

function fetchJson(url, options = {}) {
  return fetch(url, options).then(async response => {
    if (!response.ok) {
      let detail = translateOrFallback('roles.loadFailed', 'Failed to load role management data')
      try {
        const payload = await response.json()
        detail = payload.detail || detail
      } catch (error) {
        // Ignore parsing failure and use fallback detail.
      }
      throw new Error(detail)
    }
    if (response.status === 204) return null
    return response.json()
  })
}

function countEnabledPermissions(role, moduleId) {
  const permissions = role?.permissions?.[moduleId]
  if (!permissions) return 0
  if (moduleId === 'skills') {
    const moduleFlags = [
      permissions.module_permissions?.view,
      permissions.module_permissions?.manage_permissions
    ].filter(Boolean).length
    if (permissions.allow_all === true) {
      return moduleFlags + 1
    }
    const skillFlags = (permissions.skill_permissions || []).filter(skill => skill.enabled && !skill.is_provider_skill).length
    return moduleFlags + skillFlags
  }
  if (moduleId === 'providers') {
    const moduleFlags = permissions.module_permissions?.manage_permissions ? 1 : 0
    if (permissions.allow_all === true) {
      return moduleFlags + 1
    }
    const allowedProviders = (permissions.provider_permissions || []).filter(provider => provider.allowed === true).length
    return moduleFlags + allowedProviders
  }
  if (moduleId === 'channels') {
    const moduleFlags = permissions.module_permissions?.manage_permissions ? 1 : 0
    if (permissions.allow_all === true) {
      return moduleFlags + 1
    }
    const allowedChannels = (permissions.channel_permissions || []).filter(channel => channel.allowed === true).length
    return moduleFlags + allowedChannels
  }
  return Object.values(permissions).filter(Boolean).length
}

function canCreateRoles() {
  return hasPermission(currentUserAuthInfo, 'roles.create')
}

function canEditRoleMetadata() {
  return hasPermission(currentUserAuthInfo, 'roles.edit')
}

function canDeleteRoles() {
  return hasPermission(currentUserAuthInfo, 'roles.delete')
}

function canManageModule(moduleId) {
  return canManagePermissionModule(currentUserAuthInfo, moduleId)
}

function isSystemManagedBuiltinRole(role = draftRoleState) {
  return role?.is_builtin === true && ['admin', 'user'].includes(role?.identifier)
}

function canManageAnyPermissions() {
  return MODULES.some(module => canManageModule(module.id))
}

function canSaveRole() {
  if (!draftRoleState) return false
  if (draftRoleState.isNew) return canCreateRoles()
  if (draftRoleState.is_builtin) return canManageAnyPermissions()
  return canEditRoleMetadata() || canManageAnyPermissions()
}

function countModuleSummaryEnabledPermissions(role, moduleId) {
  const permissions = role?.permissions?.[moduleId]
  if (!permissions) return 0
  if (moduleId === 'skills') {
    const governanceFlags = permissions.module_permissions?.manage_permissions ? 1 : 0
    if (permissions.allow_all === true) {
      return governanceFlags + 1
    }
    const skillFlags = (permissions.skill_permissions || []).filter(skill => skill.enabled && !skill.is_provider_skill).length
    return governanceFlags + skillFlags
  }
  if (moduleId === 'providers') {
    const governanceFlags = permissions.module_permissions?.manage_permissions ? 1 : 0
    if (permissions.allow_all === true) {
      return governanceFlags + 1
    }
    const providerFlags = (permissions.provider_permissions || []).filter(provider => provider.allowed === true).length
    return governanceFlags + providerFlags
  }
  if (moduleId === 'channels') {
    const governanceFlags = permissions.module_permissions?.manage_permissions ? 1 : 0
    if (permissions.allow_all === true) {
      return governanceFlags + 1
    }
    const channelFlags = (permissions.channel_permissions || []).filter(channel => channel.allowed === true).length
    return governanceFlags + channelFlags
  }
  return countEnabledPermissions(role, moduleId)
}

function getVisibleRoles() {
  const items = draftRoleState?.isNew ? [draftRoleState, ...roles] : roles
  const search = roleSearch.trim().toLowerCase()
  if (!search) return items
  return items.filter(role => {
    const searchContent = [
      role.name,
      role.identifier,
      role.description || '',
      getRoleDisplayName(role),
      getRoleDisplayDescription(role)
    ].join(' ').toLowerCase()
    return searchContent.includes(search)
  })
}

function renderRoleList() {
  const visibleRoles = getVisibleRoles()
  if (!visibleRoles.length) {
    rolesListEl.innerHTML = `
      <div class="role-list-empty">
        <strong data-i18n="roles.noRolesTitle">No roles found</strong>
        <p data-i18n="roles.noRolesDescription">Try a different search term or create a new custom role.</p>
      </div>
    `
    return
  }

  rolesListEl.innerHTML = visibleRoles.map(role => {
    const isSelected = role.id === selectedRoleId || (role.isNew && selectedRoleId === 'new-role')
    const enabledCount = MODULES.reduce((sum, module) => sum + countEnabledPermissions(role, module.id), 0)
    const roleDisplayName = getRoleDisplayName(role)
    return `
      <button type="button" class="role-list-card ${isSelected ? 'selected' : ''}" data-role-select="${escapeHtml(role.id || 'new-role')}">
        <div class="role-list-card-top">
          <div>
            <strong>${escapeHtml(roleDisplayName || translateOrFallback('roles.newRole', 'New Role'))}</strong>
            <span>${escapeHtml(role.identifier || translateOrFallback('roles.unsavedIdentifier', 'unsaved-role'))}</span>
          </div>
          ${role.is_builtin ? '<span class="role-chip builtin" data-i18n="roles.builtinBadge">Built-in</span>' : ''}
          ${role.isNew ? '<span class="role-chip draft" data-i18n="roles.draftBadge">Draft</span>' : ''}
        </div>
        <div class="role-list-card-meta">
          <span>${enabledCount} ${escapeHtml(translateOrFallback('roles.enabledShort', 'enabled'))}</span>
          <span class="role-status ${role.is_active ? 'active' : 'inactive'}">${escapeHtml(role.is_active ? translateOrFallback('roles.statusActive', 'Active') : translateOrFallback('roles.statusInactive', 'Inactive'))}</span>
        </div>
      </button>
    `
  }).join('')
}

function renderModulePermissionTable(moduleId, permissionState, options = {}) {
  const definitions = [...(MODULE_PERMISSION_DEFINITIONS[moduleId] || [])].sort(([leftId], [rightId]) => {
    if (leftId === 'manage_permissions') return -1
    if (rightId === 'manage_permissions') return 1
    return 0
  })
  const canManageCurrentModule = options.canManage ?? (canManageModule(moduleId) && !isSystemManagedBuiltinRole(draftRoleState))
  const accessAllDefinition = ACCESS_ALL_PERMISSION_DEFINITIONS[moduleId]
  const accessAllState = draftRoleState?.permissions?.[moduleId]?.allow_all === true
  return `
    <div class="role-permission-table-wrap">
      <table class="role-permission-table">
        <thead>
          <tr>
            <th scope="col" data-i18n="roles.permissionTable.permission">Permission</th>
            <th scope="col" data-i18n="roles.permissionTable.description">Description</th>
            <th scope="col" data-i18n="roles.permissionTable.enabled">Enabled</th>
          </tr>
        </thead>
        <tbody>
      ${definitions.map(([id, titleKey, fallbackTitle, descriptionKey, fallbackDescription]) => `
          <tr class="${permissionState[id] ? 'enabled' : ''}">
            <th scope="row">
              <span class="role-permission-name">${escapeHtml(translateOrFallback(titleKey, fallbackTitle))}</span>
            </th>
            <td>
              <span class="role-permission-description">${escapeHtml(translateOrFallback(descriptionKey, fallbackDescription))}</span>
            </td>
            <td>
              <label class="role-permission-table-toggle">
                <span class="role-permission-toggle-label">${escapeHtml(translateOrFallback('roles.permissionTable.enabled', 'Enabled'))}</span>
                <span class="toggle-switch">
                  <input type="checkbox" data-module-toggle="${escapeHtml(moduleId)}" data-permission-toggle="${escapeHtml(id)}" ${permissionState[id] ? 'checked' : ''} ${canManageCurrentModule ? '' : 'disabled'}>
                  <span></span>
                </span>
              </label>
            </td>
          </tr>
      `).join('')}
      ${accessAllDefinition ? `
          <tr class="${accessAllState ? 'enabled' : ''}">
            <th scope="row">
              <span class="role-permission-name">${escapeHtml(translateOrFallback(accessAllDefinition[0], accessAllDefinition[1]))}</span>
            </th>
            <td>
              <span class="role-permission-description">${escapeHtml(translateOrFallback(accessAllDefinition[2], accessAllDefinition[3]))}</span>
            </td>
            <td>
              <label class="role-permission-table-toggle">
                <span class="role-permission-toggle-label">${escapeHtml(translateOrFallback('roles.permissionTable.enabled', 'Enabled'))}</span>
                <span class="toggle-switch">
                  <input type="checkbox" data-access-all-toggle="${escapeHtml(moduleId)}" ${accessAllState ? 'checked' : ''} ${canManageCurrentModule ? '' : 'disabled'}>
                  <span></span>
                </span>
              </label>
            </td>
          </tr>
      ` : ''}
        </tbody>
      </table>
    </div>
  `
}

function renderPermissionTable(moduleId) {
  return renderModulePermissionTable(moduleId, draftRoleState.permissions[moduleId] || {})
}

function getFilteredSkillRows(skillPermissions = []) {
  const search = skillSearch.trim().toLowerCase()
  return skillPermissions.filter(skill => {
    if (skill.is_provider_skill) return false
    if (!search) return true
    const searchContent = [
      skill.skill_name,
      skill.description || '',
      getSkillDisplayName(skill),
      getSkillDisplayDescription(skill)
    ].join(' ').toLowerCase()
    return searchContent.includes(search)
  })
}

function getFilteredProviderRows(providerPermissions = []) {
  const search = providerSearch.trim().toLowerCase()
  return providerPermissions.filter(provider => {
    if (!search) return true
    const searchContent = [
      provider.provider_type,
      provider.instance_name,
      provider.base_url || '',
      getProviderDisplayName(provider),
      getProviderDisplayDescription(provider)
    ].join(' ').toLowerCase()
    return searchContent.includes(search)
  })
}

function getFilteredChannelRows(channelPermissions = []) {
  const search = channelSearch.trim().toLowerCase()
  return channelPermissions.filter(channel => {
    if (!search) return true
    const searchContent = [
      channel.channel_type,
      channel.channel_name,
      channel.mode || '',
      getChannelDisplayName(channel),
      getChannelDisplayDescription(channel)
    ].join(' ').toLowerCase()
    return searchContent.includes(search)
  })
}

function syncSkillModulePermissions() {
  if (!draftRoleState) return
  const modulePermissions = draftRoleState.permissions.skills.module_permissions || {}
  const skillPermissions = draftRoleState.permissions.skills.skill_permissions || []
  const hasConfiguredSkills = skillPermissions.some(skill => skill.authorized || skill.enabled)
  const hasEnabledSkills = skillPermissions.some(skill => skill.enabled)

  modulePermissions.view = hasConfiguredSkills
  modulePermissions.enable_disable = hasEnabledSkills
}

function renderSkillsModule() {
  const permissions = draftRoleState.permissions.skills || { module_permissions: {}, skill_permissions: [] }
  const skillRows = getFilteredSkillRows(permissions.skill_permissions || [])
  const runtimeSkillRows = skillRows.filter(skill => skill.runtime_enabled !== false && !skill.is_provider_skill)
  const allVisibleSkillsEnabled = runtimeSkillRows.length > 0 && runtimeSkillRows.every(skill => skill.enabled)
  const allowAllSkills = permissions.allow_all === true
  // Allow admin to manage skill toggles (skills are now UI-controlled)
  const canManageSkills = canManageModule('skills')

  return `
    ${renderModulePermissionTable('skills', permissions.module_permissions || {}, { canManage: canManageSkills })}
    <section class="role-skill-section ${allowAllSkills ? 'role-readonly-preview' : ''}">
      <div class="role-skill-toolbar">
        <div class="role-skill-toolbar-title">
          <h3 data-i18n="roles.skillsListTitle">Skill Access</h3>
        </div>
        <div class="role-allowlist-toolbar-controls">
          <label class="role-skill-search" for="skillsSearchInput">
            ${ICONS.search}
            <input type="text" id="skillsSearchInput" value="${escapeHtml(skillSearch)}" data-i18n-placeholder="roles.skillSearchPlaceholder" placeholder="Search skills...">
          </label>
          <label class="role-skill-master-toggle">
            <span class="role-skill-master-label" data-i18n="roles.visibleRowsToggle">Visible rows</span>
            <span class="toggle-switch">
              <input type="checkbox" data-skill-master-toggle="enabled" ${allVisibleSkillsEnabled ? 'checked' : ''} ${canManageSkills && !allowAllSkills ? '' : 'disabled'}>
              <span></span>
            </span>
          </label>
        </div>
      </div>
      <div class="role-skill-list">
        ${skillRows.length ? skillRows.map(skill => {
          const skillDisplayName = getSkillDisplayName(skill)
          const skillDisplayDescription = getSkillDisplayDescription(skill)
          return `
          <div class="role-skill-card">
            <div class="role-skill-copy">
              <div class="role-skill-header">
                <strong>${escapeHtml(skillDisplayName)}</strong>
              </div>
              <p>${escapeHtml(skillDisplayDescription)}</p>
            </div>
            <div class="role-skill-controls">
              <label class="role-inline-toggle">
                <span data-i18n="roles.enableToggle">Enable</span>
                <span class="toggle-switch compact">
                  <input type="checkbox" data-skill-id="${escapeHtml(skill.skill_id)}" data-skill-toggle="enabled" ${skill.enabled ? 'checked' : ''} ${canManageSkills && !allowAllSkills && skill.runtime_enabled !== false ? '' : 'disabled'}>
                  <span></span>
                </span>
              </label>
            </div>
          </div>
        `}).join('') : `
          <div class="role-list-empty role-list-empty-compact">
            <strong data-i18n="roles.noSkillsTitle">No skills match</strong>
            <p data-i18n="roles.noSkillsDescription">Adjust the search term to review more skills.</p>
          </div>
        `}
      </div>
    </section>
  `
}

function renderProvidersModule() {
  const permissions = draftRoleState.permissions.providers || { module_permissions: {}, provider_permissions: [] }
  const providerRows = getFilteredProviderRows(permissions.provider_permissions || [])
  const allVisibleProvidersAllowed = providerRows.length > 0 && providerRows.every(provider => provider.allowed === true)
  const allowAllProviders = permissions.allow_all === true
  const canManageProviders = canManageModule('providers')

  return `
    ${renderModulePermissionTable('providers', permissions.module_permissions || {}, { canManage: canManageProviders })}
    <section class="role-provider-section ${allowAllProviders ? 'role-readonly-preview' : ''}">
      <div class="role-skill-toolbar">
        <div class="role-skill-toolbar-title">
          <h3 data-i18n="roles.providersListTitle">Provider Access</h3>
        </div>
        <div class="role-allowlist-toolbar-controls">
          <label class="role-skill-search" for="providersSearchInput">
            ${ICONS.search}
            <input type="text" id="providersSearchInput" value="${escapeHtml(providerSearch)}" data-i18n-placeholder="roles.providerSearchPlaceholder" placeholder="Search providers...">
          </label>
          <label class="role-skill-master-toggle">
            <span class="role-skill-master-label" data-i18n="roles.visibleRowsToggle">Visible rows</span>
            <span class="toggle-switch">
              <input type="checkbox" data-provider-master-toggle="allowed" ${allVisibleProvidersAllowed ? 'checked' : ''} ${canManageProviders && !allowAllProviders ? '' : 'disabled'}>
              <span></span>
            </span>
          </label>
        </div>
      </div>
      <div class="role-provider-list">
        ${providerRows.length ? providerRows.map(provider => {
          const providerKey = providerPermissionKey(provider.provider_type, provider.instance_name)
          return `
          <div class="role-provider-card">
            <div class="role-skill-copy">
              <div class="role-skill-header">
                <strong>${escapeHtml(getProviderDisplayName(provider))} / ${escapeHtml(provider.instance_name)}</strong>
              </div>
              <p>${escapeHtml(getProviderDisplayDescription(provider))}</p>
            </div>
            <div class="role-skill-controls">
              <label class="role-inline-toggle">
                <span data-i18n="roles.allowToggle">Allow</span>
                <span class="toggle-switch compact">
                  <input type="checkbox" data-provider-key="${escapeHtml(providerKey)}" data-provider-toggle="allowed" ${provider.allowed === true ? 'checked' : ''} ${canManageProviders && !allowAllProviders ? '' : 'disabled'}>
                  <span></span>
                </span>
              </label>
            </div>
          </div>
        `}).join('') : `
          <div class="role-list-empty role-list-empty-compact">
            <strong data-i18n="roles.noProvidersTitle">No providers match</strong>
            <p data-i18n="roles.noProvidersDescription">Adjust the search term or register provider instances first.</p>
          </div>
        `}
      </div>
    </section>
  `
}

function renderChannelsModule() {
  const permissions = draftRoleState.permissions.channels || { module_permissions: {}, channel_permissions: [] }
  const channelRows = getFilteredChannelRows(permissions.channel_permissions || [])
  const allVisibleChannelsAllowed = channelRows.length > 0 && channelRows.every(channel => channel.allowed === true)
  const allowAllChannels = permissions.allow_all === true
  const canManageChannels = canManageModule('channels')

  return `
    ${renderModulePermissionTable('channels', permissions.module_permissions || {}, { canManage: canManageChannels })}
    <section class="role-provider-section ${allowAllChannels ? 'role-readonly-preview' : ''}">
      <div class="role-skill-toolbar">
        <div class="role-skill-toolbar-title">
          <h3 data-i18n="roles.channelsListTitle">Channel Access</h3>
        </div>
        <div class="role-allowlist-toolbar-controls">
          <label class="role-skill-search" for="channelsSearchInput">
            ${ICONS.search}
            <input type="text" id="channelsSearchInput" value="${escapeHtml(channelSearch)}" data-i18n-placeholder="roles.channelSearchPlaceholder" placeholder="Search channels...">
          </label>
          <label class="role-skill-master-toggle">
            <span class="role-skill-master-label" data-i18n="roles.visibleRowsToggle">Visible rows</span>
            <span class="toggle-switch">
              <input type="checkbox" data-channel-master-toggle="allowed" ${allVisibleChannelsAllowed ? 'checked' : ''} ${canManageChannels && !allowAllChannels ? '' : 'disabled'}>
              <span></span>
            </span>
          </label>
        </div>
      </div>
      <div class="role-provider-list">
        ${channelRows.length ? channelRows.map(channel => {
          const channelKey = channelPermissionKey(channel.channel_type)
          return `
          <div class="role-provider-card">
            <div class="role-skill-copy">
              <div class="role-skill-header">
                <strong>${escapeHtml(getChannelDisplayName(channel))}</strong>
              </div>
              <p>${escapeHtml(getChannelDisplayDescription(channel))}</p>
            </div>
            <div class="role-skill-controls">
              <label class="role-inline-toggle">
                <span data-i18n="roles.allowToggle">Allow</span>
                <span class="toggle-switch compact">
                  <input type="checkbox" data-channel-key="${escapeHtml(channelKey)}" data-channel-toggle="allowed" ${channel.allowed === true ? 'checked' : ''} ${canManageChannels && !allowAllChannels ? '' : 'disabled'}>
                  <span></span>
                </span>
              </label>
            </div>
          </div>
        `}).join('') : `
          <div class="role-list-empty role-list-empty-compact">
            <strong data-i18n="roles.noChannelsTitle">No channels match</strong>
            <p data-i18n="roles.noChannelsDescription">Adjust the search term or register channel handlers first.</p>
          </div>
        `}
      </div>
    </section>
  `
}

function renderEditor() {
  if (!draftRoleState) {
    editorEl.innerHTML = `
      <div class="role-editor-empty">
        <strong data-i18n="roles.emptyTitle">Select a role to begin</strong>
        <p data-i18n="roles.emptyDescription">Choose a built-in role or create a new one to edit the right-side permission designer.</p>
      </div>
    `
    return
  }

  const module = MODULES.find(item => item.id === activeModuleId) || MODULES[0]
  const moduleMarkup = activeModuleId === 'skills'
    ? renderSkillsModule()
    : (activeModuleId === 'providers'
        ? renderProvidersModule()
        : (activeModuleId === 'channels' ? renderChannelsModule() : renderPermissionTable(activeModuleId)))
  const roleDisplayName = getRoleDisplayName(draftRoleState) || translateOrFallback('roles.untitledRole', 'Untitled Role')
  const roleDisplayDescription = getRoleDisplayDescription(draftRoleState)
  const roleNameValue = draftRoleState.is_builtin ? roleDisplayName : draftRoleState.name
  const roleDescriptionValue = draftRoleState.is_builtin ? roleDisplayDescription : draftRoleState.description
  const metadataReadonly = draftRoleState.is_builtin || !canEditRoleMetadata()
  const identifierReadonly = draftRoleState.is_builtin || !draftRoleState.isNew || !canEditRoleMetadata()
  const builtinReadonlyAttr = metadataReadonly ? 'readonly' : ''
  const identifierReadonlyAttr = identifierReadonly ? 'readonly' : ''
  const statusDisabledAttr = draftRoleState.is_builtin || !canEditRoleMetadata() ? 'disabled' : ''
  const canDeleteCurrentRole = canDeleteRoles() && !draftRoleState.is_builtin && !draftRoleState.isNew
  const canSaveCurrentRole = canSaveRole()
  const canManageActiveModule = canManageModule(activeModuleId) && (
    !isSystemManagedBuiltinRole(draftRoleState) || ['skills', 'providers', 'channels'].includes(activeModuleId)
  )

  editorEl.innerHTML = `
    <div class="role-editor-shell">
      <div class="role-editor-topbar">
        <div>
          <span class="role-editor-breadcrumb">${escapeHtml(translateOrFallback('roles.title', 'Role Management'))} / ${escapeHtml(draftRoleState.isNew ? translateOrFallback('roles.createTitle', 'Create Role') : translateOrFallback('roles.editTitle', 'Edit Role'))}</span>
          <h2>${escapeHtml(roleDisplayName)}</h2>
        </div>
      </div>
      <section class="role-summary-card">
        <div class="role-summary-grid">
          <label class="form-field">
            <span data-i18n="roles.roleName">Role Name</span>
            <input type="text" data-role-field="name" value="${escapeHtml(roleNameValue)}" placeholder="${escapeHtml(translateOrFallback('roles.roleNamePlaceholder', 'Role name'))}" ${builtinReadonlyAttr}>
          </label>
          <div class="role-summary-meta form-field">
            <span data-i18n="roles.roleIdentifier">Role Identifier</span>
            ${draftRoleState.is_builtin
              ? `<div class="role-summary-meta-row"><span class="role-chip builtin" data-i18n="roles.builtinBadge">Built-in</span><span>${escapeHtml(translateOrFallback('roles.identifierPrefix', 'Identifier:'))} ${escapeHtml(draftRoleState.identifier)}</span></div>`
              : `<input type="text" data-role-field="identifier" value="${escapeHtml(draftRoleState.identifier)}" placeholder="${escapeHtml(translateOrFallback('roles.identifierPlaceholder', 'role-identifier'))}" ${identifierReadonlyAttr}>`
            }
          </div>
          <div class="form-field">
            <span data-i18n="roles.roleStatus">Status</span>
            <div class="role-summary-meta-row role-summary-status-row">
              <span class="role-chip ${draftRoleState.is_active ? 'success' : 'muted'}">${escapeHtml(draftRoleState.is_active ? translateOrFallback('roles.statusActive', 'Active') : translateOrFallback('roles.statusInactive', 'Inactive'))}</span>
              <span class="toggle-switch">
                <input type="checkbox" data-role-field="is_active" ${draftRoleState.is_active ? 'checked' : ''} ${statusDisabledAttr}>
                <span></span>
              </span>
            </div>
          </div>
          <label class="form-field form-field-full">
            <span data-i18n="roles.roleDescription">Description</span>
            <textarea data-role-field="description" rows="2" placeholder="${escapeHtml(translateOrFallback('roles.roleDescriptionPlaceholder', 'Describe when this role should be assigned.'))}" ${builtinReadonlyAttr}>${escapeHtml(roleDescriptionValue)}</textarea>
          </label>
        </div>
      </section>
      <div class="role-designer-layout">
        <aside class="role-module-nav">
          ${MODULES.map(item => `
            <button type="button" class="role-module-nav-item ${item.id === activeModuleId ? 'active' : ''}" data-module-id="${escapeHtml(item.id)}">
              <span class="role-module-icon">${ICONS[item.icon] || ICONS.shield}</span>
              <span class="role-module-copy">
                <strong>${escapeHtml(translateOrFallback(item.titleKey, item.fallbackTitle))}</strong>
                <span>${countModuleSummaryEnabledPermissions(draftRoleState, item.id)} ${escapeHtml(translateOrFallback('roles.enabledShort', 'enabled'))}</span>
              </span>
            </button>
          `).join('')}
        </aside>
        <section class="role-module-panel">
          <div class="role-module-panel-header">
            <div>
              <h3>${escapeHtml(translateOrFallback(module.titleKey, module.fallbackTitle))}</h3>
              <p>${escapeHtml(translateOrFallback(module.descriptionKey, module.fallbackDescription))}</p>
            </div>
            <div class="role-module-panel-actions">
              <button type="button" class="btn-secondary" data-module-action="restore-defaults" data-i18n="roles.restoreDefaults" ${canManageActiveModule ? '' : 'disabled'}>Restore Defaults</button>
            </div>
          </div>
          ${moduleMarkup}
        </section>
      </div>
      <div class="role-editor-footer">
        <div class="role-editor-footer-meta">
          <span class="role-chip ${draftRoleState.is_active ? 'success' : 'muted'}">${escapeHtml(draftRoleState.is_active ? translateOrFallback('roles.statusActive', 'Active') : translateOrFallback('roles.statusInactive', 'Inactive'))}</span>
          <span class="role-editor-footer-note">${escapeHtml(draftRoleState.isNew ? translateOrFallback('roles.createTitle', 'Create Role') : translateOrFallback('roles.editTitle', 'Edit Role'))}</span>
        </div>
        <div class="role-editor-actions role-editor-actions-footer">
          ${canDeleteCurrentRole ? `<button type="button" class="btn-secondary role-delete-trigger" id="deleteRoleTrigger">${ICONS.trash}<span data-i18n="roles.deleteButton">Delete Role</span></button>` : ''}
          <button type="button" class="btn-secondary" id="cancelRoleChanges" data-i18n="roles.cancel">Cancel</button>
          <button type="button" class="btn-primary" id="saveRoleChanges" data-i18n="roles.saveButton" ${canSaveCurrentRole ? '' : 'disabled'}>Save Role</button>
        </div>
      </div>
    </div>
  `
}

function renderPage() {
  renderRoleList()
  renderEditor()
  updateContainerTranslations(container)
  const createButton = container?.querySelector('#createRoleBtn')
  if (createButton) {
    createButton.disabled = !canCreateRoles()
  }
}

async function loadSkills() {
  try {
    const data = await fetchJson('/api/skills')
    const allSkills = Array.isArray(data?.skills) ? data.skills : []
    skills = allSkills.filter(s => !s.provider_type)
    providerSkills = allSkills.filter(s => !!s.provider_type)
  } catch (error) {
    skills = []
    providerSkills = []
    console.warn('[RoleManagement] Failed to load skills:', error)
  }
}

async function loadProviders() {
  try {
    const data = await fetchJson('/api/service-providers/available-instances?include_all=true')
    providerInstances = Array.isArray(data?.providers) ? data.providers : []
  } catch (error) {
    providerInstances = []
    console.warn('[RoleManagement] Failed to load provider instances:', error)
  }
}

async function loadChannels() {
  try {
    const data = await fetchJson('/api/channels?include_all=true')
    channelTypes = Array.isArray(data) ? data : []
  } catch (error) {
    channelTypes = []
    console.warn('[RoleManagement] Failed to load channel types:', error)
  }
}

async function loadRoles(preserveRoleId = selectedRoleId) {
  const data = await fetchJson('/api/roles?page=1&page_size=100')
  const rawRoles = Array.isArray(data?.roles) ? data.roles : []
  roles = rawRoles.map(role => normalizeRole(role, skills))

  if (preserveRoleId === 'new-role' && draftRoleState?.isNew) {
    selectedRoleId = 'new-role'
    return
  }

  const currentRole = roles.find(role => role.id === preserveRoleId)
  if (currentRole) {
    selectedRoleId = currentRole.id
    draftRoleState = normalizeRole(currentRole, skills)
    identifierTouched = true
    return
  }

  if (roles.length) {
    selectedRoleId = roles[0].id
    draftRoleState = normalizeRole(roles[0], skills)
    identifierTouched = true
    return
  }

  selectedRoleId = null
  draftRoleState = null
  identifierTouched = false
}

function selectRole(roleId) {
  if (roleId === 'new-role' && draftRoleState?.isNew) {
    selectedRoleId = 'new-role'
    renderPage()
    return
  }

  const role = roles.find(item => item.id === roleId)
  if (!role) return
  selectedRoleId = role.id
  activeModuleId = 'skills'
  skillSearch = ''
  providerSearch = ''
  channelSearch = ''
  draftRoleState = normalizeRole(role, skills)
  identifierTouched = true
  renderPage()
}

function updateDraftField(field, value, shouldRender = true) {
  if (!draftRoleState) return
  if ((field === 'name' || field === 'identifier' || field === 'description' || field === 'is_active') && !canEditRoleMetadata()) {
    return
  }
  if (field === 'identifier' && !draftRoleState.isNew) {
    return
  }

  if (field === 'is_active') {
    draftRoleState.is_active = value === true
  } else if (field === 'name') {
    draftRoleState.name = value
    if (!identifierTouched && !draftRoleState.is_builtin) {
      draftRoleState.identifier = slugifyIdentifier(value)
    }
  } else if (field === 'identifier') {
    identifierTouched = true
    draftRoleState.identifier = slugifyIdentifier(value)
  } else {
    draftRoleState[field] = value
  }

  if (shouldRender) renderPage()
}

function toggleModulePermission(moduleId, permissionId, checked) {
  if (!draftRoleState) return
  if (isSystemManagedBuiltinRole(draftRoleState) && !['skills', 'providers', 'channels'].includes(moduleId)) return
  if (!canManageModule(moduleId)) return
  if (moduleId === 'skills') {
    draftRoleState.permissions.skills.module_permissions[permissionId] = checked
  } else if (moduleId === 'providers') {
    draftRoleState.permissions.providers.module_permissions[permissionId] = checked
  } else if (moduleId === 'channels') {
    draftRoleState.permissions.channels.module_permissions[permissionId] = checked
  } else {
    draftRoleState.permissions[moduleId][permissionId] = checked
  }
  renderPage()
}

function toggleAccessAllPermission(moduleId, checked) {
  if (!draftRoleState) return
  if (!canManageModule(moduleId)) return
  const permissions = draftRoleState.permissions[moduleId]
  if (!permissions || typeof permissions !== 'object') return
  permissions.allow_all = checked === true
  renderPage()
}

function toggleSkillPermission(skillId, property, checked) {
  if (!draftRoleState) return
  if (!canManageModule('skills')) return
  if (draftRoleState.permissions.skills.allow_all === true) return
  const skill = draftRoleState.permissions.skills.skill_permissions.find(item => item.skill_id === skillId)
  if (!skill) return
  if (skill.runtime_enabled === false) {
    skill.enabled = false
    skill.authorized = false
    renderPage()
    return
  }
  if (property === 'enabled') {
    skill.enabled = checked
    skill.authorized = checked
  } else {
    if (property === 'authorized' && !checked) skill.enabled = false
    skill[property] = checked
  }
  syncSkillModulePermissions()
  renderPage()
}

function toggleAllVisibleSkills(checked) {
  if (!draftRoleState) return
  if (!canManageModule('skills')) return
  if (draftRoleState.permissions.skills.allow_all === true) return
  const visibleSkillIds = new Set(getFilteredSkillRows(draftRoleState.permissions.skills.skill_permissions).map(skill => skill.skill_id))
  draftRoleState.permissions.skills.skill_permissions = draftRoleState.permissions.skills.skill_permissions.map(skill => {
    if (!visibleSkillIds.has(skill.skill_id)) {
      return skill
    }
    if (skill.runtime_enabled === false) {
      return {
        ...skill,
        authorized: false,
        enabled: false
      }
    }
    return {
      ...skill,
      authorized: checked,
      enabled: checked
    }
  })
  syncSkillModulePermissions()
  renderPage()
}

function toggleProviderPermission(providerKey, checked) {
  if (!draftRoleState) return
  if (!canManageModule('providers')) return
  if (draftRoleState.permissions.providers.allow_all === true) return
  const provider = draftRoleState.permissions.providers.provider_permissions.find(item => (
    providerPermissionKey(item.provider_type, item.instance_name) === providerKey
  ))
  if (!provider) return
  provider.allowed = checked
  renderPage()
}

function toggleAllVisibleProviders(checked) {
  if (!draftRoleState) return
  if (!canManageModule('providers')) return
  if (draftRoleState.permissions.providers.allow_all === true) return
  const visibleProviderKeys = new Set(
    getFilteredProviderRows(draftRoleState.permissions.providers.provider_permissions)
      .map(provider => providerPermissionKey(provider.provider_type, provider.instance_name))
  )
  draftRoleState.permissions.providers.provider_permissions = draftRoleState.permissions.providers.provider_permissions.map(provider => {
    if (!visibleProviderKeys.has(providerPermissionKey(provider.provider_type, provider.instance_name))) {
      return provider
    }
    return {
      ...provider,
      allowed: checked
    }
  })
  renderPage()
}

function toggleChannelPermission(channelKey, checked) {
  if (!draftRoleState) return
  if (!canManageModule('channels')) return
  if (draftRoleState.permissions.channels.allow_all === true) return
  const channel = draftRoleState.permissions.channels.channel_permissions.find(item => (
    channelPermissionKey(item.channel_type) === channelKey
  ))
  if (!channel) return
  channel.allowed = checked
  renderPage()
}

function toggleAllVisibleChannels(checked) {
  if (!draftRoleState) return
  if (!canManageModule('channels')) return
  if (draftRoleState.permissions.channels.allow_all === true) return
  const visibleChannelKeys = new Set(
    getFilteredChannelRows(draftRoleState.permissions.channels.channel_permissions)
      .map(channel => channelPermissionKey(channel.channel_type))
  )
  draftRoleState.permissions.channels.channel_permissions = draftRoleState.permissions.channels.channel_permissions.map(channel => {
    if (!visibleChannelKeys.has(channelPermissionKey(channel.channel_type))) {
      return channel
    }
    return {
      ...channel,
      allowed: checked
    }
  })
  renderPage()
}

function applyModuleAction(action) {
  if (!draftRoleState) return
  if (isSystemManagedBuiltinRole(draftRoleState) && !['skills', 'providers', 'channels'].includes(activeModuleId)) return
  if (!canManageModule(activeModuleId)) return
  if (action === 'restore-defaults') {
    const template = getRoleTemplate(draftRoleState.identifier)
    if (activeModuleId === 'skills') {
      draftRoleState.permissions.skills = normalizeRole({ permissions: { skills: template.skills } }, skills).permissions.skills
    } else if (activeModuleId === 'providers') {
      draftRoleState.permissions.providers = normalizeRole({
        permissions: { providers: template.providers }
      }, skills).permissions.providers
    } else if (activeModuleId === 'channels') {
      draftRoleState.permissions.channels = normalizeRole({
        permissions: { channels: template.channels }
      }, skills).permissions.channels
    } else {
      draftRoleState.permissions[activeModuleId] = cloneData(template[activeModuleId])
    }
  }
  renderPage()
}

function createNewRole() {
  if (!canCreateRoles()) return
  selectedRoleId = 'new-role'
  activeModuleId = 'skills'
  skillSearch = ''
  providerSearch = ''
  channelSearch = ''
  identifierTouched = false
  draftRoleState = createEmptyRole()
  renderPage()
}

function cancelDraftChanges() {
  if (!draftRoleState) return
  if (draftRoleState.isNew) {
    const firstRole = roles[0] || null
    if (!firstRole) {
      selectedRoleId = null
      draftRoleState = null
      renderPage()
      return
    }
    selectedRoleId = firstRole.id
    draftRoleState = normalizeRole(firstRole, skills)
  } else {
    const original = roles.find(item => item.id === draftRoleState.id)
    if (original) draftRoleState = normalizeRole(original, skills)
  }
  skillSearch = ''
  providerSearch = ''
  channelSearch = ''
  identifierTouched = true
  renderPage()
}

function openDeleteModal() {
  if (!canDeleteRoles()) return
  if (!draftRoleState || draftRoleState.is_builtin || draftRoleState.isNew) return
  container.querySelector('#deleteRoleId').value = draftRoleState.id
  container.querySelector('#deleteRoleName').textContent = draftRoleState.name
  deleteModal.classList.remove('hidden')
}

function closeDeleteModal() {
  deleteModal?.classList.add('hidden')
}

async function saveRole() {
  if (!draftRoleState) return
  if (!canSaveRole()) {
    showToast(translateOrFallback('roles.accessDenied', 'Access denied. You do not have permission to manage roles.'), 'error')
    return
  }
  const name = draftRoleState.name.trim()
  const identifier = draftRoleState.identifier.trim()
  if (!name || !identifier) {
    showToast(translateOrFallback('roles.validationRequired', 'Role name and identifier are required.'), 'error')
    return
  }

  const payload = draftRoleState.is_builtin
    ? {
      permissions: buildPermissionsPayload(draftRoleState)
    }
    : {
      name,
      identifier,
      description: draftRoleState.description.trim(),
      is_active: draftRoleState.is_active,
      permissions: buildPermissionsPayload(draftRoleState)
    }
  const requestOptions = {
    method: draftRoleState.isNew ? 'POST' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }
  const endpoint = draftRoleState.isNew ? '/api/roles' : `/api/roles/${draftRoleState.id}`

  try {
    const savedRole = await fetchJson(endpoint, requestOptions)
    showToast(translateOrFallback(draftRoleState.isNew ? 'roles.createSuccess' : 'roles.saveSuccess', draftRoleState.isNew ? 'Role created successfully' : 'Role saved successfully'), 'success')
    selectedRoleId = savedRole.id
    await loadRoles(savedRole.id)
    draftRoleState = normalizeRole(savedRole, skills)
    identifierTouched = true
    renderPage()
  } catch (error) {
    showToast(error.message || translateOrFallback('roles.saveFailed', 'Failed to save role'), 'error')
  }
}

async function confirmDeleteRole() {
  const roleId = container.querySelector('#deleteRoleId').value
  if (!roleId) return
  try {
    await fetchJson(`/api/roles/${roleId}`, { method: 'DELETE' })
    showToast(translateOrFallback('roles.deleteSuccess', 'Role deleted successfully'), 'success')
    closeDeleteModal()
    selectedRoleId = null
    draftRoleState = null
    identifierTouched = false
    await loadRoles()
    renderPage()
  } catch (error) {
    showToast(error.message || translateOrFallback('roles.deleteFailed', 'Failed to delete role'), 'error')
  }
}

function handleRoleListClick(event) {
  const target = event.target.closest('[data-role-select]')
  if (!target) return
  selectRole(target.getAttribute('data-role-select'))
}

function handleEditorClick(event) {
  if (event.target.closest('#deleteRoleTrigger')) {
    openDeleteModal()
    return
  }
  if (event.target.closest('#saveRoleChanges')) {
    saveRole()
    return
  }
  if (event.target.closest('#cancelRoleChanges')) {
    cancelDraftChanges()
    return
  }

  const moduleButton = event.target.closest('[data-module-id]')
  if (moduleButton) {
    activeModuleId = moduleButton.getAttribute('data-module-id')
    skillSearch = ''
    providerSearch = ''
    channelSearch = ''
    renderPage()
    return
  }

  const actionButton = event.target.closest('[data-module-action]')
  if (actionButton) {
    applyModuleAction(actionButton.getAttribute('data-module-action'))
  }
}

function handleEditorInput(event) {
  const field = event.target.getAttribute('data-role-field')
  if (field) {
    updateDraftField(field, field === 'is_active' ? event.target.checked : event.target.value, field === 'is_active')
    return
  }
  if (event.target.id === 'skillsSearchInput') {
    const selectionStart = event.target.selectionStart
    const selectionEnd = event.target.selectionEnd
    skillSearch = event.target.value
    renderPage()
    restoreInputFocus(editorEl, '#skillsSearchInput', selectionStart, selectionEnd)
    return
  }
  if (event.target.id === 'providersSearchInput') {
    const selectionStart = event.target.selectionStart
    const selectionEnd = event.target.selectionEnd
    providerSearch = event.target.value
    renderPage()
    restoreInputFocus(editorEl, '#providersSearchInput', selectionStart, selectionEnd)
    return
  }
  if (event.target.id === 'channelsSearchInput') {
    const selectionStart = event.target.selectionStart
    const selectionEnd = event.target.selectionEnd
    channelSearch = event.target.value
    renderPage()
    restoreInputFocus(editorEl, '#channelsSearchInput', selectionStart, selectionEnd)
  }
}

function handleEditorChange(event) {
  const field = event.target.getAttribute('data-role-field')
  if (field) {
    updateDraftField(field, field === 'is_active' ? event.target.checked : event.target.value)
    return
  }

  const moduleId = event.target.getAttribute('data-module-toggle')
  const permissionId = event.target.getAttribute('data-permission-toggle')
  if (moduleId && permissionId) {
    toggleModulePermission(moduleId, permissionId, event.target.checked)
    return
  }

  const accessAllModule = event.target.getAttribute('data-access-all-toggle')
  if (accessAllModule) {
    toggleAccessAllPermission(accessAllModule, event.target.checked)
    return
  }

  const skillId = event.target.getAttribute('data-skill-id')
  const skillToggle = event.target.getAttribute('data-skill-toggle')
  if (skillId && skillToggle) {
    toggleSkillPermission(skillId, skillToggle, event.target.checked)
    return
  }

  const masterSkillToggle = event.target.getAttribute('data-skill-master-toggle')
  if (masterSkillToggle === 'enabled') {
    toggleAllVisibleSkills(event.target.checked)
    return
  }

  const providerKey = event.target.getAttribute('data-provider-key')
  const providerToggle = event.target.getAttribute('data-provider-toggle')
  if (providerKey && providerToggle === 'allowed') {
    toggleProviderPermission(providerKey, event.target.checked)
    return
  }

  const masterProviderToggle = event.target.getAttribute('data-provider-master-toggle')
  if (masterProviderToggle === 'allowed') {
    toggleAllVisibleProviders(event.target.checked)
    return
  }

  const channelKey = event.target.getAttribute('data-channel-key')
  const channelToggle = event.target.getAttribute('data-channel-toggle')
  if (channelKey && channelToggle === 'allowed') {
    toggleChannelPermission(channelKey, event.target.checked)
    return
  }

  const masterChannelToggle = event.target.getAttribute('data-channel-master-toggle')
  if (masterChannelToggle === 'allowed') {
    toggleAllVisibleChannels(event.target.checked)
  }
}

function setupEventListeners() {
  addTrackedListener(container.querySelector('#createRoleBtn'), 'click', createNewRole)
  addTrackedListener(container.querySelector('#roleSearchInput'), 'input', event => {
    roleSearch = event.target.value
    renderRoleList()
    updateContainerTranslations(container)
  })
  addTrackedListener(rolesListEl, 'click', handleRoleListClick)
  addTrackedListener(editorEl, 'click', handleEditorClick)
  addTrackedListener(editorEl, 'input', handleEditorInput)
  addTrackedListener(editorEl, 'change', handleEditorChange)
  addTrackedListener(container.querySelector('#deleteRoleClose'), 'click', closeDeleteModal)
  addTrackedListener(container.querySelector('#deleteRoleCancel'), 'click', closeDeleteModal)
  addTrackedListener(container.querySelector('#deleteRoleConfirm'), 'click', confirmDeleteRole)
  addTrackedListener(deleteModal, 'click', event => {
    if (event.target === deleteModal) closeDeleteModal()
  })
  addTrackedListener(document, 'keydown', event => {
    if (event.key === 'Escape') closeDeleteModal()
  })
}

export async function mount(containerEl) {
  container = containerEl
  document.body.classList.add('role-management-active')
  const user = getAuthInfo() || await checkAuth({ redirect: true })
  if (!user) {
    document.body.classList.remove('role-management-active')
    return
  }
  currentUserAuthInfo = user
  if (!canAccessRoleManagement(user)) {
    document.body.classList.remove('role-management-active')
    showToast(translateOrFallback('roles.accessDenied', 'Access denied. You do not have permission to manage roles.'), 'error')
    window.location.href = buildAppUrl('/')
    return
  }

  if (!document.getElementById('role-management-page-css')) {
    const cssLink = document.createElement('link')
    cssLink.rel = 'stylesheet'
    cssLink.href = buildAssetUrl('/styles/role-management.css')
    cssLink.id = 'role-management-page-css'
    document.head.appendChild(cssLink)
  }

  container.innerHTML = PAGE_HTML
  rolesListEl = container.querySelector('#roleList')
  editorEl = container.querySelector('#roleEditor')
  deleteModal = container.querySelector('#deleteRoleModal')
  setupEventListeners()
  await loadSkills()
  await loadProviders()
  await loadChannels()
  await loadRoles()
  renderPage()
}

export async function unmount() {
  eventCleanupFns.forEach(fn => fn())
  eventCleanupFns = []
  document.body.classList.remove('role-management-active')
  container = null
  rolesListEl = null
  editorEl = null
  deleteModal = null
  roles = []
  skills = []
  providerSkills = []
  providerInstances = []
  channelTypes = []
  selectedRoleId = null
  roleSearch = ''
  skillSearch = ''
  providerSearch = ''
  channelSearch = ''
  activeModuleId = 'skills'
  draftRoleState = null
  identifierTouched = false
  currentUserAuthInfo = null
}

export default { mount, unmount }
