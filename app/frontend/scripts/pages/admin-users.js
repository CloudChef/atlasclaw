/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

import { t, translateIfExists, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { checkAuth, redirectToLogin } from '../auth.js'
import { buildAssetUrl, buildAppUrl } from '../config.js'
import { getAuthInfo } from '../app.js'
import { canAccessUserManagement, hasPermission } from '../permissions.js'

let container = null
let currentPage = 1
let currentSearch = ''
let currentPageSize = 20
let totalUsers = 0
let currentRoleFilter = 'all'
let currentStatusFilter = 'all'
let currentFetchedUsers = []
let availableRoles = []
let searchDebounceTimer = null
let currentViewerAuthInfo = null

let usersTableBody = null
let paginationInfo = null
let paginationBtns = null
let searchInput = null
let roleFilterSelect = null
let statusFilterSelect = null
let userModal = null
let deleteModal = null

let eventCleanupFns = []
let documentClickHandler = null
let documentKeydownHandler = null
const ROLE_FILTER_FETCH_PAGE_SIZE = 100
const NON_ADMIN_ASSIGNABLE_PERMISSION_PATHS = new Set([
  'skills.module_permissions.view',
  'skills.allow_all',
  'skills.skill_permissions',
  'providers.allow_all',
  'providers.provider_permissions',
  'channels.allow_all',
  'channels.channel_permissions',
  'tokens.view',
  'agent_configs.view',
  'provider_configs.view',
  'model_configs.view',
  'users.view',
  'roles.view'
])

const PAGE_HTML = `
<div class="user-management-page">
  <div class="user-management-shell">
    <header class="user-management-header">
      <div>
        <h1 data-i18n="admin.title">User Management</h1>
        <p data-i18n="admin.description">Administer system access, modify roles, and monitor user status across the workspace.</p>
      </div>
      <button class="btn-primary user-management-create-btn" id="createUserBtn">
        <span data-i18n="admin.createButton">+ Create User</span>
      </button>
    </header>

    <section class="user-management-toolbar">
      <label class="user-toolbar-search" for="searchInput">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="7"></circle>
          <path d="m20 20-3.5-3.5"></path>
        </svg>
        <input type="text" id="searchInput" class="search-input" data-i18n-placeholder="admin.searchPlaceholder" placeholder="Search users...">
      </label>

      <div class="user-toolbar-filters">
        <label class="user-filter-pill">
          <span data-i18n="admin.roleFilter">Role</span>
          <select id="roleFilterSelect"></select>
        </label>

        <label class="user-filter-pill">
          <span data-i18n="admin.statusFilter">Status</span>
          <select id="statusFilterSelect">
            <option value="all" data-i18n="admin.statusAll">All</option>
            <option value="enabled" data-i18n="admin.statusEnabledLabel">Enabled</option>
            <option value="disabled" data-i18n="admin.statusDisabledLabel">Disabled</option>
          </select>
        </label>

        <button type="button" class="btn-secondary user-filter-reset" id="resetFiltersBtn" data-i18n="admin.resetFilters">Reset</button>
      </div>
    </section>

    <section class="user-management-list-shell">
      <div class="user-management-table-wrap">
        <table class="user-management-table" aria-label="Users">
          <thead>
            <tr>
              <th scope="col" data-i18n="admin.user">User</th>
              <th scope="col" data-i18n="admin.userIdLabel">User ID</th>
              <th scope="col" data-i18n="admin.email">Email</th>
              <th scope="col" data-i18n="admin.loginType">Login Type</th>
              <th scope="col" data-i18n="admin.roles">Roles</th>
              <th scope="col" data-i18n="admin.status">Status</th>
              <th scope="col" data-i18n="admin.actions">Actions</th>
            </tr>
          </thead>
          <tbody id="usersTableBody">
            <tr>
              <td colspan="7">
                <div class="user-list-loading" data-i18n="admin.loading">Loading...</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pagination">
        <div class="pagination-info" id="paginationInfo">
          <span data-i18n="admin.loading">Loading...</span>
        </div>
        <div class="pagination-btns" id="paginationBtns"></div>
      </div>
    </section>
  </div>
</div>

<div id="userModal" class="modal-overlay hidden">
  <div class="modal user-management-modal">
    <div class="modal-header">
      <div>
        <h2 id="modalTitle" data-i18n="admin.createTitle">Create User</h2>
        <p id="modalDescription" class="modal-description" data-i18n="admin.modalCreateDescription">Create a workspace user and assign their access scope.</p>
      </div>
      <button class="modal-close" id="modalClose">&times;</button>
    </div>
    <div class="modal-body">
      <form id="userForm" class="user-management-form">
        <input type="hidden" id="editUserId" value="">
        <div class="user-form-grid">
          <div class="form-field">
            <label for="formUsername"><span data-i18n="admin.username">Username</span> <span class="required">*</span></label>
            <input type="text" id="formUsername" name="username" required autocomplete="off">
          </div>
          <div class="form-field">
            <label for="formDisplayName" data-i18n="admin.displayName">Display Name</label>
            <input type="text" id="formDisplayName" name="display_name" autocomplete="off">
          </div>
          <div class="form-field">
            <label for="formEmail" data-i18n="admin.email">Email</label>
            <input type="email" id="formEmail" name="email" autocomplete="off">
          </div>
          <div class="form-field">
            <label for="formAuthType" data-i18n="admin.authType">Auth Type</label>
            <select id="formAuthType" name="auth_type">
              <option value="local" data-i18n="admin.local">Local</option>
              <option value="sso" data-i18n="admin.sso">SSO</option>
            </select>
          </div>
          <div class="form-field form-field-full">
            <label for="formPassword"><span data-i18n="admin.password">Password</span> <span id="passwordRequired" class="required">*</span></label>
            <div class="password-field-shell">
              <input type="password" id="formPassword" name="password" autocomplete="new-password">
              <button type="button" id="togglePassword" class="password-toggle-btn" data-i18n-title="login.togglePassword" title="Show/hide password">
                <svg id="passwordEyeIcon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                  <circle cx="12" cy="12" r="3"></circle>
                </svg>
              </button>
            </div>
            <span class="hint" id="passwordHint" data-i18n="admin.passwordHint">Leave empty to keep current password</span>
          </div>
          <div class="form-field form-field-full">
            <label data-i18n="admin.roles">Roles</label>
            <div class="multi-select" id="rolesMultiSelect">
              <div class="multi-select-display">
                <span class="multi-select-text placeholder" data-i18n="admin.rolesPlaceholder">Select roles...</span>
                <span class="multi-select-arrow">&#9662;</span>
              </div>
              <div class="multi-select-dropdown hidden" id="rolesMultiSelectDropdown"></div>
            </div>
          </div>
        </div>
        <div class="user-form-switches">
          <label class="user-switch-row" for="formIsActive"><div><strong data-i18n="admin.activeStatus">Active</strong><span data-i18n="admin.activeStatusHint">Allows this user to sign in and receive access.</span></div><input type="checkbox" id="formIsActive" name="is_active" checked></label>
        </div>
      </form>
    </div>
    <div class="modal-footer">
      <button type="button" class="btn-secondary" id="modalCancel" data-i18n="admin.cancel">Cancel</button>
      <button type="submit" class="btn-primary" id="modalSubmit" form="userForm" data-i18n="admin.save">Save</button>
    </div>
  </div>
</div>

<div id="deleteModal" class="modal-overlay hidden">
  <div class="modal user-delete-modal">
    <div class="modal-header">
      <h2 data-i18n="admin.deleteConfirmTitle">Confirm Delete</h2>
      <button class="modal-close" id="deleteModalClose">&times;</button>
    </div>
    <div class="modal-body">
      <p class="confirm-message"><span data-i18n="admin.confirmDelete">Are you sure you want to delete this user? This action cannot be undone.</span></p>
      <strong class="delete-target-name" id="deleteUserName"></strong>
      <input type="hidden" id="deleteUserId" value="">
    </div>
    <div class="modal-footer">
      <button type="button" class="btn-secondary" id="deleteCancel" data-i18n="admin.cancel">Cancel</button>
      <button type="button" class="btn-danger" id="deleteConfirm" data-i18n="admin.delete">Delete</button>
    </div>
  </div>
</div>
`

function translateOrFallback(key, fallback) {
  const translated = t(key)
  return translated === key ? fallback : translated
}

function addTrackedListener(element, event, handler, options) {
  if (!element) return
  element.addEventListener(event, handler, options)
  eventCleanupFns.push(() => element.removeEventListener(event, handler, options))
}

function rolesDictToArray(roles) {
  if (!roles || typeof roles !== 'object') return []
  return Object.keys(roles).filter(key => roles[key])
}

function rolesArrayToDict(arr) {
  const result = {}
  if (!arr || !Array.isArray(arr)) return result
  arr.forEach(roleName => {
    if (roleName && typeof roleName === 'string') result[roleName.trim()] = true
  })
  return result
}

function buildFallbackRolePermissions(identifier) {
  if (identifier === 'viewer') {
    return {
      skills: { module_permissions: { view: true }, skill_permissions: [] },
      channels: { module_permissions: { manage_permissions: false }, channel_permissions: [] },
      tokens: { view: true },
      users: { view: true },
      roles: { view: true }
    }
  }

  if (identifier === 'user') {
    return {
      skills: { module_permissions: { view: false }, skill_permissions: [] }
    }
  }

  if (identifier === 'admin') {
    return {
      skills: { module_permissions: { view: true, enable_disable: true, manage_permissions: true }, skill_permissions: [] },
      channels: { module_permissions: { manage_permissions: true }, channel_permissions: [] },
      tokens: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
      agent_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
      provider_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
      model_configs: { view: true, create: true, edit: true, delete: true, manage_permissions: true },
      users: { view: true, create: true, edit: true, delete: true, assign_roles: true, manage_permissions: true },
      roles: { view: true, create: true, edit: true, delete: true, manage_permissions: true }
    }
  }

  return {}
}

function getFallbackRoles() {
  return [
    {
      identifier: 'admin',
      name: getBuiltinRoleDisplayName('admin', translateOrFallback('admin.roleAdmin', 'Admin')),
      is_builtin: true,
      permissions: buildFallbackRolePermissions('admin')
    },
    {
      identifier: 'user',
      name: getBuiltinRoleDisplayName('user', translateOrFallback('admin.roleUser', 'User')),
      is_builtin: true,
      permissions: buildFallbackRolePermissions('user')
    },
    {
      identifier: 'viewer',
      name: getBuiltinRoleDisplayName('viewer', translateOrFallback('admin.roleViewer', 'Viewer')),
      is_builtin: true,
      permissions: buildFallbackRolePermissions('viewer')
    }
  ]
}

function getBuiltinRoleDisplayName(identifier, fallback = '') {
  if (!identifier) return fallback

  return (
    translateIfExists(`roles.builtinRoleCatalog.${identifier}.name`)
    || (identifier === 'admin' ? translateOrFallback('admin.roleAdmin', 'Admin') : '')
    || (identifier === 'viewer' ? translateOrFallback('admin.roleViewer', 'Viewer') : '')
    || (identifier === 'user' ? translateOrFallback('admin.roleUser', 'User') : '')
    || fallback
  )
}

function getRoleDisplayName(role) {
  if (!role) return ''
  if (role.is_builtin || ['admin', 'user', 'viewer'].includes(role.identifier)) {
    return getBuiltinRoleDisplayName(role.identifier, role.name || role.identifier)
  }
  return role.name || role.identifier || ''
}

function getRoleLabel(identifier) {
  const role = availableRoles.find(entry => entry.identifier === identifier)
  if (role) return getRoleDisplayName(role)
  if (identifier === 'admin') return getBuiltinRoleDisplayName('admin', 'Admin')
  if (identifier === 'viewer') return getBuiltinRoleDisplayName('viewer', 'Viewer')
  if (identifier === 'user') return getBuiltinRoleDisplayName('user', 'User')
  return identifier
}

function collectEnabledPermissionPaths(value, prefix = '') {
  if (value === true) {
    return prefix ? [prefix] : []
  }

  if (Array.isArray(value)) {
    if (
      prefix === 'skills.skill_permissions'
      && value.some(entry => entry && typeof entry === 'object' && (entry.authorized === true || entry.enabled === true))
    ) {
      return [prefix]
    }
    if (
      prefix === 'providers.provider_permissions'
      && value.some(entry => entry && typeof entry === 'object' && entry.allowed === true)
    ) {
      return [prefix]
    }
    if (
      prefix === 'channels.channel_permissions'
      && value.some(entry => entry && typeof entry === 'object' && entry.allowed === true)
    ) {
      return [prefix]
    }
    return []
  }

  if (!value || typeof value !== 'object') {
    return []
  }

  return Object.entries(value).flatMap(([key, child]) => {
    const childPrefix = prefix ? `${prefix}.${key}` : key
    return collectEnabledPermissionPaths(child, childPrefix)
  })
}

function isNonAdminAssignableRole(role = {}) {
  const normalizedIdentifier = String(role?.identifier || '').trim().toLowerCase()
  if (normalizedIdentifier === 'admin') {
    return false
  }

  const enabledPaths = new Set(collectEnabledPermissionPaths(role?.permissions || {}))
  return [...enabledPaths].every(path => NON_ADMIN_ASSIGNABLE_PERMISSION_PATHS.has(path))
}

function renderRoleFilterOptions() {
  if (!roleFilterSelect) return
  const options = [
    `<option value="all" data-i18n="admin.roleAll">${translateOrFallback('admin.roleAll', 'All')}</option>`,
    ...availableRoles.map(role => `<option value="${escapeHtml(role.identifier)}">${escapeHtml(getRoleDisplayName(role))}</option>`)
  ]
  roleFilterSelect.innerHTML = options.join('')
  if (![ 'all', ...availableRoles.map(role => role.identifier) ].includes(currentRoleFilter)) {
    currentRoleFilter = 'all'
  }
  roleFilterSelect.value = currentRoleFilter
}

function renderRolesMultiSelectOptions() {
  const dropdown = container?.querySelector('#rolesMultiSelectDropdown')
  if (!dropdown) return
  dropdown.innerHTML = availableRoles.map(role => `
    <label class="multi-select-option">
      <input type="checkbox" name="role" value="${escapeHtml(role.identifier)}">
      <span>${escapeHtml(getRoleDisplayName(role))}</span>
    </label>
  `).join('')
}

async function loadAvailableRoles() {
  try {
    const response = await fetch('/api/roles?page=1&page_size=100')
    if (!response.ok) {
      await handleApiError(response)
      availableRoles = getFallbackRoles()
    } else {
      const data = await response.json()
      availableRoles = Array.isArray(data.roles) && data.roles.length
        ? data.roles.map(role => ({
          identifier: role.identifier,
          name: role.name,
          is_builtin: role.is_builtin === true,
          permissions: role.permissions || {}
        }))
        : getFallbackRoles()
    }
  } catch (error) {
    console.warn('[AdminUsers] Failed to load roles:', error)
    availableRoles = getFallbackRoles()
  }

  renderRoleFilterOptions()
  renderRolesMultiSelectOptions()
}

function escapeHtml(str) {
  if (!str) return ''
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function isLocalAuth(authType) {
  return String(authType || '').toLowerCase() === 'local'
}

export function formatUserAuthType(authType) {
  return String(authType || 'local').trim() || 'local'
}

function ensureAuthTypeOption(authType) {
  const normalized = formatUserAuthType(authType)
  const select = container?.querySelector('#formAuthType')
  const existing = select
    ? Array.from(select.options || []).some(option => option.value === normalized)
    : false
  if (!select || existing) {
    return normalized
  }

  const option = document.createElement('option')
  option.value = normalized
  option.textContent = normalized
  select.appendChild(option)
  return normalized
}

export function renderUserNameWithAuthType(user = {}) {
  const displayName = user.display_name || user.username || 'User'
  const authType = formatUserAuthType(user.auth_type)
  return `
    <div class="user-row-name-line">
      <strong>${escapeHtml(displayName)}</strong>
      <span class="user-auth-type-badge">${escapeHtml(authType)}</span>
    </div>
  `
}

function formatStatusText(isActive) {
  return isActive
    ? translateOrFallback('admin.statusEnabledLabel', 'Enabled')
    : translateOrFallback('admin.statusDisabledLabel', 'Disabled')
}

function resolvePrimaryRoleIdentifier(roleIdentifiers = []) {
  const priorities = ['admin', 'viewer', 'user']
  return priorities.find(identifier => roleIdentifiers.includes(identifier)) || roleIdentifiers[0] || ''
}

export function getRoleDisplayStateForUser(user = {}) {
  const roleIdentifiers = getAssignableRoleIdentifiersForUser(user)
  if (!roleIdentifiers.length) {
    return {
      label: translateOrFallback('admin.noRoles', 'No explicit roles'),
      variant: 'none'
    }
  }

  const primaryIdentifier = resolvePrimaryRoleIdentifier(roleIdentifiers)
  return {
    label: getRoleLabel(primaryIdentifier),
    variant: getRoleVariantFromIdentifier(primaryIdentifier)
  }
}

function getRoleVariantFromIdentifier(identifier) {
  if (identifier === 'admin') return 'admin'
  if (identifier === 'viewer') return 'viewer'
  if (identifier === 'user') return 'user'
  return 'custom'
}

function getUserCardId(user) {
  const source = String(user.id || user.username || '0').replace(/[^a-zA-Z0-9]/g, '')
  const tail = (source.slice(-4) || '0001').padStart(4, '0').toLowerCase()
  return `usr_${tail}`
}

function getUserInitials(user) {
  const source = (user.display_name || user.username || 'A').trim()
  return source.charAt(0).toUpperCase() || 'A'
}

function renderUserAvatar(user) {
  if (user?.avatar_url) {
    const alt = user.display_name || user.username || 'User'
    return `<img src="${escapeHtml(user.avatar_url)}" alt="${escapeHtml(alt)}">`
  }

  return `<span>${escapeHtml(getUserInitials(user))}</span>`
}

function applyRoleFilter(users) {
  if (currentRoleFilter === 'all') return users
  return users.filter(user => getAssignableRoleIdentifiersForUser(user).includes(currentRoleFilter))
}

function updateRolesDisplay() {
  const multiSelect = container.querySelector('#rolesMultiSelect')
  if (!multiSelect) return
  const textEl = multiSelect.querySelector('.multi-select-text')
  const checkboxes = multiSelect.querySelectorAll('input[type="checkbox"]:checked')
  if (checkboxes.length === 0) {
    textEl.textContent = translateOrFallback('admin.rolesPlaceholder', 'Select roles...')
    textEl.classList.add('placeholder')
    return
  }
  const labels = Array.from(checkboxes).map(cb => cb.parentElement.querySelector('span')?.textContent || cb.value)
  textEl.textContent = labels.join(', ')
  textEl.classList.remove('placeholder')
}

function initRolesMultiSelect() {
  const multiSelect = container.querySelector('#rolesMultiSelect')
  if (!multiSelect) return
  const display = multiSelect.querySelector('.multi-select-display')
  const dropdown = multiSelect.querySelector('.multi-select-dropdown')

  addTrackedListener(display, 'click', event => {
    if (!canAssignUserRoles()) {
      dropdown.classList.add('hidden')
      return
    }
    event.stopPropagation()
    dropdown.classList.toggle('hidden')
  })

  addTrackedListener(dropdown, 'change', event => {
    if (event.target.matches('input[type="checkbox"]')) updateRolesDisplay()
  })

  documentClickHandler = event => {
    if (!multiSelect.contains(event.target)) dropdown.classList.add('hidden')
  }
  document.addEventListener('click', documentClickHandler)
}

function setupPasswordToggle() {
  const toggleBtn = container.querySelector('#togglePassword')
  const passwordInput = container.querySelector('#formPassword')
  const eyeIcon = container.querySelector('#passwordEyeIcon')
  if (!toggleBtn || !passwordInput || !eyeIcon) return

  addTrackedListener(toggleBtn, 'click', () => {
    const isPassword = passwordInput.type === 'password'
    passwordInput.type = isPassword ? 'text' : 'password'
    eyeIcon.innerHTML = isPassword
      ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>'
      : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>'
  })
}

async function handleApiError(response) {
  const status = response.status
  if (status === 401) {
    showToast(translateOrFallback('admin.sessionExpired', 'Session expired. Please login again.'), 'error')
    setTimeout(() => { redirectToLogin() }, 1200)
    throw new Error('Session expired')
  }
  if (status === 403) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
    throw new Error('Access denied')
  }

  let errorMessage = translateOrFallback('admin.failedToLoad', 'Failed to load users')
  try {
    const data = await response.json()
    errorMessage = data.detail || data.error || data.message || errorMessage
  } catch {}
  throw new Error(errorMessage)
}

function buildUsersParams(page, pageSize, search = '') {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (search) params.append('search', search)
  if (currentStatusFilter === 'enabled') params.append('is_active', 'true')
  if (currentStatusFilter === 'disabled') params.append('is_active', 'false')
  return params
}

async function fetchUsersPage(page = 1, pageSize = currentPageSize, search = '') {
  const response = await fetch(`/api/users?${buildUsersParams(page, pageSize, search)}`)
  if (!response.ok) {
    await handleApiError(response)
    return null
  }

  return response.json()
}

async function fetchRoleFilteredUsers(search = '') {
  const allUsers = []
  let page = 1
  let total = 0

  while (true) {
    const data = await fetchUsersPage(page, ROLE_FILTER_FETCH_PAGE_SIZE, search)
    if (!data) {
      return null
    }

    const batch = Array.isArray(data.users) ? data.users : []
    total = data.total || 0
    allUsers.push(...batch)

    if (!batch.length || allUsers.length >= total) {
      break
    }

    page += 1
  }

  return applyRoleFilter(allUsers)
}

async function loadUsers(page = 1, search = '') {
  try {
    if (currentRoleFilter === 'all') {
      const data = await fetchUsersPage(page, currentPageSize, search)
      if (!data) {
        return
      }

      currentFetchedUsers = Array.isArray(data.users) ? data.users : []
      totalUsers = data.total || 0
      renderUserList(currentFetchedUsers)
      renderPagination(page, Math.ceil(totalUsers / currentPageSize))
      return
    }

    const filteredUsers = await fetchRoleFilteredUsers(search)
    if (!filteredUsers) {
      return
    }

    currentFetchedUsers = filteredUsers
    totalUsers = filteredUsers.length
    const startIndex = (page - 1) * currentPageSize
    renderUserList(filteredUsers.slice(startIndex, startIndex + currentPageSize))
    renderPagination(page, Math.ceil(totalUsers / currentPageSize))
  } catch (error) {
    console.error('[AdminUsers] Failed to load users:', error)
    showToast(error.message || translateOrFallback('admin.failedToLoad', 'Failed to load users'), 'error')
    if (usersTableBody) {
      usersTableBody.innerHTML = `
        <tr class="user-empty-row">
          <td colspan="7">
            <div class="user-list-empty error">${translateOrFallback('admin.failedToLoad', 'Failed to load users')}</div>
          </td>
        </tr>
      `
    }
  }
}

async function createUser(formData) {
  const response = await fetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData)
  })
  if (!response.ok) {
    await handleApiError(response)
    return null
  }
  return response.json()
}

