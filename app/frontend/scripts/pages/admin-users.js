/**
 * admin-users.js - Admin Users Page Module (SPA)
 *
 * Migrated from admin-users.html + scripts/admin-users.js
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { t, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { checkAuth } from '../auth.js'

// ===================
// Module State
// ===================
let mounted = false
let container = null

// Pagination and search state
let currentPage = 1
let currentSearch = ''
let currentPageSize = 20
let totalUsers = 0
let searchDebounceTimer = null

// DOM element references
let usersTableBody = null
let paginationInfo = null
let paginationBtns = null
let searchInput = null
let userModal = null
let deleteModal = null

// Event listener cleanup tracking
let eventCleanupFns = []
let documentClickHandler = null
let documentKeydownHandler = null

// ===================
// HTML Templates
// ===================

const PAGE_HTML = `
<div class="channel-content">
  <!-- Users Table Section -->
  <section class="channel-section">
    <p class="section-desc" data-i18n="admin.description">Manage system users and permissions</p>
    
    <!-- Toolbar: Create button left, Search right -->
    <div class="admin-toolbar">
      <button class="btn-primary" id="createUserBtn">
        <span data-i18n="admin.createButton">+ Create User</span>
      </button>
      <input type="text" id="searchInput" class="search-input" data-i18n-placeholder="admin.searchPlaceholder" placeholder="Search users...">
    </div>

    <!-- Users Table -->
    <div class="users-section">
      <table class="users-table">
        <thead>
          <tr>
            <th data-i18n="admin.username">Username</th>
            <th data-i18n="admin.displayName">Display Name</th>
            <th data-i18n="admin.email">Email</th>
            <th data-i18n="admin.authType">Auth Type</th>
            <th data-i18n="admin.status">Status</th>
            <th data-i18n="admin.isAdmin">Admin</th>
            <th data-i18n="admin.actions">Actions</th>
          </tr>
        </thead>
        <tbody id="usersTableBody">
          <tr class="loading-row">
            <td colspan="7" data-i18n="admin.loading">Loading users...</td>
          </tr>
        </tbody>
      </table>

      <!-- Pagination -->
      <div class="pagination">
        <div class="pagination-info" id="paginationInfo">
          <span data-i18n="admin.loading">Loading...</span>
        </div>
        <div class="pagination-btns" id="paginationBtns">
          <!-- Pagination buttons rendered dynamically -->
        </div>
      </div>
    </div>
  </section>
</div>

<!-- Create/Edit User Modal -->
<div id="userModal" class="modal-overlay hidden">
  <div class="modal">
    <div class="modal-header">
      <h2 id="modalTitle" data-i18n="admin.createTitle">Create User</h2>
      <button class="modal-close" id="modalClose">&times;</button>
    </div>
    <div class="modal-body">
      <form id="userForm">
        <input type="hidden" id="editUserId" value="">
        
        <div class="form-row">
          <label for="formUsername"><span data-i18n="admin.username">Username</span> <span class="required">*</span></label>
          <input type="text" id="formUsername" name="username" required autocomplete="off">
        </div>

        <div class="form-row">
          <label for="formDisplayName" data-i18n="admin.displayName">Display Name</label>
          <input type="text" id="formDisplayName" name="display_name" autocomplete="off">
        </div>

        <div class="form-row">
          <label for="formEmail" data-i18n="admin.email">Email</label>
          <input type="email" id="formEmail" name="email" autocomplete="off">
        </div>

        <div class="form-row">
          <label for="formPassword"><span data-i18n="admin.password">Password</span> <span id="passwordRequired" class="required">*</span></label>
          <div style="flex: 1; min-width: 0;">
            <div style="position: relative;">
              <input type="password" id="formPassword" name="password" autocomplete="new-password" style="padding-right: 40px; width: 100%;">
              <button type="button" id="togglePassword" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; cursor: pointer; color: #666;">
                <svg id="passwordEyeIcon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                  <circle cx="12" cy="12" r="3"></circle>
                </svg>
              </button>
            </div>
            <span class="hint" id="passwordHint" data-i18n="admin.passwordHint">Leave empty to keep current password (edit mode only)</span>
          </div>
        </div>

        <div class="form-row">
          <label for="formAuthType" data-i18n="admin.authType">Auth Type</label>
          <select id="formAuthType" name="auth_type">
            <option value="local" data-i18n="admin.local">Local</option>
            <option value="sso" data-i18n="admin.sso">SSO</option>
          </select>
        </div>

        <div class="form-row">
          <label data-i18n="admin.roles">Roles</label>
          <div class="multi-select" id="rolesMultiSelect">
            <div class="multi-select-display">
              <span class="multi-select-text placeholder" data-i18n="admin.rolesPlaceholder">Select roles...</span>
              <span class="multi-select-arrow">&#9662;</span>
            </div>
            <div class="multi-select-dropdown hidden">
              <label class="multi-select-option">
                <input type="checkbox" name="role" value="admin">
                <span data-i18n="admin.roleAdmin">Admin</span>
              </label>
              <label class="multi-select-option">
                <input type="checkbox" name="role" value="user">
                <span data-i18n="admin.roleUser">User</span>
              </label>
              <label class="multi-select-option">
                <input type="checkbox" name="role" value="viewer">
                <span data-i18n="admin.roleViewer">Viewer</span>
              </label>
            </div>
          </div>
        </div>

        <div class="form-row form-row-inline">
          <input type="checkbox" id="formIsActive" name="is_active" checked>
          <label for="formIsActive" data-i18n="admin.activeStatus">Active</label>
        </div>

        <div class="form-row form-row-inline">
          <input type="checkbox" id="formIsAdmin" name="is_admin">
          <label for="formIsAdmin" data-i18n="admin.administrator">Administrator</label>
        </div>
      </form>
    </div>
    <div class="modal-footer">
      <button type="button" class="btn-secondary" id="modalCancel" data-i18n="admin.cancel">Cancel</button>
      <button type="submit" class="btn-primary" id="modalSubmit" form="userForm" data-i18n="admin.save">Save</button>
    </div>
  </div>
</div>

<!-- Delete Confirm Modal -->
<div id="deleteModal" class="modal-overlay hidden">
  <div class="modal" style="max-width: 400px;">
    <div class="modal-header">
      <h2 data-i18n="admin.deleteConfirmTitle">Confirm Delete</h2>
      <button class="modal-close" id="deleteModalClose">&times;</button>
    </div>
    <div class="modal-body">
      <p class="confirm-message" id="deleteConfirmMessage">
        <span data-i18n="admin.confirmDelete">Are you sure you want to delete this user? This action cannot be undone.</span>
      </p>
      <input type="hidden" id="deleteUserId" value="">
    </div>
    <div class="modal-footer">
      <button type="button" class="btn-secondary" id="deleteCancel" data-i18n="admin.cancel">Cancel</button>
      <button type="button" class="btn-danger" id="deleteConfirm" data-i18n="admin.delete">Delete</button>
    </div>
  </div>
</div>
`

// ===================
// Utility Functions
// ===================

/**
 * Convert roles dict to array of role names
 * @param {Object} roles - Roles dict like {admin: true, viewer: true}
 * @returns {Array} Array of role names
 */
