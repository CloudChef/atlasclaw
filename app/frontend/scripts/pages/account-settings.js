/**
 * account-settings.js - Account Settings Page Module
 *
 * Personal account profile and security settings for SPA architecture.
 */

import { getCurrentLocale, t, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { checkAuth } from '../auth.js'

const ACCOUNT_UI_PREFS_KEY = 'atlasclaw_account_ui_preferences'

const DEFAULT_UI_PREFS = {
  twoFactor: false,
  biometric: false,
  betaFeatures: true,
  emailReports: true
}

let containerRef = null
let currentProfile = null
let currentUiPrefs = { ...DEFAULT_UI_PREFS }
let eventCleanupFns = []
let isProfileEditing = false

const PAGE_HTML = `
<div class="account-settings-page">
  <div class="account-settings-shell">
    <aside class="account-profile-pane">
      <article class="account-profile-card">
        <div class="account-avatar-spotlight">
          <div class="account-avatar-ring">
            <div class="account-avatar-shell" id="accountAvatarShell">
              <span id="accountAvatarFallback">A</span>
            </div>
            <button type="button" class="account-avatar-edit-btn" id="accountAvatarEditBtn" data-i18n-aria-label="account.uploadAvatar" aria-label="Upload avatar">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 16V5"></path>
                <path d="m7 10 5-5 5 5"></path>
                <path d="M20 16.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5"></path>
              </svg>
            </button>
            <input id="accountAvatarFileInput" class="account-avatar-file-input" type="file" accept="image/png,image/jpeg,image/webp,image/gif">
          </div>
        </div>

        <div class="account-profile-intro">
          <h2 id="accountIdentityName">Atlas User</h2>
          <span id="accountSummaryRole" class="account-profile-role">Administrator</span>
          <span id="accountProfileEmail" class="account-profile-email">user@example.com</span>
        </div>

        <div class="account-profile-divider"></div>

        <p class="account-profile-bio" id="accountProfileBio" data-i18n="account.profileBio">
          This workspace profile controls how your identity appears in notifications, approvals, and administrative activity across AtlasClaw.
        </p>

        <button type="button" class="btn-primary account-profile-edit-btn" id="accountEditPublicBtn" data-i18n="account.editPublicProfile">
          Edit Public Profile
        </button>
      </article>

      <article class="account-profile-metrics">
        <div class="account-metric-row">
          <div class="account-metric-cell">
            <strong id="accountRoleValue">Administrator</strong>
            <span data-i18n="account.metricRole">Role</span>
          </div>
          <div class="account-metric-cell">
            <strong id="accountStatusValue">Active</strong>
            <span data-i18n="account.metricStatus">Status</span>
          </div>
          <div class="account-metric-cell">
            <strong id="accountSummaryAuth">Local</strong>
            <span data-i18n="account.metricAuth">Auth</span>
          </div>
        </div>

        <div class="account-meta-list">
          <div class="account-meta-item">
            <span data-i18n="account.createdAt">Created</span>
            <strong id="accountCreatedValue">-</strong>
          </div>
          <div class="account-meta-item">
            <span data-i18n="account.lastLogin">Last login</span>
            <strong id="accountLastLoginValue">-</strong>
          </div>
        </div>
      </article>
    </aside>

    <section class="account-main-pane">
      <header class="account-main-header">
        <h1 data-i18n="account.title">Account Settings</h1>
      </header>

      <form id="accountProfileForm" class="account-stack" novalidate>
        <article class="settings-card account-identity-card">
          <div class="settings-card-header">
            <div class="settings-card-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21a8 8 0 0 0-16 0"></path>
                <circle cx="12" cy="7" r="4"></circle>
              </svg>
            </div>
            <div>
              <h2 data-i18n="account.identityTitle">Personal Identity</h2>
              <p data-i18n="account.identityDescription">Manage your public and private workspace profile data.</p>
            </div>
          </div>

          <div class="account-identity-grid">
            <label class="account-field">
              <span data-i18n="account.displayName">Display name</span>
              <input id="accountDisplayName" type="text" maxlength="200" data-i18n-placeholder="account.displayNamePlaceholder" placeholder="How your name appears">
            </label>
            <label class="account-field">
              <span data-i18n="account.workEmail">Work email</span>
              <input id="accountEmail" type="email" maxlength="255" data-i18n-placeholder="account.emailPlaceholder" placeholder="name@company.com">
            </label>
          </div>

          <div class="account-identity-advanced" id="accountIdentityAdvanced">
            <div class="account-identity-advanced-grid">
              <label class="account-field">
                <span data-i18n="account.username">Username</span>
                <input id="accountUsername" type="text" readonly>
              </label>
              <label class="account-field">
                <span data-i18n="account.authType">Authentication</span>
                <input id="accountAuthType" type="text" readonly>
              </label>
            </div>
          </div>
        </article>
      </form>

      <div class="account-detail-grid">
        <article class="settings-card account-security-card">
          <div class="settings-card-header">
            <div class="settings-card-icon security-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
              </svg>
            </div>
            <div>
              <h2 data-i18n="account.securityTitle">Security</h2>
              <p id="accountSecurityDescription" data-i18n="account.securityPanelDescription">Control local sign-in conveniences and password rotation for this account.</p>
            </div>
          </div>

          <div class="account-toggle-list" id="accountSecurityToggleList">
            <label class="account-toggle-row">
              <span data-i18n="account.twoFactorAuth">Two-Factor Auth</span>
              <span class="toggle-switch">
                <input type="checkbox" id="accountPrefTwoFactor">
                <span class="toggle-slider"></span>
              </span>
            </label>
            <label class="account-toggle-row">
              <span data-i18n="account.biometricSignin">Biometric Sign-in</span>
              <span class="toggle-switch">
                <input type="checkbox" id="accountPrefBiometric">
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>

          <p class="account-panel-note hidden" id="accountSecurityUnavailable" data-i18n="account.passwordUnavailableDescription">
            This account uses federated sign-in. Update your password through the external identity provider instead.
          </p>

          <button type="button" class="account-link-action" id="accountOpenPasswordBtn" data-i18n="account.changePasswordLink">
            Update password
          </button>
        </article>

        <article class="settings-card account-preferences-card">
          <div class="settings-card-header">
            <div class="settings-card-icon preference-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.32 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
              </svg>
            </div>
            <div>
              <h2 data-i18n="account.preferencesTitle">Preferences</h2>
              <p data-i18n="account.preferencesDescription">Store local workspace preferences for experiments and digest notifications.</p>
            </div>
          </div>

          <div class="account-toggle-list">
            <label class="account-toggle-row">
              <span data-i18n="account.betaFeatures">Beta Features</span>
              <span class="toggle-switch">
                <input type="checkbox" id="accountPrefBetaFeatures">
                <span class="toggle-slider"></span>
              </span>
            </label>
            <label class="account-toggle-row">
              <span data-i18n="account.emailReports">Email Reports</span>
              <span class="toggle-switch">
                <input type="checkbox" id="accountPrefEmailReports">
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>

          <button type="button" class="account-link-action" id="accountNotificationBtn" data-i18n="account.notificationConfig">
            Notification config
          </button>
        </article>
      </div>

      <article class="account-danger-zone">
        <div>
          <h2 data-i18n="account.dangerTitle">Danger Zone</h2>
          <p data-i18n="account.dangerDescription">Once you deactivate your account, access is revoked until an administrator restores it.</p>
        </div>
        <button type="button" class="account-danger-btn" id="accountDeactivateBtn" data-i18n="account.deactivateAccount">
          Deactivate Account
        </button>
      </article>

      <div class="account-main-actions is-hidden" id="accountMainActions">
        <button type="button" class="btn-secondary" id="accountResetBtn" data-i18n="account.cancelChanges">Cancel</button>
        <button type="button" class="btn-primary" id="accountSaveProfileBtn" data-i18n="account.saveAllChanges">Save All Changes</button>
      </div>
    </section>
  </div>

  <div id="accountPasswordModal" class="modal-overlay hidden">
    <div class="modal account-password-modal">
      <div class="modal-header">
        <div>
          <h2 data-i18n="account.changePassword">Update password</h2>
          <p class="modal-description" data-i18n="account.passwordModalDescription">Confirm your current password and choose a new one for local sign-in.</p>
        </div>
        <button class="modal-close" id="accountPasswordClose">&times;</button>
      </div>
      <div class="modal-body">
        <form id="accountPasswordForm" class="account-password-form" novalidate>
          <label class="account-field">
            <span data-i18n="account.currentPassword">Current password</span>
            <input id="accountCurrentPassword" type="password" autocomplete="current-password">
          </label>
          <label class="account-field">
            <span data-i18n="account.newPassword">New password</span>
            <input id="accountNewPassword" type="password" autocomplete="new-password">
          </label>
          <label class="account-field">
            <span data-i18n="account.confirmPassword">Confirm new password</span>
            <input id="accountConfirmPassword" type="password" autocomplete="new-password">
          </label>
        </form>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn-secondary" id="accountPasswordCancel" data-i18n="admin.cancel">Cancel</button>
        <button type="submit" class="btn-primary" id="accountSavePasswordBtn" form="accountPasswordForm" data-i18n="account.changePassword">Update password</button>
      </div>
    </div>
  </div>
</div>
`

function addTrackedListener(element, event, handler, options) {
  if (!element) return
  element.addEventListener(event, handler, options)
  eventCleanupFns.push(() => element.removeEventListener(event, handler, options))
}

function safeJsonParse(value, fallback) {
  try {
    return JSON.parse(value)
  } catch {
    return fallback
  }
}

function loadUiPreferences() {
  try {
    const raw = localStorage.getItem(ACCOUNT_UI_PREFS_KEY)
    if (!raw) return { ...DEFAULT_UI_PREFS }
    return { ...DEFAULT_UI_PREFS, ...safeJsonParse(raw, DEFAULT_UI_PREFS) }
  } catch {
    return { ...DEFAULT_UI_PREFS }
  }
}

function saveUiPreferences(nextValue) {
  currentUiPrefs = { ...DEFAULT_UI_PREFS, ...nextValue }
  try {
    localStorage.setItem(ACCOUNT_UI_PREFS_KEY, JSON.stringify(currentUiPrefs))
  } catch {
    // Ignore localStorage failures in constrained environments.
  }
}

function isLocalAuth(authType) {
  return String(authType || '').toLowerCase() === 'local'
}

function translateOrFallback(key, fallback) {
  const translated = t(key)
  return translated === key ? fallback : translated
}

function formatAuthType(authType) {
  return isLocalAuth(authType)
    ? translateOrFallback('account.authLocal', 'Local')
    : translateOrFallback('account.authSSO', 'SSO')
}

function formatStatus(isActive) {
  return isActive === false
    ? translateOrFallback('account.statusInactive', 'Inactive')
    : translateOrFallback('account.statusActive', 'Active')
}

function formatRole(profile) {
  return profile?.is_admin
    ? translateOrFallback('user.roleAdmin', 'Administrator')
    : translateOrFallback('user.roleUser', 'User')
}

function formatCompactRole(profile) {
  return profile?.is_admin
    ? translateOrFallback('admin.roleAdmin', 'Admin')
    : translateOrFallback('admin.roleUser', 'User')
}

function formatCompactAuthType(authType) {
  return isLocalAuth(authType)
    ? translateOrFallback('admin.local', 'Local')
    : translateOrFallback('admin.sso', 'SSO')
}

function formatDate(value) {
  if (!value) {
    return translateOrFallback('account.never', 'Never')
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString(getCurrentLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  })
}

function buildProfileBio(profile) {
  if (profile?.is_admin) {
    return translateOrFallback(
      'account.profileBioAdmin',
      'This workspace profile oversees administrative access, approval identity, and visibility across AtlasClaw operations.'
    )
  }

  return translateOrFallback(
    'account.profileBio',
    'This workspace profile controls how your identity appears in notifications, approvals, and administrative activity across AtlasClaw.'
  )
}

function syncUiToggles() {
  containerRef.querySelector('#accountPrefTwoFactor').checked = Boolean(currentUiPrefs.twoFactor)
  containerRef.querySelector('#accountPrefBiometric').checked = Boolean(currentUiPrefs.biometric)
  containerRef.querySelector('#accountPrefBetaFeatures').checked = Boolean(currentUiPrefs.betaFeatures)
  containerRef.querySelector('#accountPrefEmailReports').checked = Boolean(currentUiPrefs.emailReports)
}

function collectUiToggles() {
  return {
    twoFactor: containerRef.querySelector('#accountPrefTwoFactor').checked,
    biometric: containerRef.querySelector('#accountPrefBiometric').checked,
    betaFeatures: containerRef.querySelector('#accountPrefBetaFeatures').checked,
    emailReports: containerRef.querySelector('#accountPrefEmailReports').checked
  }
}

function renderAvatar(avatarUrl, displayName, username) {
  const avatarShell = containerRef?.querySelector('#accountAvatarShell')
  if (!avatarShell) return

  const seedText = (displayName || username || 'A').trim()
  const initial = seedText.charAt(0).toUpperCase() || 'A'

  if (avatarUrl) {
    avatarShell.innerHTML = `<img src="${escapeHtml(avatarUrl)}" alt="${escapeHtml(seedText)}">`
    return
  }

  avatarShell.innerHTML = `<span id="accountAvatarFallback">${escapeHtml(initial)}</span>`
}

function isProfileEditable(profile) {
  return isLocalAuth(profile?.auth_type)
}

function showProfileEditingUnavailable() {
  showToast(
    translateOrFallback(
      'account.profileEditUnavailable',
      'Profile editing is not available for federated accounts yet.'
    ),
    'error'
  )
}

function applyProfileEditState() {
  if (!containerRef) return

  const profileEditable = isProfileEditable(currentProfile)
  if (!profileEditable) {
    isProfileEditing = false
  }

  const isEditing = Boolean(isProfileEditing)
  const localAuth = isLocalAuth(currentProfile?.auth_type)
  const profileForm = containerRef.querySelector('#accountProfileForm')
  const mainActions = containerRef.querySelector('#accountMainActions')
  const displayNameInput = containerRef.querySelector('#accountDisplayName')
  const emailInput = containerRef.querySelector('#accountEmail')
  const twoFactorInput = containerRef.querySelector('#accountPrefTwoFactor')
  const biometricInput = containerRef.querySelector('#accountPrefBiometric')
  const betaInput = containerRef.querySelector('#accountPrefBetaFeatures')
  const reportsInput = containerRef.querySelector('#accountPrefEmailReports')
  const notificationBtn = containerRef.querySelector('#accountNotificationBtn')
  const securityToggleList = containerRef.querySelector('#accountSecurityToggleList')
  const editProfileBtn = containerRef.querySelector('#accountEditPublicBtn')
  const avatarEditBtn = containerRef.querySelector('#accountAvatarEditBtn')

  profileForm?.classList.toggle('is-editing', isEditing)
  mainActions?.classList.toggle('is-hidden', !profileEditable || !isEditing)

  if (displayNameInput) displayNameInput.readOnly = !profileEditable || !isEditing
  if (emailInput) emailInput.readOnly = !profileEditable || !isEditing
  if (twoFactorInput) twoFactorInput.disabled = !localAuth || !isEditing
  if (biometricInput) biometricInput.disabled = !localAuth || !isEditing
  if (betaInput) betaInput.disabled = !isEditing
  if (reportsInput) reportsInput.disabled = !isEditing
  securityToggleList?.classList.toggle('disabled', !localAuth || !isEditing)
  if (editProfileBtn) editProfileBtn.disabled = !profileEditable
  if (avatarEditBtn) avatarEditBtn.disabled = !profileEditable

  if (notificationBtn) {
    notificationBtn.disabled = !isEditing
    notificationBtn.classList.toggle('disabled', !isEditing)
  }

}

function enterProfileEditMode() {
  if (!isProfileEditable(currentProfile)) {
    showProfileEditingUnavailable()
    return
  }

  isProfileEditing = true
  applyProfileEditState()
  containerRef.querySelector('#accountDisplayName')?.focus()
}

function populateProfile(profile) {
  currentProfile = profile
  notifyProfileUpdated(profile)

  const displayName = profile.display_name || profile.username || 'Atlas User'
  const roleText = formatRole(profile)
  const authText = formatAuthType(profile.auth_type)
  const statusText = formatStatus(profile.is_active)

  containerRef.querySelector('#accountIdentityName').textContent = displayName
  containerRef.querySelector('#accountSummaryRole').textContent = roleText
  containerRef.querySelector('#accountProfileEmail').textContent = profile.email || `${profile.username}@workspace.local`
  containerRef.querySelector('#accountProfileBio').textContent = buildProfileBio(profile)

  containerRef.querySelector('#accountDisplayName').value = profile.display_name || ''
  containerRef.querySelector('#accountEmail').value = profile.email || ''
  containerRef.querySelector('#accountUsername').value = profile.username || ''
  containerRef.querySelector('#accountAuthType').value = authText

  containerRef.querySelector('#accountStatusValue').textContent = statusText
  containerRef.querySelector('#accountRoleValue').textContent = formatCompactRole(profile)
  containerRef.querySelector('#accountSummaryAuth').textContent = formatCompactAuthType(profile.auth_type)
  containerRef.querySelector('#accountCreatedValue').textContent = formatDate(profile.created_at)
  containerRef.querySelector('#accountLastLoginValue').textContent = formatDate(profile.last_login_at)

  renderAvatar(profile.avatar_url, displayName, profile.username)

  const localAuth = isLocalAuth(profile.auth_type)
  containerRef.querySelector('#accountOpenPasswordBtn').disabled = !localAuth
  containerRef.querySelector('#accountOpenPasswordBtn').classList.toggle('disabled', !localAuth)
  containerRef.querySelector('#accountSecurityUnavailable').classList.toggle('hidden', localAuth)
  applyProfileEditState()
}

async function handleApiError(response, fallbackKey) {
  let errorMessage = translateOrFallback(fallbackKey, fallbackKey)

  try {
    const data = await response.json()
    errorMessage = data.detail || data.error || data.message || errorMessage
  } catch {
    // Ignore parsing issues and keep fallback text.
  }

  throw new Error(errorMessage)
}

async function fetchProfile() {
  const response = await fetch('/api/users/me/profile')
  if (!response.ok) {
    await handleApiError(response, 'account.loadFailed')
  }
  return response.json()
}

async function updateProfile(payload) {
  const response = await fetch('/api/users/me/profile', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })

  if (!response.ok) {
    await handleApiError(response, 'account.profileSaveFailed')
  }

  return response.json()
}