async function updateUser(userId, formData) {
  const response = await fetch(`/api/users/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData)
  })
  if (!response.ok) {
    await handleApiError(response)
    return null
  }
  return response.json()
}

async function deleteUserApi(userId) {
  const response = await fetch(`/api/users/${userId}`, { method: 'DELETE' })
  if (!response.ok) {
    await handleApiError(response)
    return false
  }
  return true
}

async function toggleUserStatus(user) {
  const updated = await updateUser(user.id, { is_active: !user.is_active })
  if (!updated) return
  showToast(
    !user.is_active
      ? translateOrFallback('admin.enableSuccess', 'Enabled successfully')
      : translateOrFallback('admin.disableSuccess', 'Disabled successfully'),
    'success'
  )
  await loadUsers(currentPage, currentSearch)
}

function renderStatusBadge(isActive) {
  const statusClass = isActive ? 'enabled' : 'disabled'
  return `<div class="user-status-badge ${statusClass}"><span class="user-status-dot"></span><span>${escapeHtml(formatStatusText(isActive))}</span></div>`
}

function getRoleVariant(user) {
  return getRoleDisplayStateForUser(user).variant
}

function renderRoleBadge(user) {
  const { label, variant } = getRoleDisplayStateForUser(user)
  return `<span class="user-role-pill ${variant}">${escapeHtml(label)}</span>`
}

export function canCreateUsersForAuthInfo(authInfo) {
  return hasPermission(authInfo, 'users.create')
}

export function canAssignRolesForUserForm(authInfo) {
  return hasPermission(authInfo, 'users.assign_roles')
}

function canAssignProtectedRole(authInfo, roleIdentifier, roleCatalog = availableRoles) {
  const normalizedIdentifier = String(roleIdentifier || '').trim().toLowerCase()
  if (!normalizedIdentifier) return true
  if (authInfo?.is_admin === true) return true
  if (normalizedIdentifier === 'admin') return false

  const role = (Array.isArray(roleCatalog) ? roleCatalog : []).find(entry => entry.identifier === normalizedIdentifier)
  if (!role) return true
  return isNonAdminAssignableRole(role)
}

export function getAssignableRoleIdentifiersForUser(user = {}) {
  const roleIdentifiers = new Set(rolesDictToArray(user.roles))
  if (user.is_admin) {
    roleIdentifiers.add('admin')
  }
  return Array.from(roleIdentifiers)
}

function sanitizeSubmittedRoles(roles = {}, authInfo = null, existingRoles = null, roleCatalog = availableRoles) {
  const nextRoles = { ...(roles || {}) }
  const requestedIdentifiers = Object.keys(nextRoles)

  const existingIdentifiers = new Set(getAssignableRoleIdentifiersForUser({
    roles: existingRoles || {},
    is_admin: Boolean(existingRoles?.admin)
  }))

  requestedIdentifiers.forEach(identifier => {
    if (canAssignProtectedRole(authInfo, identifier, roleCatalog)) {
      return
    }

    if (existingIdentifiers.has(identifier)) {
      nextRoles[identifier] = true
      return
    }

    delete nextRoles[identifier]
  })

  existingIdentifiers.forEach(identifier => {
    if (!canAssignProtectedRole(authInfo, identifier, roleCatalog) && nextRoles[identifier] !== true) {
      nextRoles[identifier] = true
    }
  })

  if (!canAssignProtectedRole(authInfo, 'admin', roleCatalog) && !existingIdentifiers.has('admin')) {
    delete nextRoles.admin
  }

  return nextRoles
}

export function buildUserPayloadForSubmission({
  isEdit = false,
  authInfo = null,
  values = {},
  existingRoles = null,
  availableRoles: roleCatalog = availableRoles
} = {}) {
  const formData = {}
  const canEditProfileFields = hasPermission(authInfo, 'users.edit')
  const canAssignRoles = canAssignRolesForUserForm(authInfo)

  if (!isEdit || canEditProfileFields) {
    formData.email = values.email ?? null
    formData.display_name = values.display_name ?? null
    formData.is_active = values.is_active === true
  }

  if (canAssignRoles) {
    formData.roles = sanitizeSubmittedRoles(
      values.roles || {},
      authInfo,
      existingRoles,
      Array.isArray(roleCatalog) ? roleCatalog : availableRoles
    )
  }

  if (!isEdit) {
    formData.username = values.username || ''
    formData.auth_type = formatUserAuthType(values.auth_type)
  }

  if (values.password && (!isEdit || canEditProfileFields)) {
    formData.password = values.password
  }

  return formData
}

function canCreateUsers() {
  return canCreateUsersForAuthInfo(currentViewerAuthInfo)
}

function canEditUsers() {
  return (
    hasPermission(currentViewerAuthInfo, 'users.edit')
    || hasPermission(currentViewerAuthInfo, 'users.assign_roles')
  )
}

function canToggleUserStates() {
  return hasPermission(currentViewerAuthInfo, 'users.edit')
}

function canDeleteUsers() {
  return hasPermission(currentViewerAuthInfo, 'users.delete')
}

function canEditUserProfileFields() {
  return hasPermission(currentViewerAuthInfo, 'users.edit')
}

function canResetUserPasswords() {
  return hasPermission(currentViewerAuthInfo, 'users.edit')
}

function canAssignUserRoles() {
  return canAssignRolesForUserForm(currentViewerAuthInfo)
}

function applyEditModalPermissions(isEdit) {
  const displayNameInput = container.querySelector('#formDisplayName')
  const emailInput = container.querySelector('#formEmail')
  const authTypeSelect = container.querySelector('#formAuthType')
  const activeToggle = container.querySelector('#formIsActive')
  const passwordInput = container.querySelector('#formPassword')
  const togglePasswordBtn = container.querySelector('#togglePassword')
  const submitBtn = container.querySelector('#modalSubmit')
  const rolesMultiSelectDisplay = container.querySelector('#rolesMultiSelect .multi-select-display')

  if (displayNameInput) displayNameInput.disabled = isEdit && !canEditUserProfileFields()
  if (emailInput) emailInput.disabled = isEdit && !canEditUserProfileFields()
  if (authTypeSelect) authTypeSelect.disabled = isEdit || !canEditUserProfileFields()
  if (activeToggle) activeToggle.disabled = isEdit && !canEditUserProfileFields()
  if (passwordInput) passwordInput.disabled = isEdit && !canResetUserPasswords()
  if (togglePasswordBtn) togglePasswordBtn.disabled = isEdit && !canResetUserPasswords()
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(checkbox => {
    checkbox.disabled = (
      !canAssignUserRoles()
      || !canAssignProtectedRole(currentViewerAuthInfo, checkbox.value)
    )
  })
  if (rolesMultiSelectDisplay) {
    rolesMultiSelectDisplay.setAttribute('aria-disabled', canAssignUserRoles() ? 'false' : 'true')
  }

  if (!submitBtn) return
  if (!isEdit) {
    submitBtn.disabled = !canCreateUsers()
    return
  }

  submitBtn.disabled = !(
    canEditUserProfileFields()
    || canResetUserPasswords()
    || canAssignUserRoles()
  )
}

function renderUserList(users) {
  if (!usersTableBody) return

  if (!users || users.length === 0) {
    usersTableBody.innerHTML = `
      <tr class="user-empty-row">
        <td colspan="7">
          <div class="user-list-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            <p>${translateOrFallback('admin.noUsersFound', 'No users found')}</p>
          </div>
        </td>
      </tr>
    `
    paginationInfo.textContent = translateOrFallback('admin.listSummaryEmpty', 'No users matched the current filters')
    return
  }

  usersTableBody.innerHTML = users.map(user => `
    <tr class="user-table-row" data-user-id="${escapeHtml(user.id)}">
      <td>
        <div class="user-row-identity">
          <div class="user-row-avatar">
            ${renderUserAvatar(user)}
            <span class="user-row-presence ${user.is_active ? 'active' : 'inactive'}"></span>
          </div>
          <div class="user-row-name-block">
            <strong>${escapeHtml(user.display_name || user.username || 'User')}</strong>
          </div>
        </div>
      </td>
      <td class="user-id-cell"><strong>${escapeHtml(getUserCardId(user))}</strong></td>
      <td class="user-email-cell"><span>${escapeHtml(user.email || 'No email')}</span></td>
      <td class="user-auth-type-cell"><span class="user-auth-type-badge">${escapeHtml(formatUserAuthType(user.auth_type))}</span></td>
      <td>${renderRoleBadge(user)}</td>
      <td>${renderStatusBadge(user.is_active)}</td>
      <td>
        <div class="user-row-actions">
          <button class="user-icon-btn btn-toggle-status" title="${user.is_active ? translateOrFallback('admin.disable', 'Disable') : translateOrFallback('admin.enable', 'Enable')}" data-user='${JSON.stringify(user).replace(/'/g, '&#39;')}' ${canToggleUserStates() ? '' : 'disabled'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2v10"></path>
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
            </svg>
          </button>
          <button class="user-icon-btn btn-edit" title="${translateOrFallback('admin.edit', 'Edit')}" data-user='${JSON.stringify(user).replace(/'/g, '&#39;')}' ${canEditUsers() ? '' : 'disabled'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
            </svg>
          </button>
          <button class="user-icon-btn btn-delete" title="${translateOrFallback('admin.delete', 'Delete')}" data-user-id="${escapeHtml(user.id)}" data-username="${escapeHtml(user.display_name || user.username)}" ${canDeleteUsers() ? '' : 'disabled'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
          </button>
        </div>
      </td>
    </tr>
  `).join('')

  usersTableBody.querySelectorAll('.btn-edit').forEach(button => {
    button.addEventListener('click', () => showEditModal(JSON.parse(button.dataset.user)))
  })

  usersTableBody.querySelectorAll('.btn-toggle-status').forEach(button => {
    button.addEventListener('click', async () => {
      try {
        await toggleUserStatus(JSON.parse(button.dataset.user))
      } catch (error) {
        showToast(error.message || translateOrFallback('admin.failedToLoad', 'Failed to update user'), 'error')
      }
    })
  })

  usersTableBody.querySelectorAll('.btn-delete').forEach(button => {
    button.addEventListener('click', () => showDeleteConfirm(button.dataset.userId, button.dataset.username))
  })

  paginationInfo.textContent = translateOrFallback('admin.listSummary', 'Showing {{current}} of {{total}} users')
    .replace('{{current}}', String(users.length))
    .replace('{{total}}', String(totalUsers))
}

function renderPagination(page, totalPages) {
  if (!paginationBtns) return
  if (totalPages <= 1) {
    paginationBtns.innerHTML = ''
    return
  }

  let html = `<button class="pagination-btn" ${page === 1 ? 'disabled' : ''} data-page="${page - 1}">&#8249;</button>`
  const maxVisible = 3
  let startPage = Math.max(1, page - Math.floor(maxVisible / 2))
  let endPage = Math.min(totalPages, startPage + maxVisible - 1)
  if (endPage - startPage < maxVisible - 1) startPage = Math.max(1, endPage - maxVisible + 1)
  for (let i = startPage; i <= endPage; i++) {
    html += `<button class="pagination-btn ${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`
  }
  html += `<button class="pagination-btn" ${page === totalPages ? 'disabled' : ''} data-page="${page + 1}">&#8250;</button>`
  paginationBtns.innerHTML = html

  paginationBtns.querySelectorAll('.pagination-btn:not(:disabled)').forEach(button => {
    button.addEventListener('click', () => {
      const nextPage = parseInt(button.dataset.page, 10)
      if (nextPage !== currentPage) handlePageChange(nextPage)
    })
  })
}

