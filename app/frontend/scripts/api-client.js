/**
 * AtlasClaw API Client
 * Encapsulate communication with backend API
 */

import { buildApiUrl } from './config.js';

/**
 * List all sessions for the current user
 * @returns {Promise<Array>} List of sessions
 */
export async function listSessions() {
    const response = await fetch(buildApiUrl('/api/sessions'), {
        credentials: 'include'
    });
    
    if (!response.ok) {
        throw new Error(`Failed to list sessions: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Create session
 * @param {object} params - Session parameters
 * @returns {Promise<object>} Session info { session_key, ... }
 */
export async function createSession(params = {}) {
    const response = await fetch(buildApiUrl('/api/sessions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
            agent_id: params.agentId || 'main',
            channel: params.channel || 'web',
            chat_type: params.chatType || 'dm',
            scope: params.scope || 'main'
        })
    });
    
    if (!response.ok) {
        throw new Error(`Failed to create session: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Get session info
 * @param {string} sessionKey - Session key
 * @returns {Promise<object>} Session info
 */
export async function getSession(sessionKey) {
    const response = await fetch(buildApiUrl(`/api/sessions/${sessionKey}`), {
        credentials: 'include'
    });
    
    if (!response.ok) {
        throw new Error(`Failed to get session: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Reset session
 * @param {string} sessionKey - Session key
 * @param {boolean} archive - Whether to archive
 * @returns {Promise<object>} Result
 */
export async function resetSession(sessionKey, archive = true) {
    const response = await fetch(buildApiUrl(`/api/sessions/${sessionKey}/reset`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ archive })
    });
    
    if (!response.ok) {
        throw new Error(`Failed to reset session: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Start agent run
 * @param {string} sessionKey - Session key
 * @param {string} message - User message
 * @returns {Promise<object>} Run info { run_id, status }
 */
export async function startAgentRun(sessionKey, message) {
    const response = await fetch(buildApiUrl('/api/agent/run'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
            session_key: sessionKey,
            message: message
        })
    });
    
    if (!response.ok) {
        throw new Error(`Failed to start agent run: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Get agent run status
 * @param {string} runId - Run ID
 * @returns {Promise<object>} Status info
 */
export async function getAgentStatus(runId) {
    const response = await fetch(buildApiUrl(`/api/agent/runs/${runId}`), {
        credentials: 'include'
    });
    
    if (!response.ok) {
        throw new Error(`Failed to get agent status: ${response.status}`);
    }
    
    return response.json();
}

/**
 * Abort agent run
 * @param {string} runId - Run ID
 * @returns {Promise<object>} Result
 */
export async function abortAgentRun(runId) {
    const response = await fetch(buildApiUrl(`/api/agent/runs/${runId}/abort`), {
        method: 'POST',
        credentials: 'include'
    });
    
    if (!response.ok) {
        throw new Error(`Failed to abort agent run: ${response.status}`);
    }
    
    return response.json();
}

export default {
    listSessions,
    createSession,
    getSession,
    resetSession,
    startAgentRun,
    getAgentStatus,
    abortAgentRun
};
