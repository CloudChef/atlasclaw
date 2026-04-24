/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * channels.js - Channels Page Module
 *
 * SPA page module for channel connection management.
 * Migrated from channels.html + channel-manager.js
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { t, translateIfExists, updatePageTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { buildAssetUrl } from '../config.js'

// ========== Module State ==========
let mounted = false
let pageContainer = null

// Channel state
let currentChannelType = null
let currentSchema = null
let allChannels = []
let editingConnectionId = null
let pendingDeleteId = null

// Event handler references for cleanup
let popstateHandler = null
let channelRailCleanup = null
let syncChannelRailState = null
let suppressCardClick = false
let runtimeStatusPollTimer = null
let runtimeStatusPollInFlight = false
let verificationInFlight = false
const VALIDATION_REQUEST_TIMEOUT_MS = 3600

// ========== Channel Type SVG Icons ==========
const CHANNEL_ICONS = {
  feishu: `<img src="${buildAssetUrl('/static/channel-icons/feishu.png')}" alt="Feishu logo" width="30" height="30" decoding="async">`,
  dingtalk: `<img src="${buildAssetUrl('/static/channel-icons/dingtalk.png')}" alt="DingTalk logo" width="30" height="30" decoding="async">`,
  wecom: `<img src="${buildAssetUrl('/static/channel-icons/wecom.png')}" alt="WeCom logo" width="30" height="30" decoding="async">`,
  slack: `<svg viewBox="0 0 24 24" fill="none" width="22" height="22" aria-hidden="true">
    <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52z" fill="#E01E5A"/>
    <path d="M6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" fill="#E01E5A"/>
    <path d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834z" fill="#36C5F0"/>
    <path d="M8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" fill="#36C5F0"/>
    <path d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834z" fill="#2EB67D"/>
    <path d="M17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" fill="#2EB67D"/>
    <path d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52z" fill="#ECB22E"/>
    <path d="M15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#ECB22E"/>
  </svg>`,
  discord: `<svg viewBox="0 0 24 24" fill="none" width="22" height="22">
    <path d="M20.2 5.1c-1.55-.72-3.21-1.23-4.93-1.49l-.23.44c1.72.42 2.52 1.02 2.52 1.02-1.08-.6-2.1-.99-3.02-1.22a16.18 16.18 0 0 0-5.06 0c-.92.23-1.94.62-3.02 1.22 0 0 .84-.63 2.64-1.05l-.16-.41c-1.72.26-3.38.77-4.93 1.49C1.28 9.16.54 13.09.92 16.96c1.82 1.34 3.58 2.16 5.31 2.69l1.14-1.86a10.8 10.8 0 0 1-1.79-.86l.45-.34c3.46 1.62 7.22 1.62 10.64 0l.45.34c-.57.34-1.17.63-1.8.86l1.14 1.86c1.73-.53 3.49-1.35 5.31-2.69.45-4.49-.77-8.39-1.58-11.86z" fill="#5865F2"/>
    <ellipse cx="9.25" cy="11.95" rx="1.55" ry="1.9" fill="white"/>
    <ellipse cx="14.75" cy="11.95" rx="1.55" ry="1.9" fill="white"/>
    <path d="M8.2 15.8c1.2.86 2.6 1.31 3.8 1.31 1.2 0 2.6-.45 3.8-1.31" stroke="white" stroke-width="1.35" stroke-linecap="round"/>
  </svg>`,
  rest: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M14 12l-2 2-2-2 2-2 2 2zm-2-6l2.12 2.12 2.5-2.5L12 1 7.38 5.62l2.5 2.5L12 6zm-6 6l2.12-2.12-2.5-2.5L1 12l4.62 4.62 2.5-2.5L6 12zm12 0l-2.12 2.12 2.5 2.5L23 12l-4.62-4.62-2.5 2.5L18 12zm-6 6l-2.12-2.12-2.5 2.5L12 23l4.62-4.62-2.5-2.5L12 18z"/>
  </svg>`,
  websocket: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.94-.49-7-3.85-7-7.93s3.06-7.44 7-7.93v2.02c-2.83.48-5 2.94-5 5.91s2.17 5.43 5 5.91v2.02zm2-4.43v-7l4 3.5-4 3.5zm0 4.43v-2.02c2.83-.48 5-2.94 5-5.91s-2.17-5.43-5-5.91V4.07c3.94.49 7 3.85 7 7.93s-3.06 7.44-7 7.93z"/>
  </svg>`,
  sse: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/>
    <path d="M12 6c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm0 10c-2.21 0-4-1.79-4-4s1.79-4 4-4 4 1.79 4 4-1.79 4-4 4z"/>
    <path d="M8 12l5 3V9z"/>
  </svg>`,
  default: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14z"/>
    <path d="M9 7H7v10h2V7zm4 4h-2v6h2v-6zm4-2h-2v8h2V9z"/>
  </svg>`
}

// ========== Action SVG Icons ==========
const ACTION_ICONS = {
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <circle cx="12" cy="12" r="3"></circle>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
  </svg>`,
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
  </svg>`,
  delete: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <polyline points="3 6 5 6 21 6"></polyline>
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
    <line x1="10" y1="11" x2="10" y2="17"></line>
    <line x1="14" y1="11" x2="14" y2="17"></line>
  </svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
  </svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <polyline points="20 6 9 17 4 12"></polyline>
  </svg>`,
  plus: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <line x1="12" y1="5" x2="12" y2="19"></line>
    <line x1="5" y1="12" x2="19" y2="12"></line>
  </svg>`,
  eye: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>`,
  eyeOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
    <line x1="1" y1="1" x2="23" y2="23"></line>
  </svg>`,
  close: `<svg viewBox="0 0 12 12" fill="none" width="12" height="12" aria-hidden="true">
    <path d="M2 2L10 10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
    <path d="M10 2L2 10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
  </svg>`,
  spinner: `<svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true" class="ch-btn-spinner">
    <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" opacity="0.22"></circle>
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"></path>
  </svg>`,
  heart: `<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
    <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
  </svg>`,
  arrowLeft: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <line x1="19" y1="12" x2="5" y2="12"></line>
    <polyline points="12 19 5 12 12 5"></polyline>
  </svg>`,
  arrowRight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
    <line x1="5" y1="12" x2="19" y2="12"></line>
    <polyline points="12 5 19 12 12 19"></polyline>
  </svg>`
}

const CHANNEL_TYPE_ORDER = ['feishu', 'dingtalk', 'wecom', 'websocket', 'rest', 'sse', 'slack', 'discord']
const MOCK_ONLY_CHANNEL_TYPES = new Set()
const PLANNED_CHANNEL_TYPES = [
  { type: 'slack', name: 'Slack', mode: 'planned', connection_count: 0, planned: true },
  { type: 'discord', name: 'Discord', mode: 'planned', connection_count: 0, planned: true }
]
const REAL_STATUS_CHANNEL_TYPES = new Set(['feishu', 'dingtalk', 'wecom'])
const CHANNEL_CARD_COPY = {
  feishu: {
    descriptionKey: 'cardDescription_feishu',
    description: 'Enterprise app and bot messaging entry point for internal notifications and collaboration.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_feishuAccess', value: 'Long Connection / Webhook' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_feishuFocus', value: 'Enterprise App & Bot' }
    ]
  },
  dingtalk: {
    descriptionKey: 'cardDescription_dingtalk',
    description: 'Supports Stream and bot callback flows for internal alerts and automation.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_dingtalkAccess', value: 'Stream / Webhook' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_dingtalkFocus', value: 'Alerts & Automation' }
    ]
  },
  wecom: {
    descriptionKey: 'cardDescription_wecom',
    description: 'Covers group bot, application messaging, and WebSocket long-connection scenarios.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_wecomAccess', value: 'Webhook / App / WebSocket' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_wecomFocus', value: 'Group Bot & App Messages' }
    ]
  },
  websocket: {
    descriptionKey: 'cardDescription_websocket',
    description: 'Provides a bidirectional real-time session channel for dashboards and custom clients.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_websocketAccess', value: 'Persistent Socket' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_websocketFocus', value: 'Bidirectional Sessions' }
    ]
  },
  rest: {
    descriptionKey: 'cardDescription_rest',
    description: 'Standard HTTP API entry point for service integrations and task triggers.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_restAccess', value: 'HTTP Endpoint' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_restFocus', value: 'Request / Response' }
    ]
  },
  sse: {
    descriptionKey: 'cardDescription_sse',
    description: 'One-way real-time event delivery channel for browsers and streaming clients.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_sseAccess', value: 'Event Stream' },
      { labelKey: 'cardSpecFocus', label: 'Focus', valueKey: 'cardValue_sseFocus', value: 'Continuous Push' }
    ]
  },
  slack: {
    descriptionKey: 'cardDescription_slack',
    description: 'Reserved collaboration integration entry. Native connection support will be added later.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_slackAccess', value: 'Reserved Workspace Entry' },
      { labelKey: 'cardSpecStatus', label: 'Status', valueKey: 'cardValue_slackStatus', value: 'Planned, creation disabled' }
    ]
  },
  discord: {
    descriptionKey: 'cardDescription_discord',
    description: 'Reserved community bot integration entry. Native connection support will be added later.',
    specs: [
      { labelKey: 'cardSpecAccess', label: 'Access', valueKey: 'cardValue_discordAccess', value: 'Reserved Bot Entry' },
      { labelKey: 'cardSpecStatus', label: 'Status', valueKey: 'cardValue_discordStatus', value: 'Planned, creation disabled' }
    ]
  }
}

// ========== Mode Badge Mapping ==========
/**
 * Get mode badge for channel type
 * @param {Object} channel - Channel object
 * @returns {{ text: string, className: string }}
 */
function getModeBadge(channel) {
  if (channel?.planned) {
    return { text: t('channel.badgeComingSoon'), className: 'ch-badge-coming-soon' }
  }

  const connectionCount = Number(channel?.connection_count || 0)
  if (connectionCount <= 0) {
    return null
  }

  const TYPE_BADGE_MAP = {
    feishu: { text: t('channel.badgeLongConnection'), className: 'ch-badge-connection' },
    dingtalk: { text: t('channel.badgeStream'), className: 'ch-badge-connection' },
    wecom: { text: t('channel.badgeWebSocket'), className: 'ch-badge-connection' },
    slack: { text: t('channel.badgeEnterprise'), className: 'ch-badge-connection' },
    discord: { text: t('channel.badgeWebhook'), className: 'ch-badge-webhook' },
    rest: { text: t('channel.badgeApi'), className: 'ch-badge-connection' },
    websocket: { text: t('channel.badgeWebSocket'), className: 'ch-badge-connection' },
    sse: { text: t('channel.badgeStream'), className: 'ch-badge-connection' }
  }

  if (TYPE_BADGE_MAP[channel?.type]) {
    return TYPE_BADGE_MAP[channel.type]
  }

  // Fallback: use backend mode data for unknown types
  const mode = channel?.mode || ''
  if (mode.includes('webhook')) {
    return { text: t('channel.badgeWebhook'), className: 'ch-badge-webhook' }
  }
  if (mode.includes('stream')) {
    return { text: t('channel.badgeStream'), className: 'ch-badge-connection' }
  }
  if (mode.includes('socket')) {
    return { text: t('channel.badgeWebSocket'), className: 'ch-badge-connection' }
  }
  return { text: t('channel.badgeConnection'), className: 'ch-badge-connection' }
}

