/**
 * app.js - SPA Application Shell
 *
 * Central application initialization for the SPA.
 * Handles auth, i18n, layout rendering, and router setup.
 */

import { createRouter } from './router.js'
import { installAuthFetchInterceptor, checkAuth } from './auth.js'
import { loadConfig } from './config.js'
import { initI18n, updatePageTranslations, updateContainerTranslations } from './i18n.js'
import { renderSidebar } from './components/sidebar.js'
import { renderHeader, updateHeaderTitle, updateHeaderTitleText } from './components/header.js'
import { getAgentInfo } from './api-client.js'
import { applyEmbedModeClass } from './embed-mode.js'

/**
 * Route table - lazy loaded page modules
 * Each route has:
 * - path: URL path pattern
 * - loader: Dynamic import function for page module
 * - auth: Whether auth is required (always true for non-login pages)
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
    title: 'model.pageTitle'
  },
  {
    path: '/admin/users',
    loader: () => import('./pages/admin-users.js'),
    auth: true,
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

/**
 * Initialize the SPA application
 */
export async function initApp() {
  console.log('[App] Initializing SPA...')

  try {
    const embedMode = applyEmbedModeClass()

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

    if (!embedMode && sidebarContainer) {
      renderSidebar(sidebarContainer, { authInfo })
    } else if (sidebarContainer) {
      sidebarContainer.innerHTML = ''
    }

    if (headerContainer) {
      renderHeader(headerContainer, { authInfo })
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

    if (link.classList.contains('new-chat-btn')) {
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
    window.__spaRouter?.navigate('/', { replace: window.location.pathname === '/' })
  } catch (error) {
    console.error('[App] Failed to start new chat:', error)
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