async function changePassword(payload) {
  const response = await fetch('/api/users/me/password', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })

  if (!response.ok) {
    await handleApiError(response, 'account.passwordSaveFailed')
  }

  return response.json()
}

async function uploadAvatar(file) {
  const formData = new FormData()
  formData.append('avatar', file)

  const response = await fetch('/api/users/me/avatar', {
    method: 'POST',
    body: formData
  })

  if (!response.ok) {
    await handleApiError(response, 'account.avatarUploadFailed')
  }

  return response.json()
}

async function loadProfile() {
  try {
    const profile = await fetchProfile()
    populateProfile(profile)
  } catch (error) {
    console.error('[AccountSettingsPage] Failed to load profile:', error)
    showToast(error.message || translateOrFallback('account.loadFailed', 'Failed to load account profile'), 'error')
  }
}

function syncProfileDraft() {
  const draftName = containerRef.querySelector('#accountDisplayName').value.trim()
  const draftEmail = containerRef.querySelector('#accountEmail').value.trim()

  containerRef.querySelector('#accountIdentityName').textContent =
    draftName || currentProfile?.display_name || currentProfile?.username || 'Atlas User'
  containerRef.querySelector('#accountProfileEmail').textContent =
    draftEmail || currentProfile?.email || `${currentProfile?.username || 'user'}@workspace.local`

  renderAvatar(
    currentProfile?.avatar_url || '',
    draftName || currentProfile?.display_name || currentProfile?.username,
    currentProfile?.username
  )
}