function getPageTemplate() {
  return `
    <div class="ch-page">
      <div id="channelListView">
        <div class="ch-page-header">
          <h1 data-i18n="channel.ecosystem">Channel Ecosystem</h1>
          <p data-i18n="channel.ecosystemDesc">Integrate and manage your AI agent deployment endpoints across various enterprise platforms.</p>
        </div>
        <div class="ch-type-band-shell" id="channelTypeBandShell">
          <button class="ch-type-band-nav ch-type-band-nav-left" id="channelCardsPrev" type="button" aria-label="${t('channel.scrollLeft')}" data-i18n-aria-label="channel.scrollLeft">
            ${ACTION_ICONS.arrowLeft}
          </button>
          <div class="ch-type-cards" id="channelTypeCards"></div>
          <button class="ch-type-band-nav ch-type-band-nav-right" id="channelCardsNext" type="button" aria-label="${t('channel.scrollRight')}" data-i18n-aria-label="channel.scrollRight">
            ${ACTION_ICONS.arrowRight}
          </button>
        </div>
        <div id="connectionsSection" style="display: none;"></div>
        <div id="healthPanel" style="display: none;"></div>
      </div>

      <div id="channelEditView" style="display: none;"></div>

      <div class="ch-delete-overlay" id="deleteDialog" style="display: none;">
        <div class="ch-delete-dialog">
          <h3 data-i18n="channel.deleteConfirmTitle">Confirm Delete</h3>
          <p id="deleteMessage" data-i18n="channel.deleteConfirm">Are you sure you want to delete this connection?</p>
          <div class="ch-delete-actions">
            <button class="ch-delete-cancel" id="btnCancelDelete" data-i18n="channel.cancel">Cancel</button>
            <button class="ch-delete-confirm" id="btnConfirmDelete" data-i18n="channel.delete">Delete</button>
          </div>
        </div>
      </div>
    </div>
  `
}

// ========== Lifecycle Functions ==========

/**
 * Mount channels page into container
 * @param {HTMLElement} container - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(container, { params, route } = {}) {
  pageContainer = container
  hydrateMockState()

  // Render page HTML
  container.innerHTML = getPageTemplate()
  channelRailCleanup = bindChannelRailControls()

  // Bind delete dialog events
  bindDeleteDialogEvents()

  // Handle popstate for browser back/forward
  popstateHandler = () => handleRouteChange()
  window.addEventListener('popstate', popstateHandler)

  // Load channel types
  const channels = await fetchChannelTypes()
  renderChannelTypes(channels)

  // Default to the first mockup channel to match the design
  if (!getChannelTypeFromURL() && channels.length > 0) {
    const url = new URL(window.location.href)
    url.searchParams.set('type', channels[0].type)
    window.history.replaceState({}, '', url)
  }

  await handleRouteChange()

  // Update translations
  updatePageTranslations()

  mounted = true
}

/**
 * Unmount channels page - cleanup
 */
export async function unmount() {
  // Remove popstate listener
  if (popstateHandler) {
    window.removeEventListener('popstate', popstateHandler)
    popstateHandler = null
  }

  if (channelRailCleanup) {
    channelRailCleanup()
    channelRailCleanup = null
  }
  syncChannelRailState = null
  suppressCardClick = false
  stopRuntimeStatusPolling()

  // Reset all module state
  mounted = false
  pageContainer = null
  currentChannelType = null
  currentSchema = null
  allChannels = []
  editingConnectionId = null
  pendingDeleteId = null
}

// ========== Helper: Query within page container ==========

/**
 * Query element within page container
 */
function $(selector) {
  return pageContainer?.querySelector(selector)
}

/**
 * Query all elements within page container
 */
function $$(selector) {
  return pageContainer?.querySelectorAll(selector) || []
}

function bindChannelRailControls() {
  const shell = $('#channelTypeBandShell')
  const rail = $('#channelTypeCards')
  const prevButton = $('#channelCardsPrev')
  const nextButton = $('#channelCardsNext')

  if (!shell || !rail || !prevButton || !nextButton) {
    return null
  }

  const controller = new AbortController()
  const { signal } = controller
  let isPointerDown = false
  let dragStartX = 0
  let dragStartScrollLeft = 0
  let didDrag = false

  const getScrollStep = () => {
    const firstCard = rail.querySelector('.ch-type-card')
    const gap = parseFloat(window.getComputedStyle(rail).gap || '20')
    return (firstCard?.getBoundingClientRect().width || rail.clientWidth * 0.82) + gap
  }

  const syncState = () => {
    const maxScrollLeft = Math.max(0, rail.scrollWidth - rail.clientWidth)
    const canScrollLeft = rail.scrollLeft > 8
    const canScrollRight = rail.scrollLeft < maxScrollLeft - 8
    shell.classList.toggle('can-scroll-left', canScrollLeft)
    shell.classList.toggle('can-scroll-right', canScrollRight)
    prevButton.disabled = !canScrollLeft
    nextButton.disabled = !canScrollRight
  }

  const scrollByStep = direction => {
    rail.scrollBy({
      left: direction * getScrollStep(),
      behavior: 'smooth'
    })
  }

  const onPointerDown = event => {
    if (event.pointerType === 'touch') return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    isPointerDown = true
    didDrag = false
    dragStartX = event.clientX
    dragStartScrollLeft = rail.scrollLeft
    rail.classList.add('is-dragging')
  }

  const onPointerMove = event => {
    if (!isPointerDown) return
    const deltaX = event.clientX - dragStartX
    if (Math.abs(deltaX) > 6) {
      didDrag = true
    }
    rail.scrollLeft = dragStartScrollLeft - deltaX
  }

  const endPointerDrag = () => {
    if (!isPointerDown) return
    isPointerDown = false
    rail.classList.remove('is-dragging')
    if (didDrag) {
      suppressCardClick = true
      window.setTimeout(() => {
        suppressCardClick = false
      }, 0)
    }
  }

  prevButton.addEventListener('click', () => scrollByStep(-1), { signal })
  nextButton.addEventListener('click', () => scrollByStep(1), { signal })
  rail.addEventListener('scroll', syncState, { signal, passive: true })
  rail.addEventListener('pointerdown', onPointerDown, { signal })
  rail.addEventListener('pointermove', onPointerMove, { signal })
  rail.addEventListener('pointerup', endPointerDrag, { signal })
  rail.addEventListener('pointercancel', endPointerDrag, { signal })
  rail.addEventListener('pointerleave', endPointerDrag, { signal })
  window.addEventListener('resize', syncState, { signal })

  syncChannelRailState = syncState
  window.requestAnimationFrame(syncState)

  return () => {
    controller.abort()
    syncChannelRailState = null
  }
}

// ========== Localization Helpers ==========

/**
 * Get localized channel name
 */
function getChannelName(channel) {
  const translatedName = translateIfExists(`channel.name_${channel.type}`)
  if (translatedName) {
    return translatedName
  }
  return channel.name || channel.type
}

function getTranslatedCardCopy(key, fallback) {
  return translateIfExists(`channel.${key}`) || fallback
}

function getChannelCardCopy(channel) {
  const predefined = CHANNEL_CARD_COPY[channel?.type]
  if (predefined) {
    return {
      description: getTranslatedCardCopy(predefined.descriptionKey, predefined.description),
      specs: predefined.specs.map(spec => ({
        label: getTranslatedCardCopy(spec.labelKey, spec.label),
        value: getTranslatedCardCopy(spec.valueKey, spec.value)
      }))
    }
  }

  return {
    description: getTranslatedCardCopy(
      'cardDescription_generic',
      'Manage this channel access configuration and connection state through Atlas.'
    ),
    specs: [
      {
        label: getTranslatedCardCopy('cardSpecAccess', 'Access'),
        value: getTranslatedCardCopy('cardValue_genericAccess', 'Managed Endpoint')
      },
      {
        label: getTranslatedCardCopy('cardSpecFocus', 'Focus'),
        value: getTranslatedCardCopy('cardValue_genericFocus', 'Connection Control')
      }
    ]
  }
}

function getConnectionDisplayName(connection) {
  const translatedName = connection?.name_i18n
    ? translateIfExists(connection.name_i18n)
    : null

  if (translatedName) {
    return translatedName
  }

  return connection?.name || connection?.id || ''
}

/**
 * Get localized field text (title, description, placeholder)
 */
function getFieldText(key, textType, fallback) {
  if (currentChannelType) {
    const channelScoped = translateIfExists(`channel.field.${currentChannelType}.${key}.${textType}`)
    if (channelScoped) {
      return channelScoped
    }
  }

  const translated = translateIfExists(`channel.field.${key}.${textType}`)
  if (translated) {
    return translated
  }
  return fallback || ''
}

function renderSelectOptions(options, selectedValue, { includeBlankOption = false } = {}) {
  const normalizedSelectedValue = String(selectedValue || '')
  const optionMarkup = (Array.isArray(options) ? options : []).map(option => {
    const value = String(option?.value || '')
    const label = String(option?.label || value)
    const selected = normalizedSelectedValue === value ? 'selected' : ''
    return `<option value="${value}" ${selected}>${label}</option>`
  })

  if (!includeBlankOption) {
    return optionMarkup.join('')
  }

  const blankSelected = normalizedSelectedValue === '' ? 'selected' : ''
  return [`<option value="" ${blankSelected}></option>`, ...optionMarkup].join('')
}

function markSelectUnselected(selectElement) {
  if (!selectElement) return
  selectElement.selectedIndex = -1
}

function hasSelectValue(selectElement) {
  return !!String(selectElement?.value || '').trim()
}

function syncSelectClearButtons(form) {
  if (!form) return

  form.querySelectorAll('.ch-select-clear').forEach(button => {
    const targetName = button.dataset.clearTarget
    if (!targetName) return

    const select = form.querySelector(`select[name="${targetName}"]`)
    const canClear = !!select && !select.disabled && hasSelectValue(select)
    const control = button.closest('.ch-select-control-clearable')
    button.hidden = !canClear
    button.disabled = !canClear
    control?.classList.toggle('has-selection', canClear)
  })
}

