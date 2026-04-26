/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

import { listAgentCapabilities } from './api-client.js?v=19'
import { getAuthToken } from './auth.js?v=19'

const CACHE_MS = 10000

let currentController = null
let sharedCapabilities = []
let sharedLoadedAt = 0
let sharedLoadPromise = null
let sharedCacheKey = ''

function normalizeText(value) {
  return String(value || '').trim()
}

function normalizeLower(value) {
  return normalizeText(value).toLowerCase()
}

function findInputElement(chatElement) {
  const root = chatElement?.shadowRoot
  if (!root) return null
  return root.querySelector('textarea') ||
    root.querySelector('input[type="text"]') ||
    root.querySelector('[contenteditable="true"]')
}

function isEditableElement(input) {
  return !!input && (input.isContentEditable || input.getAttribute?.('contenteditable') === 'true')
}

function getInputText(input) {
  if (!input) return ''
  if (isEditableElement(input)) {
    return input.textContent || ''
  }
  return input.value || ''
}

function setInputText(input, text) {
  if (!input) return
  if (isEditableElement(input)) {
    input.textContent = text
    placeCaretAtEnd(input)
  } else {
    input.value = text
    input.selectionStart = text.length
    input.selectionEnd = text.length
  }
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function placeCaretAtEnd(input) {
  if (!isEditableElement(input)) return
  const selection = window.getSelection()
  if (!selection) return
  const range = document.createRange()
  range.selectNodeContents(input)
  range.collapse(false)
  selection.removeAllRanges()
  selection.addRange(range)
}

function getCaretOffset(input) {
  if (!input) return 0
  if (!isEditableElement(input)) {
    return input.selectionStart ?? getInputText(input).length
  }
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || !input.contains(selection.anchorNode)) {
    return getInputText(input).length
  }
  const range = selection.getRangeAt(0).cloneRange()
  range.selectNodeContents(input)
  range.setEnd(selection.anchorNode, selection.anchorOffset)
  return range.toString().length
}

function findSlashTrigger(text, caretOffset) {
  const beforeCaret = text.slice(0, caretOffset)
  const slashIndex = beforeCaret.lastIndexOf('/')
  if (slashIndex < 0) return null
  if (slashIndex > 0 && !/\s/.test(beforeCaret[slashIndex - 1])) return null
  const query = beforeCaret.slice(slashIndex + 1)
  if (/\s/.test(query)) return null
  return { start: slashIndex, query }
}

// Keep selected chips tied to an exact slash command token, so `/foo` never matches `/foobar`.
function hasLeadingCommand(messageText, command) {
  const raw = String(messageText || '')
  const trimmedStart = raw.replace(/^\s+/, '')
  if (!command || !trimmedStart.toLowerCase().startsWith(command.toLowerCase())) {
    return false
  }
  const nextChar = trimmedStart.charAt(command.length)
  return !nextChar || /\s/.test(nextChar)
}

function stripLeadingCommand(messageText, command) {
  const raw = String(messageText || '')
  if (!hasLeadingCommand(raw, command)) {
    return raw.trim()
  }
  const trimmedStart = raw.replace(/^\s+/, '')
  return trimmedStart.slice(command.length).trim()
}

function sanitizeCapability(capability) {
  if (!capability || typeof capability !== 'object') return null
  const keys = [
    'id',
    'kind',
    'command',
    'label',
    'provider_type',
    'provider_display_name',
    'instance_name',
    'skill_name',
    'qualified_skill_name',
    'target_provider_types',
    'target_skill_names',
    'target_tool_names',
    'target_group_ids'
  ]
  const payload = {}
  for (const key of keys) {
    if (capability[key] !== undefined) {
      payload[key] = capability[key]
    }
  }
  return payload
}

function getCapabilityCacheKey() {
  const token = getAuthToken()
  return token ? `token:${token}` : ''
}

function syncCapabilityCacheKey() {
  const cacheKey = getCapabilityCacheKey()
  if (sharedCacheKey !== cacheKey) {
    sharedCapabilities = []
    sharedLoadedAt = 0
    sharedLoadPromise = null
    sharedCacheKey = cacheKey
  }
  return cacheKey
}