function showCreateModal() {
  if (!canCreateUsers()) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
    return
  }
  configureModalMode('create')
  container.querySelector('#modalTitle').setAttribute('data-i18n', 'admin.createTitle')
  container.querySelector('#modalDescription').setAttribute('data-i18n', 'admin.modalCreateDescription')
  container.querySelector('#modalTitle').textContent = translateOrFallback('admin.createTitle', 'Create User')
  container.querySelector('#modalDescription').textContent = translateOrFallback('admin.modalCreateDescription', 'Create a workspace user and assign their access scope.')
  container.querySelector('#editUserId').value = ''
  container.querySelector('#userForm').reset()
  container.querySelector('#formIsActive').checked = true
  container.querySelector('#formUsername').disabled = false
  container.querySelector('#passwordRequired').style.display = 'inline'
  container.querySelector('#passwordHint').style.display = 'none'
  container.querySelector('#formPassword').required = true
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(checkbox => { checkbox.checked = false })
  updateRolesDisplay()
  container.querySelector('#rolesMultiSelect .multi-select-dropdown')?.classList.add('hidden')
  applyEditModalPermissions(false)
  userModal.classList.remove('hidden')
  container.querySelector('#formUsername').focus()
}

function showEditModal(user) {
  if (!canEditUsers()) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
    return
  }
  configureModalMode('edit')
  container.querySelector('#modalTitle').setAttribute('data-i18n', 'admin.editTitle')
  container.querySelector('#modalDescription').setAttribute('data-i18n', 'admin.modalEditDescription')
  container.querySelector('#modalTitle').textContent = translateOrFallback('admin.editTitle', 'Edit User')
  container.querySelector('#modalDescription').textContent = translateOrFallback('admin.modalEditDescription', 'Update identity details, sign-in method, and workspace permissions.')
  container.querySelector('#editUserId').value = user.id
  container.querySelector('#formUsername').value = user.username || ''
  container.querySelector('#formDisplayName').value = user.display_name || ''
  container.querySelector('#formEmail').value = user.email || ''
  container.querySelector('#formPassword').value = ''
  container.querySelector('#formAuthType').value = ensureAuthTypeOption(user.auth_type)
  container.querySelector('#formIsActive').checked = user.is_active !== false
  container.querySelector('#formUsername').disabled = true
  container.querySelector('#passwordRequired').style.display = 'none'
  container.querySelector('#passwordHint').style.display = 'block'
  container.querySelector('#formPassword').required = false
  const rolesList = getAssignableRoleIdentifiersForUser(user)
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(checkbox => { checkbox.checked = rolesList.includes(checkbox.value) })
  updateRolesDisplay()
  container.querySelector('#rolesMultiSelect .multi-select-dropdown')?.classList.add('hidden')
  applyEditModalPermissions(true)
  userModal.classList.remove('hidden')
  container.querySelector('#formDisplayName').focus()
}

