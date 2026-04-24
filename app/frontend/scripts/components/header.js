/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * header.js - Header Component
 *
 * Provides:
 * - renderHeader(container, { authInfo }) - Render centered title + user dropdown menu
 * - updateHeaderTitle(titleKey) - Update page title using i18n key
 */
import { t } from '../i18n.js'
import { buildAppUrl } from '../config.js'
import { logout } from '../auth.js'
import {
  canAccessChannelManagement,
  canAccessModelManagement,
  canAccessRoleManagement,
  canAccessUserManagement
} from '../permissions.js'

// Store reference to header element for updates
let headerElement = null
let titleElement = null
let dropdownAbortController = null
let currentHeaderAuthInfo = null

// SVG Icons
const ICONS = {
  account: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="8" r="5"/></svg>',
  models: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
  providers: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7H4"/><path d="M20 12H4"/><path d="M20 17H4"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
  channels: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.32 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  users: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  newChat: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M21 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-5 4V5.5A2.5 2.5 0 0 1 5.5 3h8"/><path d="M18 3v6"/><path d="M15 6h6"/></svg>',
  roles: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 6v6c0 5 3.4 8.6 7 9 3.6-.4 7-4 7-9V6l-7-3Z"/><path d="m9.5 12 1.7 1.7 3.8-4"/></svg>',
  logout: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>'
}

/**
 * Cleanup dropdown event listeners
 */
function cleanupDropdownListeners() {
  if (dropdownAbortController) {
    dropdownAbortController.abort()
    dropdownAbortController = null
  }
}

/**
 * Render header into container
 * @param {HTMLElement} container - Container element
 */
export function renderHeader(container, { authInfo, embeddedMode = isEmbeddedMode() } = {}) {
  if (!container) {
    console.warn('[Header] No container provided')
    return
  }

  cleanupDropdownListeners()
  headerElement = container
  currentHeaderAuthInfo = { ...(authInfo || {}) }

  const displayName = currentHeaderAuthInfo.display_name || currentHeaderAuthInfo.username || 'User'
  const initial = displayName.trim().charAt(0).toUpperCase() || 'U'
  const isAdmin = currentHeaderAuthInfo.is_admin === true
  const newChatLabel = translateOrFallback('app.newChat', 'New Chat')
  const canAccessUsers = canAccessUserManagement(currentHeaderAuthInfo)
  const canAccessRoles = canAccessRoleManagement(currentHeaderAuthInfo)
  const canAccessModels = canAccessModelManagement(currentHeaderAuthInfo)
  const canAccessChannels = canAccessChannelManagement(currentHeaderAuthInfo)
  const hasAdminNavigation = canAccessUsers || canAccessRoles || canAccessModels || canAccessChannels
  const roleText = isAdmin
    ? translateOrFallback('user.roleAdmin', 'Administrator')
    : translateOrFallback('user.roleUser', 'User')

  container.innerHTML = `
    <div class="chat-header-leading">
      ${embeddedMode ? `
      <a href="${buildAppUrl('/')}" class="embedded-new-chat-btn" data-new-chat aria-label="${escapeHtml(newChatLabel)}" title="${escapeHtml(newChatLabel)}">
        ${ICONS.newChat}
      </a>
      ` : ''}
    </div>
    <h1 id="page-title" class="chat-header-title" data-i18n="app.title">AtlasClaw</h1>
    <div class="header-actions">
      <div class="user-menu-container">
        <button class="user-avatar-btn" id="userAvatarBtn" title="${escapeHtml(displayName)}">
          ${renderUserAvatar(currentHeaderAuthInfo, displayName, initial)}
        </button>
        <div class="user-dropdown hidden" id="userDropdown">
          <div class="dropdown-header">
            <span class="dropdown-username">${escapeHtml(displayName)}</span>
            <span class="dropdown-role">${escapeHtml(roleText)}</span>
          </div>
          <div class="dropdown-divider"></div>
          <a href="${buildAppUrl('/account')}" class="dropdown-item" data-nav-link>
            ${ICONS.account} ${translateOrFallback('nav.account', 'Account Settings')}
          </a>
          ${hasAdminNavigation ? `
          <div class="dropdown-divider" data-admin-only></div>
          ${canAccessUsers ? `<a href="${buildAppUrl('/admin/users')}" class="dropdown-item" data-admin-only data-nav-link>
            ${ICONS.users} ${translateOrFallback('nav.users', 'User Management')}
          </a>` : ''}
          ${canAccessRoles ? `<a href="${buildAppUrl('/admin/roles')}" class="dropdown-item" data-admin-only data-nav-link>
            ${ICONS.roles} ${translateOrFallback('nav.roles', 'Role Management')}
          </a>` : ''}
          ${canAccessModels ? `<a href="${buildAppUrl('/models')}" class="dropdown-item" data-admin-only data-nav-link>
            ${ICONS.models} ${translateOrFallback('nav.models', 'Model Management')}
          </a>` : ''}
          ${canAccessChannels ? `<a href="${buildAppUrl('/channels')}" class="dropdown-item" data-admin-only data-nav-link>
            ${ICONS.channels} ${translateOrFallback('nav.channels', 'Channel Management')}
          </a>` : ''}
          ` : ''}
          <div class="dropdown-divider"></div>
          <a class="dropdown-item dropdown-item-danger" id="btnLogout">
            ${ICONS.logout} ${translateOrFallback('auth.logout', 'Sign Out')}
          </a>
        </div>
      </div>
    </div>
  `

  titleElement = container.querySelector('#page-title')
  setupDropdownListeners()
}