function resetDraftState() {
  if (!currentProfile) return
  isProfileEditing = false
  populateProfile(currentProfile)
  currentUiPrefs = loadUiPreferences()
  syncUiToggles()
}

async function handleSaveAllChanges() {
  const saveBtn = containerRef.querySelector('#accountSaveProfileBtn')

  const payload = {
    display_name: containerRef.querySelector('#accountDisplayName').value.trim() || null,
    email: containerRef.querySelector('#accountEmail').value.trim() || null
  }

  saveBtn.disabled = true
  saveBtn.textContent = translateOrFallback('account.savingProfile', 'Saving...')

  try {
    const updated = await updateProfile(payload)
    saveUiPreferences(collectUiToggles())
    isProfileEditing = false
    populateProfile(updated)
    showToast(translateOrFallback('account.profileSaved', 'Profile saved successfully'), 'success')
  } catch (error) {
    showToast(error.message || translateOrFallback('account.profileSaveFailed', 'Failed to save profile'), 'error')
  } finally {
    saveBtn.disabled = false
    saveBtn.textContent = translateOrFallback('account.saveAllChanges', 'Save All Changes')
  }
}

async function handleAvatarFileChange(event) {
  if (!isProfileEditable(currentProfile)) {
    event.target.value = ''
    showProfileEditingUnavailable()
    return
  }

  const file = event.target.files?.[0]
  if (!file) return

  const uploadBtn = containerRef.querySelector('#accountAvatarEditBtn')
  uploadBtn.disabled = true
  uploadBtn.classList.add('is-uploading')

  try {
    const updated = await uploadAvatar(file)
    populateProfile(updated)
    showToast(translateOrFallback('account.avatarUploadSuccess', 'Avatar updated successfully'), 'success')
  } catch (error) {
    showToast(error.message || translateOrFallback('account.avatarUploadFailed', 'Failed to upload avatar'), 'error')
  } finally {
    uploadBtn.disabled = false
    uploadBtn.classList.remove('is-uploading')
    event.target.value = ''
  }
}