function closeModal() {
  if (!userModal) return
  userModal.classList.add('hidden')
  container.querySelector('#userForm')?.reset()
  configureModalMode('create')
}

function showDeleteConfirm(userId, username) {
  if (!canDeleteUsers()) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
    return
  }
  container.querySelector('#deleteUserId').value = userId
  container.querySelector('#deleteUserName').textContent = username || ''
  deleteModal.classList.remove('hidden')
}

function closeDeleteModal() {
  if (!deleteModal) return
  deleteModal.classList.add('hidden')
  const nameEl = container.querySelector('#deleteUserName')
  if (nameEl) nameEl.textContent = ''
}

function handleSearch(query) {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    currentSearch = query
    currentPage = 1
    loadUsers(currentPage, currentSearch)
  }, 250)
}

function handlePageChange(page) {
  currentPage = page
  loadUsers(currentPage, currentSearch)
}

function handleRoleFilterChange(value) {
  currentRoleFilter = value
  currentPage = 1
  loadUsers(currentPage, currentSearch)
}

function handleStatusFilterChange(value) {
  currentStatusFilter = value
  currentPage = 1
  loadUsers(currentPage, currentSearch)
}

function resetFilters() {
  currentSearch = ''
  currentRoleFilter = 'all'
  currentStatusFilter = 'all'
  currentPage = 1
  if (searchInput) searchInput.value = ''
  if (roleFilterSelect) roleFilterSelect.value = 'all'
  if (statusFilterSelect) statusFilterSelect.value = 'all'
  loadUsers(currentPage, currentSearch)
}