function rolesDictToArray(roles) {
  if (!roles || typeof roles !== 'object') return []
  return Object.keys(roles).filter(key => roles[key])
}

/**
 * Convert array of role names to roles dict
 * @param {Array} arr - Array of role names
 * @returns {Object} Roles dict with true values
 */
function rolesArrayToDict(arr) {
  const result = {}
  if (!arr || !Array.isArray(arr)) return result
  arr.forEach(roleName => {
    if (roleName && typeof roleName === 'string') {
      result[roleName.trim()] = true
    }
  })
  return result
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(str) {
  if (!str) return ''
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

/**
 * Add event listener with cleanup tracking
 */
function addTrackedListener(element, event, handler, options) {
  if (!element) return
  element.addEventListener(event, handler, options)
  eventCleanupFns.push(() => element.removeEventListener(event, handler, options))
}

// ===================
// Multi-Select Component
// ===================

/**
 * Initialize roles multi-select dropdown
 */
function initRolesMultiSelect() {
  const multiSelect = container.querySelector('#rolesMultiSelect')
  if (!multiSelect) return

  const display = multiSelect.querySelector('.multi-select-display')
  const dropdown = multiSelect.querySelector('.multi-select-dropdown')
  const checkboxes = multiSelect.querySelectorAll('input[type="checkbox"]')

  // Toggle dropdown on click
  const displayClickHandler = (e) => {
    e.stopPropagation()
    dropdown.classList.toggle('hidden')
  }
  addTrackedListener(display, 'click', displayClickHandler)

  // Update display text when checkbox changes
  checkboxes.forEach(cb => {
    addTrackedListener(cb, 'change', updateRolesDisplay)
  })

  // Close dropdown when clicking outside (document-level listener)
  documentClickHandler = (e) => {
    if (!multiSelect.contains(e.target)) {
      dropdown.classList.add('hidden')
    }
  }
  document.addEventListener('click', documentClickHandler)
}

/**
 * Update the roles display text based on selected checkboxes
 */
function updateRolesDisplay() {
  const multiSelect = container.querySelector('#rolesMultiSelect')
  if (!multiSelect) return

  const textEl = multiSelect.querySelector('.multi-select-text')
  const checkboxes = multiSelect.querySelectorAll('input[type="checkbox"]:checked')

  if (checkboxes.length === 0) {
    textEl.textContent = t('admin.rolesPlaceholder') || 'Select roles...'
    textEl.classList.add('placeholder')
  } else {
    const labels = Array.from(checkboxes).map(cb => {
      const span = cb.parentElement.querySelector('span')
      return span ? span.textContent : cb.value
    })
    textEl.textContent = labels.join(', ')
    textEl.classList.remove('placeholder')
  }
}

// ===================
// Password Toggle
// ===================

/**
 * Setup password visibility toggle
 */
function setupPasswordToggle() {
  const toggleBtn = container.querySelector('#togglePassword')
  const passwordInput = container.querySelector('#formPassword')
  const eyeIcon = container.querySelector('#passwordEyeIcon')

  if (toggleBtn && passwordInput) {
    const toggleHandler = () => {
      const isPassword = passwordInput.type === 'password'
      passwordInput.type = isPassword ? 'text' : 'password'
      // Update icon: eye-off when showing text, eye when showing password
      eyeIcon.innerHTML = isPassword
        ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>'
        : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>'
    }
    addTrackedListener(toggleBtn, 'click', toggleHandler)
  }
}

// ===================
// API Calls
// ===================

/**
 * Handle API errors
 */
async function handleApiError(response) {
  const status = response.status

  if (status === 401) {
    showToast(t('admin.sessionExpired'), 'error')
    setTimeout(() => {
      window.location.href = '/login.html'
    }, 1500)
    return
  }

  if (status === 403) {
    showToast(t('admin.accessDenied'), 'error')
    return
  }

  let errorMessage = t('admin.failedToLoad')
  try {
    const data = await response.json()
    errorMessage = data.detail || data.error || data.message || errorMessage
  } catch {
    // ignore parse error
  }

  if (status === 409) {
    errorMessage = 'User already exists with this username or email'
  } else if (status === 404) {
    errorMessage = 'User not found'
  }

  throw new Error(errorMessage)
}

/**
 * Load users from API with pagination and search
 */
async function loadUsers(page = 1, search = '') {
  try {
    const params = new URLSearchParams({
      page: page.toString(),
      page_size: currentPageSize.toString()
    })

    if (search) {
      params.append('search', search)
    }

    const response = await fetch(`/api/users?${params}`)

    if (!response.ok) {
      await handleApiError(response)
      return
    }

    const data = await response.json()
    const users = data.users || []
    totalUsers = data.total || 0

    renderUserTable(users, totalUsers)
    renderPagination(page, Math.ceil(totalUsers / currentPageSize))
  } catch (error) {
    console.error('[AdminUsers] Failed to load users:', error)
    showToast(error.message || t('admin.failedToLoad'), 'error')
    if (usersTableBody) {
      usersTableBody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color: #e74c3c;">${t('admin.failedToLoad')}</td></tr>`
    }
  }
}

/**
 * Create a new user
 */
async function createUser(formData) {
  const response = await fetch('/api/users', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(formData)
  })

  if (!response.ok) {
    await handleApiError(response)
    return null
  }

  return await response.json()
}

/**
 * Update an existing user
 */
async function updateUser(userId, formData) {
  const response = await fetch(`/api/users/${userId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(formData)
  })

  if (!response.ok) {
    await handleApiError(response)
    return null
  }

  return await response.json()
}

/**
 * Delete a user
 */
async function deleteUserApi(userId) {
  const response = await fetch(`/api/users/${userId}`, {
    method: 'DELETE'
  })

  if (!response.ok) {
    await handleApiError(response)
    return false
  }

  return true
}

// ===================
// UI Rendering
// ===================

/**
 * Render the user table
 */
function renderUserTable(users, total) {
  if (!usersTableBody) return

  if (!users || users.length === 0) {
    usersTableBody.innerHTML = `
      <tr>
        <td colspan="7">
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            <p>${t('admin.noUsersFound')}</p>
          </div>
        </td>
      </tr>
    `
    return
  }

  usersTableBody.innerHTML = users.map(user => `
    <tr data-user-id="${user.id}">
      <td><strong>${escapeHtml(user.username)}</strong></td>
      <td>${escapeHtml(user.display_name || '-')}</td>
      <td>${escapeHtml(user.email || '-')}</td>
      <td>${renderAuthTypeBadge(user.auth_type)}</td>
      <td>${renderStatusBadge(user.is_active)}</td>
      <td>${renderAdminBadge(user.is_admin)}</td>
      <td>
        <div class="action-btns">
          <button class="btn-icon btn-edit" title="${t('admin.edit')}" data-user='${JSON.stringify(user).replace(/'/g, "&#39;")}'>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
            </svg>
          </button>
          <button class="btn-icon btn-delete" title="${t('admin.delete')}" data-user-id="${user.id}" data-username="${escapeHtml(user.username)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
          </button>
        </div>
      </td>
    </tr>
  `).join('')

  // Attach event listeners to action buttons
  usersTableBody.querySelectorAll('.btn-edit').forEach(btn => {
    btn.addEventListener('click', () => {
      const user = JSON.parse(btn.dataset.user)
      showEditModal(user)
    })
  })

  usersTableBody.querySelectorAll('.btn-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      showDeleteConfirm(btn.dataset.userId, btn.dataset.username)
    })
  })
}

/**
 * Render pagination controls
 */
function renderPagination(page, totalPages) {
  if (!paginationInfo || !paginationBtns) return

  const start = totalUsers === 0 ? 0 : (page - 1) * currentPageSize + 1
  const end = Math.min(page * currentPageSize, totalUsers)

  paginationInfo.textContent = t('admin.paginationInfo', { from: start, to: end, total: totalUsers })

  if (totalPages <= 1) {
    paginationBtns.innerHTML = ''
    return
  }

  let html = ''

  // Previous button
  html += `<button class="pagination-btn" ${page === 1 ? 'disabled' : ''} data-page="${page - 1}">${t('admin.prev')}</button>`

  // Page numbers
  const maxVisible = 5
  let startPage = Math.max(1, page - Math.floor(maxVisible / 2))
  let endPage = Math.min(totalPages, startPage + maxVisible - 1)

  if (endPage - startPage < maxVisible - 1) {
    startPage = Math.max(1, endPage - maxVisible + 1)
  }

  if (startPage > 1) {
    html += `<button class="pagination-btn" data-page="1">1</button>`
    if (startPage > 2) {
      html += `<span style="padding: 8px 4px; color: #888;">...</span>`
    }
  }

  for (let i = startPage; i <= endPage; i++) {
    html += `<button class="pagination-btn ${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`
  }

  if (endPage < totalPages) {
    if (endPage < totalPages - 1) {
      html += `<span style="padding: 8px 4px; color: #888;">...</span>`
    }
    html += `<button class="pagination-btn" data-page="${totalPages}">${totalPages}</button>`
  }

  // Next button
  html += `<button class="pagination-btn" ${page === totalPages ? 'disabled' : ''} data-page="${page + 1}">${t('admin.next')}</button>`

  paginationBtns.innerHTML = html

  // Attach event listeners
  paginationBtns.querySelectorAll('.pagination-btn:not(:disabled)').forEach(btn => {
    btn.addEventListener('click', () => {
      const newPage = parseInt(btn.dataset.page, 10)
      if (newPage !== currentPage) {
        handlePageChange(newPage)
      }
    })
  })
}