function openPasswordModal() {
  if (!isLocalAuth(currentProfile?.auth_type)) {
    showToast(
      translateOrFallback(
        'account.passwordUnavailableDescription',
        'This account uses federated sign-in. Update your password through the external identity provider instead.'
      ),
      'error'
    )
    return
  }

  containerRef.querySelector('#accountPasswordModal').classList.remove('hidden')
  containerRef.querySelector('#accountCurrentPassword').focus()
}

function closePasswordModal() {
  const passwordModal = containerRef.querySelector('#accountPasswordModal')
  passwordModal.classList.add('hidden')
  containerRef.querySelector('#accountPasswordForm').reset()
}

async function handlePasswordSubmit(event) {
  event.preventDefault()

  const currentPassword = containerRef.querySelector('#accountCurrentPassword').value
  const newPassword = containerRef.querySelector('#accountNewPassword').value
  const confirmPassword = containerRef.querySelector('#accountConfirmPassword').value
  const saveBtn = containerRef.querySelector('#accountSavePasswordBtn')

  if (!currentPassword || !newPassword || !confirmPassword) {
    showToast(translateOrFallback('account.passwordFieldsRequired', 'Please complete all password fields'), 'error')
    return
  }

  if (newPassword !== confirmPassword) {
    showToast(translateOrFallback('account.passwordMismatch', 'New password confirmation does not match'), 'error')
    return
  }

  saveBtn.disabled = true
  saveBtn.textContent = translateOrFallback('account.savingPassword', 'Updating...')

  try {
    await changePassword({
      current_password: currentPassword,
      new_password: newPassword
    })

    closePasswordModal()
    showToast(translateOrFallback('account.passwordSaved', 'Password updated successfully'), 'success')
  } catch (error) {
    showToast(error.message || translateOrFallback('account.passwordSaveFailed', 'Failed to update password'), 'error')
  } finally {
    saveBtn.disabled = false
    saveBtn.textContent = translateOrFallback('account.changePassword', 'Update password')
  }
}