async function handleFormSubmit(event) {
  event.preventDefault()

  const submitBtn = container.querySelector('#modalSubmit')
  const editUserId = container.querySelector('#editUserId').value
  const isEdit = Boolean(editUserId)
  const username = container.querySelector('#formUsername').value.trim()
  const password = container.querySelector('#formPassword').value
  if (password) {
    if (isEdit && !canResetUserPasswords()) {
      showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
      return
    }
  } else if (!isEdit) {
    showToast(translateOrFallback('admin.passwordRequired', 'Password is required for new users'), 'error')
    return
  }

  if (!isEdit && !username) {
    showToast(translateOrFallback('admin.usernameRequired', 'Username is required'), 'error')
    return
  }

  const formData = buildUserPayloadForSubmission({
    isEdit,
    authInfo: currentViewerAuthInfo,
    existingRoles: isEdit
      ? (currentFetchedUsers.find(user => user.id === editUserId)?.roles || {})
      : null,
    values: {
      username,
      password,
      email: container.querySelector('#formEmail').value.trim() || null,
      display_name: container.querySelector('#formDisplayName').value.trim() || null,
      auth_type: container.querySelector('#formAuthType').value,
      roles: rolesArrayToDict(Array.from(container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]:checked')).map(cb => cb.value)),
      is_active: container.querySelector('#formIsActive').checked
    }
  })

  submitBtn.disabled = true
  submitBtn.textContent = isEdit
    ? translateOrFallback('admin.saving', 'Saving...')
    : translateOrFallback('admin.creating', 'Creating...')

  try {
    const result = isEdit ? await updateUser(editUserId, formData) : await createUser(formData)
    if (!result) {
      showToast(translateOrFallback('admin.operationFailed', 'Operation failed. Please try again.'), 'error')
      return
    }
    showToast(isEdit ? translateOrFallback('admin.updateSuccess', 'User updated successfully') : translateOrFallback('admin.createSuccess', 'User created successfully'), 'success')
    closeModal()
    await loadUsers(currentPage, currentSearch)
  } catch (error) {
    showToast(error.message, 'error')
  } finally {
    submitBtn.disabled = false
    submitBtn.textContent = translateOrFallback('admin.save', 'Save')
  }
}

async function handleDeleteConfirm() {
  const userId = container.querySelector('#deleteUserId').value
  const confirmBtn = container.querySelector('#deleteConfirm')
  confirmBtn.disabled = true
  confirmBtn.textContent = translateOrFallback('admin.deleting', 'Deleting...')

  try {
    const success = await deleteUserApi(userId)
    if (success) {
      showToast(translateOrFallback('admin.deleteSuccess', 'User deleted successfully'), 'success')
      closeDeleteModal()
      const remainingOnPage = totalUsers - 1 - (currentPage - 1) * currentPageSize
      if (remainingOnPage <= 0 && currentPage > 1) currentPage--
      await loadUsers(currentPage, currentSearch)
    }
  } catch (error) {
    showToast(error.message, 'error')
  } finally {
    confirmBtn.disabled = false
    confirmBtn.textContent = translateOrFallback('admin.delete', 'Delete')
  }
}

function setupEventListeners() {
  searchInput = container.querySelector('#searchInput')
  roleFilterSelect = container.querySelector('#roleFilterSelect')
  statusFilterSelect = container.querySelector('#statusFilterSelect')

  addTrackedListener(searchInput, 'input', event => handleSearch(event.target.value))
  addTrackedListener(roleFilterSelect, 'change', event => handleRoleFilterChange(event.target.value))
  addTrackedListener(statusFilterSelect, 'change', event => handleStatusFilterChange(event.target.value))
  addTrackedListener(container.querySelector('#resetFiltersBtn'), 'click', resetFilters)
  addTrackedListener(container.querySelector('#createUserBtn'), 'click', showCreateModal)
  addTrackedListener(container.querySelector('#modalClose'), 'click', closeModal)
  addTrackedListener(container.querySelector('#modalCancel'), 'click', closeModal)
  addTrackedListener(container.querySelector('#deleteModalClose'), 'click', closeDeleteModal)
  addTrackedListener(container.querySelector('#deleteCancel'), 'click', closeDeleteModal)
  addTrackedListener(container.querySelector('#userForm'), 'submit', handleFormSubmit)
  addTrackedListener(container.querySelector('#deleteConfirm'), 'click', handleDeleteConfirm)

  if (userModal) addTrackedListener(userModal, 'click', event => { if (event.target === userModal) closeModal() })
  if (deleteModal) addTrackedListener(deleteModal, 'click', event => { if (event.target === deleteModal) closeDeleteModal() })

  documentKeydownHandler = event => {
    if (event.key === 'Escape') {
      closeModal()
      closeDeleteModal()
    }
  }
  document.addEventListener('keydown', documentKeydownHandler)

  initRolesMultiSelect()
  setupPasswordToggle()
}

export async function mount(containerEl, { params, route } = {}) {
  console.log('[AdminUsersPage] Mounting...')
  container = containerEl

  const user = getAuthInfo() || await checkAuth({ redirect: true })
  if (!user) return
  currentViewerAuthInfo = user
  if (!canAccessUserManagement(user)) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. You do not have permission to manage users.'), 'error')
    window.location.href = buildAppUrl('/')
    return
  }

  if (!document.getElementById('admin-users-page-css')) {
    const cssLink = document.createElement('link')
    cssLink.rel = 'stylesheet'
    cssLink.href = buildAssetUrl('/styles/admin-users.css')
    cssLink.id = 'admin-users-page-css'
    document.head.appendChild(cssLink)
  }

  container.innerHTML = PAGE_HTML
  usersTableBody = container.querySelector('#usersTableBody')
  paginationInfo = container.querySelector('#paginationInfo')
  paginationBtns = container.querySelector('#paginationBtns')
  userModal = container.querySelector('#userModal')
  deleteModal = container.querySelector('#deleteModal')

  setupEventListeners()
  await loadAvailableRoles()
  updateContainerTranslations(container)
  const createButton = container.querySelector('#createUserBtn')
  if (createButton) {
    createButton.disabled = !canCreateUsers()
  }
  await loadUsers(currentPage, currentSearch)
  console.log('[AdminUsersPage] Mounted')
}

