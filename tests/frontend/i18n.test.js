/**
 * i18n 模块单元测试
 */

// Mock localStorage
const localStorageMock = {
    store: {},
    getItem: jest.fn((key) => localStorageMock.store[key] || null),
    setItem: jest.fn((key, value) => { localStorageMock.store[key] = value; }),
    removeItem: jest.fn((key) => { delete localStorageMock.store[key]; }),
    clear: jest.fn(() => { localStorageMock.store = {}; })
};

// Mock navigator
Object.defineProperty(global, 'localStorage', { value: localStorageMock });
Object.defineProperty(global, 'navigator', { value: { language: 'zh-CN' }, writable: true });

// Mock fetch
global.fetch = jest.fn();

// Mock DOM
document.querySelectorAll = jest.fn(() => []);
document.querySelector = jest.fn(() => null);

describe('i18n Module', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.resetModules();
        localStorageMock.store = {};
        global.fetch.mockClear();
    });

    describe('detectBrowserLocale', () => {
        test('should return zh-CN for Chinese browser', async () => {
            global.navigator = { language: 'zh-CN' };
            const { detectBrowserLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(detectBrowserLocale()).toBe('zh-CN');
        });

        test('should return en-US for English browser', async () => {
            global.navigator = { language: 'en-US' };
            const { detectBrowserLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(detectBrowserLocale()).toBe('en-US');
        });

        test('should return zh-CN for zh prefix', async () => {
            global.navigator = { language: 'zh' };
            const { detectBrowserLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(detectBrowserLocale()).toBe('zh-CN');
        });

        test('should return en-US for en prefix', async () => {
            global.navigator = { language: 'en' };
            const { detectBrowserLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(detectBrowserLocale()).toBe('en-US');
        });

        test('should return default locale for unsupported language', async () => {
            global.navigator = { language: 'fr-FR' };
            const { detectBrowserLocale } = await import('../../app/frontend/scripts/i18n.js');
            // Default locale is en-US as defined in source
            expect(detectBrowserLocale()).toBe('en-US');
        });
    });

    describe('getSavedLocale / saveLocale', () => {
        test('should return null when no locale saved', async () => {
            const { getSavedLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(getSavedLocale()).toBeNull();
        });

        test('should restore and migrate legacy saved locale', async () => {
            localStorageMock.store['atlasclaw_locale'] = 'en-US';

            const { getSavedLocale } = await import('../../app/frontend/scripts/i18n.js');
            expect(getSavedLocale()).toBe('en-US');
            expect(localStorageMock.setItem).toHaveBeenCalledWith('xuanwu_locale', 'en-US');
            expect(localStorageMock.removeItem).toHaveBeenCalledWith('atlasclaw_locale');
        });

        test('should save locale to localStorage', async () => {
            const { saveLocale } = await import('../../app/frontend/scripts/i18n.js');
            saveLocale('en-US');
            expect(localStorageMock.setItem).toHaveBeenCalledWith('xuanwu_locale', 'en-US');
        });
    });

    describe('getSupportedLocales', () => {
        test('should return supported locales', async () => {
            const { getSupportedLocales } = await import('../../app/frontend/scripts/i18n.js');
            const locales = getSupportedLocales();
            expect(locales).toContain('zh-CN');
            expect(locales).toContain('en-US');
        });
    });

    describe('loadLocale', () => {
        test('should load locale file successfully', async () => {
            const mockTranslations = { app: { title: 'Test Title' } };
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve(mockTranslations)
            });

            const { loadLocale } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            expect(global.fetch).toHaveBeenCalledWith('/locales/zh-CN.json');
        });

        test('should fall back to default on unsupported locale', async () => {
            const mockTranslations = { app: { title: 'Test' } };
            
            global.fetch.mockResolvedValue({
                ok: true,
                json: () => Promise.resolve(mockTranslations)
            });

            const { loadLocale } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('invalid-locale');
            // Default locale is en-US as defined in source
            expect(global.fetch).toHaveBeenCalledWith('/locales/en-US.json');
        });
    });

    describe('t (translate)', () => {
        test('should return translation for valid key', async () => {
            const mockTranslations = {
                app: { title: 'Xuanwu AI', greeting: 'Hello {{name}}' }
            };
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve(mockTranslations)
            });
            
            const { loadLocale, t } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            expect(t('app.title')).toBe('Xuanwu AI');
        });

        test('should return key for missing translation', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { loadLocale, t } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            expect(t('missing.key')).toBe('missing.key');
        });

        test('should interpolate parameters', async () => {
            const mockTranslations = { app: { greeting: 'Hello {{name}}' } };
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve(mockTranslations)
            });
            
            const { loadLocale, t } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            expect(t('app.greeting', { name: 'World' })).toBe('Hello World');
        });
    });

    describe('getCurrentLocale', () => {
        test('should return current locale after loading', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { loadLocale, getCurrentLocale } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('en-US');
            expect(getCurrentLocale()).toBe('en-US');
        });
    });

    describe('isLocaleLoaded', () => {
        test('should return true after loading', async () => {
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { loadLocale, isLocaleLoaded } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            expect(isLocaleLoaded()).toBe(true);
        });
    });

    describe('updatePageTranslations', () => {
        test('should update elements with data-i18n attribute', async () => {
            const mockElement = {
                getAttribute: jest.fn(() => 'app.title'),
                setAttribute: jest.fn(),
                textContent: ''
            };
            document.querySelectorAll.mockReturnValue([mockElement]);
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: { title: 'Test Title' } })
            });
            
            const { loadLocale, updatePageTranslations } = await import('../../app/frontend/scripts/i18n.js');
            await loadLocale('zh-CN');
            updatePageTranslations();
            
            expect(document.querySelectorAll).toHaveBeenCalledWith('[data-i18n]');
        });
    });

    describe('initI18n', () => {
        test('should initialize with saved locale', async () => {
            localStorageMock.store['xuanwu_locale'] = 'en-US';
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { initI18n } = await import('../../app/frontend/scripts/i18n.js');
            await initI18n();
            expect(global.fetch).toHaveBeenCalledWith('/locales/en-US.json');
        });

        test('should detect browser locale when no saved preference', async () => {
            global.navigator = { language: 'en-US' };
            
            global.fetch.mockResolvedValueOnce({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { initI18n } = await import('../../app/frontend/scripts/i18n.js');
            await initI18n();
            expect(global.fetch).toHaveBeenCalledWith('/locales/en-US.json');
        });
    });

    describe('setLocale', () => {
        test('should switch locale and save preference', async () => {
            global.fetch.mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({ app: {} })
            });
            
            const { setLocale } = await import('../../app/frontend/scripts/i18n.js');
            await setLocale('en-US');
            
            expect(localStorageMock.setItem).toHaveBeenCalledWith('xuanwu_locale', 'en-US');
        });
    });
});
