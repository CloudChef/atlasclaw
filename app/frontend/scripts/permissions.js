/*
 *  Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.
 */

const SKILL_MODULE_PERMISSION_KEYS = new Set(['view', 'enable_disable', 'manage_permissions'])
const PROVIDER_MODULE_PERMISSION_KEYS = new Set(['manage_permissions'])
const CHANNEL_MODULE_PERMISSION_KEYS = new Set(['manage_permissions'])

export const ROLE_MANAGEMENT_ACCESS_PERMISSIONS = [
  'roles.view',
  'roles.create',
  'roles.edit',
  'roles.delete',
  'roles.manage_permissions'
]

export const USER_MANAGEMENT_ACCESS_PERMISSIONS = [
  'users.view',
  'users.create',
  'users.edit',
  'users.delete',
  'users.assign_roles'
]

export const CHANNEL_MANAGEMENT_ACCESS_PERMISSIONS = [
  'channels.manage_permissions'
]

export const MODEL_MANAGEMENT_ACCESS_PERMISSIONS = [
  'model_configs.view',
  'model_configs.create',
  'model_configs.edit',
  'model_configs.delete'
]

function normalizePermissionPath(permissionPath) {
  const parts = String(permissionPath || '')
    .split('.')
    .map(segment => segment.trim())
    .filter(Boolean)

  if (parts.length === 2 && parts[0] === 'skills' && SKILL_MODULE_PERMISSION_KEYS.has(parts[1])) {
    return ['skills', 'module_permissions', parts[1]]
  }
  if (parts.length === 2 && parts[0] === 'providers' && PROVIDER_MODULE_PERMISSION_KEYS.has(parts[1])) {
    return ['providers', 'module_permissions', parts[1]]
  }
  if (parts.length === 2 && parts[0] === 'channels' && CHANNEL_MODULE_PERMISSION_KEYS.has(parts[1])) {
    return ['channels', 'module_permissions', parts[1]]
  }

  return parts
}

export function hasPermission(authInfo, permissionPath) {
  if (!authInfo) return false

  let value = authInfo.permissions || {}
  for (const segment of normalizePermissionPath(permissionPath)) {
    if (!value || typeof value !== 'object') {
      return false
    }
    value = value[segment]
  }

  return value === true
}

export function hasAnyPermission(authInfo, permissionPaths = []) {
  return permissionPaths.some(permissionPath => hasPermission(authInfo, permissionPath))
}

export function canManagePermissionModule(authInfo, moduleId) {
  if (hasPermission(authInfo, 'roles.manage_permissions')) {
    return true
  }
  if (moduleId === 'roles') {
    return false
  }
  return hasPermission(authInfo, `${moduleId}.manage_permissions`)
}

export function canAccessRoleManagement(authInfo) {
  return hasAnyPermission(authInfo, ROLE_MANAGEMENT_ACCESS_PERMISSIONS)
}

export function canAccessUserManagement(authInfo) {
  return hasAnyPermission(authInfo, USER_MANAGEMENT_ACCESS_PERMISSIONS)
}

export function canAccessChannelManagement(authInfo) {
  if (hasAnyPermission(authInfo, CHANNEL_MANAGEMENT_ACCESS_PERMISSIONS)) {
    return true
  }
  if (hasPermission(authInfo, 'channels.allow_all')) {
    return true
  }
  const channelPermissions = authInfo?.permissions?.channels?.channel_permissions
  return Array.isArray(channelPermissions) && channelPermissions.some(channel => channel?.allowed === true)
}

export function canAccessModelManagement(authInfo) {
  return hasAnyPermission(authInfo, MODEL_MANAGEMENT_ACCESS_PERMISSIONS)
}
