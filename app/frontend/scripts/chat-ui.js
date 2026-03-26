/**
 * DeepChat UI Configuration and Interaction
 * Configure DeepChat component integration with AtlasClaw API
 */

import { getSessionKey, initSession } from './session-manager.js';
import { createStreamHandler } from './stream-handler.js';
import { buildApiUrl } from './config.js';
import { t, isLocaleLoaded } from './i18n.js';

let chatElement = null;
let currentStreamHandler = null;

// Throttle flags for UI updates (only for assistant deltas, thinking uses direct DOM)
let assistantUpdatePending = false;

// Current thinking block DOM ID (for incremental updates)
let thinkingBlockId = null;

// Scroll throttle for thinking deltas
let thinkingScrollPending = false;

// Auto-scroll state
let userHasScrolledUp = false;
const SCROLL_THRESHOLD = 50; // pixels from bottom

/**
 * Get the scrollable message container inside DeepChat's Shadow DOM
 * @returns {HTMLElement|null}
 */
function getMessageContainer() {
    const dc = document.querySelector('deep-chat');
    if (!dc?.shadowRoot) return null;
    // Find the scrollable message container inside DeepChat's shadow DOM
    return dc.shadowRoot.querySelector('.messages-container') ||
           dc.shadowRoot.querySelector('[class*="message-container"]') ||
           dc.shadowRoot.querySelector('#messages');
}

/**
 * Setup scroll listener to detect user manual scrolling
 */
function setupScrollListener() {
    const container = getMessageContainer();
    if (!container || container._scrollListenerAttached) return;
    
    container.addEventListener('scroll', () => {
        const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < SCROLL_THRESHOLD;
        userHasScrolledUp = !isNearBottom;
    });
    container._scrollListenerAttached = true;
}

/**
 * Scroll to bottom if user hasn't manually scrolled up
 */
function scrollToBottom() {
    if (userHasScrolledUp) return;
    const container = getMessageContainer();
    if (!container) return;
    container.scrollTop = container.scrollHeight;
}

// Thinking animation inline styles for Shadow DOM (minimal style)
const THINKING_STYLES = `
<style>
/* Loading dots animation - subtle bounce */
@keyframes thinking-dot-minimal{0%,100%{opacity:.4;transform:translateY(0)}50%{opacity:.8;transform:translateY(-3px)}}
/* Subtle pulse for thinking icon */
@keyframes thinking-pulse-minimal{0%,100%{opacity:1}50%{opacity:.5}}
/* Animated ellipsis for thinking label */
@keyframes dot-blink{0%,20%{opacity:0}50%{opacity:1}80%,100%{opacity:0}}
/* Loading dots container */
.thinking-loading{display:inline-flex;align-items:center;gap:4px;padding:2px 0}
.thinking-loading .dot{width:6px;height:6px;border-radius:50%;background:#999;animation:thinking-dot-minimal 1.2s ease-in-out infinite}
.thinking-loading .dot:nth-child(2){animation-delay:.15s}
.thinking-loading .dot:nth-child(3){animation-delay:.3s}
/* Thinking dots - animated ellipsis */
.thinking-dots{display:inline-flex;margin-left:2px}
.thinking-dots span{animation:dot-blink 1.4s infinite}
.thinking-dots span:nth-child(1){animation-delay:0s}
.thinking-dots span:nth-child(2){animation-delay:0.2s}
.thinking-dots span:nth-child(3){animation-delay:0.4s}
/* Thinking block - minimal style, no background/border/shadow */
.thinking-block{margin-bottom:8px}
.thinking-block.thinking{margin-bottom:8px}
/* Header - inline, muted color */
.thinking-header{display:inline-flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer;user-select:none;color:#8b8b8b;font-size:14px;transition:color .2s ease}
.thinking-header:hover{color:#666}
/* Icon - small and refined */
.thinking-icon{font-size:14px;line-height:1;display:inline-flex;align-items:center}
.thinking-block.thinking .thinking-icon{animation:thinking-pulse-minimal 1.5s ease-in-out infinite}
/* Label text */
.thinking-label{font-size:14px;font-weight:400}
/* Timer */
.thinking-timer{font-size:14px;font-variant-numeric:tabular-nums}
/* Toggle arrow */
.thinking-toggle{font-size:12px;transition:transform .15s ease;display:inline-flex}
.thinking-block.open .thinking-toggle{transform:rotate(90deg)}
/* Body - minimal, indented */
.thinking-body{max-height:0;overflow:hidden;transition:max-height .15s ease,opacity .1s ease;opacity:0;padding-left:20px;font-size:14px;line-height:1.6;color:#8b8b8b}
.thinking-block.open .thinking-body{max-height:60vh;opacity:1;padding:8px 0 8px 20px;overflow-y:auto}
.thinking-block.thinking .thinking-body{max-height:50vh;opacity:1;padding:8px 0 8px 20px;overflow-y:auto}
.thinking-content-text{white-space:pre-wrap;word-break:break-word}
/* Details-based completed state - minimal */
details.thinking-block{margin-bottom:8px}
details.thinking-block>summary{display:inline-flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer;user-select:none;color:#8b8b8b;font-size:14px;transition:color .2s ease;list-style:none}
details.thinking-block>summary::-webkit-details-marker{display:none}
details.thinking-block>summary::marker{display:none}
details.thinking-block>summary:hover{color:#666}
details.thinking-block .thinking-toggle{font-size:12px;transition:transform .15s ease;display:inline-flex}
details.thinking-block[open] .thinking-toggle{transform:rotate(90deg)}
details.thinking-block .thinking-body{padding:8px 0 8px 20px;font-size:14px;line-height:1.6;color:#8b8b8b;max-height:60vh;overflow-y:auto;opacity:1}
</style>
`;