/**
 * Render status badge
 */
function renderStatusBadge(isActive) {
  if (isActive) {
    return `<span class="badge badge-active">${t('admin.active')}</span>`
  }
  return `<span class="badge badge-inactive">${t('admin.inactive')}</span>`
}

/**
 * Render admin badge
 */
function renderAdminBadge(isAdmin) {
  if (isAdmin) {
    return `<span class="badge badge-admin">${t('admin.isAdmin')}</span>`
  }
  return '-'
}

/**
 * Render auth type badge
 */
function renderAuthTypeBadge(authType) {
  const type = (authType || 'local').toLowerCase()
  if (type === 'sso') {
    return `<span class="badge badge-sso">${t('admin.sso')}</span>`
  }
  return `<span class="badge badge-local">${t('admin.local')}</span>`
}

// ===================
// Modal Management
// ===================

/**
 * Show create user modal
 */
function showCreateModal() {
  container.querySelector('#modalTitle').textContent = t('admin.createTitle')
  container.querySelector('#editUserId').value = ''
  container.querySelector('#userForm').reset()
  container.querySelector('#formIsActive').checked = true

  // Reset roles multi-select
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(cb => cb.checked = false)
  updateRolesDisplay()

  // Close dropdown if open
  const dropdown = container.querySelector('#rolesMultiSelect .multi-select-dropdown')
  if (dropdown) dropdown.classList.add('hidden')

  // Username is editable in create mode
  container.querySelector('#formUsername').disabled = false

  // Password is required in create mode
  container.querySelector('#passwordRequired').style.display = 'inline'
  container.querySelector('#passwordHint').style.display = 'none'
  container.querySelector('#formPassword').required = true

  userModal.classList.remove('hidden')
  container.querySelector('#formUsername').focus()
}

