# RBAC Manage Permissions Design

Date: 2026-04-03

## Goal

Introduce a clear distinction between:

- business permissions: operating real workspace objects
- permission governance permissions: editing the permission model itself
- assignment permissions: making permissions effective for users

## Core Definitions

- `manage_permissions`
  Allows editing a module's permission model for roles.
  It does not imply business access to that module.
  It does not imply permission assignment.

- `assign_roles`
  Allows assigning or revoking roles for users.
  It is an authorization action, not a permission-model editing action.

- `rbac.manage_permissions`
  Root governance permission for editing role permission matrices across modules.
  This replaces the idea of `roles.manage_permissions` to avoid recursive self-governance.

## Permission Model

### Root Governance

- `rbac.manage_permissions`

### Skills

- `skills.module_permissions.view`
- `skills.module_permissions.enable_disable`
- `skills.module_permissions.manage_permissions`
- `skills.skill_permissions[].enabled`
- `skills.skill_permissions[].authorized`

### Channels

- `channels.view`
- `channels.create`
- `channels.edit`
- `channels.delete`
- `channels.manage_permissions`

### Tokens

- `tokens.view`
- `tokens.create`
- `tokens.edit`
- `tokens.delete`
- `tokens.manage_permissions`

### Users

- `users.view`
- `users.create`
- `users.edit`
- `users.delete`
- `users.reset_password`
- `users.assign_roles`
- `users.manage_permissions`

### Roles

- `roles.view`
- `roles.create`
- `roles.edit`
- `roles.delete`

## UI Mapping

- Add an `RBAC` / `Permission Governance` module to the role editor.
- Keep `roles` focused on role records and lifecycle actions.
- Show `manage_permissions` on modules that own a permission surface.
- Keep `assign_roles` under `users` because it is an authorization action.

## Non-Goals

- This change does not yet wire fine-grained runtime enforcement into backend guards.
- This change focuses on permission schema, defaults, editor support, and test coverage.
