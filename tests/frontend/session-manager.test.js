/**
 * session-manager.js 模块单元测试
 */

// Mock fetch globally
global.fetch = jest.fn();

// Mock config module
jest.mock('../../app/frontend/scripts/config.js', () => ({
    buildApiUrl: (path) => `http://127.0.0.1:8000${path}`,
    getConfig: () => ({ apiBaseUrl: 'http://127.0.0.1:8000' })
}));

// Mock sessionStorage
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

beforeEach(() => {
    jest.resetModules();
    sessionStorageMock.clear();
    sessionStorageMock.getItem.mockClear();
    sessionStorageMock.setItem.mockClear();
    sessionStorageMock.removeItem.mockClear();
    global.fetch.mockClear();
});

describe('session-manager.js', () => {
    describe('initSession', () => {
        test('should restore session from sessionStorage', async () => {
            sessionStorageMock.getItem.mockReturnValueOnce('stored-session-key');
            
            const { initSession } = await import('../../app/frontend/scripts/session-manager.js');
            const key = await initSession();
            
            expect(key).toBe('stored-session-key');
            expect(global.fetch).not.toHaveBeenCalled();
        });

        test('should create new session when none stored', async () => {
            sessionStorageMock.getItem.mockReturnValueOnce(null);
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'new-session-key' })
            });
            
            const { initSession } = await import('../../app/frontend/scripts/session-manager.js');
            const key = await initSession();
            
            expect(key).toBe('new-session-key');
            expect(global.fetch).toHaveBeenCalled();
            expect(sessionStorageMock.setItem).toHaveBeenCalledWith(
                'atlasclaw_session_key',
                'new-session-key'
            );
        });

        test('should pass params to createSession', async () => {
            sessionStorageMock.getItem.mockReturnValueOnce(null);
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session' })
            });
            
            const { initSession } = await import('../../app/frontend/scripts/session-manager.js');
            await initSession({ agentId: 'test-agent' });
            
            // buildApiUrl returns relative path when apiBaseUrl is empty or cross-origin
            expect(global.fetch).toHaveBeenCalledWith(
                '/api/sessions',
                expect.objectContaining({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
            );
        });
    });

    describe('getSessionKey', () => {
        test('should return current session key', async () => {
            sessionStorageMock.getItem.mockReturnValue('test-key');
            
            const { getSessionKey } = await import('../../app/frontend/scripts/session-manager.js');
            const key = getSessionKey();
            
            expect(key).toBe('test-key');
        });

        test('should return null when no session', async () => {
            sessionStorageMock.getItem.mockReturnValue(null);
            
            const { getSessionKey } = await import('../../app/frontend/scripts/session-manager.js');
            const key = getSessionKey();
            
            expect(key).toBeNull();
        });
    });

    describe('setSessionKey', () => {
        test('should save session key to storage', async () => {
            const { setSessionKey } = await import('../../app/frontend/scripts/session-manager.js');
            setSessionKey('new-key');
            
            expect(sessionStorageMock.setItem).toHaveBeenCalledWith(
                'atlasclaw_session_key',
                'new-key'
            );
        });

        test('should remove from storage when key is null', async () => {
            const { setSessionKey } = await import('../../app/frontend/scripts/session-manager.js');
            setSessionKey(null);
            
            expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('atlasclaw_session_key');
        });
    });

    describe('hasSession', () => {
        test('should return true when session exists', async () => {
            sessionStorageMock.getItem.mockReturnValue('session-key');
            
            const { hasSession } = await import('../../app/frontend/scripts/session-manager.js');
            expect(hasSession()).toBe(true);
        });

        test('should return false when no session', async () => {
            sessionStorageMock.getItem.mockReturnValue(null);
            
            const { hasSession } = await import('../../app/frontend/scripts/session-manager.js');
            expect(hasSession()).toBe(false);
        });
    });

    describe('startNewSession', () => {
        test('should reset old session and create new one', async () => {
            // First call returns existing key
            sessionStorageMock.getItem.mockReturnValueOnce('old-key');
            
            // Mock reset call
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });
            // Mock create call
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'new-key' })
            });
            
            const { initSession, startNewSession } = await import('../../app/frontend/scripts/session-manager.js');
            
            // First init
            await initSession();
            
            // Then start new session
            sessionStorageMock.getItem.mockReturnValueOnce(null);
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'brand-new-key' })
            });
            
            await startNewSession();
            
            expect(sessionStorageMock.removeItem).toHaveBeenCalled();
        });

        test('should handle reset failure gracefully', async () => {
            sessionStorageMock.getItem.mockReturnValueOnce('old-key');
            
            // Mock reset failure
            global.fetch.mockRejectedValueOnce(new Error('Reset failed'));
            // Mock create success
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'new-key' })
            });
            
            const { initSession, startNewSession } = await import('../../app/frontend/scripts/session-manager.js');
            await initSession();
            
            sessionStorageMock.getItem.mockReturnValueOnce(null);
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'another-new-key' })
            });
            
            // Should not throw
            await expect(startNewSession()).resolves.toBeDefined();
        });
    });

    describe('clearSession', () => {
        test('should clear session from storage', async () => {
            const { clearSession } = await import('../../app/frontend/scripts/session-manager.js');
            clearSession();
            
            expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('atlasclaw_session_key');
        });
    });
});
