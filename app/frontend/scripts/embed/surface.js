/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Parse the provider-agnostic Embed surface contract from the AtlasClaw URL.
 * Host page state is deliberately excluded: v1 receives only normalized paths
 * through the validated postMessage bridge.
 *
 * @param {Location|URL|string} locationLike - Browser location or test URL.
 * @returns {{embedded: boolean, surface: string|null, hostOrigin: string|null, nonce: string|null, integrationMode: boolean}}
 */
export function parseEmbedSurface(locationLike = window.location) {
  const url = toUrl(locationLike)
  const params = url.searchParams
  const embedded = parseBooleanParam(
    params.get('embedded') || params.get('embed') || params.get('iframe')
  )
  const requestedSurface = String(params.get('surface') || '').trim().toLowerCase()
  const surface = ['floating', 'menu'].includes(requestedSurface) ? requestedSurface : null
  const integrationMode = embedded && !!surface

  return Object.freeze({
    embedded,
    surface: integrationMode ? surface : null,
    hostOrigin: integrationMode && surface === 'floating'
      ? normalizeOrigin(params.get('host_origin'))
      : null,
    nonce: integrationMode && surface === 'floating'
      ? normalizeNonce(params.get('nonce'))
      : null,
    integrationMode
  })
}

/**
 * Apply surface classes without changing the legacy embedded menu classes.
 *
 * @param {ReturnType<typeof parseEmbedSurface>} surface - Parsed surface.
 * @param {HTMLElement} root - Document root or body.
 */
export function applySurfaceClasses(surface, root) {
  if (!root?.classList) return
  root.classList.toggle('atlas-surface-floating', surface?.surface === 'floating')
  root.classList.toggle('atlas-surface-menu', surface?.surface === 'menu')
}

function toUrl(locationLike) {
  if (locationLike instanceof URL) return locationLike
  if (typeof locationLike === 'string') return new URL(locationLike, 'http://localhost')
  return new URL(locationLike?.href || 'http://localhost/')
}

function parseBooleanParam(value) {
  return ['1', 'true', 'yes'].includes(String(value || '').trim().toLowerCase())
}

function normalizeOrigin(value) {
  if (!value) return null
  try {
    const origin = new URL(String(value)).origin
    return origin === 'null' ? null : origin
  } catch (_) {
    return null
  }
}

function normalizeNonce(value) {
  const normalized = String(value || '').trim()
  return /^[A-Za-z0-9_-]{22,256}$/.test(normalized) ? normalized : null
}