function handleSelectClear(button, schema) {
  const form = button.closest('.ch-form-card') || button.closest('#configForm')
  if (!form) return

  const targetName = button.dataset.clearTarget
  if (!targetName) return

  const select = form.querySelector(`select[name="${targetName}"]`)
  if (!select) return

  markSelectUnselected(select)

  syncSelectClearButtons(form)
}

// ========== URL Routing ==========

/**
 * Get channel type from URL query parameter
 */
function getChannelTypeFromURL() {
  const params = new URLSearchParams(window.location.search)
  return params.get('type')
}

/**
 * Get edit mode from URL query parameter
 */
function getEditModeFromURL() {
  const params = new URLSearchParams(window.location.search)
  return params.get('edit')
}

/**
 * Navigate to a channel type (update URL)
 */
function navigateToChannel(type, edit = null) {
  const url = new URL(window.location.href)
  if (type) {
    url.searchParams.set('type', type)
  } else {
    url.searchParams.delete('type')
  }
  if (edit) {
    url.searchParams.set('edit', edit)
  } else {
    url.searchParams.delete('edit')
  }
  window.history.pushState({}, '', url)
  handleRouteChange()
}

// ========== Mock Data ==========

const MOCK_STORAGE_KEY = 'atlasclaw_channel_mockup_v3'

const MOCK_CHANNEL_SCHEMAS = {
  feishu: {
    type: 'object',
    properties: {
      connection_mode: {
        type: 'string',
        title: 'Connection Mode',
        description: 'Select connection mode',
        enum: ['longconnection', 'webhook'],
        enumLabels: {
          longconnection: 'Long Connection (Enterprise App)',
          webhook: 'Webhook (Custom Bot)'
        },
        default: 'longconnection'
      },
      app_id: {
        type: 'string',
        title: 'App ID',
        description: 'Feishu application App ID',
        placeholder: 'cli_xxxxxxxxxx',
        showWhen: { connection_mode: 'longconnection' }
      },
      app_secret: {
        type: 'string',
        title: 'App Secret',
        description: 'Feishu application App Secret',
        placeholder: 'Your app secret',
        showWhen: { connection_mode: 'longconnection' }
      },
      webhook_url: {
        type: 'string',
        title: 'Webhook URL',
        description: 'Custom bot Webhook address',
        placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx',
        showWhen: { connection_mode: 'webhook' }
      }
    },
    required_by_mode: {
      longconnection: ['app_id', 'app_secret'],
      webhook: ['webhook_url']
    }
  },
  dingtalk: {
    type: 'object',
    properties: {
      connection_mode: {
        type: 'string',
        title: 'Connection Mode',
        description: 'Select connection mode',
        enum: ['stream', 'webhook'],
        enumLabels: {
          stream: 'Stream Mode (Enterprise Bot)',
          webhook: 'Webhook Robot'
        },
        default: 'stream'
      },
      client_id: {
        type: 'string',
        title: 'Client ID (AppKey)',
        description: 'Application AppKey for Stream mode',
        placeholder: 'dingxxxxxxxxxx',
        showWhen: { connection_mode: 'stream' }
      },
      client_secret: {
        type: 'string',
        title: 'Client Secret (AppSecret)',
        description: 'Application AppSecret',
        placeholder: 'Application secret',
        showWhen: { connection_mode: 'stream' }
      },
      webhook_url: {
        type: 'string',
        title: 'Webhook URL',
        description: 'Custom bot Webhook address',
        placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=xxx',
        showWhen: { connection_mode: 'webhook' }
      },
      secret: {
        type: 'string',
        title: 'Signing Secret',
        description: 'Webhook signing secret (optional)',
        placeholder: 'SEC...',
        showWhen: { connection_mode: 'webhook' }
      }
    },
    required_by_mode: {
      stream: ['client_id', 'client_secret'],
      webhook: ['webhook_url']
    }
  },
  wecom: {
    type: 'object',
    properties: {
      connection_mode: {
        type: 'string',
        title: 'Connection Mode',
        description: 'Select connection mode',
        enum: ['websocket', 'webhook'],
        enumLabels: {
          websocket: 'Long Connection (Intelligent Robot)',
          webhook: 'Webhook'
        },
        default: 'websocket'
      },
      bot_id: {
        type: 'string',
        title: 'Bot ID',
        description: 'Intelligent robot Bot ID',
        placeholder: 'aib...',
        showWhen: { connection_mode: 'websocket' }
      },
      bot_secret: {
        type: 'string',
        title: 'Bot Secret',
        description: 'Intelligent robot Secret',
        placeholder: 'Bot secret',
        showWhen: { connection_mode: 'websocket' }
      },
      webhook_url: {
        type: 'string',
        title: 'Webhook URL',
        description: 'Group bot Webhook address',
        placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx',
        showWhen: { connection_mode: 'webhook' }
      }
    },
    required_by_mode: {
      websocket: ['bot_id', 'bot_secret'],
      webhook: ['webhook_url']
    }
  },
  slack: {
    type: 'object',
    properties: {
      channel_type: {
        type: 'string',
        title: 'Channel Type',
        enum: ['slack_enterprise', 'slack_connect'],
        enumLabels: {
          slack_enterprise: 'Slack Enterprise',
          slack_connect: 'Slack Connect'
        },
        default: 'slack_enterprise'
      },
      app_id: {
        type: 'string',
        title: 'App ID',
        placeholder: 'APP-8842-XN'
      },
      app_secret: {
        type: 'string',
        title: 'App Secret',
        placeholder: 'Application secret'
      },
      encrypt_key: {
        type: 'string',
        title: 'Encrypt Key',
        placeholder: 'Optional AES-256 Key'
      },
      verification_token: {
        type: 'string',
        title: 'Verification Token',
        placeholder: 'Provider Token'
      }
    },
    required: ['channel_type', 'app_id', 'app_secret']
  },
  discord: {
    type: 'object',
    properties: {
      connection_mode: {
        type: 'string',
        title: 'Connection Mode',
        description: 'Select connection mode',
        enum: ['webhook', 'bot_gateway'],
        enumLabels: {
          webhook: 'Webhook',
          bot_gateway: 'Bot Gateway'
        },
        default: 'webhook'
      },
      bot_id: {
        type: 'string',
        title: 'Bot ID',
        placeholder: 'discord-bot-9921'
      },
      bot_secret: {
        type: 'string',
        title: 'Bot Secret',
        placeholder: 'Discord application secret'
      },
      verification_token: {
        type: 'string',
        title: 'Verification Token',
        placeholder: 'Signature token'
      }
    },
    required: ['connection_mode', 'bot_id', 'bot_secret']
  }
}

const MOCK_HEALTH = {
  feishu: { latency: '42ms', latencyWidth: 84, uptime: '99.98%', uptimeWidth: 99.98 },
  dingtalk: { latency: '48ms', latencyWidth: 82, uptime: '99.96%', uptimeWidth: 99.96 },
  wecom: { latency: '51ms', latencyWidth: 79, uptime: '99.91%', uptimeWidth: 99.91 },
  slack: { latency: '36ms', latencyWidth: 91, uptime: '99.92%', uptimeWidth: 99.92 },
  discord: { latency: '57ms', latencyWidth: 76, uptime: '99.74%', uptimeWidth: 99.74 }
}

function createInitialMockState() {
  return {
    channelTypes: [],
    connections: {}
  }
}

let mockState = createInitialMockState()
const RUNTIME_STATUS_VALUES = new Set(['connected', 'disconnected', 'connecting', 'error'])
let lastRealChannelTypesSnapshot = []
const realConnectionsSnapshotByType = new Map()

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function isMockOnlyChannelType(type) {
  return MOCK_ONLY_CHANNEL_TYPES.has(type)
}

function isPlannedChannelType(type) {
  return Boolean(allChannels.find(channel => channel.type === type)?.planned)
}

function isRealStatusChannelType(type) {
  return REAL_STATUS_CHANNEL_TYPES.has(type)
}

function normalizeRuntimeStatus(status, fallback = 'disconnected') {
  if (typeof status === 'string' && RUNTIME_STATUS_VALUES.has(status)) {
    return status
  }
  return RUNTIME_STATUS_VALUES.has(fallback) ? fallback : 'disconnected'
}

function normalizeApiConnection(connection = {}, type = '') {
  return {
    ...clone(connection),
    channel_type: connection.channel_type || type,
    enabled: Boolean(connection.enabled),
    is_default: Boolean(connection.is_default),
    runtime_status: normalizeRuntimeStatus(connection.runtime_status)
  }
}

function buildChannelTypesFromSources(realChannels = []) {
  const orderedChannels = []
  const normalizedChannels = (Array.isArray(realChannels) ? realChannels : [])
    .filter(channel => channel?.type)
  const realByType = new Map(normalizedChannels.map(channel => [channel.type, channel]))
  const plannedByType = new Map(PLANNED_CHANNEL_TYPES.map(channel => [channel.type, channel]))

  CHANNEL_TYPE_ORDER.forEach(type => {
    const realChannel = realByType.get(type)
    if (realChannel) {
      orderedChannels.push(clone(realChannel))
      return
    }

    const plannedChannel = plannedByType.get(type)
    if (plannedChannel) {
      orderedChannels.push(clone(plannedChannel))
    }
  })

  normalizedChannels.forEach(channel => {
    if (!CHANNEL_TYPE_ORDER.includes(channel.type)) {
      orderedChannels.push(clone(channel))
    }
  })

  return orderedChannels
}

async function parseApiError(response) {
  const errorData = await response.json().catch(() => ({}))
  return errorData.detail || errorData.error || `HTTP ${response.status}`
}

async function fetchWithTimeout(url, init = {}, timeoutMs = VALIDATION_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal
    })
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error(t('channel.validationTimeout'))
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function normalizeConnectionConfig(type, config = {}) {
  if (!config || typeof config !== 'object') {
    return {}
  }

  switch (type) {
    case 'feishu': {
      const nextMode =
        config.connection_mode === 'webhook' || config.webhook_url ? 'webhook' : 'longconnection'

      if (nextMode === 'webhook') {
        return {
          connection_mode: 'webhook',
          webhook_url: config.webhook_url || ''
        }
      }

      return {
        connection_mode: 'longconnection',
        app_id: config.app_id || '',
        app_secret: config.app_secret || ''
      }
    }

    case 'dingtalk': {
      const nextMode =
        config.connection_mode === 'webhook' && config.webhook_url
          ? 'webhook'
          : (config.connection_mode === 'stream' || config.connection_mode === 'stream_mode'
              ? 'stream'
              : (config.webhook_url ? 'webhook' : 'stream'))

      if (nextMode === 'webhook') {
        return {
          connection_mode: 'webhook',
          webhook_url: config.webhook_url || '',
          secret: config.secret || ''
        }
      }

      return {
        connection_mode: 'stream',
        client_id: config.client_id || config.app_key || '',
        client_secret: config.client_secret || config.app_secret || ''
      }
    }

    case 'wecom': {
      const nextMode =
        config.connection_mode === 'webhook' && config.webhook_url
          ? 'webhook'
          : (config.connection_mode === 'websocket'
              ? 'websocket'
              : ((config.bot_id || config.bot_secret || config.secret || config.corpid || config.corpsecret)
                  ? 'websocket'
                  : (config.webhook_url ? 'webhook' : 'websocket')))

      if (nextMode === 'webhook') {
        return {
          connection_mode: 'webhook',
          webhook_url: config.webhook_url || ''
        }
      }

      return {
        connection_mode: 'websocket',
        bot_id: config.bot_id || config.corpid || '',
        bot_secret: config.bot_secret || config.secret || config.corpsecret || ''
      }
    }

    default:
      return clone(config)
  }
}