export async function unmount() {
  console.log('[AdminUsersPage] Unmounting...')
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
  if (documentClickHandler) {
    document.removeEventListener('click', documentClickHandler)
    documentClickHandler = null
  }
  if (documentKeydownHandler) {
    document.removeEventListener('keydown', documentKeydownHandler)
    documentKeydownHandler = null
  }

  eventCleanupFns.forEach(fn => fn())
  eventCleanupFns = []

  currentPage = 1
  currentSearch = ''
  totalUsers = 0
  currentRoleFilter = 'all'
  currentStatusFilter = 'all'
  currentFetchedUsers = []
  availableRoles = []
  usersTableBody = null
  paginationInfo = null
  paginationBtns = null
  searchInput = null
  roleFilterSelect = null
  statusFilterSelect = null
  userModal = null
  deleteModal = null
  container = null
  currentViewerAuthInfo = null
  console.log('[AdminUsersPage] Unmounted')
}

export default { mount, unmount }

function configureModalMode(mode) {
  const modalSubmit = container?.querySelector('#modalSubmit')
  const modalCancel = container?.querySelector('#modalCancel')
  const passwordField = container?.querySelector('#formPassword')?.closest('.form-field')
  const controls = container?.querySelectorAll('#userForm input, #userForm select, #userForm button')

  if (!modalSubmit || !modalCancel || !passwordField || !controls) {
    return
  }

  controls.forEach(control => {
    if (control.id === 'formUsername') {
      control.disabled = mode === 'edit'
      return
    }

    control.disabled = false
  })

  passwordField.style.display = ''
  modalSubmit.style.display = ''
  modalCancel.textContent = translateOrFallback('admin.cancel', 'Cancel')
}
