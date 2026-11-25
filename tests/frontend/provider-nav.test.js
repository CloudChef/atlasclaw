/*
 *  Copyright 2026 Qianyun, Inc., All rights reserved.
 */

/**
 * provider-nav.js unit tests: discovery, rendering and active state.
 */

global.fetch = jest.fn();

beforeEach(() => {
    jest.resetModules();
    global.fetch.mockReset();
    document.body.innerHTML = '';
    delete window.__spaRouter;
});

describe('provider-nav.js', () => {
    test('renders provider entries and marks the active route', async () => {
        global.fetch.mockResolvedValue({
            ok: true,
            json: async () => ({
                items: [
                    { id: 'audit-report.file-audit', route: '/ext/audit-report/file-audit', title: '文件审核' },
                    { id: 'other.page', route: '/ext/other/page', title: 'Other' }
                ]
            })
        });
        const { setupProviderNav } = await import('../../app/frontend/scripts/components/provider-nav.js');
        const container = document.createElement('nav');

        await setupProviderNav(container, '/ext/audit-report/file-audit');

        const links = container.querySelectorAll('.provider-nav-link');
        expect(links).toHaveLength(2);
        expect(links[0].classList.contains('active')).toBe(true);
        expect(links[0].textContent).toContain('文件审核');
        expect(links[0].getAttribute('href')).toBe('/ext/audit-report/file-audit');
        expect(links[1].classList.contains('active')).toBe(false);
        expect(container.hidden).toBe(false);
    });

    test('hides the nav when no entries are returned', async () => {
        global.fetch.mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
        const { setupProviderNav } = await import('../../app/frontend/scripts/components/provider-nav.js');
        const container = document.createElement('nav');
        container.hidden = false;

        await setupProviderNav(container, '/');

        expect(container.hidden).toBe(true);
        expect(container.innerHTML).toBe('');
    });

    test('navigates through the SPA router on click', async () => {
        global.fetch.mockResolvedValue({
            ok: true,
            json: async () => ({ items: [{ route: '/ext/audit-report/file-audit', title: '文件审核' }] })
        });
        const navigate = jest.fn();
        window.__spaRouter = { navigate };
        const { setupProviderNav } = await import('../../app/frontend/scripts/components/provider-nav.js');
        const container = document.createElement('nav');
        await setupProviderNav(container, '/');

        container.querySelector('.provider-nav-link').click();

        expect(navigate).toHaveBeenCalledWith('/ext/audit-report/file-audit');
    });

    test('survives a failed discovery request', async () => {
        global.fetch.mockRejectedValue(new Error('offline'));
        const { setupProviderNav } = await import('../../app/frontend/scripts/components/provider-nav.js');
        const container = document.createElement('nav');

        await expect(setupProviderNav(container, '/')).resolves.toEqual([]);
        expect(container.hidden).toBe(true);
    });
});
