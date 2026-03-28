/**
 * main.js - SPA Entry Point
 *
 * This is the simplified entry point for the SPA.
 * All page initialization logic has been moved to app.js.
 *
 * Original main.js content (for chat page) has been preserved in
 * .xuanwu/main_original.js.bak for reference when creating pages/chat.js
 */

import { initApp } from './app.js'

// Initialize SPA when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => initApp())
} else {
  initApp()
}
