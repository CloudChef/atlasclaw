/**
 * auth.js unit tests
 */

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
});

describe('auth.js', () => {
    test('getAuthToken should restore and migrate legacy auth storage', async () => {
        sessionStorageMock.getItem
            .mockReturnValueOnce(null)
            .mockReturnValueOnce('legacy-auth-token');

        const { getAuthToken } = await import('../../app/frontend/scripts/auth.js');
        const token = getAuthToken();

        expect(token).toBe('legacy-auth-token');
        expect(sessionStorageMock.setItem).toHaveBeenCalledWith(
            'xuanwu_auth_token',
            'legacy-auth-token'
        );
        expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('atlasclaw_auth_token');
    });
});