class SlashCapabilityPicker {
  constructor(chatElement) {
    this.chatElement = chatElement
    this.input = null
    this.capabilities = sharedCapabilities
    this.filtered = []
    this.selectedCapability = null
    this.activeIndex = 0
    this.triggerStart = -1
    this.lastLoadedAt = 0
    this.loading = false
    this.popup = document.createElement('div')
    this.popup.className = 'slash-picker-popup'
    this.popup.hidden = true
    this.chip = document.createElement('div')
    this.chip.className = 'slash-selection-chip'
    this.chip.hidden = true
    this.chip.innerHTML = '<span class="slash-selection-label"></span><button type="button" aria-label="Clear selected capability">x</button>'
    this.onInput = this.onInput.bind(this)
    this.onKeydown = this.onKeydown.bind(this)
    this.onBlur = this.onBlur.bind(this)
    this.onResize = this.onResize.bind(this)
  }

  attach() {
    this.input = findInputElement(this.chatElement)
    if (!this.input || this.input._slashPickerAttached) return false
    this.input.addEventListener('input', this.onInput)
    this.input.addEventListener('keydown', this.onKeydown, true)
    this.input.addEventListener('blur', this.onBlur)
    this.input._slashPickerAttached = true
    this.chip.querySelector('button')?.addEventListener('click', () => this.clearSelected())
    document.body.appendChild(this.popup)
    document.body.appendChild(this.chip)
    window.addEventListener('resize', this.onResize)
    window.addEventListener('scroll', this.onResize, true)
    this.warmCapabilities()
    return true
  }

  destroy() {
    if (this.input) {
      this.input.removeEventListener('input', this.onInput)
      this.input.removeEventListener('keydown', this.onKeydown, true)
      this.input.removeEventListener('blur', this.onBlur)
      delete this.input._slashPickerAttached
    }
    window.removeEventListener('resize', this.onResize)
    window.removeEventListener('scroll', this.onResize, true)
    this.popup.remove()
    this.chip.remove()
  }

