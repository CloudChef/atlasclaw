/*
 *  Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * provider-nav.js - Provider-contributed navigation entries.
 *
 * Fetches /api/ui/extensions and renders entries in the left sidebar.
 * Entries are server-filtered by auth and provider access policy.
 */

import { buildAppUrl, stripBasePath } from '../config.js'
import { getCurrentLocale, translateIfExists } from '../i18n.js'

let itemsCache = null

function normalizePath(path) {
  const logical = stripBasePath(String(path || '/').split(/[?#]/, 1)[0] || '/')
  return logical === '/' ? '/' : logical.replace(/\/$/, '')
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, '&#96;')
}

/** Fetch provider navigation entries once per page lifetime. */
export async function fetchProviderNav(locale = getCurrentLocale()) {
  if (itemsCache) return itemsCache
  try {
    const response = await fetch(
      buildAppUrl(`/api/ui/extensions?locale=${encodeURIComponent(locale)}`),
      { credentials: 'same-origin' }
    )
    if (!response.ok) {
      itemsCache = []
      return itemsCache
    }
    const payload = await response.json()
    itemsCache = Array.isArray(payload?.items) ? payload.items : []
  } catch (error) {
    console.warn('[ProviderNav] failed to load extensions:', error)
    itemsCache = []
  }
  return itemsCache
}

/** Render provider navigation entries into a container. */
export function renderProviderNav(container, items = [], activePath = window.location.pathname) {
  if (!container) return
  if (!items.length) {
    container.innerHTML = ''
    container.hidden = true
    return
  }
  container.hidden = false
  const active = normalizePath(activePath)
  container.innerHTML = items.map(item => {
    const route = normalizePath(item.route)
    const isActive = route === active
    const title = item.title || item.nav_id || ''
    return `
      <a class="provider-nav-link${isActive ? ' active' : ''}"
         href="${escapeAttribute(buildAppUrl(item.route))}"
         data-provider-nav="${escapeAttribute(item.route)}"
         aria-current="${isActive ? 'page' : 'false'}">
        <span class="provider-nav-label">${escapeHtml(title)}</span>
      </a>
    `
  }).join('')

  container.querySelectorAll('[data-provider-nav]').forEach(link => {
    link.addEventListener('click', event => {
      event.preventDefault()
      const route = link.getAttribute('data-provider-nav')
      if (window.__spaRouter?.navigate) {
        window.__spaRouter.navigate(buildAppUrl(route))
      } else {
        window.location.href = buildAppUrl(route)
      }
    })
  })
}

/** Fetch and render provider navigation; returns the loaded items. */
export async function setupProviderNav(container, activePath = window.location.pathname) {
  if (!container) return []
  const items = await fetchProviderNav()
  renderProviderNav(container, items, activePath)
  return items
}

/** Clear the cache after login/logout or permission changes. */
export function clearProviderNavCache() {
  itemsCache = null
}

export const providerNavText = {
  loadFailed: () => translateIfExists('ext.loadFailed') || 'Unable to load provider page.',
}
