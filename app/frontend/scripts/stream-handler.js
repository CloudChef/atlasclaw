/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * SSE Stream Response Handler
 * Handle streaming events from Agent runs
 *
 * Backend SSE event types:
 * - lifecycle: { phase: 'start' | 'end' | 'error' | 'timeout' }
 * - assistant: { text: string, is_delta: boolean }
 * - tool: { tool: string, phase: 'start' | 'update' | 'end', result?: string }
 * - error: { message: string, code?: string }
 * - heartbeat: { timestamp: string }
 */

import { buildApiUrl } from './config.js';

/**
 * Backend SSE event types
 */
export const EventTypes = {
    LIFECYCLE: 'lifecycle',
    ASSISTANT: 'assistant',
    TOOL: 'tool',
    THINKING: 'thinking',
    RUNTIME: 'runtime',
    ERROR: 'error',
    HEARTBEAT: 'heartbeat'
};

/**
 * Create stream response handler
 * @param {string} runId - Agent run ID
 * @param {object} callbacks - Event callback functions
 * @returns {object} Controller { start, abort }
 */
export function createStreamHandler(runId, callbacks = {}) {
    let eventSource = null;
    let aborted = false;

    const {
        onStart = () => {},
        onDelta = () => {},
        onToolStart = () => {},
        onToolEnd = () => {},
        onThinkingStart = () => {},
        onThinkingDelta = () => {},
        onThinkingEnd = () => {},
        onRuntime = () => {},
        onHeartbeat = () => {},
        onEnd = () => {},
        onError = () => {}
    } = callbacks;

    function start() {
        if (eventSource || aborted) return;

        const url = buildApiUrl(`/api/agent/runs/${runId}/stream`);
        eventSource = new EventSource(url, { withCredentials: true });

        eventSource.onopen = () => {
            console.log('[Stream] Connected:', runId);
        };

        eventSource.onerror = (error) => {
            console.error('[Stream] Connection error:', error);
            if (eventSource && eventSource.readyState === EventSource.CLOSED) {
                onError({ message: 'Connection closed unexpectedly' });
                close();
            }
        };

        eventSource.addEventListener(EventTypes.LIFECYCLE, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Lifecycle:', data.phase, data);

            if (data.phase === 'start') {
                onStart(data);
            } else if (data.phase === 'end') {
                onEnd(data);
                close();
            } else if (data.phase === 'error' || data.phase === 'timeout') {
                onError({ message: data.error || 'Lifecycle error' });
                close();
            }
        });

        eventSource.addEventListener(EventTypes.ASSISTANT, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Assistant event:', data);
            // Backend sends { text: string, is_delta: boolean }
            // Frontend expects { content: string, is_delta: boolean }
            onDelta({ content: data.text || data.content, is_delta: data.is_delta });
        });

        eventSource.addEventListener(EventTypes.TOOL, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Tool event:', data);
            // Backend sends { tool: string, phase: string, result?: string }
            if (data.phase === 'start') {
                onToolStart({ tool_name: data.tool });
            } else if (data.phase === 'end') {
                onToolEnd({ tool_name: data.tool, result: data.result ?? data.content });
            }
        });

        eventSource.addEventListener(EventTypes.ERROR, (e) => {
            const data = parseEventData(e.data);
            onError({ message: data.message || 'Stream error', code: data.code });
            close();
        });

        eventSource.addEventListener(EventTypes.HEARTBEAT, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Heartbeat received');
            onHeartbeat(data);
        });

        eventSource.addEventListener(EventTypes.THINKING, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Thinking event:', data);
            // Backend sends { phase: 'start' | 'delta' | 'end', content?: string, elapsed?: number }
            if (data.phase === 'start') {
                onThinkingStart();
            } else if (data.phase === 'delta') {
                onThinkingDelta({ content: data.content });
            } else if (data.phase === 'end') {
                onThinkingEnd({ elapsed: data.elapsed || 0 });
            }
        });

        eventSource.addEventListener(EventTypes.RUNTIME, (e) => {
            const data = parseEventData(e.data);
            console.log('[Stream] Runtime event:', data);
            onRuntime({
                state: data.state,
                message: data.message || '',
                metadata: data
            });
        });
    }

    function parseEventData(data) {
        if (typeof data !== 'string' || data.length === 0) {
            return {};
        }
        try {
            return JSON.parse(data);
        } catch (e) {
            console.warn('[Stream] Failed to parse event data:', data);
            return {};
        }
    }

    function close() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
            console.log('[Stream] Closed:', runId);
        }
    }

    function abort() {
        aborted = true;
        close();
        console.log('[Stream] Aborted:', runId);
    }

    return {
        start,
        abort,
        isConnected: () => eventSource !== null && eventSource.readyState === EventSource.OPEN
    };
}

/**
 * Simplified stream request
 * @param {string} runId - Run ID
 * @param {function} onChunk - Content callback
 * @param {function} onComplete - Completion callback
 * @returns {function} Abort function
 */
export function streamResponse(runId, onChunk, onComplete) {
    let content = '';

    const handler = createStreamHandler(runId, {
        onDelta: (data) => {
            if (data.content) {
                content += data.content;
                onChunk(data.content, content);
            }
        },
        onEnd: () => {
            onComplete(content);
        },
        onError: (error) => {
            console.error('[Stream] Error:', error.message || error);
            onComplete(content, error);
        }
    });

    handler.start();
    return () => handler.abort();
}

export default {
    EventTypes,
    createStreamHandler,
    streamResponse
};
