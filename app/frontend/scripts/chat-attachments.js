/* Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved. */
import { buildApiUrl } from './config.js'
import { t } from './i18n.js'

const MAX_FILES = 4
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
const IMAGE_EXTENSIONS = /\.(png|jpe?g|webp)$/i

export const ACCEPTED_FORMATS = '.png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.csv,.json'

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

/** Return whether a file is handled by the native image pipeline. */
export function isImageFile(file) {
  return IMAGE_EXTENSIONS.test(file?.name || '') || String(file?.type || '').startsWith('image/')
}

/** Translate known attachment error codes, preserving the original message otherwise. */
export function attachmentErrorMessage(error) {
  const code = error?.code || String(error?.message || error || '').split(':')[0]
  const key = `chat.images.${code}`
  const translated = t(key)
  return translated !== key ? translated : (error?.message || String(error))
}

/** Backward-compatible alias kept for image-only call sites. */
export const imageErrorMessage = attachmentErrorMessage

/** Abort the pending upload; cancelling an already-created agent run is the caller's job. */
export function cancelImageUpload() {
  pendingUpload?.abort()
  pendingUpload = null
}

/** Best-effort cleanup of unsent attachments; the server preserves files bound to history. */
export function discardAttachmentDrafts(attachments) {
  return Promise.allSettled((attachments || []).map(file => fetch(
    buildApiUrl(`/api/chat/attachments/${encodeURIComponent(file.id)}`), { method: 'DELETE' }
  )))
}

/** Backward-compatible alias kept for image-only call sites. */
export const discardImageDrafts = discardAttachmentDrafts

/**
 * Upload a bounded batch of images/documents to an owned session.
 * The upload is cancellable via cancelImageUpload; server errors retain their codes.
 */
export async function uploadChatAttachments(sessionKey, files) {
  if (!files?.length) return []
  if (files.length > MAX_FILES) throw { code: 'too_many_files' }
  const oversize = files.find(file => file.size > (isImageFile(file) ? MAX_IMAGE_BYTES : MAX_DOCUMENT_BYTES))
  if (oversize) throw { code: isImageFile(oversize) ? 'image_too_large' : 'file_too_large' }
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

/** Backward-compatible alias kept for image-only call sites. */
export const uploadChatImages = uploadChatAttachments

/** Map available history attachments to Deep Chat files using authenticated content URLs. */
export function historyAttachmentFiles(attachments = []) {
  return attachments.filter(file => file.available !== false).map(file => ({
    name: file.name,
    type: file.kind === 'document' ? 'file' : 'image',
    src: buildApiUrl(
      `/api/chat/attachments/${encodeURIComponent(file.id)}/content${file.kind === 'document' ? '?inline=1' : ''}`
    )
  }))
}

/** Backward-compatible alias kept for image-only call sites. */
export const historyImageFiles = historyAttachmentFiles
