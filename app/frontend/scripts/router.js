/**
 * router.js - SPA Router based on History API
 *
 * API:
 * createRouter(routes, options) → { navigate, start, getCurrentPath }
 *
 * Route definition:
 * { path: '/models', loader: () => import('./pages/models.js'), auth: true, title: 'model.title' }
 *
 * Features:
 * - pushState / popstate based navigation
 * - Exact path matching + parameter matching (e.g. /admin/users/:id)
 * - Navigation guards (auth check before route change)
 * - Dynamic import() for lazy loading page modules
 * - Page module lifecycle: mount(container) / unmount()
 */

/**
 * Parse route path pattern to regex and extract param names
 * @param {string} pattern - Route pattern (e.g., '/users/:id')
 * @returns {{ regex: RegExp, paramNames: string[] }}
 */
function parseRoutePath(pattern) {
  const paramNames = []
  const regexStr = pattern
    .replace(/\//g, '\\/')
    .replace(/:(\w+)/g, (_, name) => {
      paramNames.push(name)
      return '([^/]+)'
    })
  return {
    regex: new RegExp(`^${regexStr}$`),
    paramNames
  }
}

/**
 * Match a path against a route pattern
 * @param {string} path - Current URL path
 * @param {string} pattern - Route pattern
 * @returns {{ matched: boolean, params: Record<string, string> }}
 */
function matchRoute(path, pattern) {
  const { regex, paramNames } = parseRoutePath(pattern)
  const match = path.match(regex)

  if (!match) {
    return { matched: false, params: {} }
  }

  const params = {}
  paramNames.forEach((name, index) => {
    params[name] = match[index + 1]
  })

  return { matched: true, params }
}

/**
 * Create SPA router instance
 * @param {Array<{path: string, loader: Function, auth?: boolean, title?: string}>} routes - Route definitions
 * @param {Object} options - Router options
 * @param {HTMLElement} options.contentContainer - Container for page content
 * @param {Function} [options.onBeforeRoute] - Called before route change with (path, route)
 * @param {Function} [options.onAfterRoute] - Called after route change with (path, route)
 * @returns {{ navigate: Function, start: Function, getCurrentPath: Function }}
 */
export function createRouter(routes, options = {}) {
  const { contentContainer, onBeforeRoute, onAfterRoute } = options

  // Current mounted page module (with unmount method)
  let currentPage = null
  let currentPath = ''

  /**
   * Find matching route for given path
   * @param {string} path - URL path to match
   * @returns {{ route: Object|null, params: Record<string, string> }}
   */
  function findRoute(path) {
    // Normalize path: remove trailing slash except for root
    const normalizedPath = path === '/' ? '/' : path.replace(/\/$/, '')

    for (const route of routes) {
      const { matched, params } = matchRoute(normalizedPath, route.path)
      if (matched) {
        return { route, params }
      }
    }
    return { route: null, params: {} }
  }

  /**
   * Load and mount a route
   * @param {string} path - URL path
   * @param {boolean} [addHistory=true] - Whether to push to history
   */
  async function loadRoute(path, addHistory = true) {
    const { route, params } = findRoute(path)

    if (!route) {
      console.warn(`[Router] No route found for: ${path}`)
      // Could render 404 page here
      return
    }

    // Call before route hook
    if (onBeforeRoute) {
      try {
        onBeforeRoute(path, route)
      } catch (err) {
        console.error('[Router] onBeforeRoute error:', err)
      }
    }

    // Unmount current page if exists
    if (currentPage && typeof currentPage.unmount === 'function') {
      try {
        await currentPage.unmount()
      } catch (err) {
        console.error('[Router] Page unmount error:', err)
      }
    }

    currentPage = null
    currentPath = path

    // Clear container
    if (contentContainer) {
      contentContainer.innerHTML = ''
    }

    // Load page module via dynamic import
    try {
      const pageModule = await route.loader()

      // Mount new page
      if (pageModule && typeof pageModule.mount === 'function') {
        currentPage = pageModule
        await pageModule.mount(contentContainer, { params, route })
      } else {
        console.warn('[Router] Page module missing mount function:', path)
      }
    } catch (err) {
      console.error('[Router] Failed to load page module:', err)
      if (contentContainer) {
        contentContainer.innerHTML = `<div class="error-message">Failed to load page</div>`
      }
    }

    // Update browser history
    if (addHistory && window.location.pathname !== path) {
      window.history.pushState({ path }, '', path)
    }

    // Call after route hook
    if (onAfterRoute) {
      try {
        onAfterRoute(path, route)
      } catch (err) {
        console.error('[Router] onAfterRoute error:', err)
      }
    }
  }

  /**
   * Navigate to a path
   * @param {string} path - Target path
   * @param {{ replace?: boolean }} [options] - Navigation options
   */
  function navigate(path, { replace = false } = {}) {
    if (replace) {
      window.history.replaceState({ path }, '', path)
    }
    loadRoute(path, !replace)
  }

  /**
   * Handle popstate event (browser back/forward)
   * @param {PopStateEvent} event
   */
  function handlePopState(event) {
    const path = window.location.pathname
    loadRoute(path, false)
  }

  /**
   * Start router - listen to popstate and handle current URL
   */
  function start() {
    // Listen for browser navigation
    window.addEventListener('popstate', handlePopState)

    // Handle current URL on initial load
    const initialPath = window.location.pathname
    loadRoute(initialPath, false)
  }

  /**
   * Get current path
   * @returns {string}
   */
  function getCurrentPath() {
    return currentPath
  }

  /**
   * Stop router - remove event listeners
   */
  function stop() {
    window.removeEventListener('popstate', handlePopState)
  }

  return {
    navigate,
    start,
    stop,
    getCurrentPath,
    findRoute
  }
}

export default createRouter