/**
 * Show edit user modal with pre-filled data
 */
function showEditModal(user) {
  container.querySelector('#modalTitle').textContent = t('admin.editTitle')
  container.querySelector('#editUserId').value = user.id

  // Pre-fill form
  container.querySelector('#formUsername').value = user.username || ''
  container.querySelector('#formDisplayName').value = user.display_name || ''
  container.querySelector('#formEmail').value = user.email || ''
  container.querySelector('#formPassword').value = ''
  container.querySelector('#formAuthType').value = user.auth_type || 'local'
  
  // Set roles multi-select
  const rolesList = rolesDictToArray(user.roles)
  container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]').forEach(cb => {
    cb.checked = rolesList.includes(cb.value)
  })
  updateRolesDisplay()

  // Close dropdown if open
  const dropdown = container.querySelector('#rolesMultiSelect .multi-select-dropdown')
  if (dropdown) dropdown.classList.add('hidden')
  
  container.querySelector('#formIsActive').checked = user.is_active !== false
  container.querySelector('#formIsAdmin').checked = user.is_admin === true

  // Username is read-only in edit mode
  container.querySelector('#formUsername').disabled = true

  // Password is optional in edit mode
  container.querySelector('#passwordRequired').style.display = 'none'
  container.querySelector('#passwordHint').style.display = 'block'
  container.querySelector('#formPassword').required = false

  userModal.classList.remove('hidden')
  container.querySelector('#formDisplayName').focus()
}

