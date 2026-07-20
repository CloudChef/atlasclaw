/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Render the latest Host object only. Objects already acted on remain visible
 * in Chat history and are intentionally not retained in this bar.
 *
 * @param {HTMLElement} container - Context slot.
 * @param {object|null} object - Resolved object summary.
 */
export function renderObjectContextBar(container, object) {
  if (!container) return
  container.replaceChildren()
  if (!object?.id) {
    container.hidden = true
    return
  }

  const bar = document.createElement('div')
  bar.className = 'embed-object-context-bar'
  bar.setAttribute('role', 'status')

  const marker = document.createElement('span')
  marker.className = 'embed-object-context-marker'
  marker.setAttribute('aria-hidden', 'true')

  const summary = document.createElement('span')
  summary.className = 'embed-object-context-summary'
  summary.textContent = object.name || object.id

  const identity = document.createElement('span')
  identity.className = 'embed-object-context-id'
  identity.textContent = object.id

  bar.append(marker, summary, identity)
  container.appendChild(bar)
  container.hidden = false
}