function normalizeMockState(savedState = {}) {
  const defaults = createInitialMockState()
  const savedChannels = Array.isArray(savedState.channelTypes) ? savedState.channelTypes : []
  const savedConnections = savedState.connections && typeof savedState.connections === 'object'
    ? savedState.connections
    : {}
  const defaultChannelsByType = new Map(defaults.channelTypes.map(channel => [channel.type, channel]))
  const defaultConnectionsByType = new Map(
    Object.entries(defaults.connections).map(([type, items]) => [
      type,
      new Map((items || []).map(item => [item.id, item]))
    ])
  )
  const savedChannelsByType = new Map(savedChannels.map(channel => [channel.type, channel]))
  const connections = { ...defaults.connections }

  Object.entries(savedConnections).forEach(([type, items]) => {
    connections[type] = Array.isArray(items)
      ? items.map(item => ({
          ...clone(defaultConnectionsByType.get(type)?.get(item?.id) || {}),
          ...clone(item),
          runtime_status: normalizeRuntimeStatus(
            item?.runtime_status,
            defaultConnectionsByType.get(type)?.get(item?.id)?.runtime_status || 'disconnected'
          ),
          config: normalizeConnectionConfig(type, item?.config || {})
        }))
      : clone(defaults.connections[type] || [])
  })

  const orderedChannelTypes = CHANNEL_TYPE_ORDER.map(type => {
    const baseChannel = defaultChannelsByType.get(type) || {
      type,
      name: type,
      mode: 'connection',
      connection_count: 0
    }
    const savedChannel = savedChannelsByType.get(type) || {}

    return {
      ...baseChannel,
      ...savedChannel,
      connection_count: Array.isArray(connections[type]) ? connections[type].length : 0
    }
  })

  const extraChannelTypes = savedChannels
    .filter(channel => channel?.type && !CHANNEL_TYPE_ORDER.includes(channel.type))
    .map(channel => ({
      ...channel,
      connection_count: Array.isArray(connections[channel.type])
        ? connections[channel.type].length
        : Number(channel.connection_count || 0)
    }))

  return {
    ...defaults,
    ...savedState,
    connections,
    channelTypes: [...orderedChannelTypes, ...extraChannelTypes]
  }
}

function hydrateMockState() {
  try {
    const saved = window?.localStorage?.getItem(MOCK_STORAGE_KEY)
    if (!saved) {
      mockState = createInitialMockState()
      return
    }
    mockState = normalizeMockState(JSON.parse(saved))
  } catch (error) {
    console.warn('[ChannelsPage] Failed to hydrate mock state:', error)
    mockState = createInitialMockState()
  }
}

function persistMockState() {
  try {
    window?.localStorage?.setItem(MOCK_STORAGE_KEY, JSON.stringify(mockState))
  } catch (error) {
    console.warn('[ChannelsPage] Failed to persist mock state:', error)
  }
}

function getMockChannel(type) {
  return mockState.channelTypes.find(channel => channel.type === type) || null
}

function getNextMockConnectionId() {
  const nextNumber = Object.values(mockState.connections)
    .flat()
    .map(connection => Number(connection.id.split('_')[1] || 0))
    .reduce((maxNumber, value) => Math.max(maxNumber, value), 0) + 1

  return `CONN_${String(nextNumber).padStart(3, '0')}`
}

function validateMockConfig(type, config) {
  const schema = MOCK_CHANNEL_SCHEMAS[type]
  if (!schema) {
    return { valid: true, errors: [] }
  }

  const requiredFields = new Set(schema.required || [])
  const modeKey = config.connection_mode || config.channel_type || ''
  const conditionalRequired = schema.required_by_mode?.[modeKey] || []
  conditionalRequired.forEach(field => requiredFields.add(field))

  const errors = [...requiredFields]
    .filter(field => !String(config[field] || '').trim())
    .map(field => `${getFieldText(field, 'title', field)} is required`)

  return {
    valid: errors.length === 0,
    errors
  }
}

// ========== API Functions ==========

/**
 * Fetch all available channel types
 */
async function fetchChannelTypes() {
  try {
    const res = await fetch('/api/channels')
    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    const channels = await res.json()
    lastRealChannelTypesSnapshot = Array.isArray(channels) ? channels : []
  } catch (error) {
    console.error('[ChannelsPage] Failed to fetch real channel types:', error)
  }

  return buildChannelTypesFromSources(lastRealChannelTypesSnapshot)
}

/**
 * Fetch channel configuration schema
 */
async function fetchChannelSchema(type) {
  if (isPlannedChannelType(type)) {
    return null
  }

  if (isMockOnlyChannelType(type)) {
    return clone(MOCK_CHANNEL_SCHEMAS[type] || null)
  }

  try {
    const res = await fetch(`/api/channels/${type}/schema`)
    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }
    return await res.json()
  } catch (error) {
    console.error(`[ChannelsPage] Failed to fetch schema for ${type}:`, error)
    return clone(MOCK_CHANNEL_SCHEMAS[type] || null)
  }
}

/**
 * Fetch connections for a channel type
 */
async function fetchConnections(type) {
  if (isPlannedChannelType(type)) {
    return {
      channel_type: type,
      connections: []
    }
  }

  if (isMockOnlyChannelType(type)) {
    return {
      channel_type: type,
      connections: clone(mockState.connections[type] || [])
    }
  }

  try {
    const res = await fetch(`/api/channels/${type}/connections`)
    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    const payload = await res.json()
    const normalized = {
      channel_type: payload?.channel_type || type,
      connections: Array.isArray(payload?.connections)
        ? payload.connections.map(connection => normalizeApiConnection(connection, type))
        : []
    }

    realConnectionsSnapshotByType.set(type, clone(normalized))
    return normalized
  } catch (error) {
    console.error(`[ChannelsPage] Failed to fetch connections for ${type}:`, error)
    return clone(realConnectionsSnapshotByType.get(type) || {
      channel_type: type,
      connections: []
    })
  }
}

/**
 * Create a new connection
 */
async function createConnection(type, data) {
  if (!isMockOnlyChannelType(type)) {
    const res = await fetch(`/api/channels/${type}/connections`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: data.name,
        config: data.config || {},
        enabled: true,
        is_default: Boolean(data.is_default)
      })
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    const connection = normalizeApiConnection(await res.json(), type)
    realConnectionsSnapshotByType.delete(type)
    return connection
  }

  const validation = validateMockConfig(type, data.config || {})
  if (!validation.valid) {
    throw new Error(validation.errors.join(', '))
  }

  const nextConnection = {
    id: getNextMockConnectionId(),
    name: data.name,
    channel_type: type,
    config: clone(data.config || {}),
    enabled: true,
    is_default: Boolean(data.is_default),
    runtime_status: 'connecting'
  }

  if (nextConnection.is_default) {
    ;(mockState.connections[type] || []).forEach(connection => {
      connection.is_default = false
    })
  }

  mockState.connections[type] = [...(mockState.connections[type] || []), nextConnection]
  const channel = getMockChannel(type)
  if (channel) {
    channel.connection_count += 1
  }
  persistMockState()
  return clone(nextConnection)
}

/**
 * Update an existing connection
 */
async function updateConnection(type, id, data) {
  if (!isMockOnlyChannelType(type)) {
    const res = await fetch(`/api/channels/${type}/connections/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: data.name,
        config: data.config,
        enabled: data.enabled,
        is_default: data.is_default
      })
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    const connection = normalizeApiConnection(await res.json(), type)
    realConnectionsSnapshotByType.delete(type)
    return connection
  }

  const connections = mockState.connections[type] || []
  const connection = connections.find(item => item.id === id)
  if (!connection) {
    throw new Error(t('channel.connectionNotFound'))
  }

  const nextConfig = data.config ? clone(data.config) : clone(connection.config)
  const validation = validateMockConfig(type, nextConfig)
  if (!validation.valid) {
    throw new Error(validation.errors.join(', '))
  }

  if (data.is_default) {
    connections.forEach(item => {
      item.is_default = false
    })
  }

  connection.name = data.name ?? connection.name
  connection.config = nextConfig
  connection.is_default = Boolean(data.is_default)
  if (typeof data.enabled === 'boolean') {
    connection.enabled = data.enabled
  }

  persistMockState()
  return clone(connection)
}

/**
 * Delete a connection
 */
async function deleteConnection(type, id) {
  if (!isMockOnlyChannelType(type)) {
    const res = await fetch(`/api/channels/${type}/connections/${id}`, {
      method: 'DELETE'
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    realConnectionsSnapshotByType.delete(type)
    return true
  }

  const connections = mockState.connections[type] || []
  const nextConnections = connections.filter(connection => connection.id !== id)
  if (nextConnections.length === connections.length) {
    throw new Error(t('channel.connectionNotFound'))
  }

  mockState.connections[type] = nextConnections
  const channel = getMockChannel(type)
  if (channel) {
    channel.connection_count = Math.max(0, channel.connection_count - 1)
  }
  persistMockState()
  return true
}

/**
 * Toggle connection enabled state
 */
async function toggleConnection(type, id, enable) {
  if (!isMockOnlyChannelType(type)) {
    const action = enable ? 'enable' : 'disable'
    const res = await fetch(`/api/channels/${type}/connections/${id}/${action}`, {
      method: 'POST'
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    realConnectionsSnapshotByType.delete(type)
    return await res.json().catch(() => ({}))
  }

  const connections = mockState.connections[type] || []
  const connection = connections.find(item => item.id === id)
  if (!connection) {
    throw new Error(t('channel.connectionNotFound'))
  }

  connection.enabled = enable
  persistMockState()
  return clone(connection)
}

/**
 * Verify connection configuration
 */
async function verifyConnection(type, id) {
  if (!isMockOnlyChannelType(type)) {
    const res = await fetchWithTimeout(`/api/channels/${type}/connections/${id}/verify`, {
      method: 'POST'
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    return await res.json()
  }

  const connection = (mockState.connections[type] || []).find(item => item.id === id)
  if (!connection) {
    throw new Error(t('channel.connectionNotFound'))
  }
  return validateMockConfig(type, connection.config || {})
}

/**
 * Validate config draft without saving
 */
async function validateConfigDraft(type, config) {
  if (!isMockOnlyChannelType(type)) {
    const res = await fetchWithTimeout(`/api/channels/${type}/validate-config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config })
    })

    if (!res.ok) {
      throw new Error(await parseApiError(res))
    }

    return await res.json()
  }

  return validateMockConfig(type, config)
}