/**
 * Close user modal
 */
function closeModal() {
  if (userModal) {
    userModal.classList.add('hidden')
    const form = container.querySelector('#userForm')
    if (form) form.reset()
  }
}

/**
 * Show delete confirmation modal
 */
function showDeleteConfirm(userId, username) {
  container.querySelector('#deleteUserId').value = userId
  deleteModal.classList.remove('hidden')
}

/**
 * Close delete modal
 */
function closeDeleteModal() {
  if (deleteModal) {
    deleteModal.classList.add('hidden')
  }
}

// ===================
// Event Handlers
// ===================

/**
 * Handle search with debounce
 */
function handleSearch(query) {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
  }

  searchDebounceTimer = setTimeout(() => {
    currentSearch = query
    currentPage = 1
    loadUsers(currentPage, currentSearch)
  }, 300)
}

/**
 * Handle page change
 */
function handlePageChange(page) {
  currentPage = page
  loadUsers(currentPage, currentSearch)
}

/**
 * Handle form submission for create/edit
 */
async function handleFormSubmit(event) {
  event.preventDefault()

  const submitBtn = container.querySelector('#modalSubmit')
  const editUserId = container.querySelector('#editUserId').value
  const isEdit = !!editUserId

  // Gather form data
  const formData = {
    email: container.querySelector('#formEmail').value.trim() || null,
    display_name: container.querySelector('#formDisplayName').value.trim() || null,
    auth_type: container.querySelector('#formAuthType').value,
    roles: rolesArrayToDict(Array.from(container.querySelectorAll('#rolesMultiSelect input[type="checkbox"]:checked')).map(cb => cb.value)),
    is_active: container.querySelector('#formIsActive').checked,
    is_admin: container.querySelector('#formIsAdmin').checked
  }

  // Add username only in create mode
  if (!isEdit) {
    formData.username = container.querySelector('#formUsername').value.trim()
  }

  // Add password if provided
  const password = container.querySelector('#formPassword').value
  if (password) {
    formData.password = password
  } else if (!isEdit) {
    showToast(t('admin.passwordRequired'), 'error')
    return
  }

  // Validate required fields
  if (!isEdit && !formData.username) {
    showToast(t('admin.usernameRequired'), 'error')
    return
  }

  // Disable submit button
  submitBtn.disabled = true
  submitBtn.textContent = isEdit ? t('admin.saving') : t('admin.creating')

  try {
    let result
    if (isEdit) {
      result = await updateUser(editUserId, formData)
    } else {
      result = await createUser(formData)
    }

    if (result) {
      showToast(isEdit ? t('admin.updateSuccess') : t('admin.createSuccess'), 'success')
      closeModal()
      await loadUsers(currentPage, currentSearch)
    }
  } catch (error) {
    showToast(error.message, 'error')
  } finally {
    submitBtn.disabled = false
    submitBtn.textContent = t('admin.save')
  }
}