/**
 * Initialize DeepChat component
 * @param {HTMLElement} element - DeepChat DOM element
 */
export async function initChat(element) {
    chatElement = element;

    try {
        await initSession();
    } catch (sessionError) {
        console.error('[ChatUI] Failed to initialize session:', sessionError);
        // Continue without session - chat may not work properly
    }
    
    // Load agent info and set welcome message
    await loadAgentInfo(element);
    
    // Use handler mode for full control over requests and thinking support
    configureHandler(element);
    configureI18nAttributes(element);

    console.log('[ChatUI] Initialized');
}

/**
 * Load agent information and set welcome message
 * @param {HTMLElement} element - DeepChat DOM element
 */
async function loadAgentInfo(element) {
    try {
        const response = await fetch('/api/agent/info');
        if (response.ok) {
            const agentInfo = await response.json();
            console.log('[ChatUI] Agent info loaded:', agentInfo);
            
            // Set welcome message
            if (agentInfo.welcome_message) {
                element.introMessage = {
                    text: agentInfo.welcome_message,
                    role: 'ai'
                };
            }
        }
    } catch (error) {
        console.error('[ChatUI] Failed to load agent info:', error);
    }
}

/**
 * Configure DeepChat handler for complete control over requests
 * Handler mode provides full support for thinking animations
 * @param {HTMLElement} element - DeepChat DOM element
 */
function configureHandler(element) {
    // Define the handler function that will process all requests
    const handlerFn = async (body, signals) => {
        console.log('[ChatUI] Handler called with body:', body);
        
        // 1. Extract user message from body
        const messageText = extractMessageFromBody(body);
        
        // 2. Ensure session exists
        let sessionKey = getSessionKey();
        if (!sessionKey) {
            sessionKey = await initSession();
        }
        
        // 3. Call API to start the run
        let runId;
        try {
            const response = await fetch(buildApiUrl('/api/agent/run'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_key: sessionKey || '',
                    message: messageText || '',
                    timeout_seconds: 600
                })
            });
            
            if (!response.ok) {
                signals.onResponse({ html: `<p style="color: #d32f2f;">Error: ${response.status} ${response.statusText}</p>` });
                signals.onClose();
                return;
            }
            
            const data = await response.json();
            runId = data.run_id || data.runId || data.id;
            
            if (!runId) {
                console.warn('[ChatUI] Missing run_id:', data);
                signals.onResponse({ html: `<p style="color: #d32f2f;">${escapeHtml(data.detail || 'Error: No run_id')}</p>` });
                signals.onClose();
                return;
            }
        } catch (err) {
            console.error('[ChatUI] API call failed:', err);
            signals.onResponse({ html: `<p style="color: #d32f2f;">Error: ${escapeHtml(err.message)}</p>` });
            signals.onClose();
            return;
        }
        
        // 4. Show loading dots with styles
        signals.onResponse({ 
            html: `${THINKING_STYLES}<div class="thinking-loading"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>` 
        });
        
        // 5. Handle SSE stream with thinking support
        await handleStreamWithSignals(runId, signals);
    };
    
    // Set handler both ways for compatibility:
    // 1. element.handler - for backward compatibility and tests
    // 2. element.connect.handler + stream: true - for DeepChat to use handler mode with streaming support
    element.handler = handlerFn;
    element.connect = { handler: handlerFn, stream: true };
}

