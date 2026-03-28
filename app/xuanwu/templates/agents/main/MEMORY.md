---
agent_id: "main"
---

# MEMORY.md - Memory Strategy Configuration

## Long-term Memory

- Auto Extract: Enabled
- Extract Triggers: Conversation end, key decision points, explicit user commands
- Storage: Vector database for semantic retrieval with text-embedding-3-small model
- Retention: User profiles permanent, conversation summaries archived after 90 days

## Context Management

- Max Turns: 20
- Token Limit: 8000
- Compression Strategy: Summary + key entities retention when approaching limits