/**
 * Handle delete confirmation
 */
async function handleDeleteConfirm() {
  const userId = container.querySelector('#deleteUserId').value
  const confirmBtn = container.querySelector('#deleteConfirm')

  confirmBtn.disabled = true
  confirmBtn.textContent = t('admin.deleting')

  try {
    const success = await deleteUserApi(userId)
    if (success) {
      showToast(t('admin.deleteSuccess'), 'success')
      closeDeleteModal()

      // If we deleted the last user on current page, go to previous page
      const remainingOnPage = totalUsers - 1 - (currentPage - 1) * currentPageSize
      if (remainingOnPage <= 0 && currentPage > 1) {
        currentPage--
      }

      await loadUsers(currentPage, currentSearch)
    }
  } catch (error) {
    showToast(error.message, 'error')
  } finally {
    confirmBtn.disabled = false
    confirmBtn.textContent = t('admin.delete')
  }
}

/**
 * Setup all event listeners
 */
function setupEventListeners() {
  // Search input with debounce
  searchInput = container.querySelector('#searchInput')
  if (searchInput) {
    addTrackedListener(searchInput, 'input', (e) => handleSearch(e.target.value))
  }

  // Create user button
  const createBtn = container.querySelector('#createUserBtn')
  if (createBtn) {
    addTrackedListener(createBtn, 'click', showCreateModal)
  }

  // Modal close buttons
  addTrackedListener(container.querySelector('#modalClose'), 'click', closeModal)
  addTrackedListener(container.querySelector('#modalCancel'), 'click', closeModal)
  addTrackedListener(container.querySelector('#deleteModalClose'), 'click', closeDeleteModal)
  addTrackedListener(container.querySelector('#deleteCancel'), 'click', closeDeleteModal)

  // Form submit
  const userForm = container.querySelector('#userForm')
  if (userForm) {
    addTrackedListener(userForm, 'submit', handleFormSubmit)
  }

  // Delete confirm
  addTrackedListener(container.querySelector('#deleteConfirm'), 'click', handleDeleteConfirm)

  // Close modals on overlay click
  if (userModal) {
    addTrackedListener(userModal, 'click', (e) => {
      if (e.target === userModal) closeModal()
    })
  }
  if (deleteModal) {
    addTrackedListener(deleteModal, 'click', (e) => {
      if (e.target === deleteModal) closeDeleteModal()
    })
  }

  // Close modals on Escape key
  documentKeydownHandler = (e) => {
    if (e.key === 'Escape') {
      closeModal()
      closeDeleteModal()
    }
  }
  document.addEventListener('keydown', documentKeydownHandler)

  // Initialize roles multi-select dropdown
  initRolesMultiSelect()

  // Setup password visibility toggle
  setupPasswordToggle()
}