/**
 * Extract user message from DeepChat handler body
 * @param {Object} body - DeepChat handler body parameter
 * @returns {string} - Extracted message text
 */
function extractMessageFromBody(body) {
    if (!body) return '';
    if (body.messages && Array.isArray(body.messages) && body.messages.length > 0) {
        const lastMsg = body.messages[body.messages.length - 1];
        if (typeof lastMsg === 'string') return lastMsg;
        return lastMsg.text || lastMsg.content || '';
    }
    if (body.text) return body.text;
    if (body.message) return body.message;
    return '';
}

function configureI18nAttributes(element) {
    if (!isLocaleLoaded()) {
        console.warn('[ChatUI] Locale not loaded, skipping i18n config');
        return;
    }

    const introMessage = t('chat.introMessage');
    element.introMessage = { text: introMessage };

    const placeholder = t('chat.placeholder');
    element.textInput = {
        placeholder: {
            text: placeholder,
            style: { color: '#999' }
        },
        styles: {
            container: {
                borderRadius: '24px',
                border: '1px solid #ddd',
                padding: '12px 20px',
                backgroundColor: '#ffffff',
                boxShadow: '0 2px 6px rgba(0,0,0,0.05)'
            },
            text: { fontSize: '15px' }
        }
    };
}

/**
 * Build response message content
 * Combines thinking block HTML with response text (minimal style)
 */
function buildMessageText(thinkingContent, responseContent, elapsedSeconds = null, isThinking = false) {
    // If we're in thinking state, show the thinking-in-progress UI with animated dots (no timer)
    if (isThinking) {
        const thinkingText = thinkingContent || '';
        // Show the thinking label with animated ellipsis, no timer display
        const thinkingLabel = `思考中<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>`;
        
        let html = THINKING_STYLES;
        html += `<div class="thinking-block thinking">`;
        html += `<div class="thinking-header">`;
        html += `<span class="thinking-icon">⚡</span>`;
        html += `<span class="thinking-label">${thinkingLabel}</span>`;
        html += `<span class="thinking-toggle">›</span>`;
        html += `</div>`;
        html += `<div class="thinking-body"><div class="thinking-content-text">${escapeHtml(thinkingText)}</div></div>`;
        html += `</div>`;
        
        return { html };
    }
    
    // Thinking completed - show collapsible block using native <details> element
    if (thinkingContent && thinkingContent.trim().length > 0) {
        // Ensure elapsed time is at least 0.1s if we have thinking content
        // (handles edge case where events arrive too quickly)
        let displayElapsed = elapsedSeconds;
        if (displayElapsed === 0 || displayElapsed === null) {
            displayElapsed = 0.1;
        }
        const timerText = displayElapsed !== null ? `${displayElapsed}s` : '';
        const labelText = timerText ? `已思考（用时 ${timerText}）` : '已思考';
        
        let html = THINKING_STYLES;
        // Use native <details>/<summary> for collapsed state
        html += `<details class="thinking-block">`;
        html += `<summary class="thinking-header">`;
        html += `<span class="thinking-icon">⚡</span>`;
        html += `<span class="thinking-label">${labelText}</span>`;
        html += `<span class="thinking-toggle">›</span>`;
        html += `</summary>`;
        html += `<div class="thinking-body"><div class="thinking-content-text">${escapeHtml(thinkingContent)}</div></div>`;
        html += `</details>`;
        
        // Append response content
        if (responseContent) {
            return { html, text: responseContent };
        }
        return { html };
    }
    
    // No thinking content - return as html to avoid mixing text/html in same stream
    return { html: `<div class="response-content">${escapeHtml(responseContent || '')}</div>` };
}

/**
 * Build HTML content for message update
 */
function buildMessageContent(thinkingContent, responseContent, elapsedSeconds = null, isThinking = false) {
    const result = buildMessageText(thinkingContent, responseContent, elapsedSeconds, isThinking);
    
    // If we have both html (thinking block) and text (response), combine them in a wrapper
    if (result.html && result.text) {
        return { html: `<div class="message-wrapper">${result.html}<div class="response-content">${escapeHtml(result.text)}</div></div>` };
    }
    
    // Ensure we always return html to avoid mixing text/html in same stream
    if (result.text !== undefined && !result.html) {
        return { html: `<div class="response-content">${escapeHtml(result.text)}</div>` };
    }
    
    // For thinking-only states, also wrap
    if (result.html) {
        return { html: `<div class="message-wrapper">${result.html}</div>` };
    }
    
    return result;
}

