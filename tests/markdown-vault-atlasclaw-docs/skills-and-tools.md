---
title: Skills and Tools
description: Runtime skills, built-in tools, and permission filtering.
---

# Skills and Tools

Skills describe agent capabilities. Tools execute concrete operations.

## Built-In Tool Areas

AtlasClaw Core includes tools for sessions, memory, web search and fetch,
provider instance selection, runtime execution, filesystem access, and browser
automation when enabled.

## Markdown Skills

Markdown skills can be loaded from workspace, user, external, or provider
locations. Provider-bound skills are qualified with the provider namespace to
avoid collisions.

A skill describes when it should be used, what inputs it expects, what scripts
or tools it may call, and what safety rules apply. Provider skills should keep
provider-specific terminology inside the provider package.

## Permission Filtering

Role skill permissions and provider instance permissions filter which skills
and tools are exposed to the runtime. Provider-bound tools are governed by
provider permissions instead of being treated as ordinary core tools.

Filtering happens before the agent chooses tools. A missing tool is therefore a
configuration or authorization signal, not a prompt-engineering problem.

## Operational Rule

If a skill is missing or disabled, the agent should explain the access blocker
instead of inventing capabilities.

## Skill Lifecycle

1. The skill is discovered from a configured skill directory or provider
   package.
2. The registry validates the skill name and metadata.
3. The role policy decides whether the skill is visible and enabled.
4. The provider policy decides whether provider-bound skills have an allowed
   provider instance.
5. The agent runtime receives only the allowed skill/tool set.
6. Tool execution records output that the agent can use as evidence.

## Write-Action Safety

Skills that submit requests, approve work, change resource state, or operate
alerts must be treated as write actions. A well-authored skill should ask for
missing information, confirm intent when appropriate, and report upstream errors
without fabricating success.
