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
        textInput: null
    };
}

describe('chat-ui.js handler mode', () => {
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
    });

    test('initChat restores persisted session history for active session', async () => {
        sessionStorage.setItem('xuanwu_session_key', 'session-123');

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
                body: JSON.stringify({
                    session_key: 'session-123',
                    message: 'hello',
                    timeout_seconds: 600
                })
            })
        );

        // Wait for SSE to be created
        await new Promise(r => setTimeout(r, 100));

        // Verify SSE stream started
        expect(MockEventSource.instances).toHaveLength(1);
        expect(MockEventSource.instances[0].url).toMatch(/\/api\/agent\/runs\/run-123\/stream$/);

        // Verify loading dots were shown
        expect(signals.onResponse).toHaveBeenCalledWith(
            expect.objectContaining({ html: expect.stringContaining('thinking-loading') })
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
});
