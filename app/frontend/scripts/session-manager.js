/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * Session State Management
 * Manage session lifecycle and persistence
 */

import { bootstrapEmbedIntegration, createThreadSession } from './api-client.js?v=32';

const SESSION_KEY_STORAGE = 'atlasclaw_session_key';
const SESSION_HAS_MESSAGES_STORAGE = 'atlasclaw_session_has_messages';

let currentSessionKey = null;
let currentSessionHasMessages = null;
let integrationChatSession = null;
let storageListenerBound = false;
let storageValidationGeneration = 0;
const validatedIntegrationChatSessions = new Set();

function getStorage() {
    return integrationChatSession ? localStorage : sessionStorage;
}

function getSessionKeyStorage() {
    return integrationChatSession?.storageKey || SESSION_KEY_STORAGE;
}

function getSessionHasMessagesStorage() {
    return integrationChatSession?.messagesStorageKey || SESSION_HAS_MESSAGES_STORAGE;
}

function readStoredSessionHasMessages() {
    const value = getStorage().getItem(getSessionHasMessagesStorage());
    if (value === '1') return true;
    if (value === '0') return false;
    return null;
}

function persistSessionHasMessages(value) {
    if (value === true) {
        getStorage().setItem(getSessionHasMessagesStorage(), '1');
        return;
    }
    if (value === false) {
        getStorage().setItem(getSessionHasMessagesStorage(), '0');
        return;
    }
    getStorage().removeItem(getSessionHasMessagesStorage());
}

/**
 * Initialize the integration-scoped Chat Active Session strategy. Authentication
 * remains the shared Host Cookie; localStorage never contains Cookie, Token or
 * Provider credentials and its candidate key is restored only after bootstrap
 * validates it for the current authenticated user.
 *
 * @param {object} surface - Parsed Embed surface contract.
 * @returns {Promise<object|null>} Bootstrap-approved profile or null degradation.
 */
export async function initializeIntegrationChatSession(surface) {
    if (!surface?.integrationMode) return null;
    storageValidationGeneration += 1;
    validatedIntegrationChatSessions.clear();
    const request = {
        surface: surface.surface,
        nonce: surface.nonce,
        candidateSessionKey: null
    };
    const profile = await bootstrapEmbedIntegration(request);
    const storageKey = buildScopedChatSessionKey(profile.agent_id, profile.session_scope);
    const candidate = localStorage.getItem(storageKey);
    let validatedProfile = profile;

    if (candidate) {
        validatedProfile = await bootstrapEmbedIntegration({
            ...request,
            candidateSessionKey: candidate
        });
        if (validatedProfile.active_session_key !== candidate) {
            localStorage.removeItem(storageKey);
        }
    }

    integrationChatSession = {
        agentId: validatedProfile.agent_id,
        sessionScope: validatedProfile.session_scope,
        storageKey,
        messagesStorageKey: `${storageKey}:has_messages`,
        bootstrapRequest: request
    };
    currentSessionKey = validatedProfile.active_session_key || null;
    currentSessionHasMessages = null;
    if (currentSessionKey) {
        validatedIntegrationChatSessions.add(currentSessionKey);
        localStorage.setItem(storageKey, currentSessionKey);
    }
    bindStorageListener();
    return validatedProfile;
}

/** Reset integration state for tests or a full application teardown. */
export function resetIntegrationChatSession() {
    integrationChatSession = null;
    currentSessionKey = null;
    currentSessionHasMessages = null;
    storageValidationGeneration += 1;
    validatedIntegrationChatSessions.clear();
}

function buildScopedChatSessionKey(agentId, sessionScope) {
    return `atlasclaw_active_session:v1:${agentId}:${sessionScope}`;
}

function bindStorageListener() {
    if (storageListenerBound || typeof window === 'undefined') return;
    storageListenerBound = true;
    if (window.__atlasclawChatSessionStorageListener) {
        window.removeEventListener('storage', window.__atlasclawChatSessionStorageListener);
    }
    const listener = (event) => {
        if (!integrationChatSession || event.storageArea !== localStorage) return;
        if (event.key !== integrationChatSession.storageKey) return;
        if (!event.newValue) {
            storageValidationGeneration += 1;
            const previousKey = currentSessionKey;
            currentSessionKey = null;
            currentSessionHasMessages = null;
            validatedIntegrationChatSessions.clear();
            if (previousKey) {
                window.dispatchEvent(new CustomEvent('atlasclaw:active-chat-session-changed', {
                    detail: { sessionKey: null, previousKey }
                }));
            }
            return;
        }
        if (event.newValue === currentSessionKey) return;
        const candidate = event.newValue;
        const validationGeneration = ++storageValidationGeneration;
        void validateChatSessionCandidate(candidate).then((validatedKey) => {
            if (validationGeneration !== storageValidationGeneration || validatedKey !== candidate) return;
            if (localStorage.getItem(integrationChatSession.storageKey) !== candidate) return;
            const previousKey = currentSessionKey;
            currentSessionKey = candidate;
            currentSessionHasMessages = null;
            window.dispatchEvent(new CustomEvent('atlasclaw:active-chat-session-changed', {
                detail: { sessionKey: candidate, previousKey }
            }));
        });
    };
    window.__atlasclawChatSessionStorageListener = listener;
    window.addEventListener('storage', listener);
}

/**
 * Validate a Chat Active Session candidate for the current authenticated user
 * and integration scope before it can become a cross-Surface pointer.
 *
 * @param {string|null} candidate - Canonical candidate Chat session key.
 * @returns {Promise<string|null>} The validated key, or null when rejected.
 */