function setupEventListeners() {
  addTrackedListener(containerRef.querySelector('#accountEditPublicBtn'), 'click', () => {
    enterProfileEditMode()
  })

  addTrackedListener(containerRef.querySelector('#accountDisplayName'), 'input', syncProfileDraft)
  addTrackedListener(containerRef.querySelector('#accountEmail'), 'input', syncProfileDraft)
  addTrackedListener(containerRef.querySelector('#accountAvatarEditBtn'), 'click', () => {
    if (!isProfileEditable(currentProfile)) {
      showProfileEditingUnavailable()
      return
    }
    containerRef.querySelector('#accountAvatarFileInput').click()
  })
  addTrackedListener(containerRef.querySelector('#accountAvatarFileInput'), 'change', handleAvatarFileChange)

  ;[
    '#accountPrefTwoFactor',
    '#accountPrefBiometric',
    '#accountPrefBetaFeatures',
    '#accountPrefEmailReports'
  ].forEach(selector => {
    addTrackedListener(containerRef.querySelector(selector), 'change', () => {
      currentUiPrefs = collectUiToggles()
    })
  })

  addTrackedListener(containerRef.querySelector('#accountNotificationBtn'), 'click', () => {
    showToast(
      translateOrFallback(
        'account.notificationInfo',
        'Notification preferences are stored locally in this preview and apply after saving.'
      ),
      'success'
    )
  })

  addTrackedListener(containerRef.querySelector('#accountDeactivateBtn'), 'click', () => {
    showToast(
      translateOrFallback(
        'account.deactivateUnavailable',
        'Self-service account deactivation is not available yet. Please contact an administrator.'
      ),
      'error'
    )
  })

  addTrackedListener(containerRef.querySelector('#accountResetBtn'), 'click', resetDraftState)
  addTrackedListener(containerRef.querySelector('#accountSaveProfileBtn'), 'click', handleSaveAllChanges)
  addTrackedListener(containerRef.querySelector('#accountOpenPasswordBtn'), 'click', openPasswordModal)
  addTrackedListener(containerRef.querySelector('#accountPasswordForm'), 'submit', handlePasswordSubmit)
  addTrackedListener(containerRef.querySelector('#accountPasswordClose'), 'click', closePasswordModal)
  addTrackedListener(containerRef.querySelector('#accountPasswordCancel'), 'click', closePasswordModal)

  const passwordModal = containerRef.querySelector('#accountPasswordModal')
  addTrackedListener(passwordModal, 'click', (event) => {
    if (event.target === passwordModal) {
      closePasswordModal()
    }
  })

  const escapeHandler = (event) => {
    if (event.key === 'Escape') {
      closePasswordModal()
    }
  }
  document.addEventListener('keydown', escapeHandler)
  eventCleanupFns.push(() => document.removeEventListener('keydown', escapeHandler))
}

