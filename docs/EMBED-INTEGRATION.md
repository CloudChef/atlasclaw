# Context-Aware Embed Integration v1

AtlasClaw can expose its existing Chat Workspace as a menu or floating iframe. The v1
protocol adds deterministic Host-page Context and exact existing Skill projection without
creating a second Chat implementation or using an LLM for route or capability matching.

## Security and identity

The existing Host Cookie/JWT middleware remains the Authentication Session and the source of
current user, tenant, Skill, and Provider permissions. Embed state never replaces that session.
AtlasClaw's scoped `localStorage` value is only a candidate **Chat Active Session** key. Bootstrap
validates its owner, agent, account scope, and existence using the authenticated request before it
can be resumed. Cookie, token, and Provider credentials must never be stored in embed messages,
browser storage, manifests, or snapshots.

Every floating message receiver must validate exact `event.origin`, `event.source`, protocol,
nonce, direction-specific event type, and payload schema. `targetOrigin="*"` is not allowed. The
iframe receives its exact Host origin in `host_origin`; the page path never determines an Origin.

## Configuration

`AtlasClawConfig.embed_integration` is optional. It selects the default Provider used by the
single embedded UI. When the Host reports its current path, Core matches that path only against
this Provider's fixed `assistant_context/routes.json`, which determines the page object and
associated existing Skill.

```json
{
  "embed_integration": {
    "provider_type": "example",
    "provider_instance": "default"
  }
}
```

These are the only configurable fields. Core fixes the Agent to `main`, derives the Chat Session
scope from `provider_type`, loads `assistant_context/routes.json` from that Provider package, and
uses fixed bounded Context retention defaults. A missing `embed_integration` disables
context-aware embedding and preserves legacy behavior. An invalid Provider package or manifest
fails startup; an unavailable configured instance fails closed when the page is resolved. The
removed multi-profile `embed_integrations` key is rejected instead of silently disabling Context.

Context snapshots are a bounded, TTL-controlled in-process store. Before enabling the integration, the
deployment owner must prove that AtlasClaw is single-process or that requests for one user/Chat
Session use sticky routing. A non-sticky multi-process or multi-replica target is a deployment
blocker for v1. Do not silently accept intermittent 404 responses and do not add Redis or database
persistence as an unreviewed workaround.

Each floating iframe uses its bootstrap-validated nonce as an AtlasClaw-only `surface_id` when
resolving Context. This value separates latest-generation coordination for concurrent tabs or
iframes; it is not an authentication or authorization credential and does not change the Host
`PAGE_CHANGED` message. Snapshot capacity remains shared by owner. Independent
per-surface generation tombstones are TTL-controlled and LRU-bounded, so Snapshot eviction cannot
revive an older completion and unsupported or failed resolves cannot grow coordination state
without bound.

## Host protocol

Protocol identifier: `atlasclaw-embed/v1`.

The floating iframe URL carries `embedded=1`, `surface=floating`, exact `host_origin`, and a
Host-generated nonce with at least 128 bits of entropy. The menu URL carries only `embedded=1` and
`surface=menu`. The Host never receives or forwards a `session_key`.

This single-Provider protocol is an atomic Host/Core compatibility boundary. The embedding Host
and AtlasClaw Core must be updated in the same release: the Host must remove the former `integration`
iframe query parameter and `integration_id` from all inbound and outbound `postMessage` payloads.
The strict v1 receiver rejects the old extra message field, so Core must not be deployed alone
against an old Host. A legacy extra URL query may be ignored by the browser parser, but it is not
part of the supported contract.

Host to iframe v1 has one event:

```json
{
  "protocol": "atlasclaw-embed/v1",
  "type": "PAGE_CHANGED",
  "nonce": "<iframe nonce>",
  "generation": 11,
  "path": "/main/items/edit/123"
}
```

`path` is the normalized router path. It must be an absolute path with one leading slash and cannot
contain a scheme, Origin, query, fragment, traversal/encoded segment escape, title, selection, or business DTO. There is no
`OBJECT_SELECTION_CHANGED` event in v1. The floating iframe sends only `ATLASCLAW_READY` and
`CLOSE_FLOATING_ASSISTANT`. Users open the full assistant through the Host menu; the floating
header has no `OPEN_FULL_AGENT` control or sender.

## REST API

- `POST /api/embed/bootstrap` loads the configured default Provider, validates the surface/nonce,
  and validates an AtlasClaw-only candidate Chat Active Session. A missing integration returns
  404; malformed nonces return 422.
