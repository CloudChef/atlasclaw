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
integration, nonce, direction-specific event type, and payload schema. `targetOrigin="*"` is not
allowed. The accepted origin comes only from the server bootstrap/profile, never from the path.

## Configuration

`AtlasClawConfig.embed_integrations` is optional and defaults to `{}`. Each enabled profile has:

```json
{
  "embed_integrations": {
    "example-assistant": {
      "enabled": true,
      "agent_id": "main",
      "provider_type": "example",
      "provider_instance": "default",
      "session_scope": "example-assistant",
      "allowed_origins": ["https://host.example"],
      "route_manifest": "assistant_context/routes.json",
      "context_ttl_seconds": 1800,
      "max_contexts_per_user": 128
    }
  }
}
```

Origins must be exact HTTP(S) origins without paths, credentials, query, fragment, or wildcards.
Manifest paths are relative to the Provider root and cannot escape it. Invalid enabled profiles
fail startup; an empty profile map preserves legacy behavior.

Context snapshots are a bounded, TTL-controlled in-process store. Before enabling a profile, the
deployment owner must prove that AtlasClaw is single-process or that requests for one user/Chat
Session use sticky routing. A non-sticky multi-process or multi-replica target is a deployment
blocker for v1. Do not silently accept intermittent 404 responses and do not add Redis or database
persistence as an unreviewed workaround.

Each floating iframe uses its bootstrap-validated nonce as an AtlasClaw-only `surface_id` when
resolving Context. This value separates latest-generation coordination for concurrent tabs or
iframes; it is not an authentication or authorization credential and does not change the Host
`PAGE_CHANGED` message. Snapshot capacity remains shared by owner and integration. Independent
per-surface generation tombstones are TTL-controlled and LRU-bounded, so Snapshot eviction cannot
revive an older completion and unsupported or failed resolves cannot grow coordination state
without bound.

## Host protocol

Protocol identifier: `atlasclaw-embed/v1`.

The floating iframe URL carries `embedded=1`, `surface=floating`, `integration`, exact
`host_origin`, and a Host-generated nonce with at least 128 bits of entropy. The menu URL carries
only `embedded=1`, `surface=menu`, and `integration`. The Host never receives or forwards a
`session_key`.

Host to iframe v1 has one event:

```json
{
  "protocol": "atlasclaw-embed/v1",
  "type": "PAGE_CHANGED",
  "integration_id": "example-assistant",
  "nonce": "<iframe nonce>",
  "generation": 11,
  "path": "/main/items/edit/123"
}
```

`path` is the normalized router path. It must be an absolute path with one leading slash and cannot
contain a scheme, Origin, query, fragment, traversal/encoded segment escape, title, selection, or business DTO. There is no
`OBJECT_SELECTION_CHANGED` event in v1. The iframe-to-Host events are `ATLASCLAW_READY`,
`OPEN_FULL_AGENT`, and `CLOSE_FLOATING_ASSISTANT`; `OPEN_FULL_AGENT` means only “navigate to the
fixed full-agent menu route” and carries no Chat Session or page context.

## REST API

- `POST /api/embed/bootstrap` validates the integration, surface, exact floating origin/nonce,
  and an AtlasClaw-only candidate Chat Active Session. Unknown/disabled integrations return 404;
  disallowed origins return 403; malformed nonces return 422.
- `POST /api/embed/context/resolve` accepts only `integration_id`, required `surface_id`,
  non-negative `generation`, and normalized `path`. AtlasClaw-owned frontend code derives
  `surface_id` from the bootstrap-validated iframe nonce. Every 200 response has a status:
  `resolved`, `unsupported`, or `unavailable`. An unmatched route is `unsupported` and may use
  ordinary Chat; a matched route whose object or Skill binding cannot be resolved is `unavailable`
  and must fail closed for that generation. Permission failures return 403. A successful response may include
  normalized `object_actions` references for the floating Context UI; each reference contains only
  the verified object identity and standard browser action fields, never Skill refs, Tool names,
  Tool arguments, or object attributes.
