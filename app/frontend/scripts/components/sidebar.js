/**
 * sidebar.js - Sidebar Component
 *
 * Provides:
 * - renderSidebar(container, { authInfo }) - Render sidebar to container
 * - updateSidebarActive(path) - Highlight active link based on current route
 */

import { logout } from '../auth.js'

// SVG icons for sidebar navigation
const ICONS = {
  users: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
    <circle cx="9" cy="7" r="4"></circle>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
  </svg>`,
  settings: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="3"></circle>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
  </svg>`,
  grid: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
    <line x1="3" y1="9" x2="21" y2="9"></line>
    <line x1="9" y1="21" x2="9" y2="9"></line>
  </svg>`,
  chat: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>`
}

// Navigation items configuration
const NAV_ITEMS = [
  { path: '/admin/users', icon: 'users', i18nKey: 'app.userManagement', requireAdmin: true },
  { path: '/channels', icon: 'settings', i18nKey: 'app.channels', requireAdmin: false },
  { path: '/models', icon: 'grid', i18nKey: 'app.models', requireAdmin: false }
]

// Store reference to sidebar element for updates
let sidebarElement = null
let currentAuthInfo = null

/**
 * Render sidebar into container
 * @param {HTMLElement} container - Container element
 * @param {{ authInfo: Object }} options - Options with auth info
 */
export function renderSidebar(container, { authInfo } = {}) {
  if (!container) {
    console.warn('[Sidebar] No container provided')
    return
  }

  sidebarElement = container
  currentAuthInfo = authInfo

  const isAdmin = authInfo?.is_admin === true

  // Build navigation items HTML
  const navItemsHtml = NAV_ITEMS
    .filter(item => !item.requireAdmin || isAdmin)
    .map(item => `
      <a href="${item.path}" class="settings-btn" data-nav-path="${item.path}">
        ${ICONS[item.icon]}
        <span data-i18n="${item.i18nKey}">${getDefaultText(item.i18nKey)}</span>
      </a>
    `)
    .join('')

  // Render sidebar HTML
  container.innerHTML = `
    <div class="sidebar-header">
      <a href="/" class="new-chat-btn" data-nav-path="/">
        ${ICONS.chat}
        <span data-i18n="app.newChat">New Chat</span>
      </a>
    </div>
    <div class="sidebar-content" id="sidebar-dynamic-content">
      <!-- Dynamic content area - can be filled by page modules -->
    </div>
    <div class="sidebar-footer">
      ${navItemsHtml}
    </div>
  `
}

/**
 * Update active state of sidebar navigation links
 * @param {string} path - Current route path
 */
export function updateSidebarActive(path) {
  if (!sidebarElement) return

  // Remove active class from all nav links
  sidebarElement.querySelectorAll('[data-nav-path]').forEach(link => {
    link.classList.remove('active')
  })

  // Add active class to matching link
  const activeLink = sidebarElement.querySelector(`[data-nav-path="${path}"]`)
  if (activeLink) {
    activeLink.classList.add('active')
  }

  // Special case: for sub-paths, check parent paths
  if (!activeLink) {
    // Check if current path starts with any nav path
    sidebarElement.querySelectorAll('[data-nav-path]').forEach(link => {
      const navPath = link.getAttribute('data-nav-path')
      if (navPath !== '/' && path.startsWith(navPath)) {
        link.classList.add('active')
      }
    })
  }
}

/**
 * Get sidebar dynamic content container
 * @returns {HTMLElement|null}
 */
export function getSidebarContent() {
  return document.getElementById('sidebar-dynamic-content')
}

/**
 * Set sidebar dynamic content
 * @param {string|HTMLElement} content - HTML string or element
 */
export function setSidebarContent(content) {
  const container = getSidebarContent()
  if (!container) return

  if (typeof content === 'string') {
    container.innerHTML = content
  } else if (content instanceof HTMLElement) {
    container.innerHTML = ''
    container.appendChild(content)
  }
}

/**
 * Clear sidebar dynamic content
 */
export function clearSidebarContent() {
  const container = getSidebarContent()
  if (container) {
    container.innerHTML = ''
  }
}

/**
 * Get default text for i18n key (fallback before translations load)
 * @param {string} key - i18n key
 * @returns {string}
 */
function getDefaultText(key) {
  const defaults = {
    'app.newChat': 'New Chat',
    'app.userManagement': 'User Management',
    'app.channels': 'Channels',
    'app.models': 'Models',
    'channel.backToChat': 'Back to Chat'
  }
  return defaults[key] || key.split('.').pop()
}

export default {
  renderSidebar,
  updateSidebarActive,
  getSidebarContent,
  setSidebarContent,
  clearSidebarContent
}
