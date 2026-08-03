/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Render the latest Host default Skill and object. This is presentation
 * context only and does not represent a selected or locked capability.
 *
 * @param {HTMLElement} container - Context slot.
 * @param {object|null} object - Resolved object summary.
 * @param {object|null} skill - Resolved default Skill summary.
 * @param {string} [status] - Current Context resolution status.
 */
export function renderObjectContextBar(container, object, skill, status = 'idle') {
  if (!container) return
  container.replaceChildren()
  if (status === 'pending') {
    const loadingBar = document.createElement('div')
    loadingBar.className = 'embed-object-context-bar embed-object-context-loading'
    loadingBar.setAttribute('aria-busy', 'true')

    const spinner = document.createElement('span')
    spinner.className = 'embed-context-loading-spinner'
    spinner.setAttribute('aria-hidden', 'true')

    loadingBar.appendChild(spinner)
    container.appendChild(loadingBar)
    container.hidden = false
    return
  }
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

  const skillName = document.createElement('span')
  skillName.className = 'embed-default-skill-name'
  skillName.textContent = skill?.name || skill?.ref || ''
  skillName.title = skill?.description || skill?.ref || ''

  const separator = document.createElement('span')
  separator.className = 'embed-context-separator'
  separator.setAttribute('aria-hidden', 'true')
  separator.textContent = '·'

  const summary = document.createElement('span')
  summary.className = 'embed-object-context-summary'
  summary.textContent = object.name || object.id

  const identity = document.createElement('span')
  identity.className = 'embed-object-context-id'
  identity.textContent = object.id

  bar.append(marker)
  if (skillName.textContent) {
    bar.append(skillName, separator)
  }
  bar.append(summary, identity)
  container.appendChild(bar)
  container.hidden = false
}
