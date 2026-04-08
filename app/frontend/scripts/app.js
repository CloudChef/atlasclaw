/**
 * app.js - SPA Application Shell
 *
 * Central application initialization for the SPA.
 * Handles auth, i18n, layout rendering, and router setup.
 */

import { createRouter } from './router.js'
import { installAuthFetchInterceptor, checkAuth } from './auth.js'
import { loadConfig, stripBasePath, buildAppUrl } from './config.js'
import { initI18n, updatePageTranslations, updateContainerTranslations } from './i18n.js'
import { renderSidebar } from './components/sidebar.js'
import { renderHeader, updateHeaderTitle, updateHeaderTitleText } from './components/header.js'
import { showToast } from './components/toast.js'
import { getAgentInfo } from './api-client.js'

/**
 * Route table - lazy loaded page modules
 * Each route has:
 * - path: URL path pattern
 * - loader: Dynamic import function for page module
 * - auth: Whether auth is required (always true for non-login pages)
 * - admin: Whether admin privileges are required
 * - title: i18n key for page title
 */
const routes = [
  {
    path: '/',
    loader: () => import('./pages/chat.js'),
    auth: true,
    title: 'app.title'
  },
  {
    path: '/channels',
    loader: () => import('./pages/channels.js'),
    auth: true,
    title: 'channel.title'
  },
  {
    path: '/account',
    loader: () => import('./pages/account-settings.js'),
    auth: true,
    title: 'account.title'
  },
  {
    path: '/models',
    loader: () => import('./pages/models.js'),
    auth: true,
    admin: true,
    title: 'model.pageTitle'
  },
  {
    path: '/admin/users',
    loader: () => import('./pages/admin-users.js'),
    auth: true,
    admin: true,
    title: 'admin.title'
  }
]

// Store auth info globally for components that need it
let currentAuthInfo = null
let currentAgentInfo = null

/**
 * Get current authenticated user info
 * @returns {Object|null}
 */
export function getAuthInfo() {
  return currentAuthInfo
}

function enforceRouteAccess(route) {
  if (!route?.admin) {
    return true
  }

  if (currentAuthInfo?.is_admin === true) {
    return true
  }

  showToast('Access denied. Admin privileges required.', 'error')

  if (window.__spaRouter) {
    setTimeout(() => {
      window.__spaRouter?.navigate('/', { replace: true })
    }, 0)
  } else {
    window.location.replace(buildAppUrl('/'))
  }

  return false
}

/**
 * Initialize the SPA application
 */
export async function initApp() {
  console.log('[App] Initializing SPA...')

  try {
    const embeddedMode = applyEmbeddedMode()

    // 1. Install auth fetch interceptor
    installAuthFetchInterceptor()

    // 2. Check auth (redirect to login.html if not authenticated)
    const authInfo = await checkAuth({ redirect: true })
    if (!authInfo) {
      return
    }
    currentAuthInfo = authInfo
    if (!window.__atlasclawProfileSyncBound) {
      document.addEventListener('atlasclaw:user-profile-updated', (event) => {
        currentAuthInfo = {
          ...(currentAuthInfo || {}),
          ...(event.detail || {})
        }
      })
      window.__atlasclawProfileSyncBound = true
    }

    // 3. Load runtime config
    await loadConfig()

    // 4. Initialize i18n (non-blocking)
    try {
      await initI18n()
    } catch (i18nError) {
      console.warn('[App] i18n initialization failed:', i18nError)
      // Continue without i18n
    }

    // 4.1 Load agent display metadata for chat UI shell
    try {
      currentAgentInfo = await getAgentInfo()
    } catch (agentInfoError) {
      console.warn('[App] Failed to load agent info:', agentInfoError)
      currentAgentInfo = null
    }

    // 5. Render layout (sidebar + header)
    const sidebarContainer = document.getElementById('sidebar')
    const headerContainer = document.getElementById('app-header')

    if (sidebarContainer) {
      renderSidebar(sidebarContainer, { authInfo })
    }

    if (headerContainer) {
      renderHeader(headerContainer, { authInfo, embeddedMode })
      if (currentAgentInfo?.name) {
        updateHeaderTitleText(currentAgentInfo.name)
      }
    }

    updatePageTranslations()

    // 6. Setup global link interception for SPA navigation
    setupLinkInterception()

    // 7. Create and start router
    const router = createRouter(routes, {
      contentContainer: document.getElementById('page-content'),
      onBeforeRoute: (path, route) => {
        if (!enforceRouteAccess(route)) {
          return false
        }

        applyEmbeddedRouteMode(path, embeddedMode)

        // Update header title
        if (path === '/' && currentAgentInfo?.name) {
          updateHeaderTitleText(currentAgentInfo.name)
        } else if (route && route.title) {
          updateHeaderTitle(route.title)
        }
      },
      onAfterRoute: () => {
        // Update translations for dynamically loaded content
        const pageContent = document.getElementById('page-content')
        if (pageContent) {
          updateContainerTranslations(pageContent)
        }
      }
    })

    // Expose router globally for components that need it
    window.__spaRouter = router

    router.start()

    console.log('[App] SPA initialized successfully')
  } catch (error) {
    console.error('[App] SPA initialization failed:', error)
  }
}