- `POST /api/embed/context/resolve` accepts only required `surface_id`, non-negative `generation`,
  and normalized `path`. AtlasClaw-owned frontend code derives
  `surface_id` from the bootstrap-validated iframe nonce. Every 200 response has a status:
  `resolved`, `unsupported`, or `unavailable`. An unmatched route is `unsupported` and may use
  ordinary Chat; a matched route whose object or Skill binding cannot be resolved is `unavailable`
  and must fail closed for that generation. Permission failures return 403. A successful response
  includes the verified object, matched existing Skill, and the Domain Skill's current
  `object_actions`. The matched Skill's authorized Tool inventory remains internal execution scope
  and is never returned as UI actions.
## Provider manifests

Providers own `assistant_context/routes.json` with `schema_version: 1`. Core matches only static
path segments and single-segment `{parameter}` placeholders. Rules sort by priority,
static-segment count, then manifest order. Core invokes the single Provider-level resolver with its
fixed server-owned route contract.

A resolver is a Provider-owned script referenced by a route-relative `entrypoint` under the loaded
Provider package. Core rejects absolute paths, traversal, missing files, and non-Python entrypoints.
The route separately declares `skill_ref`, which is the existing business Skill matched to the
resolved Context object; the resolver is not registered in that Skill and never enters LLM Tool
routing. Resolver output must contain `success: true` and an object with the route-declared `type`
plus a non-empty `id`.

For a resolver, Core requires an authenticated RBAC context and access to the Provider type/instance
explicitly selected by `embed_integration`. Object visibility is then decided by the
Provider resolver using the current request identity and its read/ACL checks. The route's matched
Skill and its normal Tool set remain subject to their existing Provider/Skill ownership
and permission checks; resolving Context does not make any Tool callable by itself.

A resolver returns the minimal `ResolvedObject` and the `object_actions` generated by the same
Domain Skill builder used by normal Chat Tools. It cannot declare Tool names or replace Tool schemas,
permissions, or confirmation behavior. Core validates the generic action contract, finds the route's
exact existing Skill, and freezes both the actions and the internal authorized Tool scope in the
user/surface/generation/TTL-bound Context snapshot.

When the floating UI invokes an Agent prompt action, it submits the exact Context ID and generation
captured with that rendered Context through the ordinary Chat path. A page change before submission aborts the
request in the browser; the Agent run endpoint independently rejects a snapshot that is no longer
its originating surface's latest page Context with HTTP 409. Agent Run requests cannot declare or
override `surface_id`: the server first resolves the unpredictable Context ID, then uses the surface
frozen in that Snapshot. Ordinary Chat, history, and menu surfaces keep their
existing turn-context behavior. While a floating page Context is still resolving, Chat submission
fails closed instead of silently falling back to ordinary capabilities. Once a Context is resolved,
the global slash catalog is unavailable for that page scope. A matched `unavailable` response also
keeps the page scope closed and blocks Agent Run creation with `EMBED_CONTEXT_UNAVAILABLE`; it never
downgrades to ordinary capability selection. An `unsupported` page, menu surfaces, and legacy embeds
retain ordinary Chat and the ordinary slash catalog.

Context actions send the current object and Provider-declared prompt into the same ordinary Chat.
The server restores the Snapshot and projects the matched Skill's authorized Tool set; the client
cannot select an exact Tool. Immediately before each Provider Tool I/O, Core revalidates the latest
generation, Provider binding, Tool ownership, and current RBAC. The Skill retains its normal schemas
and confirmation behavior; the page does not define a second Tool policy or execution API. The Agent prompt receives the complete Provider-whitelisted
`ResolvedObject`, including its bounded `attributes`, so normal Tool calls can use the current
object without Core rewriting arguments.

## Compatibility and storage

Default `/atlasclaw/` URLs retain the legacy Session strategy and visual behavior. Configured
menu/floating surfaces share a Chat Active Session through an AtlasClaw-origin
scoped pointer that must be bootstrap-validated; the Host is not part of that handoff. Menu surfaces
never attach page context.

No binary format or database migration is required. Transcript JSONL already supports additive
metadata; the Provider-whitelisted `turn_context` is persisted. Context snapshots do not survive
restart.

Context snapshots are process-local. An integrated deployment must run a single AtlasClaw process
or provide sticky routing that keeps bootstrap, Context resolution, and the page-scoped Agent turn
on the same process. Without that deployment property context-aware embedding must not be enabled;
this version does not add Redis, database storage, or a new configuration schema as a fallback.
