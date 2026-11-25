/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * sidebar.js - Sidebar Component
 *
 * Provides:
 * - renderSidebar(container, { authInfo }) - Render sidebar to container
 * - updateSidebarActive(path) - Toggle the back-to-chat shortcut for non-chat pages
 */

import { buildAppUrl, stripBasePath } from '../config.js'

// SVG icons for sidebar
const ICONS = {
  back: `<svg width="16" height="16" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="m15 18-6-6 6-6"></path>
    <path d="M21 12H9"></path>
  </svg>`,
  chat: `<svg width="18" height="18" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>`
}

// Store reference to sidebar element for updates
let sidebarElement = null
let currentAuthInfo = null

function normalizeSidebarPath(path) {
  const logicalPath = stripBasePath(String(path || window.location.pathname || '').split(/[?#]/, 1)[0] || '/')
  if (!logicalPath || logicalPath === '') {
    return '/'
  }
  return logicalPath === '/' ? '/' : logicalPath.replace(/\/$/, '')
}

function shouldShowBackToChat(path) {
  return normalizeSidebarPath(path) !== '/'
}

function syncHeaderActionState(path) {
  if (!sidebarElement) {
    return
  }

  const backLink = sidebarElement.querySelector('[data-sidebar-back]')
  const newChatLink = sidebarElement.querySelector('[data-new-chat]')
  if (!backLink || !newChatLink) {
    return
  }

  const showBackLink = shouldShowBackToChat(path)
  const showNewChat = !showBackLink

  backLink.classList.toggle('sidebar-back-link-hidden', !showBackLink)
  backLink.setAttribute('aria-hidden', showBackLink ? 'false' : 'true')
  backLink.tabIndex = showBackLink ? 0 : -1

  newChatLink.classList.toggle('sidebar-new-chat-hidden', !showNewChat)
  newChatLink.setAttribute('aria-hidden', showNewChat ? 'false' : 'true')
  newChatLink.tabIndex = showNewChat ? 0 : -1
}

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
      <a href="${buildAppUrl('/')}" class="sidebar-primary-action sidebar-back-link sidebar-back-link-hidden" data-sidebar-back data-nav-path="/">
        ${ICONS.back}
        <span data-i18n="app.backToChat">${getDefaultText('app.backToChat')}</span>
      </a>
      <a href="${buildAppUrl('/')}" class="sidebar-primary-action new-chat-btn" data-nav-path="/" data-new-chat>
        ${ICONS.chat}
        <span data-i18n="app.newChat">New Chat</span>
      </a>
    </div>
    <nav class="sidebar-provider-nav" id="sidebar-provider-nav" aria-label="Provider extensions" hidden></nav>
    <div class="sidebar-content" id="sidebar-dynamic-content">
      <!-- Dynamic content area - can be filled by page modules -->
    </div>
  `

  syncHeaderActionState(window.location.pathname)
}

/**
 * Update active state of sidebar navigation links
 * No-op - navigation moved to Header user dropdown (Task 19)
 * Kept for backward compatibility with app.js import
 * @param {string} path - Current route path (unused)
 */
export function updateSidebarActive(path) {
  syncHeaderActionState(path)
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
    'app.backToChat': 'Back to Chat',
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
