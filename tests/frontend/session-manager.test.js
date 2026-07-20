/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

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
    localStorage.clear();
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

        test('should pass params to create thread session', async () => {
            sessionStorageMock.getItem.mockReturnValueOnce(null);
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'session' })
            });
            
            const { initSession } = await import('../../app/frontend/scripts/session-manager.js');
            await initSession({ agentId: 'test-agent' });
            
            // buildApiUrl returns relative path when apiBaseUrl is empty or cross-origin
            expect(global.fetch).toHaveBeenCalledWith(
                expect.stringMatching(/\/api\/sessions\/threads$/),
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
        test('should reuse current session when it is still empty', async () => {
            const {
                setSessionKey,
                setSessionHasMessages,
                startNewSession
            } = await import('../../app/frontend/scripts/session-manager.js');

            setSessionKey('empty-key');
            setSessionHasMessages(false);
            global.fetch.mockClear();

            const result = await startNewSession();

            expect(result).toBe('empty-key');
            expect(global.fetch).not.toHaveBeenCalled();
            expect(sessionStorageMock.removeItem).not.toHaveBeenCalledWith('atlasclaw_session_key');
        });

        test('should create a new thread and clear stored active key', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'brand-new-key' })
            });

            const {
                setSessionKey,
                setSessionHasMessages,
                startNewSession
            } = await import('../../app/frontend/scripts/session-manager.js');

            setSessionKey('old-key');
            setSessionHasMessages(true);

            const result = await startNewSession();

            expect(result).toBe('brand-new-key');
            expect(global.fetch).toHaveBeenCalled();
        });

        test('should still resolve when creating another thread after existing session', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'another-new-key' })
            });

            const {
                setSessionKey,
                setSessionHasMessages,
                startNewSession
            } = await import('../../app/frontend/scripts/session-manager.js');

            setSessionKey('old-key');
            setSessionHasMessages(true);

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

    describe('integration-scoped Chat Active Session', () => {
        const surface = {
            integrationMode: true,
            integrationId: 'tenant-assistant',
            surface: 'menu',
            hostOrigin: null,
            nonce: null
        };

        test('stores only a bootstrap-validated candidate in scoped localStorage', async () => {
            const storageKey = 'atlasclaw_active_session:v1:main:tenant-scope';
            localStorage.setItem(storageKey, 'candidate-chat-key');
            global.fetch
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: null
                    })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: 'candidate-chat-key'
                    })
                });

            const { initializeIntegrationChatSession, getSessionKey } = await import(
                '../../app/frontend/scripts/session-manager.js'
            );
            await initializeIntegrationChatSession(surface);

            expect(getSessionKey()).toBe('candidate-chat-key');
            expect(localStorage.getItem(storageKey)).toBe('candidate-chat-key');
            const validationBody = JSON.parse(global.fetch.mock.calls[1][1].body);
            expect(validationBody.candidate_session_key).toBe('candidate-chat-key');
            expect(localStorage.length).toBe(1);
        });

        test('creates scoped threads and always creates a new one on explicit new chat', async () => {
            global.fetch
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: null
                    })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({ session_key: 'first-chat-key' })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: 'first-chat-key'
                    })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({ session_key: 'second-chat-key' })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: 'second-chat-key'
                    })
                });

            const {
                initializeIntegrationChatSession,
                initSession,
                setSessionHasMessages,
                startNewSession
            } = await import('../../app/frontend/scripts/session-manager.js');
            await initializeIntegrationChatSession(surface);
            await initSession();
            setSessionHasMessages(false);

            await expect(startNewSession()).resolves.toBe('second-chat-key');
            const createBodies = global.fetch.mock.calls
                .filter(([url]) => String(url).endsWith('/api/sessions/threads'))
                .map(([, options]) => JSON.parse(options.body));
            expect(createBodies).toHaveLength(2);
            expect(createBodies[0]).toMatchObject({
                agent_id: 'main',
                account_id: 'tenant-scope',
                channel: 'web',
                chat_type: 'dm'
            });
        });

        test('rejects an unvalidated localStorage candidate instead of restoring it', async () => {
            const storageKey = 'atlasclaw_active_session:v1:main:tenant-scope';
            localStorage.setItem(storageKey, 'other-user-chat-key');
            global.fetch
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: null
                    })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: null
                    })
                });

            const { initializeIntegrationChatSession, getSessionKey } = await import(
                '../../app/frontend/scripts/session-manager.js'
            );
            await initializeIntegrationChatSession(surface);

            expect(getSessionKey()).toBeNull();
            expect(localStorage.getItem(storageKey)).toBeNull();
        });

        test('validates a storage-event candidate before switching cross-Surface Chat Active Session', async () => {
            const storageKey = 'atlasclaw_active_session:v1:main:tenant-scope';
            global.fetch
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: null
                    })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({ session_key: 'current-chat-key' })
                })
                .mockResolvedValueOnce({
                    ok: true,
                    json: () => Promise.resolve({
                        agent_id: 'main',
                        session_scope: 'tenant-scope',
                        active_session_key: 'current-chat-key'
                    })
                });
            const sessionManager = await import('../../app/frontend/scripts/session-manager.js');
            await sessionManager.initializeIntegrationChatSession(surface);
            await sessionManager.initSession();

            const changed = jest.fn();
            window.addEventListener('atlasclaw:active-chat-session-changed', changed, { once: true });
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({
                    agent_id: 'main',
                    session_scope: 'tenant-scope',
                    active_session_key: 'next-chat-key'
                })
            });
            localStorage.setItem(storageKey, 'next-chat-key');
            window.dispatchEvent(new StorageEvent('storage', {
                key: storageKey,
                oldValue: 'current-chat-key',
                newValue: 'next-chat-key',
                storageArea: localStorage
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));

            expect(sessionManager.getSessionKey()).toBe('next-chat-key');
            expect(changed).toHaveBeenCalledWith(expect.objectContaining({
                detail: expect.objectContaining({ sessionKey: 'next-chat-key' })
            }));
            const validation = JSON.parse(global.fetch.mock.calls.at(-1)[1].body);
            expect(validation.candidate_session_key).toBe('next-chat-key');
        });

    });
});