/**
 * Escape HTML special characters
 */
function escapeHtml(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;')
        .replace(/\n/g, '<br>');
}

/**
 * Handle SSE stream using DeepChat signals
 * @param {string} runId - The run ID from the API
 * @param {Object} signals - DeepChat handler signals object
 */
async function handleStreamWithSignals(runId, signals) {
    console.log('[ChatUI] Starting stream for run:', runId);
    
    let aiMessageContent = '';
    let hasRenderedDelta = false;
    
    // Thinking state
    let thinkingContent = '';
    let thinkingStartTime = null;
    let thinkingElapsedSeconds = 0;
    let thinkingTimerInterval = null;
    let thinkingFinalized = false;
    let hasThinkingContent = false;
    
    
    function updateUI() {
        try {
            const content = buildMessageContent(
                thinkingContent, aiMessageContent, 
                thinkingElapsedSeconds, 
                !thinkingFinalized && hasThinkingContent
            );
            if (content.html) {
                signals.onResponse({ html: content.html, overwrite: true });
            } else if (content.text !== undefined) {
                // Must use html to match initial loading dots response (cannot mix text and html)
                signals.onResponse({ html: `<div class="response-content">${escapeHtml(content.text)}</div>`, overwrite: true });
            }
            // Setup scroll listener and auto-scroll to bottom
            setupScrollListener();
            scrollToBottom();
        } catch (e) {
            console.warn('[ChatUI] Failed to update UI:', e);
        }
    }
    
    function startThinkingTimer() {
        if (thinkingTimerInterval) return;
        thinkingStartTime = Date.now();
        // Timer runs in background for final elapsed time calculation
        // but does NOT update UI (no timer display during thinking)
        thinkingTimerInterval = setInterval(() => {
            // Calculate elapsed time in background (for final display when thinking ends)
            thinkingElapsedSeconds = Math.round((Date.now() - thinkingStartTime) / 100) / 10;
            // Note: UI is NOT updated here - animated dots handle the visual feedback
        }, 100);
    }
    
    function stopThinkingTimer() {
        if (thinkingTimerInterval) {
            clearInterval(thinkingTimerInterval);
            thinkingTimerInterval = null;
        }
        if (thinkingStartTime) {
            // Only update elapsed time if not already set by backend
            const clientElapsed = Math.round((Date.now() - thinkingStartTime) / 100) / 10;
            if (thinkingElapsedSeconds <= 0.1) {
                thinkingElapsedSeconds = clientElapsed;
            }
            // Immediately update UI to reflect final elapsed time
            updateUI();
        }
    }
    
    return new Promise((resolve) => {
        currentStreamHandler = createStreamHandler(runId, {
            onStart: () => {
                console.log('[ChatUI] Stream started');
            },
            onDelta: (data) => {
                if (!data.content) return;
                
                if (!thinkingFinalized) {
                    thinkingFinalized = true;
                    stopThinkingTimer();
                }
                
                aiMessageContent += data.content;
                hasRenderedDelta = true;
                
                // Throttle UI updates to max once per 100ms
                if (!assistantUpdatePending) {
                    assistantUpdatePending = true;
                    setTimeout(() => {
                        assistantUpdatePending = false;
                        try {
                            if (hasThinkingContent) {
                                const content = buildMessageContent(thinkingContent, aiMessageContent, thinkingElapsedSeconds, false);
                                const htmlContent = content.html || `<div class="response-content">${escapeHtml(content.text || '')}</div>`;
                                signals.onResponse({ html: htmlContent, overwrite: true });
                            } else {
                                signals.onResponse({ html: `<div class="response-content">${escapeHtml(aiMessageContent)}</div>`, overwrite: true });
                            }
                            setupScrollListener();
                            scrollToBottom();
                        } catch (e) {
                            console.warn('[ChatUI] Failed to update message:', e);
                        }
                    }, 100);
                }
            },
            onToolStart: (data) => {
                console.log('[ChatUI] Tool start:', data.tool_name);
                showToolIndicator(data.tool_name);
            },
            onToolEnd: (data) => {
                console.log('[ChatUI] Tool end:', data.tool_name);
                hideToolIndicator();
            },
            onThinkingStart: () => {
                console.log('[ChatUI] Thinking started');
                hasThinkingContent = true;
                startThinkingTimer();
                // Reset scroll state when new thinking starts
                userHasScrolledUp = false;
                
                // Generate unique block ID for incremental DOM updates
                thinkingBlockId = `tb-${Date.now()}`;
                
                // Render initial thinking block HTML (only once, includes styles)
                // Wrap in message-wrapper to ensure proper DOM structure in Shadow DOM
                const initialHtml = `<div class="message-wrapper">${THINKING_STYLES}
                    <div class="thinking-block thinking" id="${thinkingBlockId}">
                        <div class="thinking-header">
                            <span class="thinking-icon">⚡</span>
                            <span class="thinking-label">思考中<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></span>
                            <span class="thinking-toggle">›</span>
                        </div>
                        <div class="thinking-body">
                            <div class="thinking-content-text" id="${thinkingBlockId}-content"></div>
                        </div>
                    </div>
                </div>`;
                
                signals.onResponse({ html: initialHtml, overwrite: true });
            },
            onThinkingDelta: (data) => {
                const content = data?.content || '';
                if (!content) return;
                
                // Safety: ensure timer is started if we receive thinking delta
                // (handles edge case where thinking:start event was missed)
                if (!thinkingStartTime) {
                    hasThinkingContent = true;
                    startThinkingTimer();
                }
                
                thinkingContent += content;
                
                // Incremental DOM append - no full HTML rebuild
                const dc = document.querySelector('deep-chat');
                if (dc?.shadowRoot && thinkingBlockId) {
                    const contentEl = dc.shadowRoot.querySelector(`#${thinkingBlockId}-content`);
                    if (contentEl) {
                        const escapedContent = content
                            .replace(/&/g, '&amp;')
                            .replace(/</g, '&lt;')
                            .replace(/>/g, '&gt;')
                            .replace(/\n/g, '<br>');
                        contentEl.insertAdjacentHTML('beforeend', escapedContent);
                    }
                }
                
                // Throttled auto-scroll (max once per 100ms to avoid jank)
                if (!thinkingScrollPending) {
                    thinkingScrollPending = true;
                    setTimeout(() => {
                        thinkingScrollPending = false;
                        setupScrollListener();
                        scrollToBottom();
                    }, 100);
                }
            },
            onThinkingEnd: (data) => {
                console.log('[ChatUI] Thinking ended');
                thinkingFinalized = true;
                
                // Use actual elapsed time from backend if available
                if (data?.elapsed && data.elapsed > 0) {
                    thinkingElapsedSeconds = data.elapsed;
                }
                
                stopThinkingTimer();
                
                // Now do a full update to switch to completed state (<details> element)
                updateUI();
            },
            onEnd: () => {
                console.log('[ChatUI] Stream ended');
                
                // Delay final render slightly to ensure all pending deltas are processed
                // This fixes timing issues where lifecycle:end arrives before assistant delta is fully processed
                const doFinalRender = () => {
                    // Cancel pending throttle for immediate final render
                    assistantUpdatePending = false;
                    thinkingFinalized = true;
                    stopThinkingTimer();
                    
                    // Final render with complete content
                    if (hasRenderedDelta || hasThinkingContent) {
                        updateUI();
                    } else {
                        try {
                            signals.onResponse({ html: '<div class="response-content">—</div>', overwrite: true });
                        } catch (e) {}
                    }
                    
                    signals.onClose();
                    currentStreamHandler = null;
                    resolve();
                };
                
                // Always delay slightly to ensure all pending events (especially assistant deltas) are processed
                // Assistant event and lifecycle:end arrive nearly simultaneously, delay ensures correct ordering
                setTimeout(doFinalRender, 200);
            },
            onError: (error) => {
                console.error('[ChatUI] Stream error:', error);
                thinkingFinalized = true;
                stopThinkingTimer();
                
                try {
                    // Must use html to match initial loading dots response (cannot mix text and html)
                    signals.onResponse({ 
                        html: `<p style="color: #d32f2f;">Error: ${escapeHtml(error?.message || 'Unknown error')}</p>`, 
                        overwrite: true 
                    });
                } catch (e) {}
                
                signals.onClose();
                currentStreamHandler = null;
                resolve();
            }
        });
        
        currentStreamHandler.start();
    });
}

function showToolIndicator(toolName) {
    console.log('[ChatUI] Executing tool:', toolName);
}

function hideToolIndicator() {
    // Hide loading indicator
}

export function abortCurrentStream() {
    if (currentStreamHandler) {
        currentStreamHandler.abort();
        currentStreamHandler = null;
    }
}

export function getChatElement() {
    return chatElement;
}

export default {
    initChat,
    abortCurrentStream,
    getChatElement,
    configureI18nAttributes
};