/**
 * Setup dropdown menu event listeners
 */
function setupDropdownListeners() {
  const avatarBtn = document.getElementById('userAvatarBtn')
  const dropdown = document.getElementById('userDropdown')
  const logoutBtn = document.getElementById('btnLogout')

  if (!avatarBtn || !dropdown) return

  dropdownAbortController = new AbortController()
  const signal = dropdownAbortController.signal

  avatarBtn.addEventListener('click', (e) => {
    e.stopPropagation()
    dropdown.classList.toggle('hidden')
  }, { signal })

  document.addEventListener('click', (e) => {
    const userMenuContainer = document.querySelector('.user-menu-container')
    if (userMenuContainer && !userMenuContainer.contains(e.target)) {
      dropdown.classList.add('hidden')
    }
  }, { signal })

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      dropdown.classList.add('hidden')
    }
  }, { signal })

  document.addEventListener('atlasclaw:user-profile-updated', (event) => {
    updateHeaderUser(event.detail || {})
  }, { signal })

  dropdown.querySelectorAll('[data-nav-link]').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault()
      const href = link.getAttribute('href')
      dropdown.classList.add('hidden')
      if (window.__spaRouter && typeof window.__spaRouter.navigate === 'function') {
        window.__spaRouter.navigate(href)
      } else {
        window.location.href = href
      }
    }, { signal })
  })

  if (logoutBtn) {
    logoutBtn.addEventListener('click', async (e) => {
      e.preventDefault()
      dropdown.classList.add('hidden')
      await logout()
    }, { signal })
  }
}

export function updateHeaderUser(authInfo = {}) {
  if (!headerElement) {
    currentHeaderAuthInfo = { ...currentHeaderAuthInfo, ...authInfo }
    return
  }

  const titleSnapshot = {
    key: titleElement?.getAttribute('data-i18n') || '',
    text: titleElement?.textContent || ''
  }

  renderHeader(headerElement, {
    authInfo: {
      ...currentHeaderAuthInfo,
      ...authInfo
    }
  })

  if (titleSnapshot.key) {
    updateHeaderTitle(titleSnapshot.key)
  } else if (titleSnapshot.text) {
    updateHeaderTitleText(titleSnapshot.text)
  }
}

export function updateHeaderTitleText(titleText) {
  if (!titleElement) {
    titleElement = document.getElementById('page-title')
  }

  if (!titleElement) {
    return
  }

  titleElement.removeAttribute('data-i18n')
  titleElement.textContent = titleText || 'AtlasClaw'
  document.title = titleElement.textContent
}

/**
 * Update header title
 * @param {string} titleKey - i18n key for title
 */
export function updateHeaderTitle(titleKey) {
  if (!titleElement) {
    titleElement = document.getElementById('page-title')
  }

  if (titleElement) {
    titleElement.setAttribute('data-i18n', titleKey)

    const translated = t(titleKey)
    if (translated && translated !== titleKey) {
      titleElement.textContent = translated
    } else {
      titleElement.textContent = getDefaultTitle(titleKey)
    }

    document.title = `${titleElement.textContent} - AtlasClaw`
  }
}

/**
 * Get default title for i18n key (fallback before translations load)
 * @param {string} key - i18n key
 * @returns {string}
 */
function getDefaultTitle(key) {
  const defaults = {
    'app.title': 'AtlasClaw',
    'app.chatTitle': 'Chat',
    'account.title': 'Account Settings',
    'channel.title': 'Channel Management',
    'provider.title': 'Authentication Configuration',
    'model.pageTitle': 'Model Management',
    'admin.title': 'User Management',
    'app.channels': 'Channels',
    'app.models': 'Models'
  }
  return defaults[key] || key.split('.').pop()
}

/**
 * Get header element
 * @returns {HTMLElement|null}
 */
export function getHeaderElement() {
  return headerElement
}

/**
 * Cleanup header resources
 */
export function cleanupHeader() {
  cleanupDropdownListeners()
}

export default {
  renderHeader,
  updateHeaderUser,
  updateHeaderTitle,
  updateHeaderTitleText,
  getHeaderElement,
  cleanupHeader
}

function translateOrFallback(key, fallback) {
  const translated = t(key)
  return translated === key ? fallback : translated
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function renderUserAvatar(authInfo, displayName, initial) {
  if (authInfo?.avatar_url) {
    return `<img class="user-avatar user-avatar-image" src="${escapeHtml(authInfo.avatar_url)}" alt="${escapeHtml(displayName)}">`
  }

  return `<span class="user-avatar user-avatar-text">${escapeHtml(initial)}</span>`
}

function isEmbeddedMode() {
  if (typeof window.__atlasclawEmbeddedMode === 'boolean') {
    return window.__atlasclawEmbeddedMode
  }

  try {
    return window.self !== window.top
  } catch (error) {
    return true
  }
}