function getRuntimeStatusMeta(runtimeStatus) {
  const normalizedStatus = normalizeRuntimeStatus(runtimeStatus)
  const statusMeta = {
    connected: {
      className: 'connected',
      text: t('channel.runtimeConnected'),
      currentText: t('channel.currentlyConnected')
    },
    disconnected: {
      className: 'disconnected',
      text: t('channel.runtimeDisconnected'),
      currentText: t('channel.currentlyDisconnected')
    },
    connecting: {
      className: 'connecting',
      text: t('channel.runtimeConnecting'),
      currentText: translateIfExists('channel.currentlyConnecting') || t('channel.runtimeConnecting')
    },
    error: {
      className: 'error',
      text: t('channel.runtimeError'),
      currentText: translateIfExists('channel.currentlyError') || t('channel.runtimeError')
    }
  }

  return statusMeta[normalizedStatus] || statusMeta.disconnected
}

function applyRuntimeStatusToConnectionRow(row, connection) {
  if (!row || !connection) return

  const statusMeta = getRuntimeStatusMeta(connection.runtime_status)
  const statusDot = row.querySelector('.ch-status-dot')
  const statusBadge = row.querySelector('.ch-status-badge')
  const toggle = row.querySelector('.ch-toggle')
  const toggleLabel = row.querySelector('.ch-toggle-label')

  if (statusDot) {
    statusDot.className = `ch-status-dot ${statusMeta.className}`
  }

  if (statusBadge) {
    statusBadge.className = `ch-status-badge ${statusMeta.className}`
    statusBadge.textContent = statusMeta.text
  }

  if (toggle) {
    toggle.classList.toggle('checked', Boolean(connection.enabled))
  }

  if (toggleLabel) {
    toggleLabel.textContent = connection.enabled ? t('channel.enabled') : t('channel.disabled')
  }
}

function updateRenderedConnectionStates(connections) {
  const rows = $$('#connectionsTableBody .ch-table-row')
  if (!rows.length) return

  const connectionMap = new Map((connections || []).map(connection => [connection.id, connection]))
  rows.forEach(row => {
    const connection = connectionMap.get(row.dataset.connId)
    if (connection) {
      applyRuntimeStatusToConnectionRow(row, connection)
    }
  })
}

function updateEditHeaderRuntimeState(connection) {
  const statusElement = $('#editConnectionStatus')
  if (!statusElement || !connection) return

  const statusMeta = getRuntimeStatusMeta(connection.runtime_status)
  statusElement.className = `ch-connection-status ${statusMeta.className}`
  statusElement.textContent = statusMeta.currentText
}

async function refreshCurrentChannelRuntimeState({ forceRerender = false } = {}) {
  if (
    !currentChannelType ||
    isMockOnlyChannelType(currentChannelType) ||
    isPlannedChannelType(currentChannelType) ||
    runtimeStatusPollInFlight
  ) {
    return
  }

  runtimeStatusPollInFlight = true
  const type = currentChannelType
  const editId = getEditModeFromURL()

  try {
    const data = await fetchConnections(type)
    if (currentChannelType !== type) {
      return
    }

    if (editId) {
      if (editId === 'new') {
        return
      }

      const currentConnection = (data.connections || []).find(connection => connection.id === editId)
      if (currentConnection) {
        updateEditHeaderRuntimeState(currentConnection)
      }
      return
    }

    const renderedRowCount = $$('#connectionsTableBody .ch-table-row').length
    if (forceRerender || renderedRowCount !== (data.connections || []).length) {
      await renderActiveConnections(type)
      return
    }

    updateRenderedConnectionStates(data.connections || [])
  } catch (error) {
    console.error(`[ChannelsPage] Failed to refresh runtime state for ${type}:`, error)
  } finally {
    runtimeStatusPollInFlight = false
  }
}

function stopRuntimeStatusPolling() {
  if (runtimeStatusPollTimer) {
    window.clearInterval(runtimeStatusPollTimer)
    runtimeStatusPollTimer = null
  }
  runtimeStatusPollInFlight = false
}

function restartRuntimeStatusPolling() {
  stopRuntimeStatusPolling()

  if (
    !currentChannelType ||
    isMockOnlyChannelType(currentChannelType) ||
    isPlannedChannelType(currentChannelType)
  ) {
    return
  }

  runtimeStatusPollTimer = window.setInterval(() => {
    refreshCurrentChannelRuntimeState()
  }, 4000)
}

// ========== UI Rendering ==========

/**
 * Render channel type cards (main grid)
 */
function renderChannelTypes(channels) {
  allChannels = channels || []
  const container = $('#channelTypeCards')

  if (!channels || channels.length === 0) {
    if (container) {
      container.innerHTML = `<div class="ch-empty" data-i18n="channel.noChannels">${t('channel.noChannels')}</div>`
      updatePageTranslations()
    }
    return
  }

  // Render cards
  if (container) {
    container.innerHTML = channels.map(channel => {
      const isSelected = currentChannelType === channel.type
      const badge = getModeBadge(channel)
      const connectionCount = Number(channel.connection_count || 0)
      const cardCopy = getChannelCardCopy(channel)
      return `
        <div class="ch-type-card ${isSelected ? 'selected' : ''}" data-type="${channel.type}">
          <div class="ch-card-header">
            <div class="ch-card-icon">${CHANNEL_ICONS[channel.type] || CHANNEL_ICONS.default}</div>
            <div class="ch-card-info">
              <span class="ch-card-name">${getChannelName(channel)}</span>
              ${badge ? `<span class="ch-card-badge ${badge.className}">${badge.text}</span>` : ''}
            </div>
          </div>
          <div class="ch-card-body">
            <p class="ch-card-description">${cardCopy.description}</p>
            <div class="ch-card-spec-grid">
              ${cardCopy.specs.map(spec => `
                <div class="ch-card-spec">
                  <span class="ch-card-spec-label">${spec.label}</span>
                  <strong class="ch-card-spec-value">${spec.value}</strong>
                </div>
              `).join('')}
            </div>
          </div>
          <div class="ch-card-divider"></div>
          <div class="ch-card-footer">
            <div class="ch-card-connections">
              <span data-i18n="channel.connections">Connections</span>
              <strong>${connectionCount}</strong>
            </div>
            <div class="ch-card-selected">
              <span>${t('channel.selected')}</span>
              <span class="ch-selected-badge">
                <svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" width="10" height="10">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              </span>
            </div>
          </div>
        </div>
      `
    }).join('')

    // Bind click events
    container.querySelectorAll('.ch-type-card').forEach(card => {
      card.addEventListener('click', () => {
        if (suppressCardClick) return
        const type = card.dataset.type
        navigateToChannel(type)
      })
    })

    updateChannelTypeSelection()
    updatePageTranslations()
  }
}

function updateChannelTypeSelection() {
  const container = $('#channelTypeCards')
  if (!container) return

  const scrollRailTo = left => {
    if (typeof container.scrollTo === 'function') {
      container.scrollTo({
        left,
        behavior: 'smooth'
      })
      return
    }
    container.scrollLeft = left
  }

  let selectedCard = null
  container.querySelectorAll('.ch-type-card').forEach(card => {
    const isSelected = card.dataset.type === currentChannelType
    card.classList.toggle('selected', isSelected)
    if (isSelected) {
      selectedCard = card
    }
  })

  if (selectedCard) {
    const visibleInset = 18
    const cardLeft = selectedCard.offsetLeft
    const cardRight = cardLeft + selectedCard.offsetWidth
    const railLeft = container.scrollLeft
    const railRight = railLeft + container.clientWidth

    if (cardLeft < railLeft + visibleInset) {
      scrollRailTo(Math.max(cardLeft - visibleInset, 0))
    } else if (cardRight > railRight - visibleInset) {
      scrollRailTo(
        Math.min(
          cardRight - container.clientWidth + visibleInset,
          container.scrollWidth - container.clientWidth
        )
      )
    }
  }
  window.requestAnimationFrame(() => syncChannelRailState?.())
}

function setValidateButtonState(isLoading) {
  const button = $('#btnValidate')
  if (!button) return

  button.disabled = isLoading
  button.classList.toggle('is-loading', isLoading)
  button.setAttribute('aria-busy', isLoading ? 'true' : 'false')
  button.innerHTML = isLoading
    ? `${ACTION_ICONS.spinner}<span data-i18n="channel.validatingConnection">${t('channel.validatingConnection')}</span>`
    : `${ACTION_ICONS.shield}<span data-i18n="channel.validateConnection">${t('channel.validateConnection')}</span>`
}

/**
 * Render Active Connections section
 */
async function renderActiveConnections(type) {
  const section = $('#connectionsSection')
  if (!section) return

  const channelInfo = allChannels.find(c => c.type === type) || { type, name: type }
  const isPlanned = Boolean(channelInfo.planned)
  const data = await fetchConnections(type)
  const connections = data.connections || []
  const totalConnections = Number(channelInfo.connection_count || connections.length)

  section.innerHTML = `
    <div class="ch-connections-section">
      <div class="ch-connections-header">
        <div class="ch-connections-title">
          <h2 data-i18n="channel.activeConnections">Active Connections</h2>
          <p>${t('channel.managingEndpoints', { type: getChannelName(channelInfo) })}</p>
          ${isPlanned
            ? `<p class="ch-planned-note" data-i18n="channel.plannedChannelNotice">${t('channel.plannedChannelNotice')}</p>`
            : ''}
        </div>
        <button class="ch-btn-create" id="btnCreateConnection" data-action="create" ${isPlanned ? 'disabled aria-disabled="true"' : ''}>
          ${ACTION_ICONS.plus}
          <span data-i18n="${isPlanned ? 'channel.comingSoon' : 'channel.createNewConnection'}">${t(isPlanned ? 'channel.comingSoon' : 'channel.createNewConnection')}</span>
        </button>
      </div>
      <div class="ch-table">
        <div class="ch-table-header">
          <div data-i18n="channel.tableConnectionName">CONNECTION NAME</div>
          <div data-i18n="channel.tableType">TYPE</div>
          <div data-i18n="channel.tableRuntimeStatus">RUNTIME STATUS</div>
          <div data-i18n="channel.tableSettings">SETTINGS</div>
          <div data-i18n="channel.tableActions">ACTIONS</div>
        </div>
        <div class="ch-table-body" id="connectionsTableBody">
          ${connections.length === 0
            ? `<div class="ch-table-empty" style="grid-column: 1/-1; padding: 24px; text-align: center; color: var(--color-text-secondary);">${t(isPlanned ? 'channel.plannedConnectionsEmpty' : 'channel.noConnections')}</div>`
            : connections.map(conn => renderConnectionRow(conn)).join('')
          }
        </div>
      </div>
      <div class="ch-table-footer">
        <span class="ch-table-info">${t('channel.showingConnections', { current: connections.length, total: totalConnections, type: getChannelName(channelInfo) })}</span>
        <div class="ch-table-pagination">
          <button class="ch-pagination-btn" disabled>
            ${ACTION_ICONS.arrowLeft}
            <span>${t('channel.previous')}</span>
          </button>
          <button class="ch-pagination-btn" disabled>
            <span>${t('channel.next')}</span>
            ${ACTION_ICONS.arrowRight}
          </button>
        </div>
      </div>
    </div>
  `

  // Show the section
  section.style.display = 'block'

  // Render health panel
  if (isPlanned) {
    const panel = $('#healthPanel')
    if (panel) panel.style.display = 'none'
  } else {
    renderConnectionHealth(type)
  }

  // Bind events
  bindConnectionsEvents()
  updatePageTranslations()
}

