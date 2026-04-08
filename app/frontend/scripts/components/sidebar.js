/**
 * sidebar.js - Sidebar Component
 *
 * Provides:
 * - renderSidebar(container, { authInfo }) - Render sidebar to container
 * - updateSidebarActive(path) - No-op (kept for backward compatibility)
 */

import { buildAppUrl } from '../config.js'

// SVG icons for sidebar
const ICONS = {
  chat: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>`
}

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

  // Render sidebar HTML - header + dynamic content area only
  // Navigation buttons moved to Header user dropdown (Task 19)
  container.innerHTML = `
    <div class="sidebar-header">
      <a href="${buildAppUrl('/')}" class="new-chat-btn" data-nav-path="/" data-new-chat>
        ${ICONS.chat}
        <span data-i18n="app.newChat">New Chat</span>
      </a>
    </div>
    <div class="sidebar-content" id="sidebar-dynamic-content">
      <!-- Dynamic content area - can be filled by page modules -->
    </div>
  `
}

/**
 * Update active state of sidebar navigation links
 * No-op - navigation moved to Header user dropdown (Task 19)
 * Kept for backward compatibility with app.js import
 * @param {string} path - Current route path (unused)
 */
export function updateSidebarActive(path) {
  // No-op: sidebar navigation buttons removed
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
    'app.newChat': 'New Chat'
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
