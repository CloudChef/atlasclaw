/**
 * Configuration Management Module
 * Responsible for loading and providing runtime configuration
 */

const DEFAULT_CONFIG = {
    apiBaseUrl: '',
    basePath: ''
};

let config = { ...DEFAULT_CONFIG };
let configLoaded = false;

const MANAGED_FETCH_PREFIXES = [
    '/api/',
    '/config.json',
    '/locales/',
    '/user-content/',
];

export function normalizeBasePath(value) {
    const raw = String(value || '').trim();
    if (!raw || raw === '/') {
        return '';
    }
    const withLeadingSlash = raw.startsWith('/') ? raw : `/${raw}`;
    return withLeadingSlash.replace(/\/+$/, '');
}

function getRuntimeBasePath() {
    return normalizeBasePath(config.basePath || window.__atlasclawBasePath || '');
}

function normalizePath(path) {
    const raw = String(path || '/').trim();
    if (!raw || raw === '/') {
        return '/';
    }
    if (raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('//')) {
        return raw;
    }
    return raw.startsWith('/') ? raw : `/${raw}`;
}

function isAbsoluteUrl(value) {
    const raw = String(value || '');
    return raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('//');
}

/**
 * Load runtime configuration
 * @returns {Promise<object>} Configuration object
 */
export async function loadConfig() {
    if (configLoaded) {
        return config;
    }
    
    try {
        const response = await fetch(buildAppUrl('/config.json'));
        if (response.ok) {
            const loaded = await response.json();
            config = {
                ...DEFAULT_CONFIG,
                ...loaded,
                basePath: normalizeBasePath(loaded.basePath || window.__atlasclawBasePath || ''),
            };
            window.__atlasclawBasePath = config.basePath;
            console.log('[Config] Loaded:', config);
        }
    } catch (e) {
        console.warn('[Config] Using default config:', e.message);
    }
    
    configLoaded = true;
    return config;
}

/**
 * Get current configuration
 * @returns {object} Configuration object
 */
export function getConfig() {
    return { ...config };
}

/**
 * Get API base URL
 * @returns {string} API base URL
 */
export function getApiBaseUrl() {
    return config.apiBaseUrl;
}

export function getBasePath() {
    return getRuntimeBasePath();
}

export function buildAppUrl(path) {
    const cleanPath = normalizePath(path);
    if (isAbsoluteUrl(cleanPath)) {
        return cleanPath;
    }

    const basePath = getRuntimeBasePath();
    if (cleanPath === '/') {
        return basePath ? `${basePath}/` : '/';
    }
    return basePath ? `${basePath}${cleanPath}` : cleanPath;
}

export function buildAssetUrl(path) {
    return buildAppUrl(path);
}

export function stripBasePath(pathname) {
    const basePath = getRuntimeBasePath();
    const cleanPath = normalizePath(pathname);
    if (isAbsoluteUrl(cleanPath) || !basePath) {
        return cleanPath;
    }
    if (cleanPath === basePath) {
        return '/';
    }
    if (cleanPath.startsWith(`${basePath}/`)) {
        return cleanPath.slice(basePath.length) || '/';
    }
    return cleanPath;
}

export function rewriteManagedAppUrl(url) {
    const raw = String(url || '').trim();
    if (!raw.startsWith('/')) {
        return raw;
    }
    if (MANAGED_FETCH_PREFIXES.some(prefix => raw === prefix || raw.startsWith(prefix))) {
        return buildAppUrl(raw);
    }
    return raw;
}

/**
 * Build full API URL
 * @param {string} path - API path
 * @returns {string} Full URL
 */
export function buildApiUrl(path) {
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    const baseRaw = String(config.apiBaseUrl || getRuntimeBasePath() || '').trim();
    if (!baseRaw) {
        return cleanPath;
    }

    if (!isAbsoluteUrl(baseRaw)) {
        return buildAppUrl(cleanPath);
    }

    try {
        const target = new URL(baseRaw, window.location.origin);
        if (target.origin !== window.location.origin) {
            console.warn('[Config] Cross-origin apiBaseUrl detected, fallback to same-origin:', target.origin);
            return buildAppUrl(cleanPath);
        }
    } catch (_error) {
        return buildAppUrl(cleanPath);
    }

    const base = baseRaw.replace(/\/$/, '');
    return `${base}${cleanPath}`;
}


export default {
    buildAppUrl,
    buildAssetUrl,
    loadConfig,
    getConfig,
    getBasePath,
    getApiBaseUrl,
    buildApiUrl,
    normalizeBasePath,
    rewriteManagedAppUrl,
    stripBasePath,
};