/**
 * Render a single connection row
 */
function renderConnectionRow(conn) {
  const statusMeta = getRuntimeStatusMeta(conn.runtime_status)
  const toggleLabel = conn.enabled ? t('channel.enabled') : t('channel.disabled')
  const idShort = conn.id.substring(0, 8).toUpperCase()
  const channelLabel = getChannelName({ type: conn.channel_type, name: conn.channel_type })
  const connectionName = getConnectionDisplayName(conn)
  const idLabel = t('channel.idLabel')
  const editTitle = t('channel.edit')
  const deleteTitle = t('channel.delete')

  return `
    <div class="ch-table-row" data-conn-id="${conn.id}">
      <div class="ch-cell-name">
        <span class="ch-status-dot ${statusMeta.className}"></span>
        <div class="ch-cell-name-content">
          <span class="ch-cell-name-text">${connectionName}</span>
        </div>
        <span class="ch-id-badge">${idLabel}:${idShort}</span>
      </div>
      <div class="ch-cell-type">
        ${CHANNEL_ICONS[conn.channel_type] || CHANNEL_ICONS.default}
        <span>${channelLabel}</span>
      </div>
      <div class="ch-cell-status">
        <span class="ch-status-badge ${statusMeta.className}">${statusMeta.text}</span>
      </div>
      <div class="ch-cell-settings">
        <button type="button" class="ch-toggle ${conn.enabled ? 'checked' : ''}" data-conn-id="${conn.id}" data-action="toggle"></button>
        <span class="ch-toggle-label">${toggleLabel}</span>
      </div>
      <div class="ch-cell-actions">
        <button class="ch-action-btn" data-conn-id="${conn.id}" data-action="edit" title="${editTitle}" aria-label="${editTitle}">${ACTION_ICONS.edit}</button>
        <button class="ch-action-btn delete" data-conn-id="${conn.id}" data-action="delete" title="${deleteTitle}" aria-label="${deleteTitle}">${ACTION_ICONS.delete}</button>
      </div>
    </div>
  `
}

/**
 * Render Connection Health panel
 */
function renderConnectionHealth(type) {
  const panel = $('#healthPanel')
  if (!panel) return
  const health = MOCK_HEALTH[type] || MOCK_HEALTH.feishu

  panel.innerHTML = `
    <div class="ch-health-panel">
      <div class="ch-health-header">
        <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
          <path d="M12 2L4 8l8 14 8-14-8-6zM8.5 8L12 5.5 15.5 8l-3.5 6-3.5-6z"/>
        </svg>
        <h3 data-i18n="channel.connectionHealth">Connection Health</h3>
      </div>
      <div class="ch-health-metrics">
        <div class="ch-health-metric">
          <div class="ch-health-metric-header">
            <span class="ch-health-metric-label" data-i18n="channel.averageLatency">Average Latency</span>
            <span class="ch-health-metric-value">${health.latency}</span>
          </div>
          <div class="ch-progress-bar">
            <div class="ch-progress-bar-fill latency" style="width: ${health.latencyWidth}%;"></div>
          </div>
        </div>
        <div class="ch-health-metric">
          <div class="ch-health-metric-header">
            <span class="ch-health-metric-label" data-i18n="channel.uptime24h">Uptime (24h)</span>
            <span class="ch-health-metric-value">${health.uptime}</span>
          </div>
          <div class="ch-progress-bar">
            <div class="ch-progress-bar-fill uptime" style="width: ${health.uptimeWidth}%;"></div>
          </div>
        </div>
      </div>
    </div>
  `

  // Show the panel
  panel.style.display = 'block'
}

/**
 * Handle route change - render appropriate view based on URL
 */
async function handleRouteChange() {
  if (!mounted && !pageContainer) return

  const type = getChannelTypeFromURL()
  const edit = getEditModeFromURL()

  currentChannelType = type

  if (allChannels.length > 0) {
    updateChannelTypeSelection()
  }

  const channelListView = $('#channelListView')
  const channelEditView = $('#channelEditView')

  if (edit && type && isPlannedChannelType(type)) {
    navigateToChannel(type)
    return
  }

  if (edit) {
    // Show edit view
    if (channelListView) channelListView.style.display = 'none'
    if (channelEditView) channelEditView.style.display = 'block'
    await renderEditView(type, edit)
  } else if (type) {
    // Show list view with selected channel's connections
    if (channelListView) channelListView.style.display = 'block'
    if (channelEditView) channelEditView.style.display = 'none'
    await renderActiveConnections(type)
  } else {
    // Show list view without connections
    if (channelListView) channelListView.style.display = 'block'
    if (channelEditView) channelEditView.style.display = 'none'
    const section = $('#connectionsSection')
    const panel = $('#healthPanel')
    if (section) section.style.display = 'none'
    if (panel) panel.style.display = 'none'
  }

  restartRuntimeStatusPolling()
}

/**
 * Render edit view for a connection
 */
async function renderEditView(type, editId) {
  const container = $('#channelEditView')
  if (!container) return

  editingConnectionId = editId === 'new' ? null : editId
  const channelInfo = allChannels.find(channel => channel.type === type) || { type, name: type }
  const channelLabel = getChannelName(channelInfo)

  // Fetch schema and existing connection data
  const [schema, connectionsData] = await Promise.all([
    fetchChannelSchema(type),
    editId !== 'new' ? fetchConnections(type) : Promise.resolve({ connections: [] })
  ])

  currentSchema = schema

  let connectionData = {}
  if (editId !== 'new') {
    connectionData = (connectionsData.connections || []).find(c => c.id === editId) || {}
  }

  const statusMeta = getRuntimeStatusMeta(connectionData.runtime_status)
  container.innerHTML = `
    <!-- Breadcrumb -->
    <div class="ch-breadcrumb">
      <a href="#" class="ch-breadcrumb-item" id="breadcrumbBack" data-i18n="channel.title">Channel Management</a>
      <span class="ch-breadcrumb-separator">›</span>
      <span class="ch-breadcrumb-item">${channelLabel}</span>
      <span class="ch-breadcrumb-separator">›</span>
      <span class="ch-breadcrumb-item active" data-i18n="channel.configureConnection">Configure Connection</span>
    </div>

    <!-- Header -->
    <div class="ch-edit-header">
      <div class="ch-edit-header-content">
        <h1 data-i18n="channel.channelConnection">Channel Connection</h1>
        <p data-i18n="channel.channelConnectionDesc">Configure how your AI agents communicate with external platforms. Ensure credentials match your provider's developer console.</p>
      </div>
      <div class="ch-connection-status ${statusMeta.className}" id="editConnectionStatus" data-conn-id="${connectionData.id || ''}">
        ${statusMeta.currentText}
      </div>
    </div>

    <!-- Main Content -->
    <div class="ch-edit-layout">
      <div class="ch-edit-main">
        <div class="ch-form-card">
          ${renderConfigForm(schema, connectionData)}
        </div>
        <!-- Action Bar -->
        <div class="ch-action-bar">
          <button class="ch-btn-validate" id="btnValidate">
            ${ACTION_ICONS.shield}
            <span data-i18n="channel.validateConnection">Validate Connection</span>
          </button>
          <button class="ch-btn-cancel" id="btnCancelEdit" data-i18n="channel.cancel">Cancel</button>
          <button class="ch-btn-save" id="btnSaveEdit" data-i18n="channel.saveChanges">Save Changes</button>
        </div>
      </div>

      <div class="ch-edit-sidebar">
        <!-- Primary Status Card -->
        <div class="ch-primary-card">
          <div class="ch-primary-card-header">
            <h4 data-i18n="channel.primaryStatus">Primary Status</h4>
            <button type="button" class="ch-toggle ${connectionData.is_default ? 'checked' : ''}" id="primaryToggle"></button>
          </div>
          <p data-i18n="channel.primaryStatusDesc">${t('channel.primaryStatusDesc')}</p>
        </div>

        <!-- Security Best Practices Card -->
        <div class="ch-security-card">
          <div class="ch-security-card-header">
            <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
              <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/>
            </svg>
            <h4 data-i18n="channel.securityBestPractices">Security Best Practices</h4>
          </div>
          <div class="ch-security-list">
            <div class="ch-security-item">
              <span class="ch-security-check">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" width="10" height="10">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              </span>
              <span data-i18n="channel.securityTip1">Rotate your App Secret every 90 days.</span>
            </div>
            <div class="ch-security-item">
              <span class="ch-security-check">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" width="10" height="10">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              </span>
              <span data-i18n="channel.securityTip2">Always use the 'Validate Connection' tool before saving changes to production.</span>
            </div>
            <div class="ch-security-item">
              <span class="ch-security-check">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" width="10" height="10">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              </span>
              <span data-i18n="channel.securityTip3">Ensure your callback URL is restricted to the Atlas IP range.</span>
            </div>
          </div>
        </div>

      </div>
    </div>
  `

  // Bind events
  bindEditViewEvents()
  bindModeChangeEvents(schema)
  updatePageTranslations()
}

/**
 * Render config form from JSON Schema
 */
