/**
 * stream-handler.js module unit tests
 */

// Mock config module to return relative paths (matches actual behavior)
jest.mock('../../app/frontend/scripts/config.js', () => ({
    buildApiUrl: (path) => path.startsWith('/') ? path : `/${path}`
}));

// Mock EventSource
class MockEventSource {
    constructor(url, options = {}) {
        this.url = url;
        this.options = options;  // captures { withCredentials } etc.
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
    
    // Test helpers
    simulateOpen() {
        this.readyState = EventSource.OPEN;
        if (this.onopen) this.onopen();
    }
    
    simulateMessage(data) {
        if (this.onmessage) this.onmessage({ data });
    }
    
    simulateEvent(type, data) {
        const callbacks = this.listeners[type] || [];
        callbacks.forEach(cb => cb({ data: JSON.stringify(data) }));
    }
    
    simulateError(error) {
        if (this.onerror) this.onerror(error);
    }
}

MockEventSource.CONNECTING = 0;
MockEventSource.OPEN = 1;
MockEventSource.CLOSED = 2;
MockEventSource.instances = [];

global.EventSource = MockEventSource;

beforeEach(() => {
    MockEventSource.instances = [];
});

describe('stream-handler.js', () => {
    describe('EventTypes', () => {
        test('should export event type constants', async () => {
            const { EventTypes } = await import('../../app/frontend/scripts/stream-handler.js');
            
            expect(EventTypes.LIFECYCLE).toBe('lifecycle');
            expect(EventTypes.ASSISTANT).toBe('assistant');
            expect(EventTypes.TOOL).toBe('tool');
            expect(EventTypes.ERROR).toBe('error');
            expect(EventTypes.HEARTBEAT).toBe('heartbeat');
            expect(EventTypes.RUNTIME).toBe('runtime');
        });
    });

    describe('withCredentials: true (Cookie forwarding for SSE)', () => {
        test('EventSource is created with withCredentials: true', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');

            const handler = createStreamHandler('run-123', {});
            handler.start();

            expect(MockEventSource.instances.length).toBe(1);
            expect(MockEventSource.instances[0].options).toMatchObject({ withCredentials: true });
        });
    });

    describe('createStreamHandler', () => {
        test('should create handler with start and abort methods', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const handler = createStreamHandler('run-123', {});
            
            expect(handler.start).toBeDefined();
            expect(handler.abort).toBeDefined();
            expect(handler.isConnected).toBeDefined();
        });

        test('should connect to correct URL on start', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const handler = createStreamHandler('run-123', {});
            handler.start();
            
            expect(MockEventSource.instances.length).toBe(1);
            // URL is relative path since buildApiUrl returns relative paths
            expect(MockEventSource.instances[0].url).toBe('/api/agent/runs/run-123/stream');
        });

        test('should call onStart callback on lifecycle start event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onStart = jest.fn();
            const handler = createStreamHandler('run-123', { onStart });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('lifecycle', { phase: 'start' });
            
            expect(onStart).toHaveBeenCalledWith({ phase: 'start' });
        });

        test('should call onDelta callback on assistant event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onDelta = jest.fn();
            const handler = createStreamHandler('run-123', { onDelta });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('assistant', { text: 'Hello', is_delta: true });
            
            expect(onDelta).toHaveBeenCalledWith({ content: 'Hello', is_delta: true });
        });

        test('should call onToolStart callback on tool start event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onToolStart = jest.fn();
            const handler = createStreamHandler('run-123', { onToolStart });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('tool', { tool: 'search', phase: 'start' });
            
            expect(onToolStart).toHaveBeenCalledWith({ tool_name: 'search' });
        });

        test('should call onToolEnd callback on tool end event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onToolEnd = jest.fn();
            const handler = createStreamHandler('run-123', { onToolEnd });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('tool', { tool: 'search', phase: 'end', result: 'done' });
            
            expect(onToolEnd).toHaveBeenCalledWith({ tool_name: 'search', result: 'done' });
        });

        test('should call onRuntime callback on runtime event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');

            const onRuntime = jest.fn();
            const handler = createStreamHandler('run-123', { onRuntime });
            handler.start();

            const es = MockEventSource.instances[0];
            es.simulateEvent('runtime', { state: 'retrying', message: 'Retrying now', attempt: 1 });

            expect(onRuntime).toHaveBeenCalledWith({
                state: 'retrying',
                message: 'Retrying now',
                metadata: { state: 'retrying', message: 'Retrying now', attempt: 1 }
            });
        });

        test('should call onHeartbeat callback on heartbeat event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');

            const onHeartbeat = jest.fn();
            const handler = createStreamHandler('run-123', { onHeartbeat });
            handler.start();

            const es = MockEventSource.instances[0];
            es.simulateEvent('heartbeat', { timestamp: '2026-04-12T17:30:00+08:00' });

            expect(onHeartbeat).toHaveBeenCalledWith({ timestamp: '2026-04-12T17:30:00+08:00' });
        });

        test('should call onEnd callback and close on lifecycle end event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onEnd = jest.fn();
            const handler = createStreamHandler('run-123', { onEnd });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('lifecycle', { phase: 'end' });
            
            expect(onEnd).toHaveBeenCalledWith({ phase: 'end' });
            expect(es.readyState).toBe(EventSource.CLOSED);
        });

        test('should call onError callback on error event', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onError = jest.fn();
            const handler = createStreamHandler('run-123', { onError });
            handler.start();
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('error', { message: 'Something went wrong' });
            
            expect(onError).toHaveBeenCalledWith({ message: 'Something went wrong', code: undefined });
        });

        test('should close connection on abort', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const handler = createStreamHandler('run-123', {});
            handler.start();
            
            const es = MockEventSource.instances[0];
            handler.abort();
            
            expect(es.readyState).toBe(EventSource.CLOSED);
        });

        test('should not start if already aborted', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const handler = createStreamHandler('run-123', {});
            handler.abort();
            handler.start();
            
            expect(MockEventSource.instances.length).toBe(0);
        });

        test('should report connection status correctly', async () => {
            const { createStreamHandler } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const handler = createStreamHandler('run-123', {});
            expect(handler.isConnected()).toBe(false);
            
            handler.start();
            const es = MockEventSource.instances[0];
            es.readyState = EventSource.OPEN;
            
            expect(handler.isConnected()).toBe(true);
        });
    });

    describe('streamResponse', () => {
        test('should accumulate content and call onComplete', async () => {
            const { streamResponse } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const onChunk = jest.fn();
            const onComplete = jest.fn();
            
            streamResponse('run-123', onChunk, onComplete);
            
            const es = MockEventSource.instances[0];
            es.simulateEvent('assistant', { text: 'Hello ', is_delta: true });
            es.simulateEvent('assistant', { text: 'World', is_delta: true });
            es.simulateEvent('lifecycle', { phase: 'end' });
            
            expect(onChunk).toHaveBeenCalledTimes(2);
            expect(onChunk).toHaveBeenNthCalledWith(1, 'Hello ', 'Hello ');
            expect(onChunk).toHaveBeenNthCalledWith(2, 'World', 'Hello World');
            expect(onComplete).toHaveBeenCalledWith('Hello World');
        });

        test('should return abort function', async () => {
            const { streamResponse } = await import('../../app/frontend/scripts/stream-handler.js');
            
            const abort = streamResponse('run-123', jest.fn(), jest.fn());
            
            expect(typeof abort).toBe('function');
            
            abort();
            expect(MockEventSource.instances[0].readyState).toBe(EventSource.CLOSED);
        });
    });
});
