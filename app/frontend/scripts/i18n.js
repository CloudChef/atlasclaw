/**
 * Internationalization (i18n) Module
 * Provides multi-language support
 */

import { buildAssetUrl } from './config.js'

const LOCALE_STORAGE_KEY = 'atlasclaw_locale';
const SUPPORTED_LOCALES = ['zh-CN', 'en-US'];
const DEFAULT_LOCALE = 'en-US';


let currentLocale = DEFAULT_LOCALE;
let translations = {};
let localeLoaded = false;

function resolveTranslationValue(key) {
    const keys = key.split('.');
    let value = translations;

    for (const k of keys) {
        if (value && typeof value === 'object' && k in value) {
            value = value[k];
        } else {
            return null;
        }
    }

    return typeof value === 'string' ? value : null;
}

function interpolateTranslation(value, params = {}) {
    return value.replace(/\{\{(\w+)\}\}/g, (match, name) => {
        return params[name] !== undefined ? params[name] : match;
    });
}

/**
 * Detect browser language
 * @returns {string} Detected locale code
 */
export function detectBrowserLocale() {
    const browserLang = navigator.language || navigator.userLanguage;
    
    // Direct match
    if (SUPPORTED_LOCALES.includes(browserLang)) {
        return browserLang;
    }
    
    // Prefix match (e.g., 'zh' -> 'zh-CN', 'en' -> 'en-US')
    const langPrefix = browserLang.split('-')[0];
    if (langPrefix === 'zh') return 'zh-CN';
    if (langPrefix === 'en') return 'en-US';
    
    return DEFAULT_LOCALE;
}

/**
 * Get saved locale preference
 * @returns {string|null} Saved locale code
 */
export function getSavedLocale() {
    try {
        return localStorage.getItem(LOCALE_STORAGE_KEY);
    } catch (e) {
        console.warn('[i18n] Cannot access localStorage:', e.message);
        return null;
    }
}

/**
 * Save locale preference
 * @param {string} locale - Locale code
 */
export function saveLocale(locale) {
    try {
        localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    } catch (e) {
        console.warn('[i18n] Cannot save to localStorage:', e.message);
    }
}

/**
 * Load locale file
 * @param {string} locale - Locale code
 * @returns {Promise<object>} Translations object
 */
export async function loadLocale(locale) {
    if (!SUPPORTED_LOCALES.includes(locale)) {
        console.warn(`[i18n] Unsupported locale: ${locale}, falling back to ${DEFAULT_LOCALE}`);
        locale = DEFAULT_LOCALE;
    }
    
    try {
        const response = await fetch(buildAssetUrl(`/locales/${locale}.json`));
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        translations = await response.json();
        currentLocale = locale;
        localeLoaded = true;
        
        console.log(`[i18n] Loaded locale: ${locale}`);
        return translations;
    } catch (e) {
        console.error(`[i18n] Failed to load locale ${locale}:`, e.message);
        
        // If not default locale, try loading default
        if (locale !== DEFAULT_LOCALE) {
            return loadLocale(DEFAULT_LOCALE);
        }
        
        throw e;
    }
}

/**
 * Initialize i18n
 * Detect browser language and load corresponding file
 * @returns {Promise<string>} Current locale code
 */
export async function initI18n() {
    // Always use browser language
    const locale = detectBrowserLocale();
    
    await loadLocale(locale);
    return currentLocale;
}

/**
 * Get translated text
 * @param {string} key - Translation key (e.g., 'app.title')
 * @param {object} params - Interpolation parameters
 * @returns {string} Translated text
 */
export function t(key, params = {}) {
    const value = resolveTranslationValue(key);

    if (value === null) {
        console.warn(`[i18n] Missing translation: ${key}`);
        return key;
    }

    if (typeof value !== 'string') {
        console.warn(`[i18n] Invalid translation value for: ${key}`);
        return key;
    }

    return interpolateTranslation(value, params);
}

export function translateIfExists(key, params = {}) {
    const value = resolveTranslationValue(key);
    return value === null ? null : interpolateTranslation(value, params);
}

/**
 * Get current locale
 * @returns {string} Current locale code
 */
export function getCurrentLocale() {
    return currentLocale;
}

/**
 * Get supported locales list
 * @returns {string[]} Locale code array
 */
export function getSupportedLocales() {
    return [...SUPPORTED_LOCALES];
}

/**
 * Switch locale
 * @param {string} locale - Target locale code
 * @returns {Promise<void>}
 */
export async function setLocale(locale) {
    if (locale === currentLocale && localeLoaded) {
        return;
    }
    
    await loadLocale(locale);
    saveLocale(locale);
    
    // Update all elements with data-i18n attribute
    updatePageTranslations();
}

/**
 * Update all page translations
 */
export function updatePageTranslations() {
    // Skip if translations not loaded yet
    if (!localeLoaded || Object.keys(translations).length === 0) {
        console.warn('[i18n] Translations not loaded, skipping update');
        return;
    }
    
    // Update text content
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const translated = t(key);
        // Only update if translation found (not returning the key itself)
        if (translated !== key) {
            el.textContent = translated;
        }
    });
    
    // Update placeholder
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        const translated = t(key);
        if (translated !== key) {
            el.placeholder = translated;
        }
    });
    
    // Update title attribute
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        const translated = t(key);
        if (translated !== key) {
            el.title = translated;
        }
    });
    
    // Update aria-label attribute
    document.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
        const key = el.getAttribute('data-i18n-aria-label');
        const translated = t(key);
        if (translated !== key) {
            el.setAttribute('aria-label', translated);
        }
    });
    
    // Update page title
    const titleKey = document.querySelector('title[data-i18n]');
    if (titleKey) {
        const translated = t(titleKey.getAttribute('data-i18n'));
        if (translated !== titleKey.getAttribute('data-i18n')) {
            document.title = translated;
        }
    }
}



/**
 * Check if locale is loaded
 * @returns {boolean}
 */
export function isLocaleLoaded() {
    return localeLoaded;
}

/**
 * Update translations for a specific container
 * Same logic as updatePageTranslations but scoped to container
 * @param {HTMLElement} container - Container element to update
 */
export function updateContainerTranslations(container) {
    if (!container) return

    // Skip if translations not loaded yet
    if (!localeLoaded || Object.keys(translations).length === 0) {
        console.warn('[i18n] Translations not loaded, skipping container update')
        return
    }

    // Update text content
    container.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n')
        const translated = t(key)
        if (translated && translated !== key) {
            el.textContent = translated
        }
    })

    // Update placeholder
    container.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder')
        const translated = t(key)
        if (translated && translated !== key) {
            el.placeholder = translated
        }
    })

    // Update title attribute
    container.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title')
        const translated = t(key)
        if (translated && translated !== key) {
            el.title = translated
        }
    })

    // Update aria-label attribute
    container.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
        const key = el.getAttribute('data-i18n-aria-label')
        const translated = t(key)
        if (translated && translated !== key) {
            el.setAttribute('aria-label', translated)
        }
    })
}

export default {
    initI18n,
    t,
    translateIfExists,
    setLocale,
    getCurrentLocale,
    getSupportedLocales,
    detectBrowserLocale,
    updatePageTranslations,
    updateContainerTranslations,
    isLocaleLoaded
};
