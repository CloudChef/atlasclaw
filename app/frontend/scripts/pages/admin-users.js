import { t, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { checkAuth } from '../auth.js'

let container = null
let currentPage = 1
let currentSearch = ''
let currentPageSize = 5
let totalUsers = 0
let currentRoleFilter = 'all'
let currentStatusFilter = 'all'
let currentFetchedUsers = []
let searchDebounceTimer = null

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
          <select id="roleFilterSelect">
            <option value="all" data-i18n="admin.roleAll">All</option>
            <option value="admin" data-i18n="admin.roleAdmin">Admin</option>
            <option value="user" data-i18n="admin.roleUser">User</option>
            <option value="viewer" data-i18n="admin.roleViewer">Viewer</option>
          </select>
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
      <div id="usersTableBody" class="user-management-list">
        <div class="user-list-loading" data-i18n="admin.loading">Loading...</div>
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
              <div class="multi-select-dropdown hidden">
                <label class="multi-select-option"><input type="checkbox" name="role" value="admin"><span data-i18n="admin.roleAdmin">Admin</span></label>
                <label class="multi-select-option"><input type="checkbox" name="role" value="user"><span data-i18n="admin.roleUser">User</span></label>
                <label class="multi-select-option"><input type="checkbox" name="role" value="viewer"><span data-i18n="admin.roleViewer">Viewer</span></label>
              </div>
            </div>
          </div>
        </div>
        <div class="user-form-switches">
          <label class="user-switch-row" for="formIsActive"><div><strong data-i18n="admin.activeStatus">Active</strong><span data-i18n="admin.activeStatusHint">Allows this user to sign in and receive access.</span></div><input type="checkbox" id="formIsActive" name="is_active" checked></label>
          <label class="user-switch-row" for="formIsAdmin"><div><strong data-i18n="admin.administrator">Administrator</strong><span data-i18n="admin.administratorHint">Admins can manage users, channels, and model settings.</span></div><input type="checkbox" id="formIsAdmin" name="is_admin"></label>
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

function escapeHtml(str) {
  if (!str) return ''
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

function isLocalAuth(authType) {
  return String(authType || '').toLowerCase() === 'local'
}

function formatStatusText(isActive) {
  return isActive
    ? translateOrFallback('admin.statusEnabledLabel', 'Enabled')
    : translateOrFallback('admin.statusDisabledLabel', 'Disabled')
}

function getPrimaryRole(user) {
  if (user.is_admin) return translateOrFallback('admin.roleAdmin', 'Admin')
  const roles = rolesDictToArray(user.roles)
  if (roles.includes('viewer')) return translateOrFallback('admin.roleViewer', 'Viewer')
  if (roles.includes('user')) return translateOrFallback('admin.roleUser', 'User')
  if (roles.includes('admin')) return translateOrFallback('admin.roleAdmin', 'Admin')
  return translateOrFallback('admin.roleUser', 'User')
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
  return users.filter(user => {
    if (currentRoleFilter === 'admin') {
      return user.is_admin === true || rolesDictToArray(user.roles).includes('admin')
    }
    return rolesDictToArray(user.roles).includes(currentRoleFilter)
  })
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
  const checkboxes = multiSelect.querySelectorAll('input[type="checkbox"]')

  addTrackedListener(display, 'click', event => {
    event.stopPropagation()
    dropdown.classList.toggle('hidden')
  })

  checkboxes.forEach(checkbox => addTrackedListener(checkbox, 'change', updateRolesDisplay))

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
    setTimeout(() => { window.location.href = '/login.html' }, 1200)
    return
  }
  if (status === 403) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. Admin privileges required.'), 'error')
    return
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
      usersTableBody.innerHTML = `<div class="user-list-empty error">${translateOrFallback('admin.failedToLoad', 'Failed to load users')}</div>`
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
  const roles = rolesDictToArray(user.roles)
  if (user.is_admin || roles.includes('admin')) return 'admin'
  if (roles.includes('viewer')) return 'viewer'
  return 'user'
}

function renderRoleBadge(user) {
  const role = getPrimaryRole(user)
  const roleClass = getRoleVariant(user)
  return `<span class="user-role-pill ${roleClass}">${escapeHtml(role)}</span>`
}

function renderUserList(users) {
  if (!usersTableBody) return

  if (!users || users.length === 0) {
    usersTableBody.innerHTML = `
      <div class="user-list-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
          <circle cx="9" cy="7" r="4"></circle>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
        </svg>
        <p>${translateOrFallback('admin.noUsersFound', 'No users found')}</p>
      </div>
    `
    paginationInfo.textContent = translateOrFallback('admin.listSummaryEmpty', 'No users matched the current filters')
    return
  }

  usersTableBody.innerHTML = users.map(user => `
    <article class="user-row-card" data-user-id="${escapeHtml(user.id)}">
      <div class="user-row-identity">
        <div class="user-row-avatar">
          ${renderUserAvatar(user)}
          <span class="user-row-presence ${user.is_active ? 'active' : 'inactive'}"></span>
        </div>
        <div class="user-row-id-block">
          <span class="user-row-label">${translateOrFallback('admin.userIdLabel', 'User ID')}</span>
          <strong>${escapeHtml(getUserCardId(user))}</strong>
        </div>
        <div class="user-row-name-block">
          <strong>${escapeHtml(user.display_name || user.username)}</strong>
          <span>${escapeHtml(user.email || 'No email')}</span>
        </div>
      </div>

      <div class="user-row-role-block">${renderRoleBadge(user)}</div>
      <div class="user-row-status-block">${renderStatusBadge(user.is_active)}</div>

      <div class="user-row-actions">
        <button class="user-icon-btn btn-toggle-status" title="${user.is_active ? translateOrFallback('admin.disable', 'Disable') : translateOrFallback('admin.enable', 'Enable')}" data-user='${JSON.stringify(user).replace(/'/g, '&#39;')}'>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2v10"></path>
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
          </svg>
        </button>
        <button class="user-icon-btn btn-edit" title="${translateOrFallback('admin.edit', 'Edit')}" data-user='${JSON.stringify(user).replace(/'/g, '&#39;')}'>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
          </svg>
        </button>
        <button class="user-icon-btn btn-delete" title="${translateOrFallback('admin.delete', 'Delete')}" data-user-id="${escapeHtml(user.id)}" data-username="${escapeHtml(user.display_name || user.username)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      </div>
    </article>
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
  configureModalMode('create')
  container.querySelector('#modalTitle').setAttribute('data-i18n', 'admin.createTitle')
  container.querySelector('#modalDescription').setAttribute('data-i18n', 'admin.modalCreateDescription')
  container.querySelector('#modalTitle').textContent = translateOrFallback('admin.createTitle', 'Create User')
  container.querySelector('#modalDescription').textContent = translateOrFallback('admin.modalCreateDescription', 'Create a workspace user and assign their access scope.')
  container.querySelector('#editUserId').value = ''
  container.querySelector('#userForm').reset()
  container.querySelector('#formIsActive').checked = true
  container.querySelector('#formIsAdmin').checked = false
  container.querySelector('#formUsername').disabled = false
  container.querySelector('#passwordRequired').style.display = 'inline'
  container.querySelector('#passwordHint').style.display = 'none'
  container.querySelector('#formPassword').required = true
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(checkbox => { checkbox.checked = false })
  updateRolesDisplay()
  container.querySelector('#rolesMultiSelect .multi-select-dropdown')?.classList.add('hidden')
  userModal.classList.remove('hidden')
  container.querySelector('#formUsername').focus()
}

function showEditModal(user) {
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
  container.querySelector('#formAuthType').value = user.auth_type || 'local'
  container.querySelector('#formIsActive').checked = user.is_active !== false
  container.querySelector('#formIsAdmin').checked = user.is_admin === true
  container.querySelector('#formUsername').disabled = true
  container.querySelector('#passwordRequired').style.display = 'none'
  container.querySelector('#passwordHint').style.display = 'block'
  container.querySelector('#formPassword').required = false
  const rolesList = rolesDictToArray(user.roles)
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(checkbox => { checkbox.checked = rolesList.includes(checkbox.value) })
  updateRolesDisplay()
  container.querySelector('#rolesMultiSelect .multi-select-dropdown')?.classList.add('hidden')
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
  const formData = {
    email: container.querySelector('#formEmail').value.trim() || null,
    display_name: container.querySelector('#formDisplayName').value.trim() || null,
    auth_type: container.querySelector('#formAuthType').value,
    roles: rolesArrayToDict(Array.from(container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]:checked')).map(cb => cb.value)),
    is_active: container.querySelector('#formIsActive').checked,
    is_admin: container.querySelector('#formIsAdmin').checked
  }

  if (!isEdit) formData.username = container.querySelector('#formUsername').value.trim()

  const password = container.querySelector('#formPassword').value
  if (password) {
    formData.password = password
  } else if (!isEdit) {
    showToast(translateOrFallback('admin.passwordRequired', 'Password is required for new users'), 'error')
    return
  }

  if (!isEdit && !formData.username) {
    showToast(translateOrFallback('admin.usernameRequired', 'Username is required'), 'error')
    return
  }

  submitBtn.disabled = true
  submitBtn.textContent = isEdit
    ? translateOrFallback('admin.saving', 'Saving...')
    : translateOrFallback('admin.creating', 'Creating...')

  try {
    const result = isEdit ? await updateUser(editUserId, formData) : await createUser(formData)
    if (result) {
      showToast(isEdit ? translateOrFallback('admin.updateSuccess', 'User updated successfully') : translateOrFallback('admin.createSuccess', 'User created successfully'), 'success')
      closeModal()
      await loadUsers(currentPage, currentSearch)
    }
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

  const user = await checkAuth({ redirect: true })
  if (!user) return
  if (!user.is_admin) {
    showToast(translateOrFallback('admin.accessDenied', 'Access denied. Admin privileges required.'), 'error')
    window.location.href = '/'
    return
  }

  if (!document.getElementById('admin-users-page-css')) {
    const cssLink = document.createElement('link')
    cssLink.rel = 'stylesheet'
    cssLink.href = '/styles/admin-users.css'
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
  updateContainerTranslations(container)
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
  document.getElementById('admin-users-page-css')?.remove()

  currentPage = 1
  currentSearch = ''
  totalUsers = 0
  currentRoleFilter = 'all'
  currentStatusFilter = 'all'
  currentFetchedUsers = []
  usersTableBody = null
  paginationInfo = null
  paginationBtns = null
  searchInput = null
  roleFilterSelect = null
  statusFilterSelect = null
  userModal = null
  deleteModal = null
  container = null
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