export async function validateChatSessionCandidate(candidate) {
    if (!candidate) return null;
    if (!integrationChatSession) return candidate;
    if (validatedIntegrationChatSessions.has(candidate)) return candidate;

    try {
        const profile = await bootstrapEmbedIntegration({
            ...integrationChatSession.bootstrapRequest,
            candidateSessionKey: candidate
        });
        const valid = profile.active_session_key === candidate &&
            profile.agent_id === integrationChatSession.agentId &&
            profile.session_scope === integrationChatSession.sessionScope;
        if (valid) {
            validatedIntegrationChatSessions.add(candidate);
            return candidate;
        }
    } catch (error) {
        console.warn('[Session] Chat Active Session candidate validation failed:', error);
    }

    clearRejectedIntegrationCandidate(candidate);
    return null;
}

function clearRejectedIntegrationCandidate(candidate) {
    if (!integrationChatSession || localStorage.getItem(integrationChatSession.storageKey) !== candidate) {
        return;
    }
    if (currentSessionKey && validatedIntegrationChatSessions.has(currentSessionKey)) {
        localStorage.setItem(integrationChatSession.storageKey, currentSessionKey);
        return;
    }
    localStorage.removeItem(integrationChatSession.storageKey);
}

/**
 * Initialize session
 * Restore a legacy or bootstrap-validated integration Chat Active Session.
 * @param {object} params - Session parameters
 * @returns {Promise<string>} Session key
 */
export async function initSession(params = {}) {
    let storedKey = currentSessionKey || getStorage().getItem(getSessionKeyStorage());
    if (
        integrationChatSession &&
        storedKey &&
        !validatedIntegrationChatSessions.has(storedKey)
    ) {
        storedKey = await validateChatSessionCandidate(storedKey);
    }
    
    if (storedKey) {
        currentSessionKey = storedKey;
        currentSessionHasMessages = readStoredSessionHasMessages();
        console.log('[Session] Restored:', currentSessionKey);
        return currentSessionKey;
    }
    
    // Create new session
    const session = await createThreadSession(getCreateParams(params));
    if (integrationChatSession) {
        currentSessionKey = await validateChatSessionCandidate(session.session_key);
        if (!currentSessionKey) {
            throw new Error('Created Chat session did not match the configured integration scope');
        }
    } else {
        currentSessionKey = session.session_key;
    }
    getStorage().setItem(getSessionKeyStorage(), currentSessionKey);
    setSessionHasMessages(false);
    console.log('[Session] Created:', currentSessionKey);
    
    return currentSessionKey;
}

/**
 * Get current session key
 * @returns {string|null} Session key
 */
export function getSessionKey() {
    if (!currentSessionKey) {
        const storedKey = getStorage().getItem(getSessionKeyStorage());
        if (!integrationChatSession || validatedIntegrationChatSessions.has(storedKey)) {
            currentSessionKey = storedKey;
        }
    }
    return currentSessionKey;
}

/**
 * Set session key (for session restoration)
 * @param {string} key - Session key
 */
export function setSessionKey(key) {
    if (
        integrationChatSession &&
        key &&
        !validatedIntegrationChatSessions.has(key)
    ) {
        console.warn('[Session] Refused unvalidated integration Chat Active Session:', key);
        return false;
    }
    const previousKey = currentSessionKey;
    currentSessionKey = key;
    if (key) {
        getStorage().setItem(getSessionKeyStorage(), key);
        if (key !== previousKey) {
            setSessionHasMessages(null);
        }
    } else {
        getStorage().removeItem(getSessionKeyStorage());
        setSessionHasMessages(null);
    }
    return true;
}

export function setSessionHasMessages(hasMessages) {
    currentSessionHasMessages = typeof hasMessages === 'boolean' ? hasMessages : null;
    persistSessionHasMessages(currentSessionHasMessages);
}

export function getSessionHasMessages() {
    if (currentSessionHasMessages === null) {
        currentSessionHasMessages = readStoredSessionHasMessages();
    }
    return currentSessionHasMessages;
}

/**
 * Check if there is an active session
 * @returns {boolean}
 */
export function hasSession() {
    return !!getSessionKey();
}

/**
 * Clear current session and create a new independent thread
 * @param {boolean} archive - Unused compatibility argument
 * @param {object} params - New session parameters
 * @returns {Promise<string>} New session key
 */
export async function startNewSession(archive = true, params = {}) {
    // Clear storage and create a brand-new thread while preserving history entries
    void archive;
    const storage = getStorage();
    const sessionKeyStorage = getSessionKeyStorage();
    const hasMessagesStorage = getSessionHasMessagesStorage();
    const activeSessionKey = currentSessionKey || storage.getItem(sessionKeyStorage);
    if (!integrationChatSession && activeSessionKey && getSessionHasMessages() === false) {
        currentSessionKey = activeSessionKey;
        return activeSessionKey;
    }
    storage.removeItem(sessionKeyStorage);
    storage.removeItem(hasMessagesStorage);
    currentSessionKey = null;
    currentSessionHasMessages = null;

    return initSession(getCreateParams(params));
}

/**
 * Clear session (local only)
 */
export function clearSession() {
    getStorage().removeItem(getSessionKeyStorage());
    getStorage().removeItem(getSessionHasMessagesStorage());
    currentSessionKey = null;
    currentSessionHasMessages = null;
    console.log('[Session] Cleared');
}

function getCreateParams(params = {}) {
    if (!integrationChatSession) return params;
    return {
        ...params,
        agentId: integrationChatSession.agentId,
        accountId: integrationChatSession.sessionScope,
        channel: 'web',
        chatType: 'dm'
    };
}

export default {
    initSession,
    initializeIntegrationChatSession,
    resetIntegrationChatSession,
    validateChatSessionCandidate,
    getSessionKey,
    setSessionKey,
    setSessionHasMessages,
    getSessionHasMessages,
    hasSession,
    startNewSession,
    clearSession
};
