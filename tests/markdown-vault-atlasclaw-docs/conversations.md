---
title: Conversations
description: Chat with AtlasClaw and work with sessions.
---

# Conversations

The chat page is the main AtlasClaw workspace for a Standard User.

## Start a Conversation

Open the AtlasClaw web UI and send a message in the chat input. The agent uses
the current authenticated user, enabled skills, provider access, and available
model configuration to respond.

## Sessions

AtlasClaw stores conversation sessions under the authenticated user's workspace
scope. Users can switch between their own sessions and continue prior work.

Sessions are separated by user, channel, and thread. A browser chat session and
an IM thread can therefore keep different history even if the same user sends
similar messages from both places.

## Asking for Provider Work

When asking the agent to use an operational system, include enough context for
the provider skill to choose the right action:

- what you want to do;
- which environment, project, tenant, resource, or business group applies;
- whether the request is read-only or should change something;
- any approval reason, ticket reference, or business justification.

For write operations, review the agent's summary before confirming. Provider
skills may create upstream side effects such as requests, approvals, resource
operations, or alert status changes.

## Permission Messages

If a request needs a disabled skill, unavailable provider instance, or missing
credential, the agent should explain the blocker and direct the user to configure
their own token or contact an administrator as appropriate.

## Good Conversation Patterns

| Pattern | Why it helps |
| --- | --- |
| "List my available options first." | Lets the agent use read-only discovery before a write action. |
| "Use the production instance." | Avoids ambiguity when several provider instances exist. |
| "Do not submit yet; prepare a draft." | Keeps the workflow reviewable. |
| "Explain why this is blocked." | Surfaces missing permissions or credentials. |

Avoid pasting secrets into chat. Use Provider Tokens or channel configuration
forms for credentials.