## Provider manifests

Providers own `assistant_context/routes.json` with `schema_version: 1`. Core matches only static
path segments and single-segment `{parameter}` placeholders. Rules sort by priority,
static-segment count, then manifest order. Resolver arguments support only
`$route.<name>` references declared by that route template.

A resolver is a Provider-owned script referenced by a route-relative `entrypoint` under the loaded
Provider package. Core rejects absolute paths, traversal, missing files, and non-Python entrypoints.
The route separately declares `skill_ref`, which is the existing business Skill matched to the
resolved Context object; the resolver is not registered in that Skill and never enters LLM Tool
routing. Resolver output must contain `success: true` and an object with the route-declared `type`
plus a non-empty `id`.

For a resolver, Core requires an authenticated RBAC context and access to the Provider type/instance
explicitly selected by the embed integration profile. Object visibility is then decided by the
Provider resolver using the current request identity and its read/ACL checks. The route's matched
Skill and its normal Tool set remain subject to their existing Provider/Skill ownership
and permission checks; resolving Context does not make any Tool callable by itself.

A resolver may declare browser controls only in its exact top-level `object_actions` array. Core
normalizes those actions with the shared object-action contract, discards nested declarations,
unsafe URLs, and any resolver-origin action containing execution metadata, and
rebuilds the single public reference identity from the validated
`ResolvedObject`. Resolver input is also bounded before snapshot creation: at most 16 actions and
8 inputs per action, short identifiers, bounded strings/URLs, and 32 KiB total serialized action
data. Resolver actions therefore cannot forge another object or authorize Tool execution. These
controls are frozen in the
user/integration/surface/generation/TTL-bound Context snapshot and are intentionally excluded from
Agent `turn_context` and capability policies.

When the floating UI invokes one of these controls, it submits the exact Context ID, generation,
and integration captured with that rendered action. A page change before submission aborts the
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

Object actions send the current object and explicit intent into the same ordinary Chat. The
server-restored Snapshot projects only the route's unique existing Skill. That Skill retains its
normal authorized Tool set, schemas, and confirmation behavior; the page does not define a second
Tool policy or execution API. The Agent prompt receives the complete Provider-whitelisted
`ResolvedObject`, including its bounded `attributes`, so normal Tool calls can use the current
object without Core rewriting arguments.

## Compatibility and storage

Default `/atlasclaw/` and non-integration embed URLs retain the legacy Session strategy and visual
behavior. Integrated menu/floating surfaces share a Chat Active Session through an AtlasClaw-origin
scoped pointer that must be bootstrap-validated; the Host is not part of that handoff. Menu surfaces
never attach page context.

No binary format or database migration is required. Transcript JSONL already supports additive
metadata; the Provider-whitelisted `turn_context` is persisted. Context snapshots do not survive
restart.

Tools that declare `requires_approval: true` or `effect: mutate` use the same server-owned
confirmation gate in ordinary menu Chat and page-scoped Chat. The first Tool invocation never calls
the mutation handler; it creates a bounded, expiring, opaque ticket for the exact authenticated
user, Chat Session, Agent, Tool owner and contract, canonical arguments, Provider instance, and
optional page Context. Cancel creates no Run. Confirm submits the opaque token to the existing Agent
Run endpoint, which rechecks current DB/Provider visibility after acquiring the Session slot and
then consumes the ticket once before calling the original Tool handler. Natural-language replies,
client-supplied execution state, expired tickets, replay, and scope or contract drift do not
authorize execution.

Confirmation tickets and Context snapshots are process-local. An integrated deployment must run a
single AtlasClaw process or provide sticky routing that keeps bootstrap, Context resolution, the
first Tool turn, and its confirmation continuation on the same process. Without that deployment
property the integration must not be enabled; this version does not add Redis, database storage, or
a new configuration schema as a fallback.