/**
 * Setup global link interception for SPA navigation
 * Intercepts clicks on internal links and uses router.navigate instead of full page reload
 */
function setupLinkInterception() {
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]')
    if (!link) return

    const href = link.getAttribute('href')

    if (link.matches('[data-new-chat], .new-chat-btn')) {
      e.preventDefault()
      handleNewChatClick()
      return
    }

    // Skip navigation for:
    // - Empty or no href
    // - External links (http://, https://, //)
    // - New tab links (target="_blank")
    // - API endpoints
    // - Hash links
    // - Login page links
    // - Download links
    if (!href ||
        href.startsWith('http') ||
        href.startsWith('//') ||
        link.target === '_blank' ||
        href.startsWith('/api/') ||
        href.startsWith('#') ||
        href.includes('login') ||
        link.hasAttribute('download')) {
      return
    }

    e.preventDefault()
    window.__spaRouter?.navigate(href)
  })
}

async function handleNewChatClick() {
  try {
    const { startNewSession } = await import('./session-manager.js')
    await startNewSession(true, { channel: 'web', chatType: 'dm' })
    window.__spaRouter?.navigate('/', { replace: stripBasePath(window.location.pathname) === '/' })
  } catch (error) {
    console.error('[App] Failed to start new chat:', error)
  }
}

function applyEmbeddedMode() {
  const embeddedMode = isEmbeddedMode()
  window.__atlasclawEmbeddedMode = embeddedMode
  document.documentElement.classList.toggle('atlas-embedded-mode', embeddedMode)
  document.body.classList.toggle('atlas-embedded-mode', embeddedMode)
  applyEmbeddedRouteMode(window.location.pathname, embeddedMode)
  return embeddedMode
}

function applyEmbeddedRouteMode(path, embeddedMode = isEmbeddedMode()) {
  const chatEmbeddedMode = embeddedMode && isChatEmbeddedPath(path)
  const configEmbeddedMode = embeddedMode && isConfigEmbeddedPath(path)

  window.__atlasclawChatEmbeddedMode = chatEmbeddedMode
  window.__atlasclawConfigEmbeddedMode = configEmbeddedMode
  document.documentElement.classList.toggle('atlas-chat-embedded-mode', chatEmbeddedMode)
  document.body.classList.toggle('atlas-chat-embedded-mode', chatEmbeddedMode)
  document.documentElement.classList.toggle('atlas-config-embedded-mode', configEmbeddedMode)
  document.body.classList.toggle('atlas-config-embedded-mode', configEmbeddedMode)
}

function getLogicalPath(path) {
  const strippedPath = stripBasePath(String(path || window.location.pathname || '').split(/[?#]/, 1)[0] || '/')
  if (!strippedPath || strippedPath === '') {
    return '/'
  }
  return strippedPath === '/' ? '/' : strippedPath.replace(/\/$/, '')
}

function isChatEmbeddedPath(path) {
  return getLogicalPath(path) === '/'
}

function isConfigEmbeddedPath(path) {
  const logicalPath = getLogicalPath(path)
  const pageName = logicalPath === '/' ? '' : logicalPath.split('/').pop()
  return pageName === 'models' || pageName === 'channels'
}

function isEmbeddedMode() {
  const params = new URLSearchParams(window.location.search)
  const explicitMode = params.get('embedded') || params.get('embed') || params.get('iframe')
  const normalizedMode = String(explicitMode || '').trim().toLowerCase()

  if (['1', 'true', 'yes'].includes(normalizedMode)) {
    return true
  }
  if (['0', 'false', 'no'].includes(normalizedMode)) {
    return false
  }

  try {
    return window.self !== window.top
  } catch (error) {
    return true
  }
}

/**
 * Get the router instance
 * @returns {Object|null}
 */
export function getRouter() {
  return window.__spaRouter || null
}

export default { initApp, getAuthInfo, getRouter }