function renderConfigForm(schema, values = {}) {
  if (!schema || !schema.properties) {
    return `<div class="ch-form-error" data-i18n="channel.schemaLoadFailed">${t('channel.schemaLoadFailed')}</div>`
  }

  const properties = schema.properties || {}
  const required = schema.required || []
  const requiredByMode = schema.required_by_mode || {}
  const connectionNamePlaceholder =
    translateIfExists(`channel.connectionNamePlaceholder_${currentChannelType}`) ||
    t('channel.connectionNamePlaceholder')
  const connectionNameValue = getConnectionDisplayName(values)
  const getFieldGroupClassName = key => {
    if (key === 'connection_mode' || key === 'channel_type') {
      return 'ch-form-group ch-form-group-compact'
    }
    return 'ch-form-group'
  }
  const preferredFieldPairs = {
    feishu: [
      ['app_id', 'app_secret']
    ],
    dingtalk: [
      ['client_id', 'client_secret'],
      ['app_key', 'app_secret']
    ],
    wecom: [
      ['bot_id', 'bot_secret'],
      ['corpid', 'corpsecret']
    ],
    slack: [
      ['app_id', 'app_secret'],
      ['encrypt_key', 'verification_token']
    ],
    discord: [
      ['bot_id', 'bot_secret']
    ]
  }

  // Connection name field
  let html = `
    <div class="ch-form-group">
      <label class="ch-form-label">${t('channel.connectionName')} <span class="required">*</span></label>
      <input type="text" class="ch-form-input" name="_name" value="${connectionNameValue}" 
             placeholder="${connectionNamePlaceholder}" required>
    </div>
  `

  // Get current connection_mode value for conditional rendering
  const connectionModeValue = values.config?.connection_mode
  const currentMode = (connectionModeValue !== undefined && connectionModeValue !== '') 
                      ? connectionModeValue 
                      : (properties.connection_mode?.default || '')

  // Track fields for side-by-side layout
  const fieldPairs = (preferredFieldPairs[currentChannelType] || [])
    .filter(([firstKey, secondKey]) => properties[firstKey] && properties[secondKey])
  const fieldPairByField = new Map()
  fieldPairs.forEach((pair, index) => {
    const [firstKey, secondKey] = pair
    fieldPairByField.set(firstKey, { index, position: 'first' })
    fieldPairByField.set(secondKey, { index, position: 'second' })
  })
  const processedPairs = new Set()

  // Generate fields from schema properties
  for (const [key, prop] of Object.entries(properties)) {
    const rawValue = values.config?.[key]
    const value = (rawValue !== undefined && rawValue !== '') ? rawValue : (prop.default || '')

    // Check if this field is part of a pair and should be processed together
    const pairMeta = fieldPairByField.get(key)
    const pairIndex = pairMeta?.index ?? -1
    const isPaired = Boolean(pairMeta)
    const isFirstInPair = pairMeta?.position === 'first'
    const isSecondInPair = pairMeta?.position === 'second'

    // Skip second field in pair - it will be rendered with the first
    if (isSecondInPair && processedPairs.has(pairIndex)) {
      continue
    }

    // Check if field has showWhen condition
    const showWhen = prop.showWhen
    let shouldShow = true
    let showWhenAttr = ''

    if (showWhen && typeof showWhen === 'object') {
      showWhenAttr = `data-show-when='${JSON.stringify(showWhen)}'`
      for (const [condKey, condValue] of Object.entries(showWhen)) {
        const currentCondValue = values.config?.[condKey] || properties[condKey]?.default || ''
        if (currentCondValue !== condValue) {
          shouldShow = false
          break
        }
      }
    }

    // Determine if field is required based on current mode
    let isRequired = required.includes(key)
    if (requiredByMode && currentMode) {
      const modeRequired = requiredByMode[currentMode] || []
      if (modeRequired.includes(key)) {
        isRequired = true
      }
    }

    const title = getFieldText(key, 'title', prop.title || key)
    const description = getFieldText(key, 'description', prop.description || '')
    const placeholder = getFieldText(key, 'placeholder', prop.placeholder || prop.description || '')

    // Handle paired fields
    if (isPaired && isFirstInPair) {
      processedPairs.add(pairIndex)
      const secondKey = fieldPairs[pairIndex][1]
      const secondProp = properties[secondKey]
      
      if (secondProp) {
        const secondRawValue = values.config?.[secondKey]
        const secondValue = (secondRawValue !== undefined && secondRawValue !== '') ? secondRawValue : (secondProp.default || '')
        const secondTitle = getFieldText(secondKey, 'title', secondProp.title || secondKey)
        const secondDescription = getFieldText(secondKey, 'description', secondProp.description || '')
        const secondPlaceholder = getFieldText(secondKey, 'placeholder', secondProp.placeholder || secondProp.description || '')
        
        let secondIsRequired = required.includes(secondKey)
        if (requiredByMode && currentMode) {
          const modeRequired = requiredByMode[currentMode] || []
          if (modeRequired.includes(secondKey)) {
            secondIsRequired = true
          }
        }

        const secondIsPassword = secondProp.type === 'string' && 
          (secondKey.toLowerCase().includes('secret') || secondKey.toLowerCase().includes('password') || secondKey.toLowerCase().includes('key'))

        // Handle enum fields in pairs
        if (prop.enum && Array.isArray(prop.enum)) {
          const enumLabels = prop.enumLabels || {}
          html += `
            <div class="ch-form-row" ${showWhenAttr} style="${shouldShow ? '' : 'display: none;'}">
              <div class="ch-form-group">
                <label class="ch-form-label">${title.toUpperCase()} ${isRequired ? '<span class="required">*</span>' : ''}</label>
                <select name="${key}" class="ch-form-select" data-mode-selector="${key === 'connection_mode'}">
                  ${prop.enum.map(opt => {
                    const label = getFieldText(`${key}.${opt}`, 'label', enumLabels[opt] || opt)
                    const selected = value === opt ? 'selected' : ''
                    return `<option value="${opt}" ${selected}>${label}</option>`
                  }).join('')}
                </select>
                ${description ? `<span class="ch-form-hint">${description}</span>` : ''}
              </div>
              <div class="ch-form-group">
                <label class="ch-form-label">${secondTitle.toUpperCase()} ${secondIsRequired ? '<span class="required">*</span>' : ''}</label>
                ${secondIsPassword ? `
                  <div class="ch-password-wrapper">
                    <input type="password" class="ch-form-input" name="${secondKey}" value="${secondValue}" 
                           placeholder="${secondPlaceholder}" ${secondIsRequired ? 'data-conditional-required="true"' : ''}>
                    <button type="button" class="ch-password-toggle" data-target="${secondKey}">
                      ${ACTION_ICONS.eye}
                    </button>
                  </div>
                ` : `
                  <input type="text" class="ch-form-input" name="${secondKey}" value="${secondValue}" 
                         placeholder="${secondPlaceholder}" ${secondIsRequired ? 'data-conditional-required="true"' : ''}>
                `}
                ${secondDescription ? `<span class="ch-form-hint">${secondDescription}</span>` : ''}
              </div>
            </div>
          `
        } else {
          // Both are regular text/password fields
          const isPassword = prop.type === 'string' && 
            (key.toLowerCase().includes('secret') || key.toLowerCase().includes('password') || key.toLowerCase().includes('key'))
          
          html += `
            <div class="ch-form-row" ${showWhenAttr} style="${shouldShow ? '' : 'display: none;'}">
              <div class="ch-form-group">
                <label class="ch-form-label">${title.toUpperCase()} ${isRequired ? '<span class="required">*</span>' : ''}</label>
                ${isPassword ? `
                  <div class="ch-password-wrapper">
                    <input type="password" class="ch-form-input" name="${key}" value="${value}" 
                           placeholder="${placeholder}" ${isRequired ? 'data-conditional-required="true"' : ''}>
                    <button type="button" class="ch-password-toggle" data-target="${key}">
                      ${ACTION_ICONS.eye}
                    </button>
                  </div>
                ` : `
                  <input type="text" class="ch-form-input" name="${key}" value="${value}" 
                         placeholder="${placeholder}" ${isRequired ? 'data-conditional-required="true"' : ''}>
                `}
                ${description ? `<span class="ch-form-hint">${description}</span>` : ''}
              </div>
              <div class="ch-form-group">
                <label class="ch-form-label">${secondTitle.toUpperCase()} ${secondIsRequired ? '<span class="required">*</span>' : ''}</label>
                ${secondIsPassword ? `
                  <div class="ch-password-wrapper">
                    <input type="password" class="ch-form-input" name="${secondKey}" value="${secondValue}" 
                           placeholder="${secondPlaceholder}" ${secondIsRequired ? 'data-conditional-required="true"' : ''}>
                    <button type="button" class="ch-password-toggle" data-target="${secondKey}">
                      ${ACTION_ICONS.eye}
                    </button>
                  </div>
                ` : `
                  <input type="text" class="ch-form-input" name="${secondKey}" value="${secondValue}" 
                         placeholder="${secondPlaceholder}" ${secondIsRequired ? 'data-conditional-required="true"' : ''}>
                `}
                ${secondDescription ? `<span class="ch-form-hint">${secondDescription}</span>` : ''}
              </div>
            </div>
          `
        }
        continue
      }
    }

    // Skip if this is the second field in a processed pair
    if (isSecondInPair) {
      continue
    }

    // Handle enum fields (render as select dropdown) - standalone
    if (prop.enum && Array.isArray(prop.enum)) {
      const enumLabels = prop.enumLabels || {}
      const isModeSelector = key === 'connection_mode'
      const selectAttributes = []
      let optionMarkup = ''

      if (isModeSelector) {
        selectAttributes.push('data-mode-selector="true"')
      }

      const selectOptions = prop.enum.map(opt => ({
        value: opt,
        label: getFieldText(`${key}.${opt}`, 'label', enumLabels[opt] || opt)
      }))
      optionMarkup = renderSelectOptions(selectOptions, value)

      html += `
        <div class="${getFieldGroupClassName(key)}" ${showWhenAttr} style="${shouldShow ? '' : 'display: none;'}">
          <label class="ch-form-label">${title.toUpperCase()} ${isRequired ? '<span class="required">*</span>' : ''}</label>
          <select name="${key}" class="ch-form-select" ${selectAttributes.join(' ')}>
            ${optionMarkup}
          </select>
          ${description ? `<span class="ch-form-hint">${description}</span>` : ''}
        </div>
      `
      continue
    }

    // Regular text/password field - standalone
    const isPassword = prop.type === 'string' && 
      (key.toLowerCase().includes('secret') || key.toLowerCase().includes('password') || key.toLowerCase().includes('key'))
    const inputType = isPassword ? 'password' : 'text'

    html += `
      <div class="${getFieldGroupClassName(key)}" ${showWhenAttr} style="${shouldShow ? '' : 'display: none;'}">
        <label class="ch-form-label">${title.toUpperCase()} ${isRequired ? '<span class="required">*</span>' : ''}</label>
        ${isPassword ? `
          <div class="ch-password-wrapper">
            <input type="${inputType}" class="ch-form-input" name="${key}" value="${value}" 
                   placeholder="${placeholder}" ${isRequired ? 'data-conditional-required="true"' : ''}>
            <button type="button" class="ch-password-toggle" data-target="${key}">
              ${ACTION_ICONS.eye}
            </button>
          </div>
        ` : `
          <input type="${inputType}" class="ch-form-input" name="${key}" value="${value}" 
                 placeholder="${placeholder}" ${isRequired ? 'data-conditional-required="true"' : ''}>
        `}
        ${description ? `<span class="ch-form-hint">${description}</span>` : ''}
      </div>
    `
  }

  return html
}

