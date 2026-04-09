# SmartCMP Personal Token Provider Design

## Summary

This design introduces a platform-level provider authentication mode that allows a user to supply
their own provider credential, with `SmartCMP` used as the concrete example.

The user experience is centered in `Account Settings`, not in the provider catalog. Users manage
their personal `SmartCMP` access token from a new `My Providers` section. If the token is missing,
the provider stays visible but unavailable, and the UI always explains how to configure it.

This design is intentionally split into two layers:

- platform layer: a uniform provider auth type, presented to users as `User-Supplied Token`
- provider layer: provider-specific credential form fields defined by the provider itself

## Goals

- add a consistent platform concept for user-supplied provider credentials
- use `SmartCMP` as the first concrete example without hard-coding SmartCMP-specific rules into the
  platform surface
- store personal provider credentials at the user level, aligned with the existing per-user
  settings model used by channel configuration
- keep unsupported providers hidden from this experience, but keep supported-yet-unconfigured
  providers visible with clear guidance
- make the unavailable state understandable instead of failing only at action time

## Non-Goals

- no provider-wide shared token configuration in this flow
- no multi-instance per-user SmartCMP credential management in phase 1
- no OAuth browser authorization flow in this design
- no attempt to redesign the whole provider management IA beyond the new account-level entry

## Product Rules

### Platform Auth Type

The platform adds a new provider authentication mode for services that can be called with a
user-owned API token.

Recommended internal identifier:

- `user_supplied_token`

Recommended UI label:

- `User-Supplied Token`

This is a platform capability, not a SmartCMP-only behavior.

### Provider-Owned Form

If a provider declares `user_supplied_token`, the platform renders that provider inside
`My Providers`.

The provider defines its own user form schema. For `SmartCMP` in phase 1, the form contains a
single field:

- `Access Token`

The platform owns the outer shell:

- provider card
- status badge
- availability messaging
- save and validation workflow

The provider owns the inner form fields and help text.

### User-Level Storage

The user credential belongs to the authenticated user and should be stored with that user's
personal settings, consistent with current channel-style per-user configuration.

Current project docs describe `user_setting.json` as containing `channels` and `preferences`.
Implementation of this design will require extending that user-level shape to support a
`providers` section for personal provider credentials.

Illustrative target shape:

```json
{
  "channels": {},
  "providers": {
    "smartcmp": {
      "auth_type": "user_supplied_token",
      "configured": true,
      "credentials": {
        "access_token": "enc:..."
      },
      "last_verified_at": "2026-04-09T14:32:00+08:00"
    }
  },
  "preferences": {}
}
```

### Availability Gate

If a provider uses `user_supplied_token` and the user has not configured the required credential,
that provider is unavailable for that user.

The provider must:

- stay visible in UI
- show a disabled or action-required state
- explain why it is unavailable
- provide a direct path to configuration

## Information Architecture

### Entry Point

The primary entry point is `Account Settings`.

Within the current page structure, add a new card group below personal identity and above or
alongside the existing lower settings panels:

- section title: `My Providers`
- section description: `Manage provider access stored in your personal profile.`

This keeps personal provider credentials in the same mental model as personal account settings and
channel preferences.

### Main Sections

The account page gains two coordinated surfaces:

1. `My Providers`
2. `SmartCMP Access`

`My Providers` is the overview list.

`SmartCMP Access` is the detail panel for the selected provider. In phase 1, one expanded panel is
enough. A drawer or modal is not required.

## Page Structure

### Overview Card

The `SmartCMP` overview card should include:

- provider name: `SmartCMP`
- top-right status badge
- auth type line: `Auth Type: User-Supplied Token`
- short explanation
- compact metadata rows
- primary CTA

Recommended metadata rows:

- `Status`
- `Provider access`
- `Scope`
- `Storage`

Recommended values in the empty state:

- `Status: Not configured`
- `Provider access: Unavailable for this account`
- `Scope: Personal`
- `Storage: User profile`

### Detail Panel

The `SmartCMP Access` panel should include:

- provider-specific field area
- status or help banner
- availability explanation
- action row

For SmartCMP phase 1, the provider-specific field area is:

- `Access Token`
- input field
- show or hide action

Recommended primary actions:

- `Test Connection`
- `Save Token`

When already configured:

- `Test Connection`
- `Update Token`

## Low-Fidelity Mockup

```text
+------------------------------------------------------------------------------+
| Account Settings                                                             |
| Personal account profile, security, and provider access                      |
+------------------------------------------------------------------------------+
| My Providers                                              [1 requires setup] |
| Manage provider access stored in your personal profile.                      |
|                                                                              |
| +--------------------------------------------------------------------------+ |
| | SmartCMP                                             Disabled           | |
| | Auth Type: User-Supplied Token                                         | |
| |                                                                          | |
| | Access SmartCMP with your own personal token. Until configured,         | |
| | SmartCMP skills and actions stay unavailable for your account.          | |
| |                                                                          | |
| | Status            Not configured                                        | |
| | Provider access   Unavailable for this account                          | |
| | Scope             Personal                                              | |
| | Storage           User profile                                          | |
| |                                                                          | |
| |                                      [Configure Token]                  | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| SmartCMP Access                                                              |
| Provider-specific credential fields defined by SmartCMP                      |
|                                                                              |
| Access Token                                                                 |
| +---------------------------------------------------------------+ [Show]     |
| | scmp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx       |             |
| +---------------------------------------------------------------+             |
|                                                                              |
| Token used only for SmartCMP requests on your behalf. Stored with your      |
| user settings, similar to channel connection preferences.                    |
|                                                                              |
| Availability                                                                 |
| - Provider visibility: Visible to user                                       |
| - Provider usability: Disabled until token is configured                     |
| - Failure rule: If token missing, SmartCMP is not callable for this user     |
|                                                                              |
|                                      [Test Connection] [Save Token]          |
+------------------------------------------------------------------------------+
```

