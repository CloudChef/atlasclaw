/**
 * models.js - Models Page Module
 *
 * Model management page for SPA architecture.
 * Migrated from models.html inline scripts.
 *
 * Page lifecycle:
 * - mount(container, { params, route }) - Initialize and render page
 * - unmount() - Cleanup when leaving page
 */

import { t, updateContainerTranslations } from '../i18n.js'
import { showToast } from '../components/toast.js'
import { buildAssetUrl } from '../config.js'

// ========== Module State ==========
let mounted = false
let containerRef = null

// Model configs state
let modelConfigs = []
let editingModelId = null
let pendingDeleteId = null

// Provider data loaded from backend
let PROVIDER_PRESETS = {}
let PROVIDER_MODELS = {}

// Providers that don't require API Key (local/self-hosted)
const NO_API_KEY_PROVIDERS = ['ollama', 'vllm', 'custom']

// Provider brand colors and icons
const PROVIDER_ICONS = {
  openai: { color: '#10a37f', label: '', icon: 'openai' },
  anthropic: { color: '#d4a574', label: 'A\\', icon: 'anthropic' },
  google: { color: '#4285f4', label: 'G', icon: 'google' },
  deepseek: { color: '#4d6bfe', label: 'DS', icon: 'deepseek' },
  qwen: { color: '#6366f1', label: 'Q', icon: 'qwen' },
  ollama: { color: '#1a1a2e', label: '🦙', icon: 'ollama' },
  groq: { color: '#f55036', label: 'G', icon: 'groq' },
  mistral: { color: '#ff7000', label: 'M', icon: 'mistral' },
  cohere: { color: '#FF7759', label: 'C', icon: 'cohere' },
  zhipu: { color: '#3b82f6', label: '智', icon: 'zhipu' },
  moonshot: { color: '#000000', label: '🌙', icon: 'moonshot' },
  baichuan: { color: '#1e88e5', label: '百', icon: 'baichuan' },
  doubao: { color: '#ff6a00', label: '豆', icon: 'doubao' },
  hunyuan: { color: '#0052d9', label: '混', icon: 'hunyuan' },
  minimax: { color: '#5856d6', label: 'MM', icon: 'minimax' },
  spark: { color: '#0091ff', label: '讯', icon: 'spark' },
  stepfun: { color: '#7c3aed', label: 'SF', icon: 'stepfun' },
  siliconflow: { color: '#6366f1', label: 'SF', icon: 'siliconflow' },
  yi: { color: '#10b981', label: '零', icon: 'yi' },
  vllm: { color: '#9333ea', label: 'V', icon: 'vllm' },
  custom: { color: '#6b7280', label: '⚙', icon: 'custom' }
}

