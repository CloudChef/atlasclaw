/**
 * api-client.js module unit tests
 *
 * Spec coverage:
 *   - All REST fetch calls include `credentials: 'include'` (Cookie forwarding)
 *   - createSession no longer sends `user_id` in request body (identity from cookie)
 */

// Mock config module to return relative paths (matches actual behavior)
jest.mock('../../app/frontend/scripts/config.js', () => ({
    buildApiUrl: (path) => path.startsWith('/') ? path : `/${path}`
}));

// Mock fetch
global.fetch = jest.fn();

beforeEach(() => {
    global.fetch.mockClear();
});

describe('api-client.js', () => {
    // ── Auth: credentials ─────────────────────────────────────────────────────
    describe('credentials: include (Cookie forwarding)', () => {
        test('createSession sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'sk-1' })
            });
            const { createSession } = await import('../../app/frontend/scripts/api-client.js');
            await createSession();
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });

        test('getSession sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'sk-1', status: 'active' })
            });
            const { getSession } = await import('../../app/frontend/scripts/api-client.js');
            await getSession('sk-1');
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });

        test('resetSession sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });
            const { resetSession } = await import('../../app/frontend/scripts/api-client.js');
            await resetSession('sk-1');
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });

        test('startAgentRun sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'r-1', status: 'running' })
            });
            const { startAgentRun } = await import('../../app/frontend/scripts/api-client.js');
            await startAgentRun('sk-1', 'hello');
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });

        test('getAgentStatus sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'r-1', status: 'completed' })
            });
            const { getAgentStatus } = await import('../../app/frontend/scripts/api-client.js');
            await getAgentStatus('r-1');
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });

        test('abortAgentRun sends credentials: include', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });
            const { abortAgentRun } = await import('../../app/frontend/scripts/api-client.js');
            await abortAgentRun('r-1');
            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
        });
    });

    // ── createSession ─────────────────────────────────────────────────────────
    describe('createSession', () => {
        test('should create session with default params', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'test-session-123' })
            });

            const { createSession } = await import('../../app/frontend/scripts/api-client.js');
            const result = await createSession();

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/sessions',
                expect.objectContaining({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include'
                })
            );

            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.agent_id).toBe('main');
            expect(body.channel).toBe('web');
            expect(result.session_key).toBe('test-session-123');
        });

        test('should NOT include user_id in request body (identity comes from cookie)', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'custom-session' })
            });

            const { createSession } = await import('../../app/frontend/scripts/api-client.js');
            await createSession({ agentId: 'custom-agent', channel: 'telegram' });

            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.agent_id).toBe('custom-agent');
            expect(body.channel).toBe('telegram');
            expect(body.user_id).toBeUndefined();
        });

        test('should throw error on failed request', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: false,
                status: 500
            });

            const { createSession } = await import('../../app/frontend/scripts/api-client.js');
            await expect(createSession()).rejects.toThrow('Failed to create session: 500');
        });
    });

    // ── startAgentRun ─────────────────────────────────────────────────────────
    describe('startAgentRun', () => {
        test('should start agent run with session key and message', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-123', status: 'running' })
            });

            const { startAgentRun } = await import('../../app/frontend/scripts/api-client.js');
            const result = await startAgentRun('session-key', 'Hello');

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/agent/run',
                expect.objectContaining({
                    method: 'POST',
                    credentials: 'include'
                })
            );

            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.session_key).toBe('session-key');
            expect(body.message).toBe('Hello');
            expect(result.run_id).toBe('run-123');
        });

        test('should throw error on failed request', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: false,
                status: 400
            });

            const { startAgentRun } = await import('../../app/frontend/scripts/api-client.js');
            await expect(startAgentRun('key', 'msg')).rejects.toThrow('Failed to start agent run: 400');
        });
    });

    // ── getSession ────────────────────────────────────────────────────────────
    describe('getSession', () => {
        test('should get session by key', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ session_key: 'test', status: 'active' })
            });

            const { getSession } = await import('../../app/frontend/scripts/api-client.js');
            const result = await getSession('test-key');

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/sessions/test-key',
                expect.objectContaining({ credentials: 'include' })
            );
            expect(result.status).toBe('active');
        });
    });

    // ── resetSession ──────────────────────────────────────────────────────────
    describe('resetSession', () => {
        test('should reset session with archive', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });

            const { resetSession } = await import('../../app/frontend/scripts/api-client.js');
            await resetSession('session-key', true);

            expect(global.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
            const body = JSON.parse(global.fetch.mock.calls[0][1].body);
            expect(body.archive).toBe(true);
        });
    });

    // ── getAgentStatus ────────────────────────────────────────────────────────
    describe('getAgentStatus', () => {
        test('should get agent run status', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ run_id: 'run-123', status: 'completed' })
            });

            const { getAgentStatus } = await import('../../app/frontend/scripts/api-client.js');
            const result = await getAgentStatus('run-123');

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/agent/runs/run-123',
                expect.objectContaining({ credentials: 'include' })
            );
            expect(result.status).toBe('completed');
        });
    });

    // ── abortAgentRun ─────────────────────────────────────────────────────────
    describe('abortAgentRun', () => {
        test('should abort agent run', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ success: true })
            });

            const { abortAgentRun } = await import('../../app/frontend/scripts/api-client.js');
            await abortAgentRun('run-123');

            expect(global.fetch).toHaveBeenCalledWith(
                '/api/agent/runs/run-123/abort',
                expect.objectContaining({ method: 'POST', credentials: 'include' })
            );
        });
    });
});