## State Variants

### State A: Not Configured

Use a disabled presentation with a strong setup cue.

Card:

- badge: `Disabled`
- summary line: `Configure your personal SmartCMP access token to enable this provider.`
- CTA: `Configure Token`

Detail panel:

- empty token field
- inline message: `SmartCMP is currently unavailable because no personal token is saved.`
- `Test Connection` disabled until a token is entered

### State B: Configured and Available

Use a healthy connected state.

Card:

- badge: `Connected`
- summary line: `Your personal SmartCMP token is configured and ready to use.`
- metadata: `Last verified`
- CTA: `Manage Token`

Detail panel:

- masked token value
- inline help: `Token is stored in your personal profile and used only for SmartCMP.`
- actions: `Test Connection`, `Update Token`

### State C: Configured but Invalid

Use an attention state, not a hidden state.

Card:

- badge: `Action Required`
- summary line: `Your saved token can no longer access SmartCMP. Update it to continue.`
- metadata: `Token invalid or expired`
- CTA: `Fix Token`

Detail panel:

- error banner
- masked token value remains visible as existing credential state
- actions: `Retest`, `Replace Token`

## Cross-Surface Disabled Behavior

This unavailable state must be reflected anywhere SmartCMP could be chosen or invoked.

### Provider Selection Surface

Render SmartCMP as visible but disabled.

Example:

```text
SmartCMP        Disabled
Requires your personal access token
[Go to Configure]
```

### Skill or Action Launch Surface

If the user tries to invoke a SmartCMP-backed action while unavailable, show a direct blocking
message instead of a generic failure.

Recommended message:

```text
SmartCMP is unavailable for your account.
Configure your personal SmartCMP token in Account Settings > My Providers.

[Open Settings] [Cancel]
```

## Interaction Rules

### Save Flow

Recommended flow:

1. user enters or replaces token
2. user clicks `Save Token`
3. platform stores the credential in the user's personal provider config
4. optional validation may run immediately or as an explicit follow-up action
5. UI updates badge and availability state

### Validation Flow

The design should support a separate `Test Connection` action because it gives the user confidence
without forcing a blocking validation step on every save.

Validation result mapping:

- success -> `Connected`
- auth failure or expired token -> `Action Required`
- missing token -> `Disabled`

### Provider Use Gate

Runtime behavior must align with the settings state:

- missing credential: provider unavailable
- invalid credential: provider unavailable until fixed
- valid credential: provider available

The platform should never silently fall back to another credential source for a provider declared as
`user_supplied_token` unless the provider contract explicitly supports that fallback and the UI makes
it clear.

## Visual Guidance

This should match the established account settings language rather than introducing a separate admin
console aesthetic.

Recommended visual hierarchy:

- account page header remains unchanged
- `My Providers` appears as another settings card group
- provider card uses a right-aligned status badge
- detail panel uses clear input, helper text, and low-noise banners

Badge suggestions:

- `Disabled`
- `Connected`
- `Action Required`

Avoid:

- hiding SmartCMP when unconfigured
- scattering configuration across multiple pages
- presenting the auth type as a provider-specific one-off string

## Copy Recommendations

### Labels

- `My Providers`
- `SmartCMP Access`
- `Auth Type`
- `Access Token`
- `Provider access`

### Buttons

- `Configure Token`
- `Save Token`
- `Test Connection`
- `Manage Token`
- `Update Token`
- `Replace Token`
- `Go to Configure`
- `Open Settings`

### Short Explanations

- `This provider requires your personal access token before it can be used.`
- `Stored in your personal profile and only used for SmartCMP requests on your behalf.`
- `SmartCMP skills and actions stay unavailable for your account until configured.`

## Design Constraints from Current Codebase

This design aligns with the current project in the following ways:

- account-level UX builds on the existing account settings page
- user-level configuration follows the same ownership model as current channel preferences
- provider availability remains explicit and permission-safe

This design also requires future implementation changes:

- extend user config schema to support provider-scoped personal credentials
- add platform-level provider auth type support
- add runtime availability checks for user-supplied provider credentials
- update canonical docs that currently describe user config as only `channels` and `preferences`

## Open Questions Resolved in This Design

- entry point: `Account Settings / My Providers`
- example provider: `SmartCMP`
- credential cardinality: one token per user for SmartCMP
- missing credential behavior: visible but disabled, with setup guidance

## Handoff Notes

For Figma, create three component variants for the SmartCMP card and detail panel:

1. empty or not configured
2. connected
3. invalid or action required

The core invariant across all variants is:

- the provider remains discoverable
- the reason for unavailability is explicit
- the path to recovery is always one click away
