/*
 *  Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * provider-extension.js - Host page for provider UI extensions.
 *
 * Renders a same-origin iframe served by /api/ui/extensions and bridges
 * init/ready/navigation/toast messages with the provider page (Standard v1).
 */

import { getAuthInfo } from '../app.js'
import { installAuthFetchInterceptor, redirectToLogin } from '../auth.js'
import { buildAppUrl, stripBasePath } from '../config.js'
import { getCurrentLocale, translateIfExists } from '../i18n.js'
import { showToast } from '../components/toast.js'

const READY_TIMEOUT_MS = 5000
const BRIDGE_VERSION = 1

let mountState = null

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function parseRoute(params = {}) {
  const providerType = String(params.providerType || '').trim()
  const navId = String(params.navId || '').trim()
  if (providerType && navId) return { providerType, navId }
  const parts = stripBasePath(window.location.pathname).split('/').filter(Boolean)
  if (parts[0] === 'ext' && parts.length >= 3) {
    return { providerType: decodeURIComponent(parts[1]), navId: decodeURIComponent(parts[2]) }
  }
  return { providerType: '', navId: '' }
}

function currentTheme() {
  return document.documentElement.getAttribute('data-theme')
    || localStorage.getItem('atlasclaw-theme')
    || 'light'
}

function buildInitPayload(providerType, navId) {
  const auth = getAuthInfo() || {}
  const user = auth.user || auth
  return {
    type: 'atlasclaw:init',
    version: BRIDGE_VERSION,
    locale: getCurrentLocale(),
    theme: currentTheme(),
    basePath: buildAppUrl('/').replace(/\/$/, ''),
    provider: { type: providerType, navId },
    user: {
      id: String(user.user_id || user.id || ''),
      displayName: String(user.display_name || user.displayName || ''),
      isAdmin: Boolean(user.is_admin || user.isAdmin),
      roles: Array.isArray(user.roles) ? user.roles : []
    }
  }
}

function showFailure(container, item) {
  const title = item?.title || item?.nav_id || ''
  const message = translateIfExists('ext.loadFailed') || 'Unable to load the provider page.'
  const retry = translateIfExists('ext.retry') || 'Retry'
  container.innerHTML = `
    <div class="provider-extension-failed">
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      <button type="button" class="provider-extension-retry">${escapeHtml(retry)}</button>
    </div>
  `
  container.querySelector('.provider-extension-retry')?.addEventListener('click', () => {
    window.location.reload()
  })
}

function showSlowLoadState(state) {
  if (mountState !== state || state.ready) return
  const loading = state.container.querySelector('.provider-extension-loading')
  if (!loading) return
  const message = loading.querySelector('.provider-extension-loading-message')
  if (message) {
    message.textContent = translateIfExists('ext.stillLoading')
      || 'This page is taking longer than expected.'
  }
  if (loading.querySelector('.provider-extension-retry')) return
  const retry = document.createElement('button')
  retry.type = 'button'
  retry.className = 'provider-extension-retry'
  retry.textContent = translateIfExists('ext.retry') || 'Retry'
  retry.addEventListener('click', () => {
    if (mountState !== state || state.ready) return
    state.ready = false
    if (message) message.textContent = translateIfExists('ext.loading') || 'Loading…'
    retry.remove()
    armReadyTimer(state)
    state.frame.src = state.entryUrl
  })
  loading.append(retry)
}

function armReadyTimer(state) {
  window.clearTimeout(state.readyTimer)
  state.readyTimer = window.setTimeout(() => showSlowLoadState(state), READY_TIMEOUT_MS)
}

function disposeMountState() {
  if (!mountState) return
  const { onMessage, frame, onFrameLoad, onFrameError, readyTimer } = mountState
  const oldState = mountState
  mountState = null
  window.clearTimeout(readyTimer)
  window.removeEventListener('message', onMessage)
  frame?.removeEventListener('load', onFrameLoad)
  frame?.removeEventListener('error', onFrameError)
  frame?.remove()
  oldState.ready = true
}

export async function mount(container, context = {}) {
  disposeMountState()
  const { providerType, navId } = parseRoute(context.params)
  if (!providerType || !navId) {
    showFailure(container, {})
    return
  }

  container.innerHTML = `
    <div class="provider-extension-host">
      <div class="provider-extension-loading" role="status">
        <span class="provider-extension-spinner" aria-hidden="true"></span>
        <span class="provider-extension-loading-message">${escapeHtml(translateIfExists('ext.loading') || 'Loading…')}</span>
      </div>
      <iframe class="provider-extension-frame"
              title="${escapeHtml(navId)}"
              sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-modals allow-downloads"
              referrerpolicy="same-origin"></iframe>
    </div>
  `
  const frame = container.querySelector('.provider-extension-frame')
  const loading = container.querySelector('.provider-extension-loading')
  const entryUrl = buildAppUrl(
    `/api/ui/extensions/${encodeURIComponent(providerType)}/${encodeURIComponent(navId)}/`
  )

  const state = {
    container,
    frame,
    entryUrl,
    ready: false,
    readyTimer: null,
    onMessage: null,
    onFrameLoad: null,
    onFrameError: null
  }

  const onMessage = event => {
    if (mountState !== state) return
    if (event.origin !== window.location.origin) return
    if (!frame || event.source !== frame.contentWindow) return
    const data = event.data
    if (!data || typeof data !== 'object') return

    if (data.type === 'atlasclaw:ready') {
      state.ready = true
      window.clearTimeout(state.readyTimer)
      loading?.remove()
      return
    }
    if (data.type === 'atlasclaw:title' && data.title) {
      document.title = String(data.title)
      return
    }
    if (data.type === 'atlasclaw:toast' && data.message) {
      const level = ['success', 'error', 'info'].includes(data.level) ? data.level : 'info'
      showToast(String(data.message), level)
      return
    }
    if (data.type === 'atlasclaw:navigate') {
      const route = String(data.route || '')
      if (route.startsWith('/') && window.__spaRouter?.navigate) {
        window.__spaRouter.navigate(buildAppUrl(route))
      }
      return
    }
    if (data.type === 'atlasclaw:close' && window.__spaRouter?.navigate) {
      window.__spaRouter.navigate(buildAppUrl('/'))
    }
  }

  const sendInit = () => {
    try {
      frame.contentWindow?.postMessage(
        buildInitPayload(providerType, navId),
        window.location.origin
      )
    } catch (error) {
      console.warn('[ProviderExtension] init failed:', error)
    }
  }

  const onFrameLoad = () => {
    if (mountState !== state) return
    try {
      const frameUrl = new URL(frame.contentWindow.location.href)
      if (frameUrl.origin !== window.location.origin && frameUrl.href !== 'about:blank') return
      installAuthFetchInterceptor(frame.contentWindow)
      if (stripBasePath(frameUrl.pathname) === '/login.html') {
        state.ready = true
        window.clearTimeout(state.readyTimer)
        redirectToLogin()
        return
      }
    } catch (_error) {
      return
    }
    sendInit()
  }

  const onFrameError = () => {
    if (mountState !== state) return
    disposeMountState()
    showFailure(container, { title: navId, nav_id: navId })
  }
  state.onMessage = onMessage
  state.onFrameLoad = onFrameLoad
  state.onFrameError = onFrameError
  mountState = state
  frame.addEventListener('load', onFrameLoad)
  frame.addEventListener('error', onFrameError)
  window.addEventListener('message', onMessage)
  armReadyTimer(state)
  frame.src = entryUrl
}

export function unmount() {
  disposeMountState()
}
