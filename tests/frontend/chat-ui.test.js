/*
 *  Copyright 2021  Qianyun, Inc. All rights reserved.
 */

/**
 * chat-ui.js regression tests
 * Tests for DeepChat handler mode implementation
 */

jest.mock('../../app/frontend/scripts/config.js', () => ({
    buildApiUrl: (path) => `http://127.0.0.1:8000${path}`
}));

jest.mock('../../app/frontend/scripts/i18n.js', () => ({
    t: jest.fn((key) => key),
    isLocaleLoaded: jest.fn(() => false)
}));

beforeEach(() => {
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

        expect(element.history).toEqual([
            { role: 'user', text: 'hello atlas' },
            { role: 'ai', text: 'hi there' }
        ]);
        expect(element.introMessage).toBeNull();
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
                        command: '/default.linux-vm-request',
                        label: 'default.linux-vm-request',
                        provider_type: 'smartcmp',
                        provider_display_name: 'SmartCMP',
                        instance_name: 'default',
                        skill_name: 'linux-vm-request',
                        qualified_skill_name: 'smartcmp:linux-vm-request',
                        target_provider_types: ['smartcmp'],
                        target_skill_names: ['smartcmp:linux-vm-request', 'linux-vm-request'],
                        target_tool_names: ['smartcmp_linux_vm_request']
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
            { messages: [{ text: `${input.textContent}申请 1C2G Linux`, role: 'user' }] },
            signals
        );
        await new Promise(r => setTimeout(r, 80));

        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody.message).toBe('申请 1C2G Linux');
        expect(parsedBody.context.selected_capability).toMatchObject({
            kind: 'provider_skill',
            provider_type: 'smartcmp',
            instance_name: 'default',
            qualified_skill_name: 'smartcmp:linux-vm-request',
            target_tool_names: ['smartcmp_linux_vm_request']
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
                        command: '/no-provider-vm-request',
                        label: 'no-provider-vm-request',
                        skill_name: 'no-provider-vm-request',
                        qualified_skill_name: 'no-provider-vm-request',
                        target_skill_names: ['no-provider-vm-request'],
                        target_tool_names: ['no_provider_vm_request']
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
            { messages: [{ text: `${input.textContent}申请 Linux VM`, role: 'user' }] },
            signals
        );
        await new Promise(r => setTimeout(r, 80));

        const [, requestOptions] = global.fetch.mock.calls[0];
        const parsedBody = JSON.parse(requestOptions.body);
        expect(parsedBody.message).toBe('申请 Linux VM');
        expect(parsedBody.context.selected_capability).toMatchObject({
            kind: 'skill',
            qualified_skill_name: 'no-provider-vm-request',
            target_tool_names: ['no_provider_vm_request']
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

    test('handler refreshes runtime panel on heartbeat while waiting for tool decision', async () => {
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
            { messages: [{ text: 'check pending approvals', role: 'user' }] },
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

        expect(afterHeartbeatCalls).toBeGreaterThan(beforeHeartbeatCalls);
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
            { messages: [{ text: 'cmp pending', role: 'user' }] },
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
            { messages: [{ text: 'cmp pending', role: 'user' }] },
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
            { messages: [{ text: 'cmp pending', role: 'user' }] },
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
            { messages: [{ text: 'cmp pending', role: 'user' }] },
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
            json: () => Promise.resolve({ run_id: 'run-ascii-pending-output' })
        });

        const handlerPromise = element.handler(
            { messages: [{ text: 'cmp pending', role: 'user' }] },
            signals
        );

        await new Promise(r => setTimeout(r, 100));
        const stream = MockEventSource.instances[0];
        stream.simulateEvent('assistant', {
            text: '\uFEFFAnswer\n=====\n待审批列表 - 共 2 项（按优先级排序）\n==================\n+- [1] 高 --------\n| 名称：Test ticket for build verification\n| 工单号: TIC20260316000001\n|\n+- [2] 高 --------\n| 名称: 加急加急\n| 工单号：TIC20260313000006',
            is_delta: true
        });
        await new Promise(r => setTimeout(r, 160));

        const htmlPayload = signals.onResponse.mock.calls.at(-1)?.[0]?.html || '';
        expect(htmlPayload).not.toContain('>Answer<');
        expect(htmlPayload).not.toContain('=====');
        expect(htmlPayload).toContain('<h1>待审批列表 - 共 2 项（按优先级排序）</h1>');
        expect(htmlPayload).toContain('<li>名称: Test ticket for build verification</li>');
        expect(htmlPayload).toContain('<li>工单号: TIC20260316000001</li>');
        expect(htmlPayload).toContain('<li>名称: 加急加急</li>');
        expect(htmlPayload).toContain('<li>工单号: TIC20260313000006</li>');

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
                { messages: [{ text: 'cmp pending', role: 'user' }] },
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
                { messages: [{ text: 'cmp pending', role: 'user' }] },
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
