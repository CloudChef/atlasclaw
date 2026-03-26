/**
 * toast.js - Toast Notification Component
 *
 * Provides:
 * - showToast(message, type, duration) - Display a toast notification
 *
 * Types: success, error, info, warning
 */

// Toast container ID
const TOAST_CONTAINER_ID = 'toast-container'

// Default duration in milliseconds
const DEFAULT_DURATION = 3000

// Toast type icons
const TOAST_ICONS = {
  success: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="20 6 9 17 4 12"></polyline>
  </svg>`,
  error: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="15" y1="9" x2="9" y2="15"></line>
    <line x1="9" y1="9" x2="15" y2="15"></line>
  </svg>`,
  info: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="12" y1="16" x2="12" y2="12"></line>
    <line x1="12" y1="8" x2="12.01" y2="8"></line>
  </svg>`,
  warning: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
    <line x1="12" y1="9" x2="12" y2="13"></line>
    <line x1="12" y1="17" x2="12.01" y2="17"></line>
  </svg>`
}

/**
 * Ensure toast container exists in DOM
 * @returns {HTMLElement}
 */
function ensureContainer() {
  let container = document.getElementById(TOAST_CONTAINER_ID)

  if (!container) {
    container = document.createElement('div')
    container.id = TOAST_CONTAINER_ID
    container.className = 'toast-container'
    document.body.appendChild(container)
  }

  return container
}

/**
 * Show a toast notification
 * @param {string} message - Message to display
 * @param {'success'|'error'|'info'|'warning'} [type='info'] - Toast type
 * @param {number} [duration=3000] - Auto-dismiss duration in ms
 * @returns {HTMLElement} - The toast element
 */
export function showToast(message, type = 'info', duration = DEFAULT_DURATION) {
  const container = ensureContainer()

  // Create toast element
  const toast = document.createElement('div')
  toast.className = `toast toast-${type}`
  toast.setAttribute('role', 'alert')
  toast.setAttribute('aria-live', 'polite')

  // Get icon for type
  const icon = TOAST_ICONS[type] || TOAST_ICONS.info

  // Build toast content
  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <span class="toast-message">${escapeHtml(message)}</span>
    <button class="toast-close" type="button" aria-label="Close">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  `

  // Add close button handler
  const closeBtn = toast.querySelector('.toast-close')
  if (closeBtn) {
    closeBtn.addEventListener('click', () => dismissToast(toast))
  }

  // Add to container
  container.appendChild(toast)

  // Trigger animation (force reflow)
  toast.offsetHeight
  toast.classList.add('toast-show')

  // Auto dismiss
  if (duration > 0) {
    setTimeout(() => dismissToast(toast), duration)
  }

  return toast
}

/**
 * Dismiss a toast
 * @param {HTMLElement} toast - Toast element to dismiss
 */
export function dismissToast(toast) {
  if (!toast || !toast.parentNode) return

  // Add exit animation class
  toast.classList.remove('toast-show')
  toast.classList.add('toast-hide')

  // Remove after animation
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast)
    }
  }, 300)
}

/**
 * Clear all toasts
 */
export function clearAllToasts() {
  const container = document.getElementById(TOAST_CONTAINER_ID)
  if (container) {
    container.innerHTML = ''
  }
}

/**
 * Escape HTML special characters
 * @param {string} str - String to escape
 * @returns {string}
 */
function escapeHtml(str) {
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

// Convenience methods
export const toast = {
  success: (message, duration) => showToast(message, 'success', duration),
  error: (message, duration) => showToast(message, 'error', duration),
  info: (message, duration) => showToast(message, 'info', duration),
  warning: (message, duration) => showToast(message, 'warning', duration),
  clear: clearAllToasts
}

export default {
  showToast,
  dismissToast,
  clearAllToasts,
  toast
}
