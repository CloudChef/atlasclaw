/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

/**
 * chat-ui.js regression tests
 * Tests for DeepChat handler mode implementation
 */

const defaultMockTranslations = {
    'chat.placeholder': 'Enter your question...',
    'chat.copyMessage': 'Copy message',
    'chat.openObject': 'Open',
    'chat.openObjectAria': 'Open {{label}}',
    'chat.objectAction': 'Action',
    'chat.objectActions': 'Actions',
    'chat.objectActionsAria': 'Actions for {{label}}',
    'chat.objectActionConfirm': 'Confirm {{label}}?',
    'chat.objectActionRequiredInput': 'This action requires input.',
    'chat.runtimeThinking': 'Thinking',
    'chat.runtimeRetrying': 'Retrying',
    'chat.runtimeWaitingForTool': 'Waiting for tool',
    'chat.runtimeToolRunning': 'Running tool',
    'chat.runtimeControlledPath': 'Controlled path',
    'chat.runtimeFailed': 'Failed',
    'chat.modelThinking': 'Model thinking'
};
globalThis.__atlasclawTestTranslations = { ...defaultMockTranslations };

jest.mock('../../app/frontend/scripts/config.js', () => ({
    buildApiUrl: (path) => `http://127.0.0.1:8000${path}`
}));

jest.mock('../../app/frontend/scripts/i18n.js', () => ({
    t: jest.fn((key) => key),
    translateIfExists: jest.fn((key) => globalThis.__atlasclawTestTranslations?.[key] || null),
    getCurrentLocale: jest.fn(() => globalThis.__atlasclawTestLocale || 'en-US'),
    isLocaleLoaded: jest.fn(() => false)
}));

beforeEach(() => {
    globalThis.__atlasclawTestTranslations = { ...defaultMockTranslations };
    globalThis.__atlasclawTestLocale = 'en-US';
    jest.resetModules();
    global.fetch = jest.fn(() => Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ messages: [] })
    }));
    document.body.innerHTML = '';
    sessionStorageMock.clear();
    MockEventSource.instances = [];
});

const sessionStorageMock = (() => {
    let store = {};
    return {
        getItem: jest.fn((key) => store[key] || null),
        setItem: jest.fn((key, value) => { store[key] = value; }),
        removeItem: jest.fn((key) => { delete store[key]; }),
        clear: jest.fn(() => { store = {}; })
    };
})();

Object.defineProperty(global, 'sessionStorage', { value: sessionStorageMock });

class MockEventSource {
    constructor(url, options = {}) {
        this.url = url;
        this.options = options;
        this.readyState = EventSource.CONNECTING;
        this.listeners = {};
        MockEventSource.instances.push(this);
    }

    addEventListener(type, callback) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(callback);
    }

    close() {
        this.readyState = EventSource.CLOSED;
    }

    simulateEvent(type, data) {
        const callbacks = this.listeners[type] || [];
        callbacks.forEach(cb => cb({ data: JSON.stringify(data) }));
    }
}

MockEventSource.CONNECTING = 0;
MockEventSource.OPEN = 1;
MockEventSource.CLOSED = 2;
MockEventSource.instances = [];

global.EventSource = MockEventSource;

/**
 * Create mock signals object for DeepChat handler
 */
function createMockSignals() {
    return {
        onResponse: jest.fn(),
        onClose: jest.fn(),
        stopClicked: { listener: null }
    };
}

function createDomSignals(messages) {
    return {
        onResponse: jest.fn((payload = {}) => {
            if (!payload.overwrite) return;
            messages.innerHTML = payload.html || '';
        }),
        onClose: jest.fn(),
        stopClicked: { listener: null }
    };
}

/**
 * Create a mock chat element for handler mode
 */
function createChatElement() {
    return {
        handler: null,
        introMessage: null,
        textInput: null,
        addMessage: jest.fn(),
        getMessages: jest.fn(() => [])
    };
}

async function renderAssistantHtml(text, runId = 'run-render-assistant-html') {
    const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
    const element = createChatElement();
    const signals = createMockSignals();

    global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ session_key: 'session-123' })
    }).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({})
    });

    await initChat(element);
    global.fetch.mockClear();
    global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ run_id: runId })
    });

    const handlerPromise = element.handler(
        { messages: [{ text: 'render please', role: 'user' }] },
        signals
    );

    await new Promise(r => setTimeout(r, 100));
    const stream = MockEventSource.instances.at(-1);
    stream.simulateEvent('assistant', { text, is_delta: true });
    await new Promise(r => setTimeout(r, 160));

    const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
    stream.simulateEvent('lifecycle', { phase: 'end' });
    await handlerPromise;
    return htmlPayload;
}

async function startStreamingAssistant(runId = 'run-streaming-assistant') {
    const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
    const element = createChatElement();
    const signals = createMockSignals();

    global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ session_key: 'session-123' })
    }).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({})
    });

    await initChat(element);
    global.fetch.mockClear();
    global.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ run_id: runId })
    });

    const handlerPromise = element.handler(
        { messages: [{ text: 'render object links', role: 'user' }] },
        signals
    );

    await new Promise(r => setTimeout(r, 100));
    return {
        element,
        signals,
        stream: MockEventSource.instances.at(-1),
        handlerPromise
    };
}

function latestHtml(signals) {
    return signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
}

function latestAgentRunRequestBody() {
    const runRequest = global.fetch.mock.calls
        .filter(([url]) => String(url).endsWith('/api/agent/run'))
        .at(-1);
    return runRequest ? JSON.parse(runRequest[1].body) : null;
}

function parseHtml(html) {
    const container = document.createElement('div');
    container.innerHTML = html;
    return container;
}

function localizedText(defaultText, translations = {}) {
    return {
        default: defaultText,
        translations: {
            'en-US': defaultText,
            ...translations
        }
    };
}

function objectActionReference({
    href,
    actionId = 'open_detail',
    label = 'Open',
    actions = null,
    ...context
}) {
    return {
        ...context,
        object_actions: actions || [
            {
                action_id: actionId,
                kind: 'open_url',
                display_label: localizedText(label),
                href
            }
        ]
    };
}

function createDomChatElement() {
    const element = document.createElement('deep-chat');
    element.handler = null;
    element.introMessage = null;
    element.textInput = null;
    element.addMessage = jest.fn();
    element.getMessages = jest.fn(() => []);
    element.attachShadow({ mode: 'open' });

    const input = document.createElement('div');
    input.setAttribute('contenteditable', 'true');
    element.shadowRoot.appendChild(input);
    document.body.appendChild(element);

    return { element, input };
}

function createDomChatElementWithMessages() {
    const { element, input } = createDomChatElement();
    const messages = document.createElement('div');
    messages.className = 'messages-container';
    messages.innerHTML = '<div class="outer-message-container">stale message</div>';
    element.shadowRoot.appendChild(messages);
    return { element, input, messages };
}