// ===================
// Page Lifecycle
// ===================

/**
 * Mount admin users page into container
 * @param {HTMLElement} containerEl - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(containerEl, { params, route } = {}) {
  console.log('[AdminUsersPage] Mounting...')

  // Store container reference
  container = containerEl

  // Check if user is authenticated and is admin
  const user = await checkAuth({ redirect: true })
  if (!user) {
    return
  }

  if (!user.is_admin) {
    showToast(t('admin.accessDenied'), 'error')
    window.location.href = '/'
    return
  }

  // Load page-specific CSS
  const cssLink = document.createElement('link')
  cssLink.rel = 'stylesheet'
  cssLink.href = '/styles/admin-users.css'
  cssLink.id = 'admin-users-page-css'
  document.head.appendChild(cssLink)

  // Render HTML
  container.innerHTML = PAGE_HTML

  // Cache DOM elements
  usersTableBody = container.querySelector('#usersTableBody')
  paginationInfo = container.querySelector('#paginationInfo')
  paginationBtns = container.querySelector('#paginationBtns')
  userModal = container.querySelector('#userModal')
  deleteModal = container.querySelector('#deleteModal')

  // Setup event listeners
  setupEventListeners()

  // Update i18n translations
  updateContainerTranslations(container)

  // Load initial data
  await loadUsers(currentPage, currentSearch)

  mounted = true
  console.log('[AdminUsersPage] Mounted')
}

/**
 * Unmount admin users page - cleanup
 */
export async function unmount() {
  console.log('[AdminUsersPage] Unmounting...')

  // Clear search debounce timer
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }

  // Remove document-level event listeners
  if (documentClickHandler) {
    document.removeEventListener('click', documentClickHandler)
    documentClickHandler = null
  }
  if (documentKeydownHandler) {
    document.removeEventListener('keydown', documentKeydownHandler)
    documentKeydownHandler = null
  }

  // Cleanup tracked event listeners
  eventCleanupFns.forEach(fn => fn())
  eventCleanupFns = []

  // Remove dynamically loaded CSS
  document.getElementById('admin-users-page-css')?.remove()

  // Reset state
  currentPage = 1
  currentSearch = ''
  totalUsers = 0
  usersTableBody = null
  paginationInfo = null
  paginationBtns = null
  searchInput = null
  userModal = null
  deleteModal = null
  container = null
  mounted = false

  console.log('[AdminUsersPage] Unmounted')
}

export default { mount, unmount }