// SVG icon templates
const SVG_ICONS = {
  // OpenAI spiral logo
  openai: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.8956zm16.0993 3.8558-5.8428-3.3685 2.0201-1.1685a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.4021-.6813zm2.0107-3.0231-.142-.0852-4.7782-2.7582a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805-4.7782 2.7582a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654 2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997z"/>
  </svg>`,
  // Anthropic logo
  anthropic: `<svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
    <path d="M13.827 3.52h3.603L24 20.48h-3.603l-6.57-16.96zm-7.258 0h3.767L16.906 20.48h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522zm4.132 10.69L8.453 8.2l-2.248 6.01h4.496z"/>
  </svg>`,
  // DeepSeek logo (official whale mascot)
  deepseek: `<svg viewBox="0 0 512 510" fill="currentColor" width="22" height="22">
    <path d="M440.9 139.2c-4-2-5.7 1.8-8 3.7-.8.6-1.5 1.4-2.2 2.1-5.8 6.2-12.7 10.3-21.6 9.9-13-.7-24.2 3.4-34 13.3-2.1-12.3-9-19.7-19.6-24.4-5.5-2.4-11.1-4.9-15-10.2-2.7-3.8-3.4-8-4.8-12.2-.9-2.5-1.7-5.1-4.6-5.5-3.1-.5-4.4 2.1-5.6 4.3-4.9 9-6.8 18.9-6.6 29 .4 22.6 10 40.6 28.9 53.4 2.2 1.5 2.7 2.9 2 5.1-1.3 4.4-2.8 8.7-4.2 13.1-.9 2.8-2.2 3.4-5.2 2.2-10.4-4.3-19.4-10.8-27.3-18.6-13.5-13-25.7-27.4-40.9-38.7a177.6 177.6 0 00-10.8-7.4c-15.5-15.1 2-27.4 6.1-28.9 4.2-1.5 1.5-6.8-12.3-6.7-13.7.1-26.3 4.7-42.3 10.8-2.3.9-4.8 1.6-7.3 2.1-14.5-2.8-29.6-3.4-45.4-1.6-29.7 3.3-53.4 17.3-70.8 41.3-20.9 28.8-25.9 61.5-19.8 95.6 6.3 35.9 24.7 65.7 52.9 89 29.2 24.1 62.9 35.9 101.3 33.7 23.3-1.3 49.3-4.5 78.6-29.3 7.4 3.7 15.1 5.1 28 6.2 9.9.9 19.5-.5 26.8-2 11.6-2.4 10.8-13.2 6.6-15.1-33.9-15.8-26.5-9.4-33.2-14.6 17.2-20.4 43.2-41.6 53.4-110.2.8-5.4.1-8.9 0-13.3-.1-2.7.6-3.7 3.6-4 8.5-1 16.7-3.3 24.3-7.5 22-12 30.8-31.7 32.9-55.4.3-3.6-.1-7.3-3.9-9.2zM249.4 351.9c-32.9-25.8-48.8-34.4-55.4-34-6.2.4-5 7.4-3.7 12 1.4 4.5 3.3 7.7 5.8 11.6 1.8 2.6 3 6.6-1.8 9.5-10.6 6.6-29-2.2-29.9-2.6-21.4-12.6-39.3-29.3-52-52-12.2-21.9-19.3-45.4-20.4-70.5-.3-6.1 1.5-8.2 7.5-9.3 7.9-1.5 16.1-1.8 24.1-.6 33.5 4.9 62.1 19.9 86 43.7 13.7 13.5 24 29.7 34.7 45.5 11.3 16.8 23.5 32.8 39 45.9 5.5 4.6 9.8 8.1 14 10.7-12.6 1.4-33.7 1.7-48.1-9.7zm15.9-102.5c.5-2.1 2.4-3.7 4.7-3.7a4.7 4.7 0 011.7.3c.7.2 1.3.6 1.8 1.2.9.9 1.4 2.1 1.4 3.4 0 2.7-2.2 4.8-4.9 4.8a4.7 4.7 0 01-4.7-4 5 5 0 01.1-2zm47.2 26.9c-2.6 1-5.2 1.8-7.7 1.9-4.7.2-9.8-1.7-12.6-4-4.3-3.6-7.4-5.6-8.7-11.9-.6-2.7-.2-6.9.2-9.2 1.1-5.1-.1-8.5-3.8-11.5-3-2.4-6.7-3.1-10.8-3.1-1.5 0-3-.7-4-1.2-1.7-.9-3.1-3-1.8-5.6.4-.9 2.5-2.9 3-3.3 5.6-3.2 12.1-2.1 18 .2 5.5 2.3 9.7 6.4 15.8 12.3 6.2 7.1 7.3 9.1 10.8 14.4 2.8 4.2 5.3 8.5 7 13.3.9 2.6.1 4.7-2.3 6.3-1 .6-2.1 1-3.2 1.5z"/>
  </svg>`,
  // Edit pencil icon
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18">
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
  </svg>`,
  // Delete trash icon
  delete: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18">
    <polyline points="3 6 5 6 21 6"></polyline>
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
    <line x1="10" y1="11" x2="10" y2="17"></line>
    <line x1="14" y1="11" x2="14" y2="17"></line>
  </svg>`,
  // Eye icon for API key visibility
  eyeOpen: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>`,
  eyeClosed: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
    <line x1="1" y1="1" x2="23" y2="23"></line>
  </svg>`,
  // Google Gemini logo
  google: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z"></path>
  </svg>`,
  // Qwen logo
  qwen: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M12.604 1.34c.393.69.784 1.382 1.174 2.075a.18.18 0 00.157.091h5.552c.174 0 .322.11.446.327l1.454 2.57c.19.337.24.478.024.837-.26.43-.513.864-.76 1.3l-.367.658c-.106.196-.223.28-.04.512l2.652 4.637c.172.301.111.494-.043.77-.437.785-.882 1.564-1.335 2.34-.159.272-.352.375-.68.37-.777-.016-1.552-.01-2.327.016a.099.099 0 00-.081.05 575.097 575.097 0 01-2.705 4.74c-.169.293-.38.363-.725.364-.997.003-2.002.004-3.017.002a.537.537 0 01-.465-.271l-1.335-2.323a.09.09 0 00-.083-.049H4.982c-.285.03-.553-.001-.805-.092l-1.603-2.77a.543.543 0 01-.002-.54l1.207-2.12a.198.198 0 000-.197 550.951 550.951 0 01-1.875-3.272l-.79-1.395c-.16-.31-.173-.496.095-.965.465-.813.927-1.625 1.387-2.436.132-.234.304-.334.584-.335a338.3 338.3 0 012.589-.001.124.124 0 00.107-.063l2.806-4.895a.488.488 0 01.422-.246c.524-.001 1.053 0 1.583-.006L11.704 1c.341-.003.724.032.9.34zm-3.432.403a.06.06 0 00-.052.03L6.254 6.788a.157.157 0 01-.135.078H3.253c-.056 0-.07.025-.041.074l5.81 10.156c.025.042.013.062-.034.063l-2.795.015a.218.218 0 00-.2.116l-1.32 2.31c-.044.078-.021.118.068.118l5.716.008c.046 0 .08.02.104.061l1.403 2.454c.046.081.092.082.139 0l5.006-8.76.783-1.382a.055.055 0 01.096 0l1.424 2.53a.122.122 0 00.107.062l2.763-.02a.04.04 0 00.035-.02.041.041 0 000-.04l-2.9-5.086a.108.108 0 010-.113l.293-.507 1.12-1.977c.024-.041.012-.062-.035-.062H9.2c-.059 0-.073-.026-.043-.077l1.434-2.505a.107.107 0 000-.114L9.225 1.774a.06.06 0 00-.053-.031zm6.29 8.02c.046 0 .058.02.034.06l-.832 1.465-2.613 4.585a.056.056 0 01-.05.029.058.058 0 01-.05-.029L8.498 9.841c-.02-.034-.01-.052.028-.054l.216-.012 6.722-.012z"></path>
  </svg>`,
  // Ollama logo
  ollama: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M7.905 1.09c.216.085.411.225.588.41.295.306.544.744.734 1.263.191.522.315 1.1.362 1.68a5.054 5.054 0 012.049-.636l.051-.004c.87-.07 1.73.087 2.48.474.101.053.2.11.297.17.05-.569.172-1.134.36-1.644.19-.52.439-.957.733-1.264a1.67 1.67 0 01.589-.41c.257-.1.53-.118.796-.042.401.114.745.368 1.016.737.248.337.434.769.561 1.287.23.934.27 2.163.115 3.645l.053.04.026.019c.757.576 1.284 1.397 1.563 2.35.435 1.487.216 3.155-.534 4.088l-.018.021.002.003c.417.762.67 1.567.724 2.4l.002.03c.064 1.065-.2 2.137-.814 3.19l-.007.01.01.024c.472 1.157.62 2.322.438 3.486l-.006.039a.651.651 0 01-.747.536.648.648 0 01-.54-.742c.167-1.033.01-2.069-.48-3.123a.643.643 0 01.04-.617l.004-.006c.604-.924.854-1.83.8-2.72-.046-.779-.325-1.544-.8-2.273a.644.644 0 01.18-.886l.009-.006c.243-.159.467-.565.58-1.12a4.229 4.229 0 00-.095-1.974c-.205-.7-.58-1.284-1.105-1.683-.595-.454-1.383-.673-2.38-.61a.653.653 0 01-.632-.371c-.314-.665-.772-1.141-1.343-1.436a3.288 3.288 0 00-1.772-.332c-1.245.099-2.343.801-2.67 1.686a.652.652 0 01-.61.425c-1.067.002-1.893.252-2.497.703-.522.39-.878.935-1.066 1.588a4.07 4.07 0 00-.068 1.886c.112.558.331 1.02.582 1.269l.008.007c.212.207.257.53.109.785-.36.622-.629 1.549-.673 2.44-.05 1.018.186 1.902.719 2.536l.016.019a.643.643 0 01.095.69c-.576 1.236-.753 2.252-.562 3.052a.652.652 0 01-1.269.298c-.243-1.018-.078-2.184.473-3.498l.014-.035-.008-.012a4.339 4.339 0 01-.598-1.309l-.005-.019a5.764 5.764 0 01-.177-1.785c.044-.91.278-1.842.622-2.59l.012-.026-.002-.002c-.293-.418-.51-.953-.63-1.545l-.005-.024a5.352 5.352 0 01.093-2.49c.262-.915.777-1.701 1.536-2.269.06-.045.123-.09.186-.132-.159-1.493-.119-2.73.112-3.67.127-.518.314-.95.562-1.287.27-.368.614-.622 1.015-.737.266-.076.54-.059.797.042zm4.116 9.455c-.05.05-.073.115-.073.193a.271.271 0 00.273.27.27.27 0 00.193-.08.27.27 0 00.08-.193.266.266 0 00-.079-.19.266.266 0 00-.19-.079.271.271 0 00-.204.079zm-4.4-.104a.403.403 0 00-.114.284c0 .224.182.406.406.406a.404.404 0 00.407-.406.401.401 0 00-.407-.402.405.405 0 00-.291.118z"></path>
    <path d="M10.04 14.123c.022.042.028.077.028.129l-.001.062a1.397 1.397 0 01-.433.933 1.395 1.395 0 01-.98.405 1.393 1.393 0 01-1.413-1.396v-.001c0-.048.005-.095.011-.142a3.2 3.2 0 01-1.072-.606 2.4 2.4 0 01-.73-1.113 2.588 2.588 0 01-.093-.907 2.56 2.56 0 01.24-.91c.075-.152.174-.311.304-.483l.036-.046-.007-.014a3.384 3.384 0 01-.31-1.055 3.16 3.16 0 01.045-1.074c.067-.295.175-.571.323-.831a2.84 2.84 0 01.573-.696 3.044 3.044 0 011.26-.682 3.696 3.696 0 011.007-.112c.426.016.841.09 1.233.228l.061.023.032-.054c.274-.448.621-.815 1.03-1.093a3.456 3.456 0 011.427-.545 3.843 3.843 0 011.521.072c.518.13.987.362 1.387.685.422.341.762.774.99 1.266l.025.058.056-.022a3.5 3.5 0 011.31-.224c.33.007.656.051.97.137a2.94 2.94 0 011.153.625c.185.168.353.355.498.564.227.328.388.685.486 1.062.097.373.133.761.107 1.147a3.4 3.4 0 01-.253 1.082l-.024.057c.172.197.314.41.424.642.137.29.226.6.264.922.02.165.026.333.02.501a2.528 2.528 0 01-.182.864c-.11.265-.26.51-.448.727a3.19 3.19 0 01-1.162.854 1.393 1.393 0 01-.02.25 1.396 1.396 0 01-1.39 1.168 1.395 1.395 0 01-1.363-1.72c-.257.04-.52.062-.784.062a4.72 4.72 0 01-.91-.088 1.397 1.397 0 01-1.379 1.185c-.497 0-.944-.26-1.193-.678a4.09 4.09 0 01-.659.052c-.268-.001-.533-.031-.79-.087zm4.668-3.136a.65.65 0 01.103-.35l.002-.003c-.038-.046-.083-.087-.133-.121a1.047 1.047 0 00-.595-.186 1.054 1.054 0 00-.91.523.65.65 0 01.113.364.65.65 0 01-.651.652.65.65 0 01-.651-.652c0-.104.024-.202.068-.288a1.048 1.048 0 00-.86-.049 1.054 1.054 0 00-.482.352l-.03.042.011.018a.646.646 0 01.098.344.65.65 0 01-.652.651.65.65 0 01-.651-.651c0-.183.077-.349.199-.467a1.059 1.059 0 00-.26.7c.001.303.13.593.351.798.222.205.515.318.82.315a1.134 1.134 0 00.742-.28 1.896 1.896 0 002.256-.058c.218.185.5.29.792.293.32.003.629-.114.857-.325a1.134 1.134 0 00.34-.854 1.06 1.06 0 00-.327-.729.65.65 0 01.45.62z"></path>
  </svg>`,
  // Groq logo
  groq: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M12.036 2c-3.853-.035-7 3-7.036 6.781-.035 3.782 3.055 6.872 6.908 6.907h2.42v-2.566h-2.292c-2.407.028-4.38-1.866-4.408-4.23-.029-2.362 1.901-4.298 4.308-4.326h.1c2.407 0 4.358 1.915 4.365 4.278v6.305c0 2.342-1.944 4.25-4.323 4.279a4.375 4.375 0 01-3.033-1.252l-1.851 1.818A7 7 0 0012.029 22h.092c3.803-.056 6.858-3.083 6.879-6.816v-6.5C18.907 4.963 15.817 2 12.036 2z"></path>
  </svg>`,
  // Mistral logo
  mistral: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path clip-rule="evenodd" d="M3.428 3.4h3.429v3.428h3.429v3.429h-.002 3.431V6.828h3.427V3.4h3.43v13.714H24v3.429H13.714v-3.428h-3.428v-3.429h-3.43v3.428h3.43v3.429H0v-3.429h3.428V3.4zm10.286 13.715h3.428v-3.429h-3.427v3.429z"></path>
  </svg>`,
  // Cohere logo
  cohere: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path clip-rule="evenodd" d="M8.128 14.099c.592 0 1.77-.033 3.398-.703 1.897-.781 5.672-2.2 8.395-3.656 1.905-1.018 2.74-2.366 2.74-4.18A4.56 4.56 0 0018.1 1H7.549A6.55 6.55 0 001 7.55c0 3.617 2.745 6.549 7.128 6.549z"></path>
    <path clip-rule="evenodd" d="M9.912 18.61a4.387 4.387 0 012.705-4.052l3.323-1.38c3.361-1.394 7.06 1.076 7.06 4.715a5.104 5.104 0 01-5.105 5.104l-3.597-.001a4.386 4.386 0 01-4.386-4.387z"></path>
    <path d="M4.776 14.962A3.775 3.775 0 001 18.738v.489a3.776 3.776 0 007.551 0v-.49a3.775 3.775 0 00-3.775-3.775z"></path>
  </svg>`,
  // Zhipu logo
  zhipu: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M11.991 23.503a.24.24 0 00-.244.248.24.24 0 00.244.249.24.24 0 00.245-.249.24.24 0 00-.22-.247l-.025-.001zM9.671 5.365a1.697 1.697 0 011.099 2.132l-.071.172-.016.04-.018.054c-.07.16-.104.32-.104.498-.035.71.47 1.279 1.186 1.314h.366c1.309.053 2.338 1.173 2.286 2.523-.052 1.332-1.152 2.38-2.478 2.327h-.174c-.715.018-1.274.64-1.239 1.368 0 .124.018.23.053.337.209.373.54.658.96.8.75.23 1.517-.125 1.9-.782l.018-.035c.402-.64 1.17-.96 1.92-.711.854.284 1.378 1.226 1.099 2.167a1.661 1.661 0 01-2.077 1.102 1.711 1.711 0 01-.907-.711l-.017-.035c-.2-.323-.463-.58-.851-.711l-.056-.018a1.646 1.646 0 00-1.954.746 1.66 1.66 0 01-1.065.764 1.677 1.677 0 01-1.989-1.279c-.209-.906.332-1.83 1.257-2.043a1.51 1.51 0 01.296-.035h.018c.68-.071 1.151-.622 1.116-1.333a1.307 1.307 0 00-.227-.693 2.515 2.515 0 01-.366-1.403 2.39 2.39 0 01.366-1.208c.14-.195.21-.444.227-.693.018-.71-.506-1.261-1.186-1.332l-.07-.018a1.43 1.43 0 01-.299-.07l-.05-.019a1.7 1.7 0 01-1.047-2.114 1.68 1.68 0 012.094-1.101zm-5.575 10.11c.26-.264.639-.367.994-.27.355.096.633.379.728.74.095.362-.007.748-.267 1.013-.402.41-1.053.41-1.455 0a1.062 1.062 0 010-1.482zm14.845-.294c.359-.09.738.024.992.297.254.274.344.665.237 1.025-.107.36-.396.634-.756.718-.551.128-1.1-.22-1.23-.781a1.05 1.05 0 01.757-1.26zm-.064-4.39c.314.32.49.753.49 1.206 0 .452-.176.886-.49 1.206-.315.32-.74.5-1.185.5-.444 0-.87-.18-1.184-.5a1.727 1.727 0 010-2.412 1.654 1.654 0 012.369 0zm-11.243.163c.364.484.447 1.128.218 1.691a1.665 1.665 0 01-2.188.923c-.855-.36-1.26-1.358-.907-2.228a1.68 1.68 0 011.33-1.038c.593-.08 1.183.169 1.547.652zm11.545-4.221c.368 0 .708.2.892.524.184.324.184.724 0 1.048a1.026 1.026 0 01-.892.524c-.568 0-1.03-.47-1.03-1.048 0-.579.462-1.048 1.03-1.048zm-14.358 0c.368 0 .707.2.891.524.184.324.184.724 0 1.048a1.026 1.026 0 01-.891.524c-.569 0-1.03-.47-1.03-1.048 0-.579.461-1.048 1.03-1.048zm10.031-1.475c.925 0 1.675.764 1.675 1.706s-.75 1.705-1.675 1.705-1.674-.763-1.674-1.705c0-.942.75-1.706 1.674-1.706zm-2.626-.684c.362-.082.653-.356.761-.718a1.062 1.062 0 00-.238-1.028 1.017 1.017 0 00-.996-.294c-.547.14-.881.7-.752 1.257.13.558.675.907 1.225.783zm0 16.876c.359-.087.644-.36.75-.72a1.062 1.062 0 00-.237-1.019 1.018 1.018 0 00-.985-.301 1.037 1.037 0 00-.762.717c-.108.361-.017.754.239 1.028.245.263.606.377.953.305l.043-.01zM17.19 3.5a.631.631 0 00.628-.64c0-.355-.279-.64-.628-.64a.631.631 0 00-.628.64c0 .355.28.64.628.64zm-10.38 0a.631.631 0 00.628-.64c0-.355-.28-.64-.628-.64a.631.631 0 00-.628.64c0 .355.279.64.628.64zm-5.182 7.852a.631.631 0 00-.628.64c0 .354.28.639.628.639a.63.63 0 00.627-.606l.001-.034a.62.62 0 00-.628-.64zm5.182 9.13a.631.631 0 00-.628.64c0 .355.279.64.628.64a.631.631 0 00.628-.64c0-.355-.28-.64-.628-.64zm10.38.018a.631.631 0 00-.628.64c0 .355.28.64.628.64a.631.631 0 00.628-.64c0-.355-.279-.64-.628-.64zm5.182-9.148a.631.631 0 00-.628.64c0 .354.279.639.628.639a.631.631 0 00.628-.64c0-.355-.28-.64-.628-.64zm-.384-4.992a.24.24 0 00.244-.249.24.24 0 00-.244-.249.24.24 0 00-.244.249c0 .142.122.249.244.249zM11.991.497a.24.24 0 00.245-.248A.24.24 0 0011.99 0a.24.24 0 00-.244.249c0 .133.108.236.223.247l.021.001zM2.011 6.36a.24.24 0 00.245-.249.24.24 0 00-.244-.249.24.24 0 00-.244.249.24.24 0 00.244.249zm0 11.263a.24.24 0 00-.243.248.24.24 0 00.244.249.24.24 0 00.244-.249.252.252 0 00-.244-.248zm19.995-.018a.24.24 0 00-.245.248.24.24 0 00.245.25.24.24 0 00.244-.25.252.252 0 00-.244-.248z"></path>
  </svg>`,
  // Moonshot/Kimi logo
  moonshot: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M21.846 0a1.923 1.923 0 110 3.846H20.15a.226.226 0 01-.227-.226V1.923C19.923.861 20.784 0 21.846 0z"></path>
    <path d="M11.065 11.199l7.257-7.2c.137-.136.06-.41-.116-.41H14.3a.164.164 0 00-.117.051l-7.82 7.756c-.122.12-.302.013-.302-.179V3.82c0-.127-.083-.23-.185-.23H3.186c-.103 0-.186.103-.186.23V19.77c0 .128.083.23.186.23h2.69c.103 0 .186-.102.186-.23v-3.25c0-.069.025-.135.069-.178l2.424-2.406a.158.158 0 01.205-.023l6.484 4.772a7.677 7.677 0 003.453 1.283c.108.012.2-.095.2-.23v-3.06c0-.117-.07-.212-.164-.227a5.028 5.028 0 01-2.027-.807l-5.613-4.064c-.117-.078-.132-.279-.028-.381z"></path>
  </svg>`,
  // Baichuan logo
  baichuan: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M7.333 2h-3.2l-2 4.333V17.8L0 22h5.2l2.028-4.2L7.333 2zm7.334 0h-5.2v20h5.2V2zM16.8 7.733H22V22h-5.2V7.733zM22 2h-5.2v4.133H22V2z"></path>
  </svg>`,
  // Doubao logo
  doubao: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M5.31 15.756c.172-3.75 1.883-5.999 2.549-6.739-3.26 2.058-5.425 5.658-6.358 8.308v1.12C1.501 21.513 4.226 24 7.59 24a6.59 6.59 0 002.2-.375c.353-.12.7-.248 1.039-.378.913-.899 1.65-1.91 2.243-2.992-4.877 2.431-7.974.072-7.763-4.5l.002.001z" fill-opacity=".5"></path>
    <path d="M22.57 10.283c-1.212-.901-4.109-2.404-7.397-2.8.295 3.792.093 8.766-2.1 12.773a12.782 12.782 0 01-2.244 2.992c3.764-1.448 6.746-3.457 8.596-5.219 2.82-2.683 3.353-5.178 3.361-6.66a2.737 2.737 0 00-.216-1.084v-.002zM14.303 1.867C12.955.7 11.248 0 9.39 0 7.532 0 5.883.677 4.545 1.807 2.791 3.29 1.627 5.557 1.5 8.125v9.201c.932-2.65 3.097-6.25 6.357-8.307.5-.318 1.025-.595 1.569-.829 1.883-.801 3.878-.932 5.746-.706-.222-2.83-.718-5.002-.87-5.617h.001z"></path>
    <path d="M17.305 4.961a199.47 199.47 0 01-1.08-1.094c-.202-.213-.398-.419-.586-.622l-1.333-1.378c.151.615.648 2.786.869 5.617 3.288.395 6.185 1.898 7.396 2.8-1.306-1.275-3.475-3.487-5.266-5.323z" fill-opacity=".5"></path>
  </svg>`,
  // Hunyuan logo
  hunyuan: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M12 0c6.627 0 12 5.373 12 12s-5.373 12-12 12S0 18.627 0 12 5.373 0 12 0zm1.652 1.123l-.01-.001c.533.097 1.023.233 1.41.404 6.084 2.683 7.396 9.214 1.601 14.338a3.781 3.781 0 01-5.337-.328 3.654 3.654 0 01-.884-3.044c-1.934.6-3.295 2.305-3.524 4.45-.204 1.912.324 4.044 2.056 5.634l.245.067C10.1 22.876 11.036 23 12 23c6.075 0 11-4.925 11-11 0-5.513-4.056-10.08-9.348-10.877zM2.748 6.21c-.178.269-.348.536-.51.803l-.235.394.078-.167A10.957 10.957 0 001 12c0 4.919 3.228 9.083 7.682 10.49l.214.065C3.523 18.528 2.84 14.149 6.47 8.68A2.234 2.234 0 102.748 6.21zm10.157-5.172c4.408 1.33 3.61 5.41 2.447 6.924-.86 1.117-2.922 1.46-3.708 2.238-.666.657-1.077 1.462-1.212 2.291A5.303 5.303 0 0112 12.258a5.672 5.672 0 001.404-11.169 10.51 10.51 0 00-.5-.052z"></path>
  </svg>`,
  // Minimax logo
  minimax: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M16.278 2c1.156 0 2.093.927 2.093 2.07v12.501a.74.74 0 00.744.709.74.74 0 00.743-.709V9.099a2.06 2.06 0 012.071-2.049A2.06 2.06 0 0124 9.1v6.561a.649.649 0 01-.652.645.649.649 0 01-.653-.645V9.1a.762.762 0 00-.766-.758.762.762 0 00-.766.758v7.472a2.037 2.037 0 01-2.048 2.026 2.037 2.037 0 01-2.048-2.026v-12.5a.785.785 0 00-.788-.753.785.785 0 00-.789.752l-.001 15.904A2.037 2.037 0 0113.441 22a2.037 2.037 0 01-2.048-2.026V18.04c0-.356.292-.645.652-.645.36 0 .652.289.652.645v1.934c0 .263.142.506.372.638.23.131.514.131.744 0a.734.734 0 00.372-.638V4.07c0-1.143.937-2.07 2.093-2.07zm-5.674 0c1.156 0 2.093.927 2.093 2.07v11.523a.648.648 0 01-.652.645.648.648 0 01-.652-.645V4.07a.785.785 0 00-.789-.78.785.785 0 00-.789.78v14.013a2.06 2.06 0 01-2.07 2.048 2.06 2.06 0 01-2.071-2.048V9.1a.762.762 0 00-.766-.758.762.762 0 00-.766.758v3.8a2.06 2.06 0 01-2.071 2.049A2.06 2.06 0 010 12.9v-1.378c0-.357.292-.646.652-.646.36 0 .653.29.653.646V12.9c0 .418.343.757.766.757s.766-.339.766-.757V9.099a2.06 2.06 0 012.07-2.048 2.06 2.06 0 012.071 2.048v8.984c0 .419.343.758.767.758.423 0 .766-.339.766-.758V4.07c0-1.143.937-2.07 2.093-2.07z"></path>
  </svg>`,
  // Spark logo
  spark: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M11.615 0l6.237 6.107c2.382 2.338 2.823 3.743 3.161 6.15-1.197-1.732-1.776-2.02-4.504-2.772C12.48 8.374 11.095 5.933 11.615 0z"></path>
    <path d="M9.32 2.122C4.771 6.367 2 9.182 2 13.08c0 5.76 4.288 9.788 9.745 9.918 5.457.13 9.441-5.284 9.095-8.403-.347-3.118-4.418-3.81-4.418-3.81 1.69 3.16-.13 8.098-4.894 8.098-5.154 0-6.8-6.02-4.2-9.008.82 1.617 1.879 2.563 2.674 3.273.717.64 1.219 1.09 1.136 1.664-.173 1.213-1.385.866-1.385.866.346.607 3.6 1.473 4.59-1.342.613-1.741-.423-2.789-1.714-4.096-1.632-1.651-3.672-3.717-3.31-8.118z"></path>
  </svg>`,
  // Stepfun logo
  stepfun: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M22.012 0h1.032v.927H24v.968h-.956V3.78h-1.032V1.896h-1.878v-.97h1.878V0zM2.6 12.371V1.87h.969v10.502h-.97zm10.423.66h10.95v.918h-6.208v9.579h-4.742V13.03zM5.629 3.333v12.356H0v4.51h10.386V8L20.859 8l-.003-4.668-15.227.001z"></path>
  </svg>`,
  // SiliconFlow logo
  siliconflow: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path clip-rule="evenodd" d="M22.956 6.521H12.522c-.577 0-1.044.468-1.044 1.044v3.13c0 .577-.466 1.044-1.043 1.044H1.044c-.577 0-1.044.467-1.044 1.044v4.174C0 17.533.467 18 1.044 18h10.434c.577 0 1.044-.467 1.044-1.043v-3.13c0-.578.466-1.044 1.043-1.044h9.391c.577 0 1.044-.467 1.044-1.044V7.565c0-.576-.467-1.044-1.044-1.044z"></path>
  </svg>`,
  // Yi logo
  yi: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M18.62 13.927c.611 0 1.107.505 1.107 1.128v5.817c0 .623-.496 1.128-1.108 1.128a1.118 1.118 0 01-1.108-1.128v-5.817c0-.623.496-1.128 1.108-1.128zM16.59 3.052a1.094 1.094 0 011.562-.129c.466.404.522 1.116.126 1.59l-5.938 7.111v9.147c0 .624-.496 1.129-1.108 1.129a1.118 1.118 0 01-1.108-1.129v-9.477l.003-.088.01-.087c.015-.232.102-.462.261-.654l6.192-7.413zM2.906 2.256a1.094 1.094 0 011.559.157l4.387 5.45a1.142 1.142 0 01-.155 1.587 1.094 1.094 0 01-1.559-.157l-4.387-5.45a1.144 1.144 0 01.06-1.498l.095-.09z"></path>
    <ellipse cx="20.146" cy="10.692" rx="1.354" ry="1.379"></ellipse>
  </svg>`,
  // vLLM logo
  vllm: `<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
    <path d="M0 4.973h9.324V23L0 4.973z"></path>
    <path d="M13.986 4.351L22.378 0l-6.216 23H9.324l4.662-18.649z"></path>
  </svg>`
}

/**
 * Get provider icon HTML
 * @param {string} provider - Provider name
 * @returns {string} HTML for provider icon
 */
function getProviderIcon(provider) {
  const normalizedProvider = provider?.toLowerCase() || 'custom'
  const info = PROVIDER_ICONS[normalizedProvider] || PROVIDER_ICONS.custom
  const hasSvg = SVG_ICONS[normalizedProvider]

  if (hasSvg) {
    return `<div class="provider-icon" style="background-color: ${info.color};">${hasSvg}</div>`
  }

  return `<div class="provider-icon" style="background-color: ${info.color};"><span class="provider-label">${info.label}</span></div>`
}

/**
 * Get provider brand color
 * @param {string} provider - Provider name
 * @returns {string} Hex color code
 */
function getProviderColor(provider) {
  const normalizedProvider = provider?.toLowerCase() || 'custom'
  const info = PROVIDER_ICONS[normalizedProvider] || PROVIDER_ICONS.custom
  return info.color
}

/**
 * Get badge text for model identity (e.g., "G4" for gpt-4, "C3" for claude-3)
 * @param {Object} config - Model config object
 * @returns {string} Badge text (2 chars)
 */
function getBadgeText(config) {
  const provider = config.provider?.toLowerCase() || ''
  const modelId = config.model_id || ''
  
  // Get first letter of provider (uppercase)
  let firstChar = provider.charAt(0).toUpperCase()
  
  // Special cases for provider abbreviations
  if (provider === 'openai') firstChar = 'G'  // GPT
  if (provider === 'anthropic') firstChar = 'C'  // Claude
  if (provider === 'ollama') firstChar = 'L'  // Llama/Local
  if (provider === 'deepseek') firstChar = 'D'
  
  // Extract version number from model_id
  const versionMatch = modelId.match(/(\d+(?:\.\d+)?)/)
  const version = versionMatch ? versionMatch[1].split('.')[0] : ''
  
  return version ? `${firstChar}${version}` : firstChar
}

// Debounce timer for API key input
let fetchDebounceTimer = null

// Event listener references for cleanup
let eventListeners = []

// ========== Provider Options HTML ==========
const PROVIDER_OPTIONS_HTML = `
  <option value="" disabled selected data-i18n="model.providerPlaceholder">Select provider</option>
  <option value="anthropic" data-i18n="model.providers.anthropic">Anthropic</option>
  <option value="baichuan" data-i18n="model.providers.baichuan">Baichuan</option>
  <option value="cohere" data-i18n="model.providers.cohere">Cohere</option>
  <option value="deepseek" data-i18n="model.providers.deepseek">DeepSeek</option>
  <option value="doubao" data-i18n="model.providers.doubao">Doubao</option>
  <option value="google" data-i18n="model.providers.google">Google Gemini</option>
  <option value="groq" data-i18n="model.providers.groq">Groq</option>
  <option value="hunyuan" data-i18n="model.providers.hunyuan">Hunyuan</option>
  <option value="minimax" data-i18n="model.providers.minimax">MiniMax</option>
  <option value="mistral" data-i18n="model.providers.mistral">Mistral AI</option>
  <option value="moonshot" data-i18n="model.providers.moonshot">Moonshot</option>
  <option value="ollama" data-i18n="model.providers.ollama">Ollama</option>
  <option value="openai" data-i18n="model.providers.openai">OpenAI</option>
  <option value="qwen" data-i18n="model.providers.qwen">Qwen</option>
  <option value="siliconflow" data-i18n="model.providers.siliconflow">SiliconFlow</option>
  <option value="spark" data-i18n="model.providers.spark">Spark</option>
  <option value="stepfun" data-i18n="model.providers.stepfun">StepFun</option>
  <option value="vllm" data-i18n="model.providers.vllm">vLLM</option>
  <option value="yi" data-i18n="model.providers.yi">Yi</option>
  <option value="zhipu" data-i18n="model.providers.zhipu">ZhipuAI</option>
  <option value="custom" data-i18n="model.providers.custom">Custom</option>
`

// ========== HTML Templates ==========

const PAGE_HTML = `
<!-- List View -->
<div class="model-list-page">
  <div id="modelConfigsList">
    <div class="channel-loading" data-i18n="model.loading">Loading...</div>
  </div>
</div>

<!-- Form View (Full Page) -->
<div class="model-form-page" style="display: none;">
  <!-- Form Title Area -->
  <div class="model-form-title-area">
    <h1 class="model-form-main-title" data-i18n="model.formTitle">Model Configuration</h1>
    <p class="model-form-main-subtitle" data-i18n="model.formSubtitle">Deploy and tune your cognitive infrastructure</p>
  </div>

  <!-- Left-Right Split Layout -->
  <div class="model-form-layout">
    <!-- Left: Core Parameters Form (~70%) -->
    <div class="model-form-main">
      <div class="form-section-card">
        <!-- Section Header with icon -->
        <div class="form-section-header">
          <div class="icon-box">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.32 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </div>
          <div>
            <h2 class="form-section-title" data-i18n="model.coreParams">Core Parameters</h2>
            <p class="form-section-subtitle" data-i18n="model.coreParamsDesc">Define identity and access protocols</p>
          </div>
        </div>

        <!-- Form Fields -->
        <div class="form-fields">
          <!-- Row 1: Display Name + Model ID (side by side) -->
          <div class="form-row form-row-2col">
            <div class="form-field">
              <label class="form-label" data-i18n="model.displayName">MODEL DISPLAY NAME</label>
              <input type="text" id="modelDisplayName" class="form-input" data-i18n-placeholder="model.displayNamePlaceholder" placeholder="e.g. Aurora-Pro-V1">
            </div>
            <div class="form-field">
              <label class="form-label" data-i18n="model.name">MODEL ID <span class="required">*</span></label>
              <input type="text" id="modelName" class="form-input" data-i18n-placeholder="model.namePlaceholder" placeholder="e.g. gpt-4-main" required>
            </div>
          </div>

          <!-- Row 2: Infrastructure Provider (full width) -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label" data-i18n="model.provider">INFRASTRUCTURE PROVIDER <span class="required">*</span></label>
              <select id="modelProvider" class="form-input form-select">
                ${PROVIDER_OPTIONS_HTML}
              </select>
            </div>
          </div>

          <!-- Row 3: Model ID Selection (full width) -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label" data-i18n="model.modelId">MODEL ID <span class="required">*</span></label>
              <select id="modelModelId" class="form-input form-select">
                <option value="" disabled selected data-i18n="model.modelIdPlaceholder">Select or enter model ID</option>
              </select>
              <input type="text" id="modelModelIdCustom" class="form-input" style="display: none; margin-top: 8px;" data-i18n-placeholder="model.modelIdCustomPlaceholder" placeholder="Enter custom model ID">
            </div>
          </div>

          <!-- Row 4: API Endpoint URL (full width, with link icon) -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label" data-i18n="model.baseUrl">API ENDPOINT URL</label>
              <div class="input-with-icon">
                <svg class="input-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                <input type="text" id="modelBaseUrl" class="form-input" data-i18n-placeholder="model.baseUrlPlaceholder" placeholder="https://api.provider.com/v1/completions">
              </div>
            </div>
          </div>

          <!-- Row 5: API Authentication Key (full width, with key icon + eye toggle) -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label" data-i18n="model.apiKey">API AUTHENTICATION KEY <span class="required api-key-required" id="apiKeyRequired" style="display:none">*</span></label>
              <div class="input-with-icon">
                <svg class="input-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
                <input type="password" id="modelApiKey" class="form-input" data-i18n-placeholder="model.apiKeyPlaceholder" placeholder="••••••••••••••••••••••">
                <button type="button" class="input-icon-btn" id="toggleApiKeyVisibility" title="${t('model.toggleApiKeyVisibility') || 'Toggle visibility'}">
                  ${SVG_ICONS.eyeClosed}
                </button>
              </div>
            </div>
          </div>

          <div class="form-divider"></div>

          <!-- Row 6: API Type + Context Window -->
          <div class="form-row form-row-2col">
            <div class="form-field">
              <label class="form-label" data-i18n="model.apiType">API TYPE</label>
              <select id="modelApiType" class="form-input form-select">
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google">Google</option>
              </select>
            </div>
            <div class="form-field">
              <label class="form-label" data-i18n="model.contextWindow">CONTEXT WINDOW</label>
              <input type="number" id="modelContextWindow" class="form-input" value="128000">
            </div>
          </div>

          <!-- Row 7: Max Tokens + Is Active -->
          <div class="form-row form-row-2col">
            <div class="form-field">
              <label class="form-label" data-i18n="model.maxTokens">MAX TOKENS</label>
              <input type="number" id="modelMaxTokens" class="form-input" value="4096">
            </div>
            <div class="form-field form-field-toggle-inline">
              <label class="form-label" data-i18n="model.isActive">ACTIVE</label>
              <label class="toggle-switch">
                <input type="checkbox" id="modelIsActive" checked>
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- Hidden fields for temperature and description (preserve default values) -->
          <input type="hidden" id="modelTemperature" value="0.7">
          <input type="hidden" id="modelDescription" value="">
        </div>

        <!-- Form Actions -->
        <div class="form-actions">
          <button class="btn-cancel" id="btnFormCancel" data-i18n="model.cancel">CANCEL</button>
          <button class="btn-primary btn-save-config" id="btnFormSave" data-i18n="model.saveConfig">Save Model Configuration →</button>
        </div>
      </div>
    </div>

    <!-- Right: Sidebar (~30%, 320px) -->
    <div class="model-form-sidebar">
      <!-- Token Management Card (dark) - Mockup Style -->
      <div class="sidebar-card sidebar-card-dark">
        <div class="sidebar-card-header">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          <h3 data-i18n="model.tokenManagement">Token Management</h3>
        </div>

        <!-- Quota Limit -->
        <div class="sidebar-quota">
          <div class="sidebar-quota-header">
            <span class="sidebar-label" data-i18n="model.quotaLimit">QUOTA LIMIT</span>
            <span class="sidebar-quota-value" data-i18n="model.quotaValue">2.5M MONTHLY</span>
          </div>
          <div class="sidebar-progress">
            <div class="sidebar-progress-bar" style="width: 35%"></div>
          </div>
        </div>

        <!-- Usage Threshold Alert -->
        <div class="sidebar-threshold">
          <span class="sidebar-label" data-i18n="model.usageThreshold">USAGE THRESHOLD ALERT</span>
          <div class="sidebar-threshold-row">
            <span class="sidebar-threshold-text" data-i18n="model.usageThresholdDesc">Notify at 80% usage</span>
            <label class="toggle-switch toggle-switch-small">
              <input type="checkbox" checked disabled>
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>

        <!-- Config Tip -->
        <div class="sidebar-tip" data-i18n="model.configTip">
          Configuration changes take effect across all active agent nodes immediately upon saving.
        </div>
      </div>

      <!-- Live Preview Card -->
      <div class="sidebar-card sidebar-card-preview">
        <h4 class="preview-title" data-i18n="model.livePreview">LIVE PREVIEW</h4>
        <div class="preview-skeleton">
          <div class="skeleton-row skeleton-avatar"></div>
          <div class="skeleton-row skeleton-line" style="width: 80%"></div>
          <div class="skeleton-row skeleton-line" style="width: 60%"></div>
          <div class="skeleton-row skeleton-block"></div>
        </div>
        <p class="preview-desc"><em data-i18n="model.livePreviewDesc">Interface simulation based on selected provider latency and response structure.</em></p>
      </div>
    </div>
  </div>
</div>

<!-- Delete Confirm Dialog (Keep as modal) -->
<div id="deleteDialog" class="confirm-dialog hidden">
  <div class="confirm-content">
    <h3 data-i18n="model.deleteConfirmTitle">Confirm Delete</h3>
    <p id="deleteMessage" data-i18n="model.deleteConfirmMessage">Are you sure you want to delete this model config?</p>
    <div class="confirm-buttons">
      <button class="btn-cancel" id="btnCancelDelete" data-i18n="model.cancel">Cancel</button>
      <button class="btn-confirm" id="btnConfirmDelete" data-i18n="model.delete">Delete</button>
    </div>
  </div>
</div>
`

// ========== API Functions ==========

async function fetchModelConfigs() {
  try {
    const res = await fetch('/api/model-configs')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    return Array.isArray(data) ? data : (data.configs || data.model_configs || [])
  } catch (error) {
    console.error('[ModelsPage] Failed to fetch:', error)
    return []
  }
}

async function createModelConfig(data) {
  const res = await fetch('/api/model-configs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

async function updateModelConfig(id, data) {
  const res = await fetch(`/api/model-configs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

async function deleteModelConfigApi(id) {
  const res = await fetch(`/api/model-configs/${id}`, {
    method: 'DELETE'
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return true
}

// ========== Provider Data ==========

async function loadProviderData() {
  try {
    const res = await fetch('/api/providers')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()

    // Build PROVIDER_PRESETS and PROVIDER_MODELS from API response
    for (const [name, info] of Object.entries(data)) {
      PROVIDER_PRESETS[name] = {
        base_url: info.base_url || '',
        api_type: info.api_type || 'openai',
      }
      PROVIDER_MODELS[name] = info.models || []
    }
  } catch (error) {
    console.error('[ModelsPage] Failed to load provider data:', error)
    // Fallback: leave empty, static options will work from select
  }

  // Restore cached models from localStorage (with 24h TTL)
  try {
    const cached = JSON.parse(localStorage.getItem('atlasclaw_fetched_models') || '{}')
    const now = Date.now()
    const TTL = 24 * 60 * 60 * 1000 // 24 hours
    for (const [prov, entry] of Object.entries(cached)) {
      if (entry.timestamp && (now - entry.timestamp) < TTL && entry.models?.length) {
        // Merge cached models with static preset
        const staticModels = PROVIDER_MODELS[prov] || []
        PROVIDER_MODELS[prov] = [...new Set([...entry.models, ...staticModels])].sort((a, b) => a.localeCompare(b))
      }
    }
  } catch (e) {
    // Ignore storage errors
  }
}

// ========== Fetch Models from Provider API ==========

async function fetchModelsFromProvider(silent = false) {
  const provider = containerRef?.querySelector('#modelProvider')?.value
  const apiKey = containerRef?.querySelector('#modelApiKey')?.value?.trim()
  const baseUrl = containerRef?.querySelector('#modelBaseUrl')?.value?.trim()

  if (!provider || provider === 'custom') {
    if (!silent) showToast(t('model.providerRequired'), 'error')
    return
  }

  // Show loading state on model select
  const modelSelect = containerRef?.querySelector('#modelModelId')
  if (modelSelect) {
    modelSelect.disabled = true
    // Add loading option at the beginning
    const loadingOption = document.createElement('option')
    loadingOption.value = '__loading__'
    loadingOption.textContent = t('model.loadingModels')
    loadingOption.disabled = true
    modelSelect.insertBefore(loadingOption, modelSelect.firstChild)
    modelSelect.value = '__loading__'
  }

  try {
    const res = await fetch('/api/providers/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, base_url: baseUrl, api_key: apiKey })
    })

    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()

    // Get current model ID to preserve selection
    const currentModelId = containerRef?.querySelector('#modelModelIdCustom')?.value?.trim() ||
      (modelSelect?.value && modelSelect.value !== '__loading__' && modelSelect.value !== '__custom__' ? modelSelect.value : '')

    if (data.models && data.models.length > 0) {
      // MERGE with static preset list
      const staticModels = PROVIDER_MODELS[provider] || []
      const allModels = [...new Set([...data.models, ...staticModels])]
      allModels.sort((a, b) => a.localeCompare(b))

      // Update cache
      PROVIDER_MODELS[provider] = allModels

      // Save to localStorage
      try {
        const cached = JSON.parse(localStorage.getItem('atlasclaw_fetched_models') || '{}')
        cached[provider] = { models: allModels, timestamp: Date.now() }
        localStorage.setItem('atlasclaw_fetched_models', JSON.stringify(cached))
      } catch (e) { /* ignore storage errors */ }

      // Rebuild dropdown
      updateModelIdOptions(provider, currentModelId)

      if (!silent) {
        showToast(t('model.fetchSuccess'), 'success')
      }
    } else if (!silent) {
      showToast(t('model.noModelsFound'), 'warning')
    }
  } catch (error) {
    console.error('[ModelsPage] Failed to fetch models:', error)
    if (!silent) {
      showToast(error.message, 'error')
    }
    // Silent mode: just restore the dropdown without fetched models
    const currentModelId = containerRef?.querySelector('#modelModelIdCustom')?.value?.trim() || ''
    updateModelIdOptions(provider, currentModelId)
  } finally {
    if (modelSelect) {
      modelSelect.disabled = false
      // Remove loading option if still present
      const loadingOption = modelSelect.querySelector('option[value="__loading__"]')
      if (loadingOption) loadingOption.remove()
    }
  }
}

// ========== Model ID Dropdown Logic ==========

function updateModelIdOptions(provider, currentValue) {
  const select = containerRef?.querySelector('#modelModelId')
  const customInput = containerRef?.querySelector('#modelModelIdCustom')
  if (!select) return

  const models = PROVIDER_MODELS[provider] || []

  // Build options
  let html = `<option value="" disabled>${t('model.modelIdPlaceholder')}</option>`
  models.forEach(m => {
    html += `<option value="${m}"${m === currentValue ? ' selected' : ''}>${m}</option>`
  })
  html += `<option value="__custom__">${t('model.customModelId')}</option>`
  select.innerHTML = html

  // If currentValue exists but not in the list, select custom and show input
  if (currentValue && !models.includes(currentValue) && currentValue !== '__custom__') {
    select.value = '__custom__'
    customInput.style.display = 'block'
    customInput.value = currentValue
  } else if (currentValue && models.includes(currentValue)) {
    select.value = currentValue
    customInput.style.display = 'none'
    customInput.value = ''
  } else {
    select.value = ''
    customInput.style.display = 'none'
    customInput.value = ''
  }
}

// ========== API Key Required ==========

function updateApiKeyRequired(provider) {
  const required = provider && !NO_API_KEY_PROVIDERS.includes(provider)
  const asterisk = containerRef?.querySelector('#apiKeyRequired')
  if (asterisk) {
    asterisk.style.display = required ? 'inline' : 'none'
  }
}

// ========== UI Rendering ==========

function renderModelConfigs(configs) {
  modelConfigs = configs || []
  const container = containerRef?.querySelector('#modelConfigsList')
  if (!container) return

  if (!configs || configs.length === 0) {
    container.innerHTML = `
      <div class="model-page-header">
        <div class="model-page-header-left">
          <p class="model-page-subtitle">${t('model.subtitle') || 'Orchestrate your model ecosystem, monitor real-time performance, and configure intelligent routing policies across distributed endpoints.'}</p>
        </div>
        <div class="model-page-header-actions">
          <button class="btn-primary" id="btnCreateModelInPage">${t('model.deployModel') || '+ Deploy New Model'}</button>
        </div>
      </div>
      <div class="connections-empty" data-i18n="model.noModels">${t('model.noModels')}</div>
    `
    // Bind create button event
    const btnCreate = container.querySelector('#btnCreateModelInPage')
    if (btnCreate) {
      btnCreate.addEventListener('click', () => openCreateModal())
    }
    return
  }

  // Count active models
  const activeCount = configs.filter(c => c.is_active).length

  // Render table row for each model
  const renderModelRow = (config) => {
    return `
    <tr class="model-row" data-id="${config.id}">
      <td class="model-identity-cell">
        <span class="model-badge">${getBadgeText(config)}</span>
        <div class="model-identity-info">
          <span class="model-identity-name">${config.display_name || config.name}</span>
          <span class="model-identity-sub">${config.model_id}</span>
        </div>
      </td>
      <td>
        <span class="status-dot ${config.is_active ? 'status-active' : 'status-inactive'}"></span>
        ${config.is_active ? (t('model.active') || 'Active') : (t('model.inactive') || 'Inactive')}
      </td>
      <td>${t('model.providers.' + config.provider) || config.provider}</td>
      <td>${(config.context_window || 128000).toLocaleString()}</td>
      <td>${(config.max_tokens || 4096).toLocaleString()}</td>
      <td class="model-actions-cell">
        <a class="action-link action-configure" data-action="edit" data-id="${config.id}">${t('model.configure') || 'Configure'}</a>
        <button class="action-btn action-delete" data-action="delete" data-id="${config.id}" title="${t('model.delete') || 'Delete'}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        </button>
      </td>
    </tr>`
  }

  // Build full page HTML
  container.innerHTML = `
    <!-- Page Header -->
    <div class="model-page-header">
      <div class="model-page-header-left">
        <h1 class="model-page-title">${t('model.pageTitle') || 'Model Management'}</h1>
        <p class="model-page-subtitle">${t('model.subtitle') || 'Orchestrate your model ecosystem, monitor real-time performance, and configure intelligent routing policies across distributed endpoints.'}</p>
      </div>
      <div class="model-page-header-actions">
        <button class="btn-primary" id="btnCreateModelInPage">${t('model.deployModel') || '+ Deploy New Model'}</button>
      </div>
    </div>

    <!-- Stats Dashboard -->
    <div class="stats-dashboard">
      <!-- 左侧大卡片：延迟 -->
      <div class="stats-card stats-card-latency">
        <div class="stats-card-header">
          <span class="stats-card-title">${t('model.avgGlobalLatency') || 'AVG GLOBAL LATENCY'}</span>
          <span class="stats-trend">~12.4%</span>
        </div>
        <div class="stats-big-number">142<span class="stats-unit">ms</span></div>
        <div class="stats-chart">
          <div class="chart-bar" style="height: 30%"></div>
          <div class="chart-bar" style="height: 35%"></div>
          <div class="chart-bar" style="height: 40%"></div>
          <div class="chart-bar" style="height: 50%"></div>
          <div class="chart-bar" style="height: 65%"></div>
          <div class="chart-bar" style="height: 55%"></div>
          <div class="chart-bar" style="height: 75%"></div>
          <div class="chart-bar" style="height: 70%"></div>
        </div>
      </div>
      
      <!-- 中间卡片：集群 -->
      <div class="stats-card stats-card-clusters">
        <div class="stats-icon-box stats-icon-box-light">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z"/>
            <path d="M12 15l-3-3a22 22 0 012-3.95A12.88 12.88 0 0122 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 01-4 2z"/>
            <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/>
            <path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>
          </svg>
        </div>
        <span class="stats-card-title">${t('model.activeClusters') || 'ACTIVE CLUSTERS'}</span>
        <div class="stats-big-number">24</div>
        <span class="stats-card-footer">${t('model.regionsActive') || '9 Regions Active'}</span>
      </div>
      
      <!-- 右侧卡片：请求量 -->
      <div class="stats-card stats-card-requests">
        <div class="stats-icon-box stats-icon-box-purple">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <ellipse cx="12" cy="5" rx="9" ry="3"/>
            <path d="M21 12c0 1.66-4.03 3-9 3s-9-1.34-9-3"/>
            <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
          </svg>
        </div>
        <span class="stats-card-title">${t('model.requests24h') || 'REQUESTS (24H)'}</span>
        <div class="stats-big-number">1.2M</div>
        <div class="stats-card-footer"><span class="stats-success-dot"></span> ${t('model.successRate') || '99.98% Success'}</div>
      </div>
    </div>

    <!-- Active Models Ecosystem Table -->
    <div class="model-ecosystem">
      <div class="ecosystem-header">
        <h2 class="ecosystem-title">${t('model.activeEcosystem') || 'Active Models Ecosystem'}</h2>
        <div class="ecosystem-badges">
          <span class="badge badge-neutral">${t('model.totalModels', { count: configs.length }) || `Total: ${configs.length} Models`}</span>
          <span class="badge badge-success">${t('model.allHealthy') || 'All Systems Healthy'}</span>
        </div>
      </div>
      <table class="model-table">
        <thead>
          <tr>
            <th>${t('model.tableModelIdentity') || 'MODEL IDENTITY'}</th>
            <th>${t('model.tableStatus') || 'STATUS'}</th>
            <th>${t('model.tableProvider') || 'PROVIDER'}</th>
            <th>${t('model.tableContextWindow') || 'CONTEXT WINDOW'}</th>
            <th>${t('model.tableMaxTokens') || 'MAX TOKENS'}</th>
            <th>${t('model.tableActions') || 'ACTIONS'}</th>
          </tr>
        </thead>
        <tbody>
          ${configs.map(config => renderModelRow(config)).join('')}
        </tbody>
      </table>
    </div>

    <!-- Bottom Panels -->
    <div class="model-bottom-panels">
      <!-- Left: Dynamic Traffic Routing -->
      <div class="panel-card">
        <div class="panel-card-header">
          <div>
            <h3>${t('model.trafficRouting') || 'Dynamic Traffic Routing'}</h3>
            <p class="panel-subtitle">${t('model.trafficRoutingDesc') || 'Manage how requests are distributed across your cluster'}</p>
          </div>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="18" cy="18" r="3" fill="var(--color-primary)" stroke="none"/>
            <circle cx="6" cy="6" r="3" fill="var(--color-primary)" stroke="none"/>
            <path d="M13 6h3a2 2 0 012 2v7"/>
            <path d="M11 18H8a2 2 0 01-2-2V9"/>
          </svg>
        </div>
        <div class="routing-strategies">
          <button class="strategy-card strategy-active" data-strategy="latency-first">
            <span class="strategy-label">${t('model.strategyLatencyFirst') || 'LATENCY-FIRST'}</span>
            <span class="strategy-desc">${t('model.strategyLatencyFirstDesc') || 'Routes to fastest available node'}</span>
          </button>
          <button class="strategy-card" data-strategy="cost-first">
            <span class="strategy-label">${t('model.strategyCostFirst') || 'COST-FIRST'}</span>
            <span class="strategy-desc">${t('model.strategyCostFirstDesc') || 'Prioritizes cheapest model tiers'}</span>
          </button>
          <button class="strategy-card" data-strategy="balanced">
            <span class="strategy-label">${t('model.strategyBalanced') || 'BALANCED'}</span>
            <span class="strategy-desc">${t('model.strategyBalancedDesc') || 'Weighted distribution 50/50'}</span>
          </button>
        </div>
      </div>
      
      <!-- Right: Token Economy -->
      <div class="panel-card panel-card-dark">
        <h3 class="token-title">${t('model.tokenEconomy') || 'Token Economy'}</h3>
        <div class="token-label">${t('model.projectedMonthlySpend') || 'PROJECTED MONTHLY SPEND'}</div>
        <div class="token-value">$12,450.00</div>
        <div class="token-progress"><div class="token-progress-bar" style="width: 75%"></div></div>
        <div class="token-meta">${t('model.budgetUsed') || '75% of $16,000 budget used'}</div>
        <div class="token-divider"></div>
        <div class="token-spenders-title">${t('model.topSpendersByApp') || 'TOP SPENDERS BY APP'}</div>
        <div class="token-spender"><span>Customer Support Bot</span><span>$4.2k</span></div>
        <div class="token-spender"><span>Market Intelligence</span><span>$3.8k</span></div>
        <div class="token-spender"><span>Creative Suite AI</span><span>$1.1k</span></div>
        <button class="token-manage-btn" disabled>${t('model.manageBilling') || 'Manage Billing & Quotas'}</button>
      </div>
    </div>
  `

  // Bind events
  bindListEvents(container)
}

/**
 * Bind events for list view using event delegation
 */
function bindListEvents(container) {
  // Create button
  const btnCreate = container.querySelector('#btnCreateModelInPage')
  if (btnCreate) {
    btnCreate.addEventListener('click', () => openCreateModal())
  }

  // Table row actions (event delegation)
  const table = container.querySelector('.model-table')
  if (table) {
    table.addEventListener('click', (e) => {
      const target = e.target.closest('[data-action]')
      if (!target) return

      const action = target.dataset.action
      const configId = target.dataset.id

      if (action === 'edit' && configId) {
        e.preventDefault()
        handleModelAction('edit', configId)
      } else if (action === 'delete' && configId) {
        handleModelAction('delete', configId)
      }
    })
  }

  // Routing strategy toggle (mock)
  const routingPanel = container.querySelector('.routing-strategies')
  if (routingPanel) {
    routingPanel.addEventListener('click', (e) => {
      const btn = e.target.closest('.strategy-card')
      if (!btn) return
      routingPanel.querySelectorAll('.strategy-card').forEach(c => c.classList.remove('strategy-active'))
      btn.classList.add('strategy-active')
    })
  }
}

// ========== View Switching ==========

function showFormView(isEdit = false, configData = null) {
  const listPage = containerRef?.querySelector('.model-list-page')
  const formPage = containerRef?.querySelector('.model-form-page')

  if (!listPage || !formPage) return

  // Hide global header when entering form view
  const appHeader = document.getElementById('app-header')
  if (appHeader) {
    appHeader.style.display = 'none'
  }

  // Hide list, show form
  listPage.style.display = 'none'
  formPage.style.display = 'block'

  // Reset or fill form
  if (isEdit && configData) {
    fillFormData(configData)
  } else {
    resetFormData()
  }

  // Update translations
  updateContainerTranslations(formPage)
}

function showListView() {
  const listPage = containerRef?.querySelector('.model-list-page')
  const formPage = containerRef?.querySelector('.model-form-page')

  if (!listPage || !formPage) return

  // Show global header when returning to list view
  const appHeader = document.getElementById('app-header')
  if (appHeader) {
    appHeader.style.display = ''
  }

  // Hide form, show list
  formPage.style.display = 'none'
  listPage.style.display = 'block'

  // Reset editing state
  editingModelId = null

  // Refresh list
  loadModelConfigs()
}

function resetFormData() {
  editingModelId = null

  // Reset all form fields
  const fields = {
    '#modelName': '',
    '#modelDisplayName': '',
    '#modelProvider': '',
    '#modelApiKey': '',
    '#modelBaseUrl': '',
    '#modelApiType': 'openai',
    '#modelContextWindow': 128000,
    '#modelMaxTokens': 4096,
    '#modelTemperature': 0.7,
    '#modelWeight': 100,
    '#modelDescription': ''
  }

  for (const [selector, value] of Object.entries(fields)) {
    const el = containerRef?.querySelector(selector)
    if (el) el.value = value
  }

  // Reset is_active checkbox
  const isActiveCheckbox = containerRef?.querySelector('#modelIsActive')
  if (isActiveCheckbox) {
    isActiveCheckbox.checked = true
  }

  // Reset model ID dropdown
  const modelSelect = containerRef?.querySelector('#modelModelId')
  if (modelSelect) {
    modelSelect.innerHTML = `<option value="" disabled selected>${t('model.modelIdPlaceholder')}</option>`
  }
  const customInput = containerRef?.querySelector('#modelModelIdCustom')
  if (customInput) {
    customInput.style.display = 'none'
    customInput.value = ''
  }

  // Reset base URL auto-fill tracking
  const baseUrlInput = containerRef?.querySelector('#modelBaseUrl')
  if (baseUrlInput) baseUrlInput.dataset.autoFilled = 'false'

  // Hide API Key required indicator
  updateApiKeyRequired('')
}

function fillFormData(config) {
  editingModelId = config.id

  // Fill basic fields
  const setValue = (selector, value) => {
    const el = containerRef?.querySelector(selector)
    if (el) el.value = value ?? ''
  }

  setValue('#modelName', config.name)
  setValue('#modelDisplayName', config.display_name)
  setValue('#modelProvider', config.provider)
  setValue('#modelApiKey', config.api_key)
  setValue('#modelBaseUrl', config.base_url)
  setValue('#modelApiType', config.api_type || 'openai')
  setValue('#modelContextWindow', config.context_window || 128000)
  setValue('#modelMaxTokens', config.max_tokens || 4096)
  setValue('#modelTemperature', config.temperature ?? 0.7)
  setValue('#modelWeight', config.weight || 100)
  setValue('#modelDescription', config.description)

  // Set is_active checkbox
  const isActiveCheckbox = containerRef?.querySelector('#modelIsActive')
  if (isActiveCheckbox) {
    isActiveCheckbox.checked = config.is_active !== false
  }

  // Update API Key required indicator
  updateApiKeyRequired(config.provider || '')

  // Update model ID dropdown based on provider
  updateModelIdOptions(config.provider || '', config.model_id || '')

  // Mark base_url as not auto-filled when editing
  const baseUrlInput = containerRef?.querySelector('#modelBaseUrl')
  if (baseUrlInput) baseUrlInput.dataset.autoFilled = 'false'
}

// ========== Form Actions ==========

function openCreateModal() {
  showFormView(false)
}

async function openEditModal(id) {
  const config = modelConfigs.find(c => c.id === id)
  if (!config) {
    showToast(t('model.notFound'), 'error')
    return
  }
  showFormView(true, config)
}

async function saveModelConfig() {
  const modelIdSelect = containerRef?.querySelector('#modelModelId')
  const modelIdCustom = containerRef?.querySelector('#modelModelIdCustom')

  const data = {
    name: containerRef?.querySelector('#modelName')?.value?.trim(),
    display_name: containerRef?.querySelector('#modelDisplayName')?.value?.trim() || null,
    provider: containerRef?.querySelector('#modelProvider')?.value?.trim(),
    model_id: (modelIdSelect?.value === '__custom__'
      ? modelIdCustom?.value?.trim()
      : modelIdSelect?.value?.trim()),
    base_url: containerRef?.querySelector('#modelBaseUrl')?.value?.trim() || null,
    api_key: containerRef?.querySelector('#modelApiKey')?.value?.trim() || null,
    api_type: containerRef?.querySelector('#modelApiType')?.value || 'openai',
    context_window: parseInt(containerRef?.querySelector('#modelContextWindow')?.value) || 128000,
    max_tokens: parseInt(containerRef?.querySelector('#modelMaxTokens')?.value) || 4096,
    temperature: parseFloat(containerRef?.querySelector('#modelTemperature')?.value) || 0.7,
    weight: parseInt(containerRef?.querySelector('#modelWeight')?.value) || 100,
    description: containerRef?.querySelector('#modelDescription')?.value?.trim() || null,
    is_active: containerRef?.querySelector('#modelIsActive')?.checked ?? true
  }

  // Validation
  if (!data.name) {
    showToast(t('model.nameRequired'), 'error')
    return
  }
  if (!data.provider) {
    showToast(t('model.providerRequired'), 'error')
    return
  }
  if (!data.model_id || data.model_id === '__custom__' || data.model_id === '__loading__') {
    showToast(t('model.modelIdRequired'), 'error')
    return
  }
  // API Key validation - required for cloud providers
  if (!NO_API_KEY_PROVIDERS.includes(data.provider) && !data.api_key) {
    showToast(t('model.apiKeyRequired') || 'API Key is required for this provider', 'error')
    return
  }

  try {
    if (editingModelId) {
      await updateModelConfig(editingModelId, data)
      showToast(t('model.updateSuccess'), 'success')
    } else {
      await createModelConfig(data)
      showToast(t('model.createSuccess'), 'success')
    }
    showListView()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== Delete Dialog ==========

function showDeleteConfirm(id) {
  pendingDeleteId = id
  containerRef?.querySelector('#deleteDialog')?.classList.remove('hidden')
}

function hideDeleteDialog() {
  pendingDeleteId = null
  containerRef?.querySelector('#deleteDialog')?.classList.add('hidden')
}

async function confirmDelete() {
  if (!pendingDeleteId) return

  try {
    await deleteModelConfigApi(pendingDeleteId)
    showToast(t('model.deleteSuccess'), 'success')
    hideDeleteDialog()
    await loadModelConfigs()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== Action Handlers ==========

async function handleModelAction(action, configId, isActive) {
  switch (action) {
    case 'edit':
      await openEditModal(configId)
      break
    case 'toggle':
      await toggleModelStatus(configId, isActive)
      break
    case 'delete':
      showDeleteConfirm(configId)
      break
  }
}

async function toggleModelStatus(id, currentStatus) {
  try {
    const config = modelConfigs.find(c => c.id === id)
    if (!config) return

    await updateModelConfig(id, { ...config, is_active: !currentStatus })
    showToast(!currentStatus ? t('model.activated') : t('model.deactivated'), 'success')
    await loadModelConfigs()
  } catch (error) {
    showToast(error.message, 'error')
  }
}

// ========== API Key Toggle ==========

function setupApiKeyToggle() {
  const toggleBtn = containerRef?.querySelector('#toggleApiKeyVisibility')
  const apiKeyInput = containerRef?.querySelector('#modelApiKey')

  if (toggleBtn && apiKeyInput) {
    const handler = () => {
      const isPassword = apiKeyInput.type === 'password'
      apiKeyInput.type = isPassword ? 'text' : 'password'
      toggleBtn.innerHTML = isPassword ? SVG_ICONS.eyeOpen : SVG_ICONS.eyeClosed
    }
    toggleBtn.addEventListener('click', handler)
    eventListeners.push({ el: toggleBtn, type: 'click', handler })
  }
}

// ========== Load Data ==========

async function loadModelConfigs() {
  const configs = await fetchModelConfigs()
  renderModelConfigs(configs)
}

// ========== Event Binding ==========

function bindEvents() {
  // Note: btnCreateModelInPage is bound in renderModelConfigs via bindListEvents

  // Cancel button (form view)
  const btnCancel = containerRef?.querySelector('#btnFormCancel')
  if (btnCancel) {
    const handler = () => showListView()
    btnCancel.addEventListener('click', handler)
    eventListeners.push({ el: btnCancel, type: 'click', handler })
  }

  // Save button (form view)
  const btnSave = containerRef?.querySelector('#btnFormSave')
  if (btnSave) {
    const handler = () => saveModelConfig()
    btnSave.addEventListener('click', handler)
    eventListeners.push({ el: btnSave, type: 'click', handler })
  }

  // Delete dialog buttons
  const btnCancelDelete = containerRef?.querySelector('#btnCancelDelete')
  if (btnCancelDelete) {
    const handler = () => hideDeleteDialog()
    btnCancelDelete.addEventListener('click', handler)
    eventListeners.push({ el: btnCancelDelete, type: 'click', handler })
  }

  const btnConfirmDelete = containerRef?.querySelector('#btnConfirmDelete')
  if (btnConfirmDelete) {
    const handler = () => confirmDelete()
    btnConfirmDelete.addEventListener('click', handler)
    eventListeners.push({ el: btnConfirmDelete, type: 'click', handler })
  }

  // Setup API key toggle
  setupApiKeyToggle()

  // Provider change handler
  const providerSelect = containerRef?.querySelector('#modelProvider')
  if (providerSelect) {
    const handler = (e) => {
      // Update API Key required indicator
      updateApiKeyRequired(e.target.value)

      const preset = PROVIDER_PRESETS[e.target.value]
      if (preset) {
        const baseUrlInput = containerRef?.querySelector('#modelBaseUrl')
        const apiTypeSelect = containerRef?.querySelector('#modelApiType')
        // Only auto-fill if the field is empty or user hasn't manually edited
        if (!baseUrlInput.value || baseUrlInput.dataset.autoFilled === 'true') {
          baseUrlInput.value = preset.base_url
          baseUrlInput.dataset.autoFilled = 'true'
        }
        if (apiTypeSelect) {
          apiTypeSelect.value = preset.api_type
        }
      }
      // Update model ID options for the selected provider
      updateModelIdOptions(e.target.value, '')

      // Auto-fetch models if API key already exists
      const provider = e.target.value
      const apiKey = containerRef?.querySelector('#modelApiKey')?.value?.trim()
      if (provider && provider !== 'custom' && apiKey && apiKey.length >= 8) {
        // Auto-fetch with slight delay to let UI update first
        setTimeout(() => fetchModelsFromProvider(true), 200)
      }
    }
    providerSelect.addEventListener('change', handler)
    eventListeners.push({ el: providerSelect, type: 'change', handler })
  }

  // API key input with debounced fetch
  const apiKeyInput = containerRef?.querySelector('#modelApiKey')
  if (apiKeyInput) {
    const handler = function() {
      clearTimeout(fetchDebounceTimer)
      const provider = containerRef?.querySelector('#modelProvider')?.value
      const apiKey = this.value?.trim()
      // Only auto-fetch for non-custom providers with a valid-looking API key
      if (provider && provider !== 'custom' && apiKey && apiKey.length >= 8) {
        fetchDebounceTimer = setTimeout(() => {
          fetchModelsFromProvider(true) // silent mode - no toast on success
        }, 800) // 800ms debounce
      }
    }
    apiKeyInput.addEventListener('input', handler)
    eventListeners.push({ el: apiKeyInput, type: 'input', handler })
  }

  // Model ID select change
  const modelIdSelect = containerRef?.querySelector('#modelModelId')
  if (modelIdSelect) {
    const handler = (e) => {
      const customInput = containerRef?.querySelector('#modelModelIdCustom')
      if (e.target.value === '__custom__') {
        customInput.style.display = 'block'
        customInput.focus()
      } else {
        customInput.style.display = 'none'
        customInput.value = ''
      }
    }
    modelIdSelect.addEventListener('change', handler)
    eventListeners.push({ el: modelIdSelect, type: 'change', handler })
  }

  // Mark base_url as manually edited when user types
  const baseUrlInput = containerRef?.querySelector('#modelBaseUrl')
  if (baseUrlInput) {
    const handler = (e) => {
      e.target.dataset.autoFilled = 'false'
    }
    baseUrlInput.addEventListener('input', handler)
    eventListeners.push({ el: baseUrlInput, type: 'input', handler })
  }

  // Close delete dialog on backdrop click
  const deleteDialog = containerRef?.querySelector('#deleteDialog')
  if (deleteDialog) {
    const handler = (e) => {
      if (e.target.id === 'deleteDialog') hideDeleteDialog()
    }
    deleteDialog.addEventListener('click', handler)
    eventListeners.push({ el: deleteDialog, type: 'click', handler })
  }
}

// ========== Header Button Injection ==========

/**
 * Inject "Create Model" button into global header for admin page layout
 * NOTE: Disabled - button is now rendered inside the page content
 */
function injectHeaderButton() {
  // No longer needed - button is now part of page content
}

/**
 * Remove injected header button and class when leaving page
 * NOTE: Disabled - button is now rendered inside the page content
 */
function cleanupHeaderButton() {
  // Restore header visibility (in case we left from form view)
  const appHeader = document.getElementById('app-header')
  if (appHeader) {
    appHeader.style.display = ''
  }
}

// ========== CSS Loading ==========

function loadPageCSS() {
  // Check if CSS is already loaded
  if (document.getElementById('models-page-css')) return

  const cssLink = document.createElement('link')
  cssLink.rel = 'stylesheet'
  cssLink.href = buildAssetUrl('/styles/models.css')
  cssLink.id = 'models-page-css'
  document.head.appendChild(cssLink)
}

function unloadPageCSS() {
  document.getElementById('models-page-css')?.remove()
}

// ========== Mount / Unmount ==========

/**
 * Mount models page into container
 * @param {HTMLElement} container - Page content container
 * @param {{ params: Object, route: Object }} context - Route context
 */
export async function mount(container, { params, route } = {}) {
  containerRef = container

  // Load page-specific CSS
  loadPageCSS()

  // Render HTML
  container.innerHTML = PAGE_HTML

  // Update i18n translations
  updateContainerTranslations(container)

  // Inject header modifications for admin page layout
  injectHeaderButton()

  // Load provider data from backend
  await loadProviderData()

  // Load model configs
  await loadModelConfigs()

  // Bind all events
  bindEvents()

  mounted = true
}

/**
 * Unmount models page - cleanup
 */
export async function unmount() {
  // Clear debounce timer
  if (fetchDebounceTimer) {
    clearTimeout(fetchDebounceTimer)
    fetchDebounceTimer = null
  }

  // Remove event listeners
  eventListeners.forEach(({ el, type, handler }) => {
    el?.removeEventListener(type, handler)
  })
  eventListeners = []

  // Cleanup header button injection
  cleanupHeaderButton()

  // Unload page CSS
  unloadPageCSS()

  // Reset state
  modelConfigs = []
  editingModelId = null
  pendingDeleteId = null
  PROVIDER_PRESETS = {}
  PROVIDER_MODELS = {}
  containerRef = null
  mounted = false
}

export default { mount, unmount }
