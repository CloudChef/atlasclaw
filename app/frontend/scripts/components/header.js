/**
 * header.js - Header Component
 *
 * Provides:
 * - renderHeader(container) - Render header skeleton + logout button
 * - updateHeaderTitle(titleKey) - Update page title using i18n key
 */

import { logout } from '../auth.js'
import { t } from '../i18n.js'

// Logout icon SVG
const LOGOUT_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
  <polyline points="16 17 21 12 16 7"></polyline>
  <line x1="21" y1="12" x2="9" y2="12"></line>
</svg>`

// Store reference to header element for updates
let headerElement = null
let titleElement = null

/**
 * Render header into container
 * @param {HTMLElement} container - Container element
 */
export function renderHeader(container) {
  if (!container) {
    console.warn('[Header] No container provided')
    return
  }

  headerElement = container

  // Render header HTML
  container.innerHTML = `
    <h1 id="page-title" data-i18n="app.title">Xuanwu</h1>
    <div class="header-actions">
      <button id="logoutBtn" class="logout-btn" type="button" data-i18n-title="logout.title" data-i18n-aria-label="logout.title" title="Logout" aria-label="Logout">
        ${LOGOUT_ICON}
      </button>
    </div>
  `

  titleElement = container.querySelector('#page-title')

  // Bind logout button
  const logoutBtn = container.querySelector('#logoutBtn')
  if (logoutBtn) {
    logoutBtn.addEventListener('click', handleLogout)
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
  titleElement.textContent = titleText || 'Xuanwu'
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
    // Update the data-i18n attribute
    titleElement.setAttribute('data-i18n', titleKey)

    // Try to get translated text
    const translated = t(titleKey)
    if (translated && translated !== titleKey) {
      titleElement.textContent = translated
    } else {
      // Fallback to key's last part
      titleElement.textContent = getDefaultTitle(titleKey)
    }

    // Also update document title
    document.title = titleElement.textContent + ' - Xuanwu'
  }
}

/**
 * Handle logout button click
 */
async function handleLogout() {
  try {
    await logout({ redirect: true })
  } catch (error) {
    console.error('[Header] Logout failed:', error)
    // Fallback: redirect to login anyway
    window.location.href = '/login.html'
  }
}

/**
 * Get default title for i18n key (fallback before translations load)
 * @param {string} key - i18n key
 * @returns {string}
 */
function getDefaultTitle(key) {
  const defaults = {
    'app.title': 'Xuanwu',
    'app.chatTitle': 'Chat',
    'channel.title': 'Channel Management',
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

export default {
  renderHeader,
  updateHeaderTitle,
  updateHeaderTitleText,
  getHeaderElement
}