function setEditableText(input, text) {
    input.textContent = text;
    const range = document.createRange();
    range.selectNodeContents(input);
    range.collapse(false);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

function appendRenderedMessage(messages, role, text) {
    const outer = document.createElement('div');
    outer.className = 'outer-message-container';
    const bubbleClass = role === 'user' ? 'user-message-text' : 'ai-message-text';
    outer.innerHTML = `
        <div class="inner-message-container">
            <div class="message-bubble ${bubbleClass}">${text}</div>
        </div>
    `;
    messages.appendChild(outer);
    return outer.querySelector('.message-bubble');
}

function appendHistoryMessage(messages, message) {
    appendRenderedMessage(
        messages,
        message.role === 'user' ? 'user' : 'ai',
        message.role === 'user' ? message.text : message.html
    );
}

function waitForMutationObserver() {
    return new Promise(resolve => setTimeout(resolve, 0));
}

function parseRenderedElapsedSeconds(value) {
    const text = String(value || '').trim();
    if (text.endsWith('ms')) return Number.parseFloat(text.slice(0, -2)) / 1000;
    if (text.endsWith('s')) return Number.parseFloat(text.slice(0, -1));
    return Number.parseFloat(text);
}

function createDeferred() {
    let resolve;
    const promise = new Promise((promiseResolve) => {
        resolve = promiseResolve;
    });
    return { promise, resolve };
}

describe('chat-ui.js handler mode', () => {
    test('enter is blocked while IME composition is still active', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        await initChat(element);

        const deepChatSubmitListener = jest.fn();
        input.addEventListener('keydown', deepChatSubmitListener);

        input.dispatchEvent(new Event('compositionstart', { bubbles: true }));

        const composingEnter = new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true
        });
        const dispatchResult = input.dispatchEvent(composingEnter);

        expect(dispatchResult).toBe(false);
        expect(composingEnter.defaultPrevented).toBe(true);
        expect(deepChatSubmitListener).not.toHaveBeenCalled();
    });

    test('composition commit enter is blocked once before normal submit resumes', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        await initChat(element);

        const deepChatSubmitListener = jest.fn();
        input.addEventListener('keydown', deepChatSubmitListener);

        input.dispatchEvent(new Event('compositionstart', { bubbles: true }));
        input.dispatchEvent(new Event('compositionend', { bubbles: true }));

        const firstEnter = new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true
        });
        const firstDispatchResult = input.dispatchEvent(firstEnter);

        expect(firstDispatchResult).toBe(false);
        expect(firstEnter.defaultPrevented).toBe(true);
        expect(deepChatSubmitListener).not.toHaveBeenCalled();

        const secondEnter = new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true
        });
        const secondDispatchResult = input.dispatchEvent(secondEnter);

        expect(secondDispatchResult).toBe(true);
        expect(secondEnter.defaultPrevented).toBe(false);
        expect(deepChatSubmitListener).toHaveBeenCalledTimes(1);
    });

    test('shift+enter is not blocked by the IME guard', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        await initChat(element);

        const deepChatSubmitListener = jest.fn();
        input.addEventListener('keydown', deepChatSubmitListener);
        input.dispatchEvent(new Event('compositionstart', { bubbles: true }));
        input.dispatchEvent(new Event('compositionend', { bubbles: true }));

        const shiftEnter = new KeyboardEvent('keydown', {
            key: 'Enter',
            shiftKey: true,
            bubbles: true,
            cancelable: true
        });
        const dispatchResult = input.dispatchEvent(shiftEnter);

        expect(dispatchResult).toBe(true);
        expect(shiftEnter.defaultPrevented).toBe(false);
        expect(deepChatSubmitListener).toHaveBeenCalledTimes(1);
    });

    test('composition guard still blocks enter after Deep Chat replaces the input', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        await initChat(element);

        const replacementInput = document.createElement('div');
        replacementInput.setAttribute('contenteditable', 'true');
        element.shadowRoot.replaceChild(replacementInput, input);

        const deepChatSubmitListener = jest.fn();
        replacementInput.addEventListener('keydown', deepChatSubmitListener);

        replacementInput.dispatchEvent(new Event('compositionstart', { bubbles: true }));
        replacementInput.dispatchEvent(new Event('compositionend', { bubbles: true }));

        const commitEnter = new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true
        });
        const dispatchResult = replacementInput.dispatchEvent(commitEnter);

        expect(dispatchResult).toBe(false);
        expect(commitEnter.defaultPrevented).toBe(true);
        expect(deepChatSubmitListener).not.toHaveBeenCalled();
    });

    test('initChat configures handler on element', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ welcome_message: 'Hello!' })
        });

        await initChat(element);

        expect(typeof element.handler).toBe('function');
        expect(element.auxiliaryStyle).not.toContain('#text-input-container { border: none !important; background: transparent !important; box-shadow: none !important; }');
        expect(element.auxiliaryStyle).not.toContain('#input { background: transparent !important; }');
    });

    test('decorates only user messages with a localized copy action', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ messages: [] })
            });

        try {
            await initChat(element);
            appendRenderedMessage(messages, 'user', 'copy this user message');
            appendRenderedMessage(messages, 'ai', 'assistant messages stay untouched');
            await waitForMutationObserver();

            const buttons = messages.querySelectorAll('.atlas-user-message-copy-btn');
            expect(buttons).toHaveLength(1);
            expect(buttons[0].title).toBe('Copy message');
            expect(buttons[0].getAttribute('aria-label')).toBe('Copy message');
            expect(messages.querySelector('.ai-message-text + .atlas-user-message-copy-btn')).toBeNull();
        } finally {
            cancelChatInputFocusRetry();
        }
    });

    test('decorates restored history user messages with copy actions', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();
        element.loadHistory = jest.fn((history) => {
            messages.innerHTML = '';
            history.forEach((message) => {
                appendHistoryMessage(messages, message);
            });
        });

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    messages: [
                        { role: 'user', content: 'historical user prompt' },
                        { role: 'assistant', content: 'historical answer' }
                    ]
                })
            });

        try {
            await initChat(element);
            await waitForMutationObserver();

            const buttons = messages.querySelectorAll('.atlas-user-message-copy-btn');
            expect(buttons).toHaveLength(1);
            expect(messages.querySelector('.user-message-text')?.textContent).toBe('historical user prompt');
            expect(messages.querySelector('.ai-message-text + .atlas-user-message-copy-btn')).toBeNull();
        } finally {
            cancelChatInputFocusRetry();
        }
    });

    test('decorates user messages after DeepChat replaces the message container', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ messages: [] })
            });

        try {
            await initChat(element);
            const replacement = document.createElement('div');
            replacement.className = 'messages-container';
            messages.replaceWith(replacement);
            appendRenderedMessage(replacement, 'user', 'late restored user message');
            await waitForMutationObserver();
            await waitForMutationObserver();

            const buttons = replacement.querySelectorAll('.atlas-user-message-copy-btn');
            expect(buttons).toHaveLength(1);
            expect(buttons[0].title).toBe('Copy message');
        } finally {
            cancelChatInputFocusRetry();
        }
    });

    test('copy action writes user message text and briefly shows success state', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const writeText = jest.fn(() => Promise.resolve());
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText }
        });

        const { initChat, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ messages: [] })
            });

        try {
            await initChat(element);
            appendRenderedMessage(messages, 'user', 'copy probe message');
            await waitForMutationObserver();

            const button = messages.querySelector('.atlas-user-message-copy-btn');
            expect(button).not.toBeNull();

            jest.useFakeTimers();
            button.click();
            await Promise.resolve();
            await Promise.resolve();

            expect(writeText).toHaveBeenCalledWith('copy probe message');
            expect(button.classList.contains('copied')).toBe(true);
            expect(button.title).toBe('Copy message');
            expect(button.getAttribute('aria-label')).toBe('Copy message');

            await jest.advanceTimersByTimeAsync(1200);

            expect(button.classList.contains('copied')).toBe(false);
            expect(button.title).toBe('Copy message');
        } finally {
            jest.useRealTimers();
            cancelChatInputFocusRetry();
        }
    });

    test('initChat focuses the chat input when it is ready', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ messages: [] })
            });

        await initChat(element);

        expect(document.activeElement).toBe(element);
        expect(element.shadowRoot.activeElement).toBe(input);
    });

    test('focus retry reattaches slash picker after DeepChat replaces the input', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');
        sessionStorage.setItem('atlasclaw_auth_token', 'token-replaced-input');

        global.fetch = jest.fn((url) => {
            const target = String(url);
            if (target.includes('/api/agent/capabilities')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({
                        capabilities: [
                            {
                                id: 'replacement-skill',
                                kind: 'skill',
                                command: '/replacement-skill',
                                label: 'replacement-skill',
                                skill_name: 'replacement-skill',
                                qualified_skill_name: 'replacement-skill',
                                target_skill_names: ['replacement-skill'],
                                target_tool_names: ['replacement_skill']
                            }
                        ]
                    })
                });
            }
            if (target.includes('/api/agent/info')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({})
                });
            }
            if (target.includes('/history')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ messages: [] })
                });
            }
            return Promise.resolve({
                ok: true,
                json: () => Promise.resolve({})
            });
        });

        const { initChat, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();

        try {
            await initChat(element);

            const replacementInput = document.createElement('div');
            replacementInput.setAttribute('contenteditable', 'true');
            element.shadowRoot.replaceChild(replacementInput, input);

            await new Promise(r => setTimeout(r, 160));

            expect(document.activeElement).toBe(element);
            expect(element.shadowRoot.activeElement).toBe(replacementInput);
            expect(replacementInput._slashPickerAttached).toBe(true);

            setEditableText(replacementInput, '/');
            await new Promise(r => setTimeout(r, 80));

            expect(document.querySelector('.slash-picker-row')?.textContent).toContain('/replacement-skill');
        } finally {
            cancelChatInputFocusRetry();
        }
    });

    test('initChat restores persisted session history for active session', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ welcome_message: 'Hello!' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    messages: [
                        { role: 'user', content: 'hello atlas', timestamp: '2026-03-27T10:00:00' },
                        { role: 'assistant', content: 'hi there', timestamp: '2026-03-27T10:00:01' }
                    ]
                })
            });

        await initChat(element);

        expect(element.history).toHaveLength(2);
        expect(element.history[0]).toEqual({ role: 'user', text: 'hello atlas' });
        expect(element.history[1].role).toBe('ai');
        expect(element.history[1].html).toContain('<div class="response-content">');
        expect(element.history[1].html).toContain('hi there');
        expect(element.introMessage).toBeNull();
    });

    test('initChat restores assistant download controls from persisted history metadata', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ welcome_message: 'Hello!' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    messages: [
                        { role: 'user', content: 'make report', timestamp: '2026-03-27T10:00:00' },
                        {
                            role: 'assistant',
                            content: '## Report ready\nThe report is ready: report.xlsx',
                            timestamp: '2026-03-27T10:00:01',
                            workspace_downloads: [{ path: 'report.xlsx' }]
                        }
                    ]
                })
            });

        await initChat(element);

        const restoredAssistant = element.history[1];
        expect(restoredAssistant.role).toBe('ai');
        expect(restoredAssistant.html).toContain('<h2>Report ready</h2>');
        expect(restoredAssistant.html).toContain('class="workspace-download-link"');
        expect(restoredAssistant.html).toContain('/api/workspace/files/download?path=report.xlsx');
        expect(restoredAssistant.html).toContain('<span class="workspace-download-text">report.xlsx</span>');
    });

    test('activateSession clears rendered messages when switching to an empty session', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat, activateSession } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ welcome_message: 'Hello!' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ messages: [] })
            });

        await initChat(element);

        messages.innerHTML = '<div class="outer-message-container">stale message</div>';

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ messages: [] })
        });

        await activateSession('session-456');

        expect(messages.innerHTML).toBe('');
        expect(element.history).toEqual([]);
    });

    test('activateSession ignores stale history responses from earlier session switches', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-initial');

        const slowHistory = createDeferred();
        const fastMessages = [
            { role: 'user', content: 'fast user prompt', timestamp: '2026-05-05T09:00:00' },
            { role: 'assistant', content: 'fast assistant answer', timestamp: '2026-05-05T09:00:01' }
        ];
        const slowMessages = [
            { role: 'user', content: 'slow stale prompt', timestamp: '2026-05-05T08:00:00' }
        ];

        global.fetch = jest.fn((url) => {
            const target = String(url);
            if (target.includes('/api/agent/info')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ welcome_message: 'Hello!' })
                });
            }
            if (target.includes('/api/sessions/session-initial/history')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ messages: [] })
                });
            }
            if (target.includes('/api/sessions/session-slow/history')) {
                return slowHistory.promise;
            }
            if (target.includes('/api/sessions/session-fast/history')) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ messages: fastMessages })
                });
            }
            return Promise.resolve({
                ok: true,
                json: () => Promise.resolve({})
            });
        });

        const { initChat, activateSession, cancelChatInputFocusRetry } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();
        element.loadHistory = jest.fn((history) => {
            messages.innerHTML = '';
            history.forEach((message) => {
                appendHistoryMessage(messages, message);
            });
        });

        try {
            await initChat(element);

            const slowActivation = activateSession('session-slow');
            const fastActivation = activateSession('session-fast');

            await fastActivation;
            expect(Array.from(messages.querySelectorAll('.message-bubble')).map((bubble) => bubble.textContent)).toEqual([
                'fast user prompt',
                'fast assistant answer'
            ]);

            slowHistory.resolve({
                ok: true,
                json: () => Promise.resolve({ messages: slowMessages })
            });
            await slowActivation;

            expect(Array.from(messages.querySelectorAll('.message-bubble')).map((bubble) => bubble.textContent)).toEqual([
                'fast user prompt',
                'fast assistant answer'
            ]);
            expect(element.loadHistory).not.toHaveBeenLastCalledWith([
                { role: 'user', text: 'slow stale prompt' }
            ]);
        } finally {
            cancelChatInputFocusRetry();
        }
    });

    test('handler calls API with correct body and starts SSE stream', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();
        
        // Mock session init
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        });
        // Mock agent info
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        // Mock /api/agent/run response
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-123' })
        });

        // Call handler with mock body
        const handlerPromise = element.handler(
            { messages: [{ text: 'hello', role: 'user' }] },
            signals
        );

        // Wait for API call
        await new Promise(r => setTimeout(r, 50));

        // Verify API was called with correct body
        expect(global.fetch).toHaveBeenCalledWith(
            expect.stringMatching(/\/api\/agent\/run$/),
            expect.objectContaining({
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            })
        );
        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody).toMatchObject({
            session_key: 'session-123',
            message: 'hello',
            timeout_seconds: 600,
            context: expect.objectContaining({
                ui_locale: expect.any(String),
                timezone: expect.any(String),
            }),
        });

        // Wait for SSE to be created
        await new Promise(r => setTimeout(r, 100));

        // Verify SSE stream started
        expect(MockEventSource.instances).toHaveLength(1);
        expect(MockEventSource.instances[0].url).toMatch(/\/api\/agent\/runs\/run-123\/stream$/);

        // Runtime panel should show initial runtime receipt/analysis status.
        expect(signals.onResponse).toHaveBeenCalled();
        expect(signals.onResponse).toHaveBeenLastCalledWith(
            expect.objectContaining({
                html: expect.stringContaining('Starting response analysis.')
            })
        );

        // Simulate stream end to complete handler
        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;

        expect(signals.onClose).toHaveBeenCalled();
    });

    test('handler sends selected provider skill capability from slash picker', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();
        const signals = createMockSignals();
        sessionStorage.setItem('atlasclaw_auth_token', 'token-provider');

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'provider-skill',
                        kind: 'provider_skill',
                        command: '/default.resource-request',
                        label: 'default.resource-request',
                        provider_type: 'demo-provider',
                        provider_display_name: 'Demo Provider',
                        instance_name: 'default',
                        skill_name: 'resource-request',
                        qualified_skill_name: 'demo-provider:resource-request',
                        target_provider_types: ['demo-provider'],
                        target_skill_names: ['demo-provider:resource-request', 'resource-request'],
                        target_tool_names: ['demo_provider_resource_request']
                    }
                ]
            })
        });

        setEditableText(input, '/def');
        await new Promise(r => setTimeout(r, 80));
        document.querySelector('.slash-picker-row').click();

        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-provider-selection' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: `${input.textContent}create resource`, role: 'user' }] },
            signals
        );
        await new Promise(r => setTimeout(r, 80));

        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody.message).toBe('create resource');
        expect(parsedBody.context.selected_capability).toMatchObject({
            kind: 'provider_skill',
            provider_type: 'demo-provider',
            instance_name: 'default',
            qualified_skill_name: 'demo-provider:resource-request',
            target_tool_names: ['demo_provider_resource_request']
        });

        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not consume selected capability after command prefix edit', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();
        const signals = createMockSignals();
        sessionStorage.setItem('atlasclaw_auth_token', 'token-prefix-edit');

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'foo-skill',
                        kind: 'skill',
                        command: '/foo',
                        label: 'foo',
                        skill_name: 'foo',
                        qualified_skill_name: 'foo',
                        target_skill_names: ['foo'],
                        target_tool_names: ['foo_tool']
                    }
                ]
            })
        });

        setEditableText(input, '/fo');
        await new Promise(r => setTimeout(r, 80));
        document.querySelector('.slash-picker-row').click();

        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-prefix-edit' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: '/foobar do x', role: 'user' }] },
            signals
        );
        await new Promise(r => setTimeout(r, 80));

        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody.message).toBe('/foobar do x');
        expect(parsedBody.context.selected_capability).toBeUndefined();

        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler sends selected standalone skill capability from slash picker', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input } = createDomChatElement();
        const signals = createMockSignals();
        sessionStorage.setItem('atlasclaw_auth_token', 'token-skill');

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'standalone-skill',
                        kind: 'skill',
                        command: '/no-provider-resource-request',
                        label: 'no-provider-resource-request',
                        skill_name: 'no-provider-resource-request',
                        qualified_skill_name: 'no-provider-resource-request',
                        target_skill_names: ['no-provider-resource-request'],
                        target_tool_names: ['no_provider_resource_request']
                    }
                ]
            })
        });

        setEditableText(input, '/no-provider');
        await new Promise(r => setTimeout(r, 80));
        document.querySelector('.slash-picker-row').click();

        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-skill-selection' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: `${input.textContent}create resource`, role: 'user' }] },
            signals
        );
        await new Promise(r => setTimeout(r, 80));

        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody.message).toBe('create resource');
        expect(parsedBody.context.selected_capability).toMatchObject({
            kind: 'skill',
            qualified_skill_name: 'no-provider-resource-request',
            target_tool_names: ['no_provider_resource_request']
        });

        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('slash picker does not reuse cached capabilities after auth token changes', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();

        sessionStorage.setItem('atlasclaw_auth_token', 'token-a');
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'old-skill',
                        kind: 'skill',
                        command: '/old-skill',
                        label: 'old-skill',
                        skill_name: 'old-skill',
                        qualified_skill_name: 'old-skill',
                        target_skill_names: ['old-skill'],
                        target_tool_names: ['old_skill']
                    }
                ]
            })
        });

        setupSlashCapabilityPicker(element);
        setEditableText(input, '/old');
        await new Promise(r => setTimeout(r, 80));
        expect(document.querySelector('.slash-picker-row')?.textContent).toContain('/old-skill');

        sessionStorage.setItem('atlasclaw_auth_token', 'token-b');
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'new-skill',
                        kind: 'skill',
                        command: '/new-skill',
                        label: 'new-skill',
                        skill_name: 'new-skill',
                        qualified_skill_name: 'new-skill',
                        target_skill_names: ['new-skill'],
                        target_tool_names: ['new_skill']
                    }
                ]
            })
        });

        setEditableText(input, '/new');
        await new Promise(r => setTimeout(r, 80));

        const popupText = document.querySelector('.slash-picker-popup')?.textContent || '';
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(popupText).toContain('/new-skill');
        expect(popupText).not.toContain('/old-skill');
    });

    test('slash picker bypasses shared cache when no AtlasClaw auth token is present', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();

        global.fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'old-skill',
                        kind: 'skill',
                        command: '/old-skill',
                        label: 'old-skill',
                        skill_name: 'old-skill',
                        qualified_skill_name: 'old-skill',
                        target_skill_names: ['old-skill'],
                        target_tool_names: ['old_skill']
                    }
                ]
            })
        });

        setupSlashCapabilityPicker(element);
        setEditableText(input, '/old');
        await new Promise(r => setTimeout(r, 80));
        expect(document.querySelector('.slash-picker-row')?.textContent).toContain('/old-skill');

        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'new-skill',
                        kind: 'skill',
                        command: '/new-skill',
                        label: 'new-skill',
                        skill_name: 'new-skill',
                        qualified_skill_name: 'new-skill',
                        target_skill_names: ['new-skill'],
                        target_tool_names: ['new_skill']
                    }
                ]
            })
        });

        setEditableText(input, '/new');
        await new Promise(r => setTimeout(r, 80));

        const popupText = document.querySelector('.slash-picker-popup')?.textContent || '';
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(popupText).toContain('/new-skill');
        expect(popupText).not.toContain('/old-skill');
    });

    test('slash picker restores first active row after navigating an empty result set', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();

        sessionStorage.setItem('atlasclaw_auth_token', 'token-a');
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'new-skill',
                        kind: 'skill',
                        command: '/new-skill',
                        label: 'new-skill',
                        skill_name: 'new-skill',
                        qualified_skill_name: 'new-skill',
                        target_skill_names: ['new-skill'],
                        target_tool_names: ['new_skill']
                    }
                ]
            })
        });

        setupSlashCapabilityPicker(element);
        setEditableText(input, '/zzz');
        await new Promise(r => setTimeout(r, 80));
        expect(document.querySelector('.slash-picker-empty')?.textContent).toBe('No matching skills');

        input.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'ArrowDown',
            bubbles: true,
            cancelable: true
        }));
        setEditableText(input, '/new');
        await new Promise(r => setTimeout(r, 80));

        input.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true
        }));

        expect(input.textContent).toContain('/new-skill');
    });

    test('slash picker scrolls active row into view during keyboard navigation', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();
        const originalClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
        const originalOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
        const originalOffsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetTop');

        Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
            configurable: true,
            get() {
                return this.classList?.contains('slash-picker-popup') ? 60 : 0;
            }
        });
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
            configurable: true,
            get() {
                return this.classList?.contains('slash-picker-row') ? 24 : 0;
            }
        });
        Object.defineProperty(HTMLElement.prototype, 'offsetTop', {
            configurable: true,
            get() {
                if (!this.classList?.contains('slash-picker-row') || !this.parentElement) return 0;
                return Array.from(this.parentElement.querySelectorAll('.slash-picker-row')).indexOf(this) * 24;
            }
        });

        try {
            sessionStorage.setItem('atlasclaw_auth_token', 'token-scroll');
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    capabilities: Array.from({ length: 12 }, (_, index) => ({
                        id: `skill-${index}`,
                        kind: 'skill',
                        command: `/skill-${String(index).padStart(2, '0')}`,
                        label: `skill-${index}`,
                        skill_name: `skill-${index}`,
                        qualified_skill_name: `skill-${index}`,
                        target_skill_names: [`skill-${index}`],
                        target_tool_names: [`skill_${index}`]
                    }))
                })
            });

            setupSlashCapabilityPicker(element);
            setEditableText(input, '/');
            await new Promise(r => setTimeout(r, 80));

            const popup = document.querySelector('.slash-picker-popup');
            expect(popup.scrollTop).toBe(0);

            for (let i = 0; i < 5; i += 1) {
                input.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'ArrowDown',
                    bubbles: true,
                    cancelable: true
                }));
            }

            expect(document.querySelector('.slash-picker-row.active')?.textContent).toContain('/skill-05');
            expect(popup.scrollTop).toBeGreaterThan(0);
        } finally {
            if (originalClientHeight) {
                Object.defineProperty(HTMLElement.prototype, 'clientHeight', originalClientHeight);
            } else {
                delete HTMLElement.prototype.clientHeight;
            }
            if (originalOffsetHeight) {
                Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalOffsetHeight);
            } else {
                delete HTMLElement.prototype.offsetHeight;
            }
            if (originalOffsetTop) {
                Object.defineProperty(HTMLElement.prototype, 'offsetTop', originalOffsetTop);
            } else {
                delete HTMLElement.prototype.offsetTop;
            }
        }
    });

    test('slash picker renders every matching capability in the scroll list', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();

        sessionStorage.setItem('atlasclaw_auth_token', 'token-all-matches');
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: Array.from({ length: 12 }, (_, index) => ({
                    id: `skill-${index}`,
                    kind: 'skill',
                    command: `/skill-${String(index).padStart(2, '0')}`,
                    label: `skill-${index}`,
                    skill_name: `skill-${index}`,
                    qualified_skill_name: `skill-${index}`,
                    target_skill_names: [`skill-${index}`],
                    target_tool_names: [`skill_${index}`]
                }))
            })
        });

        setupSlashCapabilityPicker(element);
        setEditableText(input, '/');
        await new Promise(r => setTimeout(r, 80));

        const rows = Array.from(document.querySelectorAll('.slash-picker-row'));
        expect(rows).toHaveLength(12);
        expect(rows.at(-1)?.textContent).toContain('/skill-11');
    });

    test('slash picker opens for slash text entered before listener attach', async () => {
        const { setupSlashCapabilityPicker } = await import('../../app/frontend/scripts/slash-picker.js');
        const { element, input } = createDomChatElement();

        sessionStorage.setItem('atlasclaw_auth_token', 'token-pre-attach-slash');
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                capabilities: [
                    {
                        id: 'pre-attach-skill',
                        kind: 'skill',
                        command: '/pre-attach-skill',
                        label: 'pre-attach-skill',
                        skill_name: 'pre-attach-skill',
                        qualified_skill_name: 'pre-attach-skill',
                        target_skill_names: ['pre-attach-skill'],
                        target_tool_names: ['pre_attach_skill']
                    }
                ]
            })
        });

        setEditableText(input, '/');
        expect(input._slashPickerAttached).toBeUndefined();

        setupSlashCapabilityPicker(element);
        await new Promise(r => setTimeout(r, 80));

        const popup = document.querySelector('.slash-picker-popup');
        expect(popup.hidden).toBe(false);
        expect(document.querySelector('.slash-picker-row')?.textContent).toContain('/pre-attach-skill');
    });

    test('handler uses signals.onResponse with overwrite for stream updates', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();
        
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-456' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'test message', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        
        // Simulate streaming delta
        stream.simulateEvent('assistant', { text: 'Hello', is_delta: true });

        // Wait for 100ms throttle timer to complete
        await new Promise(r => setTimeout(r, 150));

        // Verify onResponse called with html (not text, since we use html mode for streaming)
        expect(signals.onResponse).toHaveBeenCalledWith(
            expect.objectContaining({ html: expect.stringContaining('Hello'), overwrite: true })
        );

        // Simulate stream end
        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;

        expect(signals.onClose).toHaveBeenCalled();
    });

    test('handler renders assistant markdown safely', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-markdown' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'markdown please', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: '## 标题\n- **加粗项**\n- [链接](https://example.com)',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('<h2>标题</h2>');
        expect(htmlPayload).toContain('<strong>加粗项</strong>');
        expect(htmlPayload).toContain('<a href="https://example.com"');
        expect(htmlPayload).not.toContain('**加粗项**');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders workspace markdown links as download controls', async () => {
        const htmlPayload = await renderAssistantHtml(
            'Download [conversation record](workspace://conversation_2026-04-28.txt)',
            'run-workspace-markdown-link'
        );

        expect(htmlPayload).toContain('class="workspace-download-link"');
        expect(htmlPayload).toContain('download');
        expect(htmlPayload).toContain('/api/workspace/files/download?path=conversation_2026-04-28.txt');
        expect(htmlPayload).toContain('<span class="workspace-download-text">conversation_2026-04-28.txt</span>');
        expect(htmlPayload).not.toContain('conversation record');
        expect(htmlPayload).not.toContain('target="_blank"');
    });

    test('handler renders bare workspace references as download controls', async () => {
        const htmlPayload = await renderAssistantHtml(
            'Download workspace://conversation_2026-04-28.txt',
            'run-bare-workspace-link'
        );

        expect(htmlPayload).toContain('class="workspace-download-link"');
        expect(htmlPayload).toContain('/api/workspace/files/download?path=conversation_2026-04-28.txt');
        expect(htmlPayload).toContain('<span class="workspace-download-text">conversation_2026-04-28.txt</span>');
        expect(htmlPayload).not.toContain('target="_blank"');
    });

    test('handler does not render hidden runtime workspace references as download controls', async () => {
        const htmlPayload = await renderAssistantHtml(
            'Download workspace://.AtlasClaw/skills/skill-pdf/tmp/debug.pdf',
            'run-hidden-runtime-workspace-link'
        );

        expect(htmlPayload).not.toContain('class="workspace-download-link"');
        expect(htmlPayload).not.toContain('/api/workspace/files/download');
    });

    test('handler renders runtime workspace artifacts as download controls', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-runtime-artifact-link' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'make pptx', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('assistant', {
            text: 'Done! I created empty.pptx.',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Generated file ready for download.',
            phase: 'workspace_downloads',
            workspace_downloads: [
                { path: 'empty.pptx' }
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('class="workspace-download-link"');
        expect(htmlPayload).toContain('/api/workspace/files/download?path=empty.pptx');
        expect(htmlPayload).toContain('<span class="workspace-download-text">empty.pptx</span>');
        expect(htmlPayload).not.toContain('workspace-download-text">.atlasclaw');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not duplicate generated artifact controls already shown in final text', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-runtime-artifact-dedupe' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'write file', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('assistant', {
            text: 'workspace://conversation.txt',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Generated file ready for download.',
            phase: 'workspace_downloads',
            workspace_downloads: [
                { path: 'conversation.txt' }
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        const downloadLinkCount = (htmlPayload.match(/workspace-download-link/g) || []).length;
        expect(downloadLinkCount).toBe(1);
        expect(htmlPayload).toContain('/api/workspace/files/download?path=conversation.txt');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders tool-end workspace artifact content as download controls', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-tool-artifact-link' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'make pptx', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('tool', {
            tool: 'pptx_create_deck',
            phase: 'end',
            content: JSON.stringify({
                workspace_downloads: [
                    { path: 'tool-result.pptx' }
                ]
            })
        });
        await new Promise(r => setTimeout(r, 80));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('class="workspace-download-link"');
        expect(htmlPayload).toContain('/api/workspace/files/download?path=tool-result.pptx');
        expect(htmlPayload).toContain('<span class="workspace-download-text">tool-result.pptx</span>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders live tool-end object_action content as open actions', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-tool-object-action');

        stream.simulateEvent('assistant', {
            text: 'Created approval request.',
            is_delta: true
        });
        stream.simulateEvent('tool', {
            tool: 'provider_create_request',
            phase: 'end',
            content: JSON.stringify({
                object_id: 'REQ-001',
                object_name: 'Approval REQ-001',
                object_actions: [
                    objectActionReference({
                        href: 'https://cmp.example.com/requests/REQ-001',
                        object_id: 'REQ-001',
                        object_name: 'Approval REQ-001'
                    })
                ]
            })
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const link = dom.querySelector('a.object-action-link');
        expect(link).not.toBeNull();
        expect(link.getAttribute('href')).toBe('https://cmp.example.com/requests/REQ-001');
        expect(link.getAttribute('target')).toBe('_blank');
        expect(link.getAttribute('rel')).toBe('noopener noreferrer');
        expect(link.textContent).toContain('Open');
        expect(link.textContent).not.toContain('Approval REQ-001');
        expect(link.getAttribute('aria-label')).toBe('Open Approval REQ-001');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler extracts object_action from tool-end internal metadata strings', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-tool-object-action-internal');

        stream.simulateEvent('assistant', {
            text: 'Found approval details.',
            is_delta: true
        });
        stream.simulateEvent('tool', {
            tool: 'provider_detail',
            phase: 'end',
            content: JSON.stringify({
                success: true,
                output: 'human detail',
                _internal: JSON.stringify({
                    object_id: 'REQ-002',
                    object_name: 'Approval REQ-002',
	                    object_actions: [
	                        objectActionReference({
	                            href: 'https://cmp.example.com/requests/REQ-002',
	                            object_id: 'REQ-002',
	                            object_name: 'Approval REQ-002'
	                        })
	                    ]
                })
            })
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const link = dom.querySelector('a.object-action-link');
        expect(link).not.toBeNull();
        expect(link.getAttribute('href')).toBe('https://cmp.example.com/requests/REQ-002');
        expect(link.getAttribute('aria-label')).toBe('Open Approval REQ-002');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders runtime metadata object_actions as open actions', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-runtime-object-actions');

        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned object references.',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/res-001',
                    object_id: 'res-001',
                    object_name: 'Runtime resource'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 120));

        const dom = parseHtml(latestHtml(signals));
        const link = dom.querySelector('.object-actions a.object-action-link');
        expect(link).not.toBeNull();
        expect(link.getAttribute('href')).toBe('https://cmp.example.com/resources/res-001');
        expect(link.textContent).toContain('Open');
        expect(link.textContent).not.toContain('Runtime resource');
        expect(link.getAttribute('aria-label')).toBe('Open Runtime resource');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler keeps single detail object_action in the rendered DOM when metadata arrives before answer', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();
        const signals = createDomSignals(messages);

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-runtime-detail-object-action' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'show Linux-test-mysqlds detail', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Object actions ready.',
            phase: 'object_actions',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/vm-46499/details',
                    object_id: 'vm-46499',
                    object_name: 'Linux-test-mysqlds'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 120));

        expect(messages.querySelector('.object-actions a.object-action-link')).not.toBeNull();

        stream.simulateEvent('assistant', {
            text: [
                'Linux-test-mysqlds',
                '- Status: started',
                '- Compute: 1 CPU / 1 GB',
                'Disks',
                '- Disk 1: 100 | CentOS 4/5 (64 位) | thin'
            ].join('\n'),
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const links = messages.querySelectorAll('.object-actions a.object-action-link');
        expect(links).toHaveLength(1);
        expect(links[0].getAttribute('href')).toBe('https://cmp.example.com/resources/vm-46499/details');
        expect(links[0].getAttribute('target')).toBe('_blank');
        expect(links[0].getAttribute('rel')).toBe('noopener noreferrer');
        expect(links[0].getAttribute('aria-label')).toBe('Open Linux-test-mysqlds');
        expect(messages.querySelectorAll('.response-table-action a.object-action-link')).toHaveLength(0);
        expect(messages.textContent).toContain('Disk 1: 100');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('initChat restores assistant object_action controls from persisted history metadata', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ welcome_message: 'Hello!' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    messages: [
                        { role: 'user', content: 'show request', timestamp: '2026-03-27T10:00:00' },
                        {
                            role: 'assistant',
                            content: 'Request created.',
                            timestamp: '2026-03-27T10:00:01',
	                            object_actions: [
	                                objectActionReference({
	                                    href: 'https://cmp.example.com/requests/REQ-100',
	                                    object_id: 'REQ-100',
	                                    object_name: 'Restored request'
	                                })
	                            ]
                        }
                    ]
                })
            });

        await initChat(element);

        const restoredAssistant = element.history[1];
        const dom = parseHtml(restoredAssistant.html);
        const link = dom.querySelector('a.object-action-link');
        expect(restoredAssistant.role).toBe('ai');
        expect(link).not.toBeNull();
        expect(link.getAttribute('href')).toBe('https://cmp.example.com/requests/REQ-100');
        expect(link.textContent).toContain('Open');
        expect(link.textContent).not.toContain('Restored request');
        expect(link.getAttribute('aria-label')).toBe('Open Restored request');
    });

    test('initChat binds restored object_action prompt buttons after history renders', async () => {
        sessionStorage.setItem('atlasclaw_session_key', 'session-123');

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();
        const deepChatContainer = document.createElement('div');
        deepChatContainer.id = 'container';
        element.shadowRoot.appendChild(deepChatContainer);
        element.loadHistory = jest.fn((history) => {
            messages.innerHTML = '';
            history.forEach((message) => {
                appendHistoryMessage(messages, message);
            });
        });

        global.fetch
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ welcome_message: 'Hello!' })
            })
            .mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    messages: [
                        {
                            role: 'assistant',
                            content: 'Request detail.',
                            timestamp: '2026-03-27T10:00:01',
                            object_actions: [
                                objectActionReference({
                                    actionId: 'analyze',
                                    actions: [
                                        {
                                            action_id: 'analyze',
                                            kind: 'agent_prompt',
                                            display_label: localizedText('Analyze'),
                                            agent_prompt: localizedText('Analyze REQ-100')
                                        }
                                    ],
                                    object_id: 'REQ-100',
                                    object_name: 'Restored request'
                                })
                            ]
                        }
                    ]
                })
            });

        await initChat(element);

        const restoredButton = messages.querySelector('button[data-object-action-payload]');
        expect(restoredButton).not.toBeNull();
        expect(messages._objectActionClickBound).toBe(true);

        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-restored-object-action' })
        });

        restoredButton.click();
        await new Promise(r => setTimeout(r, 50));

        expect(element.addMessage.mock.calls.some(([message]) => message?.role === 'user')).toBe(false);
        expect(global.fetch).toHaveBeenCalledWith(
            expect.stringMatching(/\/api\/agent\/run$/),
            expect.objectContaining({
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: expect.stringContaining('"message":"Analyze REQ-100"')
            })
        );
        const directStream = MockEventSource.instances.at(-1);
        directStream?.simulateEvent('lifecycle', { phase: 'end' });
        await new Promise(r => setTimeout(r, 0));
    });

    test('handler ignores unsafe object_action values', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-unsafe-object-actions');

        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned unsafe object references.',
            object_actions: [
                objectActionReference({ href: 'javascript:alert(1)', label: 'script' }),
                objectActionReference({ href: 'data:text/html,hello', label: 'data' }),
                objectActionReference({ href: 'file:///tmp/object', label: 'file' }),
                objectActionReference({ href: 'workspace://object/1', label: 'workspace' }),
                objectActionReference({ href: '/relative/object/1', label: 'relative' }),
                objectActionReference({ href: '   ', label: 'blank' })
            ]
        });
        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = latestHtml(signals);
        expect(htmlPayload).not.toContain('object-action-link');
        expect(htmlPayload).not.toContain('javascript:alert');
        expect(htmlPayload).not.toContain('data:text/html');
        expect(htmlPayload).not.toContain('file:///tmp/object');
        expect(htmlPayload).not.toContain('workspace://object/1');
        expect(htmlPayload).not.toContain('/relative/object/1');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler ignores reserved execute_tool object actions', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-execute-tool-object-actions');

        stream.simulateEvent('assistant', {
            text: 'Provider returned a reserved direct execution action.',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned reserved object actions.',
            object_actions: [
                objectActionReference({
                    object_id: 'vm-1',
                    object_name: 'vm-1',
                    actions: [
                        {
                            action_id: 'restart',
                            kind: 'execute_tool',
                            label: 'Restart',
                            executor: { tool_name: 'provider_restart_vm' }
                        }
                    ]
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(dom.querySelectorAll('.object-actions *')).toHaveLength(0);
        expect(dom.textContent).toContain('Provider returned a reserved direct execution action.');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler ignores ordinary url and href fields for object open actions', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-ignore-ordinary-links');

        stream.simulateEvent('assistant', {
            text: 'Provider returned ordinary URLs.',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider metadata has ordinary link fields.',
            url: 'https://cmp.example.com/url-field',
            href: 'https://cmp.example.com/href-field',
            link: 'https://cmp.example.com/link-field',
            source_url: 'https://cmp.example.com/source-field',
            api_url: 'https://cmp.example.com/api-field',
            doc_url: 'https://cmp.example.com/doc-field'
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = latestHtml(signals);
        expect(htmlPayload).not.toContain('object-action-link');
        expect(htmlPayload).not.toContain('https://cmp.example.com/url-field');
        expect(htmlPayload).not.toContain('https://cmp.example.com/href-field');
        expect(htmlPayload).toContain('Provider returned ordinary URLs.');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler appends table object actions by numeric index column', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-table-object-index');

        stream.simulateEvent('assistant', {
            text: [
                '| # | Name | Status |',
                '| --- | --- | --- |',
                '| 1 | Database cluster | Active |',
                '| 2 | Web frontend | Pending |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned table object links.',
            object_actions: [
                objectActionReference({
                    index: 2,
                    href: 'https://cmp.example.com/resources/web',
                    object_name: 'Web frontend'
                }),
                objectActionReference({
                    index: 1,
                    href: 'https://cmp.example.com/resources/db',
                    object_name: 'Database cluster'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const rows = dom.querySelectorAll('.response-table tbody tr');
        expect(dom.querySelector('.response-table-action-header')).not.toBeNull();
        expect(dom.querySelector('.response-table-action-header')?.textContent).toBe('Actions');
        expect(rows[0].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/resources/db');
        expect(rows[1].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/resources/web');
        expect(dom.querySelectorAll('.response-content > .object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler appends provider resource table object actions on the right', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-provider-resource-table-object-links');

        stream.simulateEvent('assistant', {
            text: [
                'Found 2 virtual machine(s):',
                '',
                '| # | Name | Status | OS |',
                '| --- | --- | --- | --- |',
                '| 1 | Linux-test-mysqlds | started | CentOS |',
                '| 2 | Linux-test-adwedsew | stopped | CentOS |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned resource object links.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    object_name: 'Linux-test-mysqlds',
                    href: 'https://cmp.example.com/#/main/virtual-machines/vm-1/details'
                }),
                objectActionReference({
                    index: 2,
                    object_name: 'Linux-test-adwedsew',
                    href: 'https://cmp.example.com/#/main/virtual-machines/vm-2/details'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const rows = dom.querySelectorAll('.response-table tbody tr');
        expect(dom.querySelector('.response-table-action-header')?.textContent).toBe('Actions');
        expect(rows[0].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/#/main/virtual-machines/vm-1/details');
        expect(rows[1].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/#/main/virtual-machines/vm-2/details');
        expect(dom.querySelectorAll('.response-content > .object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler appends provider all-resource table object actions on the right', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-provider-all-resource-table-object-links');

        stream.simulateEvent('assistant', {
            text: [
                'Found 2 resource(s):',
                '',
                '| # | Name | Status | Resource Type | Component Type |',
                '| --- | --- | --- | --- | --- |',
                '| 1 | Linux-test-mysqlds | started | cloudchef.nodes.Compute | iaas.machine.virtual_machine |',
                '| 2 | mysql-prod | running | cloudchef.nodes.Database | paas.database.mysql |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned resource object links.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    object_name: 'Linux-test-mysqlds',
                    href: 'https://cmp.example.com/#/main/cloud-resource/res-1/details'
                }),
                objectActionReference({
                    index: 2,
                    object_name: 'mysql-prod',
                    href: 'https://cmp.example.com/#/main/cloud-resource/res-2/details'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const rows = dom.querySelectorAll('.response-table tbody tr');
        expect(dom.querySelector('.response-table-action-header')?.textContent).toBe('Actions');
        expect(rows[0].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/#/main/cloud-resource/res-1/details');
        expect(rows[1].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/#/main/cloud-resource/res-2/details');
        expect(dom.querySelectorAll('.response-content > .object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler appends approval table object actions on the right by index', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-approval-table-object-links');

        stream.simulateEvent('assistant', {
            text: [
                'Pending approvals - total 2 (sorted by updated time desc)',
                '',
                '| # | Request ID | Name | Catalog | Applicant | Priority |',
                '| --- | --- | --- | --- | --- | --- |',
                '| 1 | RES20260427000004 | Linux-test-agent | Linux VM | Admin User | low |',
                '| 2 | RES20260426000003 | older urgent request | General Ticket | Admin User | high |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned approval object links.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    object_id: 'RES20260427000004',
                    object_name: 'Linux-test-agent',
                    href: 'https://cmp.example.com/#/main/new-application/pendingApproval/PROVISION_BP/approval-1?from=normal&fromPagePartUrl=SR_MY_APPROVAL',
                    actions: [
                        {
                            action_id: 'view_detail',
                            kind: 'agent_prompt',
                            display_label: localizedText('View details', { 'zh-CN': '查看详情' }),
                            agent_prompt: localizedText(
                                'Show approval details for RES20260427000004',
                                { 'zh-CN': '查看 RES20260427000004 的审批详情' }
                            )
                        },
                        {
                            action_id: 'open_detail',
                            kind: 'open_url',
                            display_label: localizedText('Open', { 'zh-CN': '打开' }),
                            href: 'https://cmp.example.com/#/main/new-application/pendingApproval/PROVISION_BP/approval-1?from=normal&fromPagePartUrl=SR_MY_APPROVAL'
                        }
                    ]
                }),
                objectActionReference({
                    index: 2,
                    object_id: 'RES20260426000003',
                    object_name: 'older urgent request',
                    href: 'https://cmp.example.com/#/main/service-request/my-approval'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const rows = dom.querySelectorAll('.response-table tbody tr');
        expect(dom.querySelector('.response-table-action-header')?.textContent).toBe('Actions');
        expect(rows[0].querySelector('.response-table-action button')?.textContent).toBe('View details');
        expect(rows[0].querySelector('.response-table-action a')?.getAttribute('href')).toContain('/#/main/new-application/pendingApproval/');
        expect(rows[1].querySelector('.response-table-action a')?.getAttribute('href')).toBe('https://cmp.example.com/#/main/service-request/my-approval');
        expect(dom.querySelectorAll('.response-content > .object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders approval detail actions and submits prompt actions from buttons', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input, messages } = createDomChatElementWithMessages();
        const deepChatContainer = document.createElement('div');
        deepChatContainer.id = 'container';
        element.shadowRoot.appendChild(deepChatContainer);
        const signals = createDomSignals(messages);

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-approval-detail-actions' })
        });

        const submitListener = jest.fn();
        input.addEventListener('keydown', submitListener);
        const handlerPromise = element.handler(
            { messages: [{ text: 'show approval detail', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned approval detail actions.',
            object_actions: [
                objectActionReference({
                    object_id: 'RES20260427000004',
                    object_name: 'Linux-test-agent',
                    actions: [
                        {
                            action_id: 'open_detail',
                            kind: 'open_url',
                            display_label: localizedText('Open', { 'zh-CN': '打开' }),
                            href: 'https://cmp.example.com/#/main/service-request/my-approval'
                        },
                        {
                            action_id: 'analyze',
                            kind: 'agent_prompt',
                            display_label: localizedText('Analyze', { 'zh-CN': '分析' }),
                            agent_prompt: localizedText(
                                'Analyze approval details for RES20260427000004',
                                { 'zh-CN': '分析 RES20260427000004 的审批详情' }
                            )
                        },
                        {
                            action_id: 'approve',
                            kind: 'agent_prompt',
                            display_label: localizedText('Approve', { 'zh-CN': '同意' }),
                            agent_prompt: localizedText(
                                'Approve RES20260427000004',
                                { 'zh-CN': '批准 RES20260427000004' }
                            ),
                            confirmation_message: localizedText(
                                'Confirm approving RES20260427000004?',
                                { 'zh-CN': '确认同意 RES20260427000004？' }
                            ),
                            requires_confirmation: true,
                            tone: 'success'
                        },
                        {
                            action_id: 'reject',
                            kind: 'agent_prompt',
                            display_label: localizedText('Reject', { 'zh-CN': '拒绝' }),
                            agent_prompt_template: localizedText(
                                'Reject RES20260427000004, reason: {{reason}}',
                                { 'zh-CN': '拒绝 RES20260427000004，原因：{{reason}}' }
                            ),
                            confirmation_message: localizedText(
                                'Confirm rejecting RES20260427000004?',
                                { 'zh-CN': '确认拒绝 RES20260427000004？' }
                            ),
                            requires_confirmation: true,
                            tone: 'danger',
                            inputs: [
                                {
                                    name: 'reason',
                                    display_label: localizedText('Rejection reason', { 'zh-CN': '拒绝原因' }),
                                    type: 'textarea',
                                    required: true
                                }
                            ]
                        }
                    ]
                })
            ]
        });
        stream.simulateEvent('assistant', {
            text: [
                'Linux-test-agent',
                '- Request ID: RES20260427000004',
                '- Status: Pending'
            ].join('\n'),
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const actionGroup = messages.querySelector('.response-content > .object-actions');
        expect(actionGroup).not.toBeNull();
        expect(Array.from(actionGroup.children).map((node) => node.textContent.trim())).toEqual([
            'Open',
            'Analyze',
            'Approve',
            'Reject'
        ]);
        expect(actionGroup.querySelector('a.object-action-link')?.getAttribute('href')).toBe(
            'https://cmp.example.com/#/main/service-request/my-approval'
        );

        const originalConfirm = window.confirm;
        const originalPrompt = window.prompt;
        const originalAlert = window.alert;
        window.confirm = jest.fn(() => true);
        window.prompt = jest.fn(() => '库存不足');
        window.alert = jest.fn();
        try {
            global.fetch.mockClear();
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-approval-approve-action' })
            });
            actionGroup.querySelector('button.tone-success').click();
            await new Promise(r => setTimeout(r, 60));
            expect(window.confirm).toHaveBeenCalledWith('Confirm approving RES20260427000004?');
            let runBody = latestAgentRunRequestBody();
            expect(runBody.message).toBe('Approve RES20260427000004');
            expect(runBody.context.visible_user_turn).toBe(false);
            expect(input.textContent).toBe('');
            expect(submitListener).not.toHaveBeenCalled();

            global.fetch.mockClear();
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-approval-reject-action' })
            });
            actionGroup.querySelector('button.tone-danger').click();
            await new Promise(r => setTimeout(r, 60));
            expect(window.prompt).toHaveBeenCalledWith('Rejection reason', '');
            expect(window.confirm).toHaveBeenLastCalledWith('Confirm rejecting RES20260427000004?');
            runBody = latestAgentRunRequestBody();
            expect(runBody.message).toBe('Reject RES20260427000004, reason: 库存不足');
            expect(runBody.context.visible_user_turn).toBe(false);
            expect(input.textContent).toBe('');
            expect(submitListener).not.toHaveBeenCalled();

            window.confirm = jest.fn(() => false);
            global.fetch.mockClear();
            actionGroup.querySelector('button.tone-success').click();
            await new Promise(r => setTimeout(r, 0));
            expect(window.confirm).toHaveBeenCalledWith('Confirm approving RES20260427000004?');
            expect(latestAgentRunRequestBody()).toBeNull();
            expect(submitListener).not.toHaveBeenCalled();

            window.prompt = jest.fn(() => null);
            global.fetch.mockClear();
            actionGroup.querySelector('button.tone-danger').click();
            await new Promise(r => setTimeout(r, 0));
            expect(window.prompt).toHaveBeenCalledWith('Rejection reason', '');
            expect(latestAgentRunRequestBody()).toBeNull();
            expect(submitListener).not.toHaveBeenCalled();

            window.prompt = jest.fn(() => '   ');
            global.fetch.mockClear();
            actionGroup.querySelector('button.tone-danger').click();
            await new Promise(r => setTimeout(r, 0));
            expect(window.alert).toHaveBeenCalledWith('This action requires input.');
            expect(latestAgentRunRequestBody()).toBeNull();
            expect(submitListener).not.toHaveBeenCalled();
        } finally {
            window.confirm = originalConfirm;
            window.prompt = originalPrompt;
            window.alert = originalAlert;
        }

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler resolves approval action prompts from the current locale', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input, messages } = createDomChatElementWithMessages();
        const deepChatContainer = document.createElement('div');
        deepChatContainer.id = 'container';
        element.shadowRoot.appendChild(deepChatContainer);
        const signals = createDomSignals(messages);

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-approval-detail-actions-en' })
        });

        const submitListener = jest.fn();
        input.addEventListener('keydown', submitListener);
        const handlerPromise = element.handler(
            { messages: [{ text: 'show approval detail', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned localized approval detail actions.',
            object_actions: [
                objectActionReference({
                    object_id: 'RES20260427000004',
                    object_name: 'Linux-test-agent',
                    actions: [
                        {
                            action_id: 'approve',
                            kind: 'agent_prompt',
                            display_label: localizedText('Approve', { 'zh-CN': '同意' }),
                            agent_prompt: localizedText(
                                'Approve RES20260427000004',
                                { 'zh-CN': '批准 RES20260427000004' }
                            ),
                            confirmation_message: localizedText(
                                'Confirm approving RES20260427000004?',
                                { 'zh-CN': '确认同意 RES20260427000004？' }
                            ),
                            requires_confirmation: true,
                            tone: 'success'
                        },
                        {
                            action_id: 'reject',
                            kind: 'agent_prompt',
                            display_label: localizedText('Reject', { 'zh-CN': '拒绝' }),
                            agent_prompt_template: localizedText(
                                'Reject RES20260427000004, reason: {{reason}}',
                                { 'zh-CN': '拒绝 RES20260427000004，原因：{{reason}}' }
                            ),
                            confirmation_message: localizedText(
                                'Confirm rejecting RES20260427000004?',
                                { 'zh-CN': '确认拒绝 RES20260427000004？' }
                            ),
                            requires_confirmation: true,
                            tone: 'danger',
                            inputs: [
                                {
                                    name: 'reason',
                                    display_label: localizedText('Rejection reason', { 'zh-CN': '拒绝原因' }),
                                    type: 'textarea',
                                    required: true
                                }
                            ]
                        }
                    ]
                })
            ]
        });
        stream.simulateEvent('assistant', {
            text: 'Approval detail for RES20260427000004',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const actionGroup = messages.querySelector('.response-content > .object-actions');
        expect(Array.from(actionGroup.children).map((node) => node.textContent.trim())).toEqual([
            'Approve',
            'Reject'
        ]);

        const originalConfirm = window.confirm;
        const originalPrompt = window.prompt;
        try {
            window.confirm = jest.fn(() => true);
            window.prompt = jest.fn(() => 'not needed');

            global.fetch.mockClear();
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-approval-approve-action-en' })
            });
            actionGroup.querySelector('button.tone-success').click();
            await new Promise(r => setTimeout(r, 60));
            expect(window.confirm).toHaveBeenCalledWith('Confirm approving RES20260427000004?');
            let runBody = latestAgentRunRequestBody();
            expect(runBody.message).toBe('Approve RES20260427000004');
            expect(runBody.context.visible_user_turn).toBe(false);
            expect(input.textContent).toBe('');
            expect(submitListener).not.toHaveBeenCalled();

            global.fetch.mockClear();
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-approval-reject-action-en' })
            });
            actionGroup.querySelector('button.tone-danger').click();
            await new Promise(r => setTimeout(r, 60));
            expect(window.prompt).toHaveBeenCalledWith('Rejection reason', '');
            expect(window.confirm).toHaveBeenLastCalledWith('Confirm rejecting RES20260427000004?');
            runBody = latestAgentRunRequestBody();
            expect(runBody.message).toBe('Reject RES20260427000004, reason: not needed');
            expect(runBody.context.visible_user_turn).toBe(false);
            expect(input.textContent).toBe('');
            expect(submitListener).not.toHaveBeenCalled();
        } finally {
            window.confirm = originalConfirm;
            window.prompt = originalPrompt;
        }

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('object action prompt fails closed when direct submit is unavailable', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, input, messages } = createDomChatElementWithMessages();
        const signals = createDomSignals(messages);

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-approval-action-without-direct-submit' })
        });

        const submitListener = jest.fn();
        input.addEventListener('keydown', submitListener);
        const handlerPromise = element.handler(
            { messages: [{ text: 'show approval detail', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances.at(-1);
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned approval detail action.',
            object_actions: [
                objectActionReference({
                    object_id: 'RES20260427000004',
                    object_name: 'Linux-test-agent',
                    actions: [
                        {
                            action_id: 'approve',
                            kind: 'agent_prompt',
                            display_label: localizedText('Approve'),
                            agent_prompt: localizedText('Approve RES20260427000004'),
                            confirmation_message: localizedText('Confirm approving RES20260427000004?'),
                            requires_confirmation: true,
                            tone: 'success'
                        }
                    ]
                })
            ]
        });
        stream.simulateEvent('assistant', {
            text: 'Approval detail for RES20260427000004',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const originalConfirm = window.confirm;
        const originalWarn = console.warn;
        try {
            window.confirm = jest.fn(() => true);
            console.warn = jest.fn();
            global.fetch.mockClear();

            messages.querySelector('.response-content > .object-actions button').click();
            await new Promise(r => setTimeout(r, 0));

            expect(window.confirm).toHaveBeenCalledWith('Confirm approving RES20260427000004?');
            expect(global.fetch).not.toHaveBeenCalled();
            expect(input.textContent).toBe('');
            expect(submitListener).not.toHaveBeenCalled();
            expect(console.warn).toHaveBeenCalledWith('[ChatUI] Failed to submit object action prompt');
        } finally {
            window.confirm = originalConfirm;
            console.warn = originalWarn;
        }

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('object action direct submit does not append the resolved prompt as a user message', async () => {
        globalThis.__atlasclawTestLocale = 'zh-CN';
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const { element, messages } = createDomChatElementWithMessages();
        const deepChatContainer = document.createElement('div');
        deepChatContainer.id = 'container';
        element.shadowRoot.appendChild(deepChatContainer);
        const signals = createDomSignals(messages);
        const onUserTurnStarted = jest.fn();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element, { onUserTurnStarted });
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-approval-list-actions' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: '查看我的审批', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const listStream = MockEventSource.instances.at(-1);
        listStream.simulateEvent('assistant', {
            text: [
                '| # | Request ID | Name |',
                '| --- | --- | --- |',
                '| 1 | RES20260518000001 | test |'
            ].join('\n'),
            is_delta: true
        });
        listStream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned approval object links.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    object_id: 'RES20260518000001',
                    object_name: 'test',
                    actions: [
                        {
                            action_id: 'view_detail',
                            kind: 'agent_prompt',
                            display_label: localizedText('查看详情', { 'en-US': 'View details' }),
                            agent_prompt: localizedText(
                                '查看 RES20260518000001 的审批详情',
                                { 'en-US': 'Show approval details for RES20260518000001' }
                            )
                        }
                    ]
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));
        listStream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;

        const viewDetailButton = messages.querySelector('.response-table-action button');
        expect(viewDetailButton).not.toBeNull();
        element.addMessage.mockClear();
        onUserTurnStarted.mockClear();
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-approval-detail-direct-action' })
        });

        viewDetailButton.click();
        await new Promise(r => setTimeout(r, 60));

        expect(global.fetch).toHaveBeenCalledWith(
            expect.stringMatching(/\/api\/agent\/run$/),
            expect.objectContaining({
                body: expect.stringContaining('Show approval details for RES20260518000001')
            })
        );
        const runRequest = global.fetch.mock.calls.find(([url]) => String(url).endsWith('/api/agent/run'));
        expect(JSON.parse(runRequest[1].body).context.visible_user_turn).toBe(false);
        expect(onUserTurnStarted).not.toHaveBeenCalled();
        expect(element.addMessage.mock.calls.some(([message]) => message?.role === 'user')).toBe(false);

        const detailStream = MockEventSource.instances.at(-1);
        expect(element.addMessage).toHaveBeenNthCalledWith(1, expect.objectContaining({
            role: 'ai',
            overwrite: false
        }));
        detailStream.simulateEvent('assistant', {
            text: 'CMP Request Detail: RES20260518000001',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        expect(element.addMessage).toHaveBeenCalledWith(expect.objectContaining({
            role: 'ai',
            html: expect.stringContaining('CMP Request Detail: RES20260518000001'),
            overwrite: true
        }));
        detailStream.simulateEvent('lifecycle', { phase: 'end' });
        await new Promise(r => setTimeout(r, 0));
    });

    test('handler keeps single-row detail table actions at the bottom', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-detail-table-bottom-actions');

        stream.simulateEvent('assistant', {
            text: [
                '| Request ID | Name | Status |',
                '| --- | --- | --- |',
                '| RES20260427000004 | Linux-test-agent | Pending |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            phase: 'object_actions',
            message: 'Provider returned single approval detail actions.',
            object_actions: [
                objectActionReference({
                    object_id: 'RES20260427000004',
                    object_name: 'Linux-test-agent',
                    actions: [
                        {
                            action_id: 'open_detail',
                            kind: 'open_url',
                            display_label: localizedText('Open', { 'zh-CN': '打开' }),
                            href: 'https://cmp.example.com/#/main/new-application/pendingApproval/PROVISION_BP/approval-1'
                        },
                        {
                            action_id: 'analyze',
                            kind: 'agent_prompt',
                            display_label: localizedText('Analyze', { 'zh-CN': '分析' }),
                            agent_prompt: localizedText(
                                'Analyze approval details for RES20260427000004',
                                { 'zh-CN': '分析 RES20260427000004 的审批详情' }
                            )
                        }
                    ]
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(dom.querySelectorAll('.response-table-action')).toHaveLength(0);
        const bottomActions = dom.querySelectorAll('.response-content > .object-actions > *');
        expect(Array.from(bottomActions).map((node) => node.textContent.trim())).toEqual([
            'Open',
            'Analyze'
        ]);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not attach object actions by row order on a single unrelated table', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-single-table-no-row-order-object-links');

        stream.simulateEvent('assistant', {
            text: [
                '| Metric | Value |',
                '| --- | --- |',
                '| Count | 2 |',
                '| Status | Active |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned unmatched object links.',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/db',
                    object_name: 'Database cluster'
                }),
                objectActionReference({
                    href: 'https://cmp.example.com/resources/web',
                    object_name: 'Web frontend'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(dom.querySelectorAll('.response-table-action a')).toHaveLength(0);
        expect(dom.querySelectorAll('.object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not use row-order fallback on unrelated tables before the object table', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-multiple-table-object-links');

        stream.simulateEvent('assistant', {
            text: [
                '| Metric | Value |',
                '| --- | --- |',
                '| Count | 2 |',
                '| Status | Active |',
                '',
                '| Object ID | Object Name |',
                '| --- | --- |',
                '| db-1 | Database cluster |',
                '| web-1 | Web frontend |'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned table object links.',
            object_actions: [
                objectActionReference({
                    object_id: 'db-1',
                    href: 'https://cmp.example.com/resources/db',
                    object_name: 'Database cluster'
                }),
                objectActionReference({
                    object_id: 'web-1',
                    href: 'https://cmp.example.com/resources/web',
                    object_name: 'Web frontend'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const tables = dom.querySelectorAll('.response-table');
        expect(tables).toHaveLength(2);
        expect(tables[0].querySelectorAll('.response-table-action a')).toHaveLength(0);
        expect(tables[1].querySelectorAll('.response-table-action a')).toHaveLength(2);
        expect(tables[1].querySelectorAll('.response-table-action a')[0].getAttribute('href')).toBe('https://cmp.example.com/resources/db');
        expect(tables[1].querySelectorAll('.response-table-action a')[1].getAttribute('href')).toBe('https://cmp.example.com/resources/web');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler hides raw object_actions markdown table columns', async () => {
        const objectAction = 'https://cmp.example.com/approvals/APP-001';
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-table-hide-object-action');

        stream.simulateEvent('assistant', {
            text: [
                '| # | Name | object_actions | Status |',
                '| --- | --- | --- | --- |',
                `| 1 | Approval A | ${objectAction} | Pending |`
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned hidden column link.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    href: objectAction,
                    object_name: 'Approval A'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(Array.from(dom.querySelectorAll('.response-table th')).map((cell) => cell.textContent)).toEqual([
            '#',
            'Name',
            'Status',
            'Actions'
        ]);
        expect(dom.textContent).not.toContain(objectAction);
        expect(dom.querySelector('.response-table-action a')?.getAttribute('href')).toBe(objectAction);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler keeps raw object_actions markdown visible without sidecar metadata', async () => {
        const tableHref = 'https://cmp.example.com/approvals/APP-raw-table';
        const detailHref = 'https://cmp.example.com/approvals/APP-raw-detail';
        const html = await renderAssistantHtml([
            '| # | Name | object_actions | Status |',
            '| --- | --- | --- | --- |',
            `| 1 | Approval A | ${tableHref} | Pending |`,
            '',
            `object_actions: ${detailHref}`
        ].join('\n'), 'run-raw-object-actions-without-metadata');

        const dom = parseHtml(html);
        expect(Array.from(dom.querySelectorAll('.response-table th')).map((cell) => cell.textContent)).toEqual([
            '#',
            'Name',
            'object_actions',
            'Status'
        ]);
        expect(dom.querySelectorAll('.response-table-action')).toHaveLength(0);
        expect(dom.querySelectorAll('.object-actions')).toHaveLength(0);
        expect(dom.textContent).toContain(tableHref);
        expect(dom.textContent).toContain('object_actions');
        expect(dom.textContent).toContain(detailHref);
    });

    test('handler renders a single detail object action as a compact bottom action group', async () => {
        const objectAction = 'https://cmp.example.com/approvals/APP-002';
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-detail-single-object-action');

        stream.simulateEvent('assistant', {
            text: [
                'Approval detail',
                `object_actions: ${objectAction}`,
                'Status: Pending'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned single detail link.',
            object_actions: [
                objectActionReference({
                    href: objectAction,
                    object_name: 'Approval detail'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const actions = dom.querySelectorAll('.object-actions a.object-action-link');
        expect(actions).toHaveLength(1);
        expect(actions[0].getAttribute('href')).toBe(objectAction);
        expect(actions[0].textContent).toContain('Open');
        expect(actions[0].textContent).not.toContain('Approval detail');
        expect(actions[0].getAttribute('aria-label')).toBe('Open Approval detail');
        expect(dom.textContent).not.toContain(objectAction);
        expect(dom.textContent).toContain('Status: Pending');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler suppresses multiple unmatched object_actions instead of putting them at the bottom', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-multiple-unmatched-object-actions');

        stream.simulateEvent('assistant', {
            text: 'Created two provider objects.',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned unmatched object links.',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/objects/one',
                    label: 'First object'
                }),
                objectActionReference({
                    href: 'https://cmp.example.com/objects/two',
                    label: 'Second object'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const actions = dom.querySelectorAll('.object-actions a.object-action-link');
        expect(actions).toHaveLength(0);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler replaces list object actions with the latest detail object action metadata', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-detail-replaces-list-object-actions');

        stream.simulateEvent('assistant', {
            text: [
                'Found 2 virtual machines:',
                '[1] vm-1 | status: started',
                '[2] vm-2 | status: stopped',
                '',
                'vm-1',
                '- Status: started'
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned list links.',
            phase: 'object_actions',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/vm-1',
                    index: 1,
                    object_name: 'vm-1'
                }),
                objectActionReference({
                    href: 'https://cmp.example.com/resources/vm-2',
                    index: 2,
                    object_name: 'vm-2'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));
        expect(parseHtml(latestHtml(signals)).querySelectorAll('.object-actions a.object-action-link')).toHaveLength(0);

        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned detail link.',
            phase: 'object_actions',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/vm-1',
                    object_name: 'vm-1'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        const actions = dom.querySelectorAll('.object-actions a.object-action-link');
        expect(actions).toHaveLength(1);
        expect(actions[0].getAttribute('href')).toBe('https://cmp.example.com/resources/vm-1');
        expect(actions[0].getAttribute('aria-label')).toBe('Open vm-1');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler clears object actions when replacement metadata is empty', async () => {
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-empty-object-action-replacement');

        stream.simulateEvent('assistant', {
            text: 'vm-1 detail',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned detail link.',
            phase: 'object_actions',
            object_actions: [
                objectActionReference({
                    href: 'https://cmp.example.com/resources/vm-1',
                    object_id: 'vm-1',
                    object_name: 'vm-1'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));
        expect(parseHtml(latestHtml(signals)).querySelectorAll('.object-actions a.object-action-link')).toHaveLength(1);

        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned no current object actions.',
            phase: 'object_actions',
            object_actions: []
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(dom.querySelectorAll('.object-actions a.object-action-link')).toHaveLength(0);
        expect(dom.textContent).toContain('vm-1 detail');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not show raw object_actions values as table or detail text', async () => {
        const tableHref = 'https://cmp.example.com/table/APP-003';
        const detailHref = 'https://cmp.example.com/detail/APP-004';
        const { signals, stream, handlerPromise } = await startStreamingAssistant('run-hide-raw-object-action-text');

        stream.simulateEvent('assistant', {
            text: [
                '| # | Name | object_actions |',
                '| --- | --- | --- |',
                `| 1 | Table approval | ${tableHref} |`,
                '',
                `object_actions: ${detailHref}`
            ].join('\n'),
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'artifact',
            message: 'Provider returned raw-value coverage links.',
            object_actions: [
                objectActionReference({
                    index: 1,
                    href: tableHref,
                    object_name: 'Table approval'
                }),
                objectActionReference({
                    href: detailHref,
                    label: 'Detail approval'
                })
            ]
        });
        await new Promise(r => setTimeout(r, 160));

        const dom = parseHtml(latestHtml(signals));
        expect(dom.textContent).not.toContain(tableHref);
        expect(dom.textContent).not.toContain(detailHref);
        expect(dom.querySelector(`a[href="${tableHref}"]`)).not.toBeNull();
        expect(dom.querySelector(`a[href="${detailHref}"]`)).not.toBeNull();

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not linkify unsafe local markdown links', async () => {
        const htmlPayload = await renderAssistantHtml(
            '[local](/etc/passwd)',
            'run-unsafe-local-link'
        );

        expect(htmlPayload).not.toContain('workspace-download-link');
        expect(htmlPayload).not.toContain('/api/workspace/files/download');
        expect(htmlPayload).toContain('<a href="#"');
    });

    test('handler preserves external links as normal links', async () => {
        const htmlPayload = await renderAssistantHtml(
            '[docs](https://example.com/path?q=1)',
            'run-external-link'
        );

        expect(htmlPayload).toContain('<a href="https://example.com/path?q=1"');
        expect(htmlPayload).toContain('target="_blank"');
        expect(htmlPayload).not.toContain('workspace-download-link');
    });

    test('handler keeps short low-density markdown tables compact', async () => {
        const htmlPayload = await renderAssistantHtml(
            [
                '准备好了！有 2 个业务组 可选，请回复业务组编号和资源名称：',
                '',
                '| # | 业务组 |',
                '| --- | --- |',
                '| 1 | 开发部 |',
                '| 2 | 测试部 |'
            ].join('\n'),
            'run-compact-choice-table'
        );
        const dom = parseHtml(htmlPayload);

        expect(dom.querySelector('.response-table-wrap-compact')).not.toBeNull();
        expect(dom.querySelector('.response-table-wrap-wide')).toBeNull();
        expect(dom.querySelector('.response-table')?.textContent).toContain('开发部');
        expect(dom.querySelector('.response-table')?.textContent).toContain('测试部');
    });

    test('handler keeps two-column tables wide when row content is dense', async () => {
        const htmlPayload = await renderAssistantHtml(
            [
                'Options with detailed descriptions:',
                '',
                '| # | Description |',
                '| --- | --- |',
                '| 1 | Production cluster with cross-region disaster recovery and strict approval policy |',
                '| 2 | Development cluster for short-lived experiments and shared integration tests |'
            ].join('\n'),
            'run-wide-dense-table'
        );
        const dom = parseHtml(htmlPayload);

        expect(dom.querySelector('.response-table-wrap-wide')).not.toBeNull();
        expect(dom.querySelector('.response-table-wrap-compact')).toBeNull();
        expect(dom.querySelector('.response-table')?.textContent).toContain('cross-region disaster recovery');
    });

    test('handler keeps request confirmation summaries compact before json previews', async () => {
        const htmlPayload = await renderAssistantHtml(
            [
                '| 服务目录 | Windows VM 2019 |',
                '| --- | --- |',
                '| 业务组 | 开发部 |',
                '| 资源名称 | mytest-vm-adskj |',
                '| 规格 | Tiny（1核1GB） |',
                '| 系统盘 | 50 GB |',
                '| 安全组 | 2 个默认安全组 |',
                '| 登录用户 | administrator（默认） |',
                '',
                'JSON 预览：',
                '',
                '```json',
                '{',
                '  "catalogName": "Windows VM 2019",',
                '  "businessGroupName": "开发部",',
                '  "name": "mytest-vm-adskj"',
                '}',
                '```'
            ].join('\n'),
            'run-compact-confirmation-table'
        );
        const dom = parseHtml(htmlPayload);

        expect(dom.querySelector('.response-table-wrap-compact')).not.toBeNull();
        expect(dom.querySelector('.response-table-wrap-wide')).toBeNull();
        expect(dom.querySelector('.response-table')?.textContent).toContain('Windows VM 2019');
        expect(dom.querySelector('pre code.language-json')?.textContent).toContain('"catalogName"');
    });

    test('handler renders assistant pipe tables as aligned tables', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-markdown-table' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'render inventory table', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: [
                'Inventory snapshot - 2 items',
                '',
                '| # | Item ID | Name | Updated At | Status |',
                '- --- | --- | --- | --- | --- |',
                '- 1 | ITEM-001 | Database cluster | 2026-04-27 22:58 | Active |',
                '- 2 | ITEM-002 | Web frontend | 2026-04-26 22:39 | Pending |',
                'Status summary: Active 1 | Pending 1'
            ].join('\n'),
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('response-table-wrap-wide');
        expect(htmlPayload).toContain('<table class="response-table">');
        expect(htmlPayload).toContain('<th>Item ID</th>');
        expect(htmlPayload).toContain('<td>ITEM-001</td>');
        expect(htmlPayload).toContain('<td>Database cluster</td>');
        expect(htmlPayload).toContain('<p>Status summary: Active 1 | Pending 1</p>');
        expect(htmlPayload).not.toContain('<li>1 | ITEM-001');
        expect(htmlPayload).not.toContain('<td>Status summary');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler renders fenced json preview as a code block during streaming', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-json-preview' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'show json preview', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: [
                'JSON 预览：',
                '',
                '```json',
                '{',
                '  "name": "test-linux-vm-01"',
                '}',
                '```',
                '',
                '请确认。'
            ].join('\n'),
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('<pre><code class="language-json">');
        expect(htmlPayload).toContain('&quot;name&quot;: &quot;test-linux-vm-01&quot;');
        expect(htmlPayload).toContain('</code></pre>');
        expect(htmlPayload).not.toContain('<p>```json');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler preserves thinking content and runtime states after final answer arrives', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-thinking' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'test message', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'I am checking options.' });
        stream.simulateEvent('runtime', { state: 'retrying', message: 'Retrying with stricter policy.' });
        stream.simulateEvent('assistant', { text: 'Use high-speed rail.', is_delta: true });

        await new Promise(r => setTimeout(r, 180));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('Thinking');
        expect(htmlPayload).toContain('I am checking options.');
        expect(htmlPayload).toContain('Retrying');
        expect(htmlPayload).toContain('Use high-speed rail.');
        expect(htmlPayload).toContain('<details');
        expect((htmlPayload.match(/runtime-chip reasoning/g) || []).length).toBe(1);
        expect(htmlPayload).toContain('<span class="runtime-title">Thinking</span><span class="thinking-dots thinking-title-dots">');
        expect(htmlPayload).not.toContain('class="runtime-state-icon done"');
        expect(htmlPayload).not.toContain('Answered');
        expect(htmlPayload).not.toContain('details class="runtime-panel" open');
        expect(htmlPayload).toMatch(/runtime-log-time">([0-9]+ms|[0-9.]+s)</);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler localizes runtime panel labels through i18n', async () => {
        globalThis.__atlasclawTestTranslations = {
            'chat.placeholder': '请输入您的问题...',
            'chat.copyMessage': '复制消息',
            'chat.runtimeThinking': '思考中',
            'chat.runtimeRetrying': '重试中',
            'chat.modelThinking': '模型思考'
        };

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({
                chat: {
                    placeholder: '请输入您的问题...',
                    copyMessage: '复制消息',
                    runtimeThinking: '思考中',
                    runtimeRetrying: '重试中',
                    modelThinking: '模型思考'
                }
            })
        });
        const i18n = await import('../../app/frontend/scripts/i18n.js');
        if (typeof i18n.loadLocale === 'function') {
            await i18n.loadLocale('zh-CN');
        }

        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-zh-runtime' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: '我有多少审批', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'Checking approvals.' });
        stream.simulateEvent('runtime', { state: 'retrying', message: 'Retrying with stricter policy.' });

        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('<span class="runtime-title">思考中</span>');
        expect(htmlPayload).toContain('思考中');
        expect(htmlPayload).toContain('重试中');
        expect(htmlPayload).toContain('模型思考');
        expect(htmlPayload).not.toContain('<span class="runtime-title">Thinking</span>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler keeps title in thinking state until answered event arrives', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-waiting-answer' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'keep waiting', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'Still reasoning.' });
        stream.simulateEvent('thinking', { phase: 'end', elapsed: 1.2 });

        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('<span class="runtime-title">Thinking</span><span class="thinking-dots thinking-title-dots">');
        expect(htmlPayload).not.toContain('class="runtime-state-icon done"');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler preserves manual runtime panel expansion during thinking rerenders', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-thinking-panel-open' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'show thinking details', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'First thought.' });

            await jest.advanceTimersByTimeAsync(160);

            const panel = messages.querySelector('details.runtime-panel');
            expect(panel).not.toBeNull();
            expect(panel.open).toBe(false);

            const summary = panel.querySelector('summary');
            expect(summary).not.toBeNull();
            summary.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

            stream.simulateEvent('thinking', { phase: 'delta', content: ' Second thought.' });
            await jest.advanceTimersByTimeAsync(160);

            const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
            const rerenderedPanel = messages.querySelector('details.runtime-panel');
            expect(htmlPayload).toContain('details class="runtime-panel" open');
            expect(rerenderedPanel).not.toBeNull();
            expect(rerenderedPanel.open).toBe(true);

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler keeps manual runtime panel expansion when no new thinking delta arrives', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-thinking-panel-stable-open' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'keep panel open', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'First thought.' });

            await jest.advanceTimersByTimeAsync(160);

            const panel = messages.querySelector('details.runtime-panel');
            expect(panel).not.toBeNull();
            expect(panel.open).toBe(false);

            const summary = panel.querySelector('summary');
            expect(summary).not.toBeNull();
            summary.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

            await jest.advanceTimersByTimeAsync(250);

            const stablePanel = messages.querySelector('details.runtime-panel');
            expect(stablePanel).not.toBeNull();
            expect(stablePanel.open).toBe(true);

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler opens runtime panel from mousedown before click completes', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-thinking-panel-mousedown-open' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'open from mousedown', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'First thought.' });

            await jest.advanceTimersByTimeAsync(160);

            const panel = messages.querySelector('details.runtime-panel');
            expect(panel).not.toBeNull();
            expect(panel.open).toBe(false);

            const summary = panel.querySelector('summary');
            expect(summary).not.toBeNull();
            summary.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));

            await jest.advanceTimersByTimeAsync(250);

            const stablePanel = messages.querySelector('details.runtime-panel');
            expect(stablePanel).not.toBeNull();
            expect(stablePanel.open).toBe(true);

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler avoids overwriting an open runtime panel for elapsed-only timer ticks', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-thinking-panel-elapsed-only' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'open without flicker', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'First thought.' });
            stream.simulateEvent('runtime', {
                state: 'reasoning',
                message: 'Waiting for model tool decision.',
                elapsed: 0.1,
                phase: 'agent_first_node_wait'
            });

            await jest.advanceTimersByTimeAsync(160);

            const panel = messages.querySelector('details.runtime-panel');
            expect(panel).not.toBeNull();
            expect(panel.open).toBe(false);

            const summary = panel.querySelector('summary');
            expect(summary).not.toBeNull();
            summary.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));

            const responseCallsAfterOpen = signals.onResponse.mock.calls.length;
            const titleElapsedBefore = panel.querySelector('.runtime-title-elapsed')?.textContent || '';
            const activeLogElapsedBefore = panel.querySelector('.runtime-log-item.active .runtime-log-time')?.textContent || '';
            await jest.advanceTimersByTimeAsync(350);

            const stablePanel = messages.querySelector('details.runtime-panel');
            const titleElapsedAfter = stablePanel.querySelector('.runtime-title-elapsed')?.textContent || '';
            const activeLogElapsedAfter = stablePanel.querySelector('.runtime-log-item.active .runtime-log-time')?.textContent || '';
            expect(signals.onResponse).toHaveBeenCalledTimes(responseCallsAfterOpen);
            expect(stablePanel).not.toBeNull();
            expect(stablePanel.open).toBe(true);
            expect(parseRenderedElapsedSeconds(titleElapsedAfter)).toBeGreaterThan(parseRenderedElapsedSeconds(titleElapsedBefore));
            expect(parseRenderedElapsedSeconds(activeLogElapsedAfter)).toBeGreaterThan(parseRenderedElapsedSeconds(activeLogElapsedBefore));

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler renders pending thinking delta after runtime panel opens before debounce', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-thinking-panel-pending-delta' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'open during pending delta', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('runtime', {
                state: 'reasoning',
                message: 'Waiting for model tool decision.',
                elapsed: 0.1,
                phase: 'agent_first_node_wait'
            });
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'First thought.' });

            const panel = messages.querySelector('details.runtime-panel');
            expect(panel).not.toBeNull();
            expect(messages.innerHTML).not.toContain('First thought.');

            const summary = panel.querySelector('summary');
            expect(summary).not.toBeNull();
            summary.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));

            await jest.advanceTimersByTimeAsync(90);

            const stablePanel = messages.querySelector('details.runtime-panel');
            expect(messages.innerHTML).toContain('First thought.');
            expect(stablePanel).not.toBeNull();
            expect(stablePanel.open).toBe(true);

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler clears runtime timers when stream is aborted during session switch', async () => {
        jest.useFakeTimers();
        try {
            const { initChat, abortCurrentStream } = await import('../../app/frontend/scripts/chat-ui.js');
            const { element, messages } = createDomChatElementWithMessages();
            const signals = createDomSignals(messages);

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-aborted-timer-cleanup' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'old stream still thinking', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(100);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('thinking', { phase: 'start' });
            stream.simulateEvent('thinking', { phase: 'delta', content: 'Old thought.' });

            await jest.advanceTimersByTimeAsync(160);
            expect(messages.innerHTML).toContain('Old thought.');

            abortCurrentStream();
            await handlerPromise;

            signals.onResponse.mockClear();
            messages.innerHTML = `
                <details class="runtime-panel">
                    <summary>
                        <div class="runtime-summary-left">
                            <span class="runtime-title">Thinking</span>
                            <span class="runtime-state-icon done">✓</span>
                            <span class="runtime-title-elapsed">0.0s</span>
                        </div>
                    </summary>
                    <div class="runtime-body"></div>
                </details>
            `;

            await jest.advanceTimersByTimeAsync(1200);

            expect(signals.onResponse).not.toHaveBeenCalled();
            expect(messages.querySelector('.runtime-title-elapsed')?.textContent).toBe('0.0s');
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler does not reload session history immediately after stream end', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-no-history-reload' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'keep thinking visible', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'Checking grounded sources.' });
        stream.simulateEvent('assistant', { text: 'Here is the grounded answer.', is_delta: true });

        await new Promise(r => setTimeout(r, 160));

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;

        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(global.fetch).toHaveBeenCalledWith(
            expect.stringMatching(/\/api\/agent\/run$/),
            expect.any(Object)
        );
    });

    test('handler surfaces tool_running runtime state when tool execution starts', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-tool' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'search something', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'Planning tool calls.' });
        stream.simulateEvent('tool', { tool: 'web_search', phase: 'start' });

        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('Running tool');
        expect(htmlPayload).toContain('web_search');
        expect(htmlPayload).not.toContain('details class="runtime-panel" open');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler handles API error gracefully', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();
        
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        // Mock API error
        global.fetch.mockResolvedValueOnce({
            ok: false,
            status: 500,
            statusText: 'Internal Server Error'
        });

        await element.handler(
            { messages: [{ text: 'test', role: 'user' }] },
            signals
        );

        // Verify error response (uses html format)
        expect(signals.onResponse).toHaveBeenCalledWith(
            expect.objectContaining({ html: expect.stringContaining('Error: 500') })
        );
        expect(signals.onClose).toHaveBeenCalled();
    });

    test('handler extracts message from various body formats', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);

        // Test with messages array format
        global.fetch.mockClear();
        global.fetch.mockResolvedValueOnce({
            ok: false, status: 400, statusText: 'Bad Request'
        });
        
        await element.handler(
            { messages: [{ text: 'from messages array', role: 'user' }] },
            createMockSignals()
        );

        expect(global.fetch).toHaveBeenCalledWith(
            expect.any(String),
            expect.objectContaining({
                body: expect.stringContaining('from messages array')
            })
        );
    });

    test('handler does not manually append a second user message while stream is running', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-optimistic' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'show immediately', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 120));

        expect(element.addMessage).not.toHaveBeenCalled();

        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not append optimistic user message when deep-chat already rendered it', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-no-dup' })
        });

        element.getMessages.mockImplementation(() => ([
            { role: 'user', text: '你好' }
        ]));

        const handlerPromise = element.handler(
            { messages: [{ text: '你好', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 120));

        expect(element.addMessage).not.toHaveBeenCalled();

        MockEventSource.instances[0].simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler records per-step elapsed time from run start', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-timing' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'timed thinking', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 120));

        const stream = MockEventSource.instances[0];
        await new Promise(r => setTimeout(r, 260));
        stream.simulateEvent('thinking', { phase: 'start' });
        stream.simulateEvent('thinking', { phase: 'delta', content: 'Tracing elapsed runtime steps.' });
        await new Promise(r => setTimeout(r, 360));
        stream.simulateEvent('runtime', { state: 'waiting_for_tool', message: 'Waiting for tool selection.' });
        await new Promise(r => setTimeout(r, 520));
        stream.simulateEvent('assistant', { text: 'Done.', is_delta: true });

        await new Promise(r => setTimeout(r, 180));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        const matches = [...htmlPayload.matchAll(/runtime-log-time">([^<]+)</g)];
        const times = matches.map((match) => match[1]);
        expect(times.length).toBeGreaterThan(1);
        expect(times.some((value) => /[2-9]\d\dms|[1-9]\.\ds/.test(value))).toBe(true);

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler keeps runtime panel stable on heartbeat when only elapsed time changes', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-heartbeat-refresh' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'check external status', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('runtime', {
            state: 'reasoning',
            message: 'Waiting for model tool decision.',
            elapsed: 0.1
        });

        await new Promise(r => setTimeout(r, 50));
        const beforeHeartbeatCalls = signals.onResponse.mock.calls.length;

        stream.simulateEvent('heartbeat', { timestamp: '2026-04-12T17:35:00+08:00' });

        await new Promise(r => setTimeout(r, 50));
        const afterHeartbeatCalls = signals.onResponse.mock.calls.length;
        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';

        expect(afterHeartbeatCalls).toBe(beforeHeartbeatCalls);
        expect(htmlPayload).toContain('Waiting for model tool decision.');
        expect(htmlPayload).not.toContain('Model accepted the request and started reasoning.');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler prefers backend runtime elapsed when provided', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-server-elapsed' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'external status', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('runtime', {
            state: 'reasoning',
            message: 'Waiting for model tool decision.',
            elapsed: 12.3,
            phase: 'agent_first_node_wait'
        });
        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('12.3s');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler shows intermediate runtime phases before thinking text arrives', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-runtime-progress' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'external status', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('runtime', {
            state: 'reasoning',
            message: 'Preparing model request context.',
            elapsed: 0.1,
            phase: 'model_message_history_build'
        });
        stream.simulateEvent('runtime', {
            state: 'reasoning',
            message: 'Waiting for model tool decision.',
            elapsed: 5.2,
            phase: 'agent_first_node_wait'
        });
        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).toContain('Preparing model request context.');
        expect(htmlPayload).toContain('Waiting for model tool decision.');
        expect(htmlPayload).toContain('5.2s');
        expect(htmlPayload).not.toContain('Model thinking');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler does not synthesize answered state when stream ends without assistant content', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-no-answer' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: '明天上海天气', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));

        const stream = MockEventSource.instances[0];
        stream.simulateEvent('runtime', { state: 'controlled_path', message: 'Entering controlled path.' });
        await new Promise(r => setTimeout(r, 80));
        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('Answered');
        expect(htmlPayload).toContain('Failed');
        expect(htmlPayload).toContain('Run ended without a usable answer.');
    });

    test('handler strips wrapper answer heading and setext underline from final markdown', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-wrapper-heading' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'show wrapper heading issue', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: 'Answer\n=====\n- 第一项\n- 第二项',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('>Answer<');
        expect(htmlPayload).not.toContain('=====');
        expect(htmlPayload).toContain('<li>第一项</li>');
        expect(htmlPayload).toContain('<li>第二项</li>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler strips plain answer heading from final markdown', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-plain-answer-heading' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'plain answer heading', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: 'Answer\n\n- 第一项\n- 第二项',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('>Answer<');
        expect(htmlPayload).toContain('<li>第一项</li>');
        expect(htmlPayload).toContain('<li>第二项</li>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler hides answered runtime rows even if backend sends capitalized state', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-capitalized-answered' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'external status', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: '### 列表\n- 第一项',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'Answered',
            message: 'Final answer ready.',
            elapsed: 5.2
        });

        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('Answered');
        expect(htmlPayload).toContain('<h3>列表</h3>');
        expect(htmlPayload).toContain('<li>第一项</li>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler hides reasoning completed terminal row when final answer is present', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-hide-completed-row' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'external status', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: '### 列表\n- 第一项',
            is_delta: true
        });
        stream.simulateEvent('runtime', {
            state: 'reasoning',
            message: 'Reasoning phase completed.',
            elapsed: 5.0
        });
        stream.simulateEvent('runtime', {
            state: 'answered',
            message: 'Final answer ready.',
            elapsed: 5.1
        });

        await new Promise(r => setTimeout(r, 120));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('Reasoning phase completed.');
        expect(htmlPayload).not.toContain('Answered');
        expect(htmlPayload).toContain('<h3>列表</h3>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler normalizes ascii tool output with wrapper heading and pipe fields', async () => {
        const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
        const element = createChatElement();
        const signals = createMockSignals();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ session_key: 'session-123' })
        }).mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({})
        });

        await initChat(element);
        global.fetch.mockClear();

        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ run_id: 'run-ascii-tool-output' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'tool status list', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: '\uFEFFAnswer\n=====\nInventory report - 2 items (by status)\n==================\n+- [1] Active --------\n| Name: Build verification item\n| Ticket: ITEM-20260316000001\n|\n+- [2] Pending --------\n| Name: Expedited item\n| Ticket: ITEM-20260313000006',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('>Answer<');
        expect(htmlPayload).not.toContain('=====');
        expect(htmlPayload).toContain('<h1>Inventory report - 2 items (by status)</h1>');
        expect(htmlPayload).toContain('<li>Name: Build verification item</li>');
        expect(htmlPayload).toContain('<li>Ticket: ITEM-20260316000001</li>');
        expect(htmlPayload).toContain('<li>Name: Expedited item</li>');
        expect(htmlPayload).toContain('<li>Ticket: ITEM-20260313000006</li>');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
    });

    test('handler advances waiting-for-tool-decision progress locally without heartbeat', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const element = createChatElement();
            const signals = createMockSignals();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-local-wait-progress' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'external status', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(120);

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('runtime', {
                state: 'reasoning',
                message: 'Waiting for model tool decision.',
                elapsed: 0.1,
                phase: 'agent_first_node_wait'
            });

            await jest.advanceTimersByTimeAsync(5100);

            const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
            expect(htmlPayload).toContain('Still waiting for model tool decision.');

            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });

    test('handler seeds early runtime phases before backend runtime arrives', async () => {
        jest.useFakeTimers();
        try {
            const { initChat } = await import('../../app/frontend/scripts/chat-ui.js');
            const element = createChatElement();
            const signals = createMockSignals();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session-123' })
            }).mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({})
            });

            await initChat(element);
            global.fetch.mockClear();

            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-seeded-runtime-phases' })
            });

            const handlerPromise = element.handler(
                { messages: [{ text: 'external status', role: 'user' }] },
                signals
            );

            await jest.advanceTimersByTimeAsync(700);

            const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
            expect(htmlPayload).toContain('Preparing model request context.');
            expect(htmlPayload).toContain('Starting model session.');
            expect(htmlPayload).toContain('Waiting for model tool decision.');

            const stream = MockEventSource.instances[0];
            stream.simulateEvent('lifecycle', { phase: 'end' });
            await jest.advanceTimersByTimeAsync(300);
            await handlerPromise;
        } finally {
            jest.useRealTimers();
        }
    });
});
