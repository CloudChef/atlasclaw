/* Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved. */
import { buildApiUrl } from './config.js'
import { t } from './i18n.js'

let pendingUpload = null

/** Read the latest user text and files from Deep Chat's multipart or object payload. */
export function extractChatSubmission(body) {
  if (body instanceof FormData) {
    const messages = [...body.entries()].filter(([key]) => /^message\d+$/.test(key))
      .sort(([a], [b]) => Number(a.slice(7)) - Number(b.slice(7)))
    const last = messages.length ? JSON.parse(messages.at(-1)[1]) : {}
    return { text: last.text || '', files: [...body.values()].filter(value => value instanceof File) }
  }
  const last = body?.messages?.at(-1) || body || {}
  return { text: typeof last === 'string' ? last : (last.text || last.content || ''), files: body?.files || [] }
}

/** Translate known image error codes, preserving the original message for other errors. */
export function imageErrorMessage(error) {
  const code = error?.code || String(error?.message || error || '').split(':')[0]
  const key = `chat.images.${code}`
  const translated = t(key)
  return translated !== key ? translated : (error?.message || String(error))
}

/** Abort the pending upload; cancelling an already-created agent run is the caller's job. */
export function cancelImageUpload() {
  pendingUpload?.abort()
  pendingUpload = null
}

/** Best-effort cleanup of unsent images; the server preserves images bound to history. */
export function discardImageDrafts(attachments) {
  return Promise.allSettled(attachments.map(file => fetch(
    buildApiUrl(`/api/chat/attachments/${encodeURIComponent(file.id)}`), { method: 'DELETE' }
  )))
}

/**
 * Upload a bounded batch to an owned session and return attachment references.
 * The upload is cancellable via cancelImageUpload; server errors retain their codes.
 */
export async function uploadChatImages(sessionKey, files) {
  if (!files?.length) return []
  if (files.length > 4) throw { code: 'too_many_images' }
  if (files.some(file => file.size > 5 * 1024 * 1024)) throw { code: 'image_too_large' }
  const controller = new AbortController()
  pendingUpload = controller
  try {
    const form = new FormData()
    form.set('session_key', sessionKey)
    files.forEach(file => form.append('files', file))
    const response = await fetch(buildApiUrl('/api/chat/attachments'), {
      method: 'POST', body: form, signal: controller.signal
    })
    const data = await response.json()
    if (!response.ok) throw data.detail || new Error(`HTTP ${response.status}`)
    return data.attachments
  } finally {
    if (pendingUpload === controller) pendingUpload = null
  }
}

/** Map available history attachments to Deep Chat files using authenticated content URLs. */
export function historyImageFiles(attachments = []) {
  return attachments.filter(file => file.available !== false).map(file => ({
    name: file.name, type: 'image',
    src: buildApiUrl(`/api/chat/attachments/${encodeURIComponent(file.id)}/content`)
  }))
}