export async function mount(container, { params, route } = {}) {
  console.log('[AccountSettingsPage] Mounting...')

  containerRef = container

  const user = await checkAuth({ redirect: true })
  if (!user) {
    return
  }

  currentUiPrefs = loadUiPreferences()

  if (!document.getElementById('account-settings-page-css')) {
    const cssLink = document.createElement('link')
    cssLink.rel = 'stylesheet'
    cssLink.href = '/styles/account-settings.css'
    cssLink.id = 'account-settings-page-css'
    document.head.appendChild(cssLink)
  }

  containerRef.innerHTML = PAGE_HTML
  updateContainerTranslations(containerRef)
  syncUiToggles()
  isProfileEditing = false
  applyProfileEditState()
  setupEventListeners()
  await loadProfile()

  console.log('[AccountSettingsPage] Mounted')
}

export async function unmount() {
  console.log('[AccountSettingsPage] Unmounting...')

  eventCleanupFns.forEach(fn => fn())
  eventCleanupFns = []

  document.getElementById('account-settings-page-css')?.remove()

  currentProfile = null
  currentUiPrefs = { ...DEFAULT_UI_PREFS }
  isProfileEditing = false
  containerRef = null

  console.log('[AccountSettingsPage] Unmounted')
}

export default { mount, unmount }

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function notifyProfileUpdated(profile) {
  document.dispatchEvent(new CustomEvent('atlasclaw:user-profile-updated', {
    detail: {
      username: profile?.username || '',
      display_name: profile?.display_name || '',
      email: profile?.email || '',
      avatar_url: profile?.avatar_url || '',
      auth_type: profile?.auth_type || '',
      is_admin: profile?.is_admin === true,
      is_active: profile?.is_active !== false
    }
  }))
}
