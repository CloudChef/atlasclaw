/**
 * Application Entry Point
 * Initialize all modules
 */

import { loadConfig } from './config.js';
import { initSession, startNewSession, clearSession } from './session-manager.js';
import { initChat, abortCurrentStream } from './chat-ui.js';
import { initI18n, t, setLocale, getCurrentLocale, updatePageTranslations } from './i18n.js';
import { checkAuth, installAuthFetchInterceptor, logout } from './auth.js';




/**
 * Initialize application
 */
async function init() {
    console.log('[App] Initializing...');
    
    try {
        installAuthFetchInterceptor();

        // 1. Check authentication
        const authInfo = await checkAuth({ redirect: true });

        if (!authInfo) {
            return;
        }

        // Show admin link if user is admin
        if (authInfo.is_admin) {
            const adminLink = document.getElementById('adminUsersLink');
            if (adminLink) {
                adminLink.style.display = '';
            }
        }

        // 2. Load configuration
        await loadConfig();
        
        // 3. Initialize i18n

        await initI18n();
        updatePageTranslations();
        
        // 3. Initialize session
        await initSession();
        
        // 4. Initialize chat UI
        const chatElement = document.getElementById('chat');
        if (chatElement) {
            await initChat(chatElement);
        }
        
        // 5. Bind global events
        bindGlobalEvents();
        
        console.log('[App] Initialized successfully');
    } catch (error) {
        console.error('[App] Initialization failed:', error);
    }
}

/**
 * Bind global event handlers
 */
function bindGlobalEvents() {
    // New chat button
    const newChatBtn = document.querySelector('.new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', handleNewChat);
    }
    
    // Confirm dialog buttons
    const confirmBtn = document.querySelector('.btn-confirm');
    const cancelBtn = document.querySelector('.btn-cancel');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', () => handleConfirm(true));
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => handleConfirm(false));
    }
    
    // Language switch buttons
    const langSwitcher = document.querySelector('.language-switcher');
    if (langSwitcher) {
        langSwitcher.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-lang]');
            if (btn) {
                handleLanguageSwitch(btn.dataset.lang);
            }
        });
    }

    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', handleLogout);
    }
}


/**
 * Handle logout
 */
async function handleLogout() {
    await logout({ redirect: true });
}

/**
 * Handle new chat creation
 */
async function handleNewChat() {

    console.log('[App] Starting new chat...');
    
    // Abort current stream
    abortCurrentStream();
    
    // Clear session and create new one
    try {
        await startNewSession();
        // Refresh page to reset UI
        location.reload();
    } catch (error) {
        console.error('[App] Failed to start new chat:', error);
        // Fallback: clear local state only
        clearSession();
        location.reload();
    }
}

// Confirm dialog state
let pendingActionId = null;

/**
 * Show confirm dialog
 */
export function showConfirmDialog(actionId, message) {
    pendingActionId = actionId;
    const dialog = document.getElementById('confirmDialog');
    const messageEl = document.getElementById('confirmMessage');
    
    if (dialog && messageEl) {
        messageEl.textContent = message;
        dialog.classList.remove('hidden');
    }
}

/**
 * Hide confirm dialog
 */
export function hideConfirmDialog() {
    const dialog = document.getElementById('confirmDialog');
    if (dialog) {
        dialog.classList.add('hidden');
    }
    pendingActionId = null;
}

/**
 * Handle confirm/cancel action
 */
async function handleConfirm(confirmed) {
    hideConfirmDialog();
    
    if (!pendingActionId) return;
    
    try {
        const response = await fetch('/api/chat/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action_id: pendingActionId,
                confirmed: confirmed
            })
        });
        
        const result = await response.json();
        const chatElement = document.getElementById('chat');
        
        if (chatElement) {
            if (confirmed) {
                chatElement.addMessage({ 
                    text: `✅ ${result.message || t('dialog.operationExecuted')}`, 
                    role: 'ai' 
                });
            } else {
                chatElement.addMessage({ 
                    text: `❌ ${t('dialog.operationCancelled')}`, 
                    role: 'ai' 
                });
            }
        }
    } catch (error) {
        console.error('[App] Confirm error:', error);
    }
}

/**
 * Handle language switch
 */
async function handleLanguageSwitch(locale) {
    console.log('[App] Switching language to:', locale);
    await setLocale(locale);
    
    // Update language switcher active state
    document.querySelectorAll('.language-switcher [data-lang]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === locale);
    });
    
    // Reinitialize chat UI to update introMessage
    const chatElement = document.getElementById('chat');
    if (chatElement) {
        await initChat(chatElement);
    }
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Export i18n functions for external use
export { setLocale, getCurrentLocale };
