const EMBED_MODE_VALUES = new Set(['1', 'true', 'embed', 'chat-only', 'cmp'])

function getQueryParams() {
  return new URLSearchParams(window.location.search)
}

export function getEmbedModeValue() {
  const params = getQueryParams()
  return (
    params.get('embed') ||
    params.get('layout') ||
    params.get('mode') ||
    ''
  ).toLowerCase()
}

export function isEmbedMode() {
  return EMBED_MODE_VALUES.has(getEmbedModeValue())
}

export function applyEmbedModeClass() {
  const embedMode = isEmbedMode()
  document.documentElement.classList.toggle('embed-mode', embedMode)

  if (document.body) {
    document.body.classList.toggle('embed-mode', embedMode)
    document.body.dataset.layoutMode = embedMode ? 'embed' : 'default'
  }

  return embedMode
}

export function getEmbedParentOrigin() {
  const explicitOrigin = getQueryParams().get('parentOrigin')
  if (explicitOrigin) {
    return explicitOrigin
  }

  if (document.referrer) {
    try {
      return new URL(document.referrer).origin
    } catch (_error) {
      return '*'
    }
  }

  return '*'
}