/**
 * Handle connection mode change - show/hide fields dynamically
 */
function handleModeChange(selectElement, schema) {
  const selectedMode = selectElement.value
  const form = selectElement.closest('.ch-form-card') || selectElement.closest('#configForm')
  if (!form) return

  const requiredByMode = schema?.required_by_mode || {}
  const modeRequired = requiredByMode[selectedMode] || []
  const updateFieldGroupState = fieldGroup => {
    const input = fieldGroup.querySelector('input, select')
    if (!input || !input.name) return

    const isRequired = modeRequired.includes(input.name)
    if (isRequired) {
      input.setAttribute('data-conditional-required', 'true')
    } else {
      input.removeAttribute('data-conditional-required')
    }

    const label = fieldGroup.querySelector('.ch-form-label')
    if (!label) return

    const existingRequired = label.querySelector('.required')
    if (isRequired && !existingRequired) {
      label.innerHTML += ' <span class="required">*</span>'
    } else if (!isRequired && existingRequired) {
      existingRequired.remove()
    }
  }

  // Find all fields with showWhen conditions
  form.querySelectorAll('[data-show-when]').forEach(group => {
    const showWhen = JSON.parse(group.dataset.showWhen)
    let shouldShow = true

    for (const [condKey, condValue] of Object.entries(showWhen)) {
      const condInput = form.querySelector(`[name="${condKey}"]`)
      if (condInput && condInput.value !== condValue) {
        shouldShow = false
        break
      }
    }

    group.style.display = shouldShow ? '' : 'none'

    const nestedFieldGroups = group.matches('.ch-form-row')
      ? group.querySelectorAll('.ch-form-group')
      : []

    if (nestedFieldGroups.length > 0) {
      nestedFieldGroups.forEach(updateFieldGroupState)
      return
    }

    updateFieldGroupState(group)
  })
}

/**
 * Bind mode change events after form render
 */
function bindModeChangeEvents(schema) {
  const form = $('.ch-form-card')
  if (!form) return

  form.querySelectorAll('select[data-mode-selector="true"]').forEach(select => {
    select.addEventListener('change', () => handleModeChange(select, schema))
  })

  form.querySelectorAll('select[data-force-unselected="true"]').forEach(select => {
    markSelectUnselected(select)
    delete select.dataset.forceUnselected
  })

  form.querySelectorAll('.ch-select-clear').forEach(button => {
    button.addEventListener('click', () => handleSelectClear(button, schema))
  })

  syncSelectClearButtons(form)

  // Bind password toggle events
  form.querySelectorAll('.ch-password-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetName = btn.dataset.target
      const input = form.querySelector(`input[name="${targetName}"]`)
      if (input) {
        const isPassword = input.type === 'password'
        input.type = isPassword ? 'text' : 'password'
        btn.innerHTML = isPassword ? ACTION_ICONS.eyeOff : ACTION_ICONS.eye
      }
    })
  })
}

// ========== Event Binding ==========

/**
 * Bind delete dialog events
 */
function bindDeleteDialogEvents() {
  $('#btnCancelDelete')?.addEventListener('click', hideDeleteDialog)
  $('#btnConfirmDelete')?.addEventListener('click', handleDelete)
}

/**
 * Bind events for connections section
 */
function bindConnectionsEvents() {
  // Create new connection button
  $('#btnCreateConnection')?.addEventListener('click', () => {
    navigateToChannel(currentChannelType, 'new')
  })

  // Table row actions (using event delegation)
  const tableBody = $('#connectionsTableBody')
  if (tableBody) {
    tableBody.addEventListener('click', (e) => {
      // Handle toggle button click
      const toggle = e.target.closest('.ch-toggle[data-action="toggle"]')
      if (toggle) {
        const connId = toggle.dataset.connId
        const isCurrentlyEnabled = toggle.classList.contains('checked')
        handleToggle(connId, !isCurrentlyEnabled)
        return
      }

      // Handle action buttons
      const btn = e.target.closest('.ch-action-btn')
      if (btn) {
        const action = btn.dataset.action
        const connId = btn.dataset.connId
        handleConnectionAction(action, connId)
        return
      }
    })
  }
}

/**
 * Bind events for edit view
 */
function bindEditViewEvents() {
  // Breadcrumb back link
  $('#breadcrumbBack')?.addEventListener('click', (e) => {
    e.preventDefault()
    navigateToChannel(currentChannelType)
  })

  // Cancel button
  $('#btnCancelEdit')?.addEventListener('click', () => {
    navigateToChannel(currentChannelType)
  })

  // Save button
  $('#btnSaveEdit')?.addEventListener('click', handleSave)

  // Validate button
  $('#btnValidate')?.addEventListener('click', handleVerify)
  setValidateButtonState(false)

  // Primary toggle click handler
  $('#primaryToggle')?.addEventListener('click', (e) => {
    e.target.classList.toggle('checked')
  })
}

// ========== Form Actions ==========

/**
 * Handle connection actions (settings/edit/delete)
 */
async function handleConnectionAction(action, connectionId) {
  switch (action) {
    case 'settings':
    case 'edit':
      navigateToChannel(currentChannelType, connectionId)
      break
    case 'delete':
      showDeleteConfirm(connectionId)
      break
  }
}

/**
 * Handle form save
 */
async function handleSave() {
  const form = $('.ch-form-card')
  if (!form) return

  const inputs = form.querySelectorAll('input, select')
  const config = {}
  let name = ''
  let hasValidationError = false

  // Validate all required fields (only visible ones with conditional-required)
  for (const input of inputs) {
    const formGroup = input.closest('.ch-form-group') || input.closest('.ch-form-row')
    const isVisible = formGroup && formGroup.style.display !== 'none'
    const isConditionalRequired = input.hasAttribute('data-conditional-required')
    const isRequired = input.required || (isVisible && isConditionalRequired)

    if (isRequired && isVisible && !input.value.trim()) {
      input.classList.add('ch-input-error')
      hasValidationError = true
    } else {
      input.classList.remove('ch-input-error')
    }

    if (input.name === '_name') {
      name = input.value.trim()
    } else if (input.value.trim()) {
      config[input.name] = input.value.trim()
    }
  }

  if (hasValidationError) {
    showToast(t('channel.requiredFieldsMissing') || 'Please fill in all required fields', 'error')
    return
  }

  if (!name) {
    showToast(t('channel.nameRequired'), 'error')
    return
  }

  // Get is_default value from primary toggle
  const primaryToggle = $('#primaryToggle')
  const isDefault = primaryToggle ? primaryToggle.classList.contains('checked') : false

  try {
    if (editingConnectionId) {
      await updateConnection(currentChannelType, editingConnectionId, { name, config, is_default: isDefault })
      showToast(t('channel.updateSuccess'), 'success')
    } else {
      await createConnection(currentChannelType, { name, config, is_default: isDefault })
      showToast(t('channel.createSuccess'), 'success')
    }

    // Navigate back to list view
    navigateToChannel(currentChannelType)
    await refreshChannelTypes()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Handle verify button - validate config without saving
 */
async function handleVerify() {
  if (verificationInFlight) return

  const form = $('.ch-form-card')
  if (!form) return

  const inputs = form.querySelectorAll('input, select')
  const config = {}
  let hasValidationError = false

  // Collect config from form and validate visible required fields
  for (const input of inputs) {
    // Skip connection name field - it's not part of config
    if (input.name === '_name') continue

    const formGroup = input.closest('.ch-form-group') || input.closest('.ch-form-row')
    const isVisible = formGroup && formGroup.style.display !== 'none'
    const isConditionalRequired = input.hasAttribute('data-conditional-required')
    const isRequired = input.required || (isVisible && isConditionalRequired)

    if (isRequired && isVisible && !input.value.trim()) {
      input.classList.add('ch-input-error')
      hasValidationError = true
    } else {
      input.classList.remove('ch-input-error')
    }

    // Collect config value
    if (input.value.trim()) {
      config[input.name] = input.value.trim()
    }
  }

  if (hasValidationError) {
    showToast(t('channel.requiredFieldsMissing') || 'Please fill in all required fields', 'error')
    return
  }

  verificationInFlight = true
  setValidateButtonState(true)

  try {
    const result = await validateConfigDraft(currentChannelType, config)
    if (result.valid) {
      showToast(t('channel.verifySuccess'), 'success')
    } else {
      showToast(result.errors?.join(', ') || t('channel.verifyFailed'), 'error')
    }
  } catch (error) {
    showToast(error.message, 'error')
  } finally {
    verificationInFlight = false
    setValidateButtonState(false)
  }
}

/**
 * Handle toggle enable/disable
 */
async function handleToggle(connectionId, enable) {
  try {
    await toggleConnection(currentChannelType, connectionId, enable)
    showToast(enable 
      ? t('channel.enableSuccess') 
      : t('channel.disableSuccess'), 'success')
    // Refresh connections
    await renderActiveConnections(currentChannelType)
    if (!isMockOnlyChannelType(currentChannelType)) {
      window.setTimeout(() => {
        refreshCurrentChannelRuntimeState({ forceRerender: true })
      }, 1200)
    }
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Handle delete confirmation
 */
async function handleDelete() {
  if (!pendingDeleteId) return

  try {
    await deleteConnection(currentChannelType, pendingDeleteId)
    showToast(t('channel.deleteSuccess'), 'success')
    hideDeleteDialog()
    // Refresh connections
    await renderActiveConnections(currentChannelType)
    await refreshChannelTypes()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

/**
 * Refresh channel types list
 */
async function refreshChannelTypes() {
  const channels = await fetchChannelTypes()
  renderChannelTypes(channels)
  updatePageTranslations()
}

// ========== Delete Dialog ==========

/**
 * Show delete confirmation dialog
 */
function showDeleteConfirm(connectionId) {
  pendingDeleteId = connectionId
  const dialog = $('#deleteDialog')
  if (dialog) {
    dialog.style.display = 'flex'
    dialog.classList.add('visible')
  }
}

/**
 * Hide delete dialog
 */
function hideDeleteDialog() {
  pendingDeleteId = null
  const dialog = $('#deleteDialog')
  if (dialog) {
    dialog.classList.remove('visible')
    dialog.style.display = 'none'
  }
}

export default { mount, unmount }