  warmCapabilities() {
    const load = () => {
      this.loadCapabilities({ silent: true })
    }
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(load, { timeout: 1200 })
    } else {
      setTimeout(load, 0)
    }
  }

  async loadCapabilities({ force = false, silent = false } = {}) {
    const cacheKey = syncCapabilityCacheKey()
    const now = Date.now()
    if (cacheKey && !force && sharedCapabilities.length && now - sharedLoadedAt < CACHE_MS) {
      this.capabilities = sharedCapabilities
      this.lastLoadedAt = sharedLoadedAt
      return
    }

    if (cacheKey && sharedLoadPromise) {
      try {
        this.capabilities = await sharedLoadPromise
        this.lastLoadedAt = sharedLoadedAt
      } finally {
        this.loading = false
        if (!silent || !this.popup.hidden) {
          this.updateFilter()
        }
      }
      return
    }

    this.loading = !silent && !this.capabilities.length
    if (!silent) {
      this.render()
    }
    const loadPromise = listAgentCapabilities()
      .then((payload) => {
        const capabilities = Array.isArray(payload?.capabilities) ? payload.capabilities : []
        if (!cacheKey) {
          return capabilities
        }
        if (sharedCacheKey !== cacheKey) {
          return sharedCapabilities
        }
        sharedCapabilities = capabilities
        sharedLoadedAt = Date.now()
        return sharedCapabilities
      })
    if (cacheKey) {
      sharedLoadPromise = loadPromise.finally(() => {
        sharedLoadPromise = null
      })
    }
    try {
      this.capabilities = await (cacheKey ? sharedLoadPromise : loadPromise)
      this.lastLoadedAt = sharedLoadedAt
    } catch (error) {
      console.warn('[SlashPicker] Failed to load capabilities:', error)
      this.capabilities = cacheKey ? sharedCapabilities : []
    } finally {
      this.loading = false
      if (!silent || !this.popup.hidden) {
        this.updateFilter()
      }
    }
  }

  onInput() {
    const cacheKey = syncCapabilityCacheKey()
    if (!cacheKey) {
      this.capabilities = []
    }
    const text = getInputText(this.input)
    if (
      this.selectedCapability &&
      !hasLeadingCommand(text, this.selectedCapability.command)
    ) {
      this.clearSelected({ keepInput: true })
    }

    const trigger = findSlashTrigger(text, getCaretOffset(this.input))
    if (!trigger) {
      this.hidePopup()
      return
    }
    this.triggerStart = trigger.start
    this.query = trigger.query
    this.showPopup()
    this.updateFilter()
    const cacheStale = Date.now() - sharedLoadedAt > CACHE_MS
    this.loadCapabilities({
      force: !cacheKey || cacheStale,
      silent: cacheKey && this.capabilities.length > 0
    })
  }

  onKeydown(event) {
    if (this.popup.hidden) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!this.filtered.length) return
      this.activeIndex = Math.min(this.filtered.length - 1, this.activeIndex + 1)
      this.render()
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!this.filtered.length) return
      this.activeIndex = Math.max(0, this.activeIndex - 1)
      this.render()
      return
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      if (this.filtered[this.activeIndex]) {
        event.preventDefault()
        event.stopPropagation()
        event.stopImmediatePropagation()
        this.selectCapability(this.filtered[this.activeIndex])
      }
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      this.hidePopup()
    }
  }

  onBlur() {
    setTimeout(() => this.hidePopup(), 120)
  }

  onResize() {
    if (!this.popup.hidden) {
      this.positionPopup()
    }
    if (!this.chip.hidden) {
      this.positionChip()
    }
  }

  showPopup() {
    this.popup.hidden = false
    this.positionPopup()
  }

  hidePopup() {
    this.popup.hidden = true
  }

  updateFilter() {
    const query = normalizeLower(this.query || '')
    const items = this.capabilities.filter((item) => {
      if (!query) return true
      const haystack = [
        item.command,
        item.label,
        item.description,
        item.provider_type,
        item.provider_display_name,
        item.instance_name,
        item.skill_name,
        item.qualified_skill_name
      ].map(normalizeLower).join(' ')
      return haystack.includes(query)
    })
    items.sort((left, right) => {
      const leftCommand = normalizeLower(left.command)
      const rightCommand = normalizeLower(right.command)
      const leftPrefix = query && leftCommand.startsWith(`/${query}`) ? 0 : 1
      const rightPrefix = query && rightCommand.startsWith(`/${query}`) ? 0 : 1
      if (leftPrefix !== rightPrefix) return leftPrefix - rightPrefix
      return leftCommand.localeCompare(rightCommand)
    })
    this.filtered = items
    if (!this.filtered.length) {
      this.activeIndex = 0
    } else {
      this.activeIndex = Math.min(
        Math.max(0, this.activeIndex),
        this.filtered.length - 1
      )
    }
    this.render()
  }

  render() {
    this.popup.innerHTML = ''
    if (this.loading && !this.filtered.length) {
      const row = document.createElement('div')
      row.className = 'slash-picker-empty'
      row.textContent = 'Loading skills'
      this.popup.appendChild(row)
      this.positionPopup()
      return
    }
    if (!this.filtered.length) {
      const row = document.createElement('div')
      row.className = 'slash-picker-empty'
      row.textContent = 'No matching skills'
      this.popup.appendChild(row)
      this.positionPopup()
      return
    }
    this.filtered.forEach((item, index) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = `slash-picker-row${index === this.activeIndex ? ' active' : ''}`
      button.addEventListener('mousedown', (event) => event.preventDefault())
      button.addEventListener('click', () => this.selectCapability(item))

      const main = document.createElement('span')
      main.className = 'slash-picker-main'
      main.textContent = item.command || item.label || item.skill_name

      const detail = document.createElement('span')
      detail.className = 'slash-picker-detail'
      const providerText = item.kind === 'provider_skill'
        ? `${item.provider_display_name || item.provider_type} / ${item.instance_name}`
        : 'Skill'
      detail.textContent = providerText

      const description = document.createElement('span')
      description.className = 'slash-picker-description'
      description.textContent = item.description || ''

      button.appendChild(main)
      button.appendChild(detail)
      if (description.textContent) {
        button.appendChild(description)
      }
      this.popup.appendChild(button)
    })
    this.positionPopup()
    this.scrollActiveRowIntoView()
  }

  scrollActiveRowIntoView() {
    if (!this.popup || this.popup.hidden) return
    const activeRow = this.popup.querySelector('.slash-picker-row.active')
    if (!activeRow) return

    const popupHeight = this.popup.clientHeight
    const rowTop = activeRow.offsetTop
    const rowBottom = rowTop + activeRow.offsetHeight
    const visibleTop = this.popup.scrollTop
    const visibleBottom = visibleTop + popupHeight

    if (popupHeight > 0 && rowTop < visibleTop) {
      this.popup.scrollTop = rowTop
      return
    }
    if (popupHeight > 0 && rowBottom > visibleBottom) {
      this.popup.scrollTop = Math.max(0, rowBottom - popupHeight)
      return
    }
    if (popupHeight <= 0 && typeof activeRow.scrollIntoView === 'function') {
      activeRow.scrollIntoView({ block: 'nearest' })
    }
  }

  positionPopup() {
    if (!this.input || this.popup.hidden) return
    const rect = this.input.getBoundingClientRect()
    const width = Math.min(Math.max(rect.width, 320), 620)
    this.popup.style.width = `${width}px`
    const left = Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12))
    this.popup.style.left = `${left}px`
    const popupHeight = this.popup.offsetHeight || 260
    const top = Math.max(12, rect.top - popupHeight - 8)
    this.popup.style.top = `${top}px`
  }

  positionChip() {
    if (!this.input || this.chip.hidden) return
    const rect = this.input.getBoundingClientRect()
    this.chip.style.left = `${Math.max(12, rect.left)}px`
    this.chip.style.top = `${Math.max(12, rect.top - 38)}px`
    this.chip.style.maxWidth = `${Math.max(260, Math.min(rect.width, 620))}px`
  }

  selectCapability(capability) {
    const text = getInputText(this.input)
    const caret = getCaretOffset(this.input)
    const trigger = findSlashTrigger(text, caret)
    const start = trigger ? trigger.start : this.triggerStart
    const before = start >= 0 ? text.slice(0, start) : ''
    const after = text.slice(caret)
    const command = capability.command || ''
    const spacer = after && /^\s/.test(after) ? '' : '\u00a0'
    const nextText = `${before}${command}${spacer}${after}`
    setInputText(this.input, nextText)
    this.selectedCapability = capability
    this.hidePopup()
    this.renderChip()
    this.input?.focus()
  }

  renderChip() {
    if (!this.selectedCapability) {
      this.chip.hidden = true
      return
    }
    const label = this.chip.querySelector('.slash-selection-label')
    if (label) {
      label.textContent = this.selectedCapability.command || this.selectedCapability.label || ''
    }
    this.chip.hidden = false
    this.positionChip()
  }

  clearSelected({ keepInput = false } = {}) {
    const command = this.selectedCapability?.command
    this.selectedCapability = null
    this.chip.hidden = true
    if (!keepInput && command && this.input) {
      const text = getInputText(this.input)
      const stripped = stripLeadingCommand(text, command)
      setInputText(this.input, stripped)
    }
  }

  resolveCommand(messageText) {
    const token = normalizeText(messageText).match(/^\/\S+/)?.[0] || ''
    if (!token) return null
    const matches = this.capabilities.filter((item) => normalizeLower(item.command) === normalizeLower(token))
    return matches.length === 1 ? matches[0] : null
  }

  consumeSelectionForMessage(messageText) {
    let selected = this.selectedCapability
    if (selected && !hasLeadingCommand(messageText, selected.command)) {
      this.clearSelected({ keepInput: true })
      selected = null
    }
    if (!selected) {
      selected = this.resolveCommand(messageText)
    }
    if (!selected) {
      return { messageText: normalizeText(messageText), selectedCapability: null }
    }
    const cleanedMessage = stripLeadingCommand(messageText, selected.command)
    const selectedCapability = sanitizeCapability(selected)
    this.clearSelected({ keepInput: true })
    return {
      messageText: cleanedMessage || selected.command || normalizeText(messageText),
      selectedCapability
    }
  }
}

export function setupSlashCapabilityPicker(chatElement) {
  if (!(chatElement instanceof HTMLElement)) return null
  if (chatElement._slashCapabilityPickerController) {
    currentController = chatElement._slashCapabilityPickerController
    return currentController
  }
  const controller = new SlashCapabilityPicker(chatElement)
  if (controller.attach()) {
    chatElement._slashCapabilityPickerController = controller
    currentController = controller
    return controller
  }
  setTimeout(() => setupSlashCapabilityPicker(chatElement), 500)
  return null
}

export function prepareSlashCapabilityMessage(messageText) {
  if (!currentController) {
    return { messageText: normalizeText(messageText), selectedCapability: null }
  }
  return currentController.consumeSelectionForMessage(messageText)
}
