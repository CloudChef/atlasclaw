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

describe('chat-ui.js handler mode', () => {
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
        expect(htmlPayload).toContain('Runtime');
        expect(htmlPayload).toContain('I am checking options.');
        expect(htmlPayload).toContain('Retrying');
        expect(htmlPayload).toContain('Use high-speed rail.');
        expect(htmlPayload).toContain('<details');
        expect((htmlPayload.match(/runtime-chip reasoning/g) || []).length).toBe(1);
        expect(htmlPayload).toContain('<span class="runtime-title">Runtime</span><span class="runtime-state-icon done">✓</span>');
        expect(htmlPayload).toContain('class="runtime-state-icon done"');
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
        expect(htmlPayload).toContain('<span class="runtime-title">Runtime</span><span class="thinking-dots thinking-title-dots">');
        expect(htmlPayload).not.toContain('class="runtime-state-icon done"');

        stream.simulateEvent('lifecycle', { phase: 'end' });
        await handlerPromise;
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
});
