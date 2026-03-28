# Xuanwu Skill Development Guide

## Table of Contents

1. [Overview](#overview)
2. [Skill Types](#skill-types)
3. [Skill Structure](#skill-structure)
4. [Skill Metadata](#skill-metadata)
5. [Skill Deployment](#skill-deployment)
6. [Skill Discovery and Loading](#skill-discovery-and-loading)
7. [Skill Execution Flow](#skill-execution-flow)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)

---

## Overview

### What is a Skill?

A **Skill** in Xuanwu is a discrete unit of functionality that the AI Agent can execute. Skills encapsulate:

- **Capability Definition** - What the Skill can do
- **Input Parameters** - What data the Skill needs
- **Execution Logic** - How the Skill performs its task
- **Output Format** - What the Skill returns
- **Documentation** - How the Agent should use the Skill

### Skill Characteristics

| Characteristic | Description |
|----------------|-------------|
| **Atomic** | Each Skill performs a single, well-defined task |
| **Composable** | Skills can be combined to create complex workflows |
| **Discoverable** | Skills are automatically discovered and registered |
| **Documented** | Self-documenting through metadata |
| **Testable** | Can be tested in isolation |

### Skill Categories

| Category | Description | Examples |
|----------|-------------|----------|
| **Provider Skills** | Integrate with external systems | Jira issue creation, Slack messaging |
| **System Skills** | Interact with the operating system | File operations, process execution |
| **Utility Skills** | General-purpose utilities | Data transformation, calculations |
| **Workflow Skills** | Orchestrate multiple Skills | Multi-step approval process |

---

## Skill Types

### 1. Executable Skills

**Definition**: Skills implemented as Python functions that perform actions.

**Characteristics**:
- Written in Python
- Registered via `SKILL_METADATA`
- Can access external APIs
- Can use dependencies

**Use Cases**:
- API integrations
- Database operations
- File system operations
- External service calls

### 2. Markdown Skills (MD Skills)

**Definition**: Documentation-based Skills that provide knowledge and guidance.

**Characteristics**:
- Written in Markdown
- No executable code
- Injected into Agent prompts
- Provide context and examples

**Use Cases**:
- Best practice documentation
- Domain knowledge
- Process guidelines
- Reference information

### 3. Hybrid Skills

**Definition**: Skills that combine executable code with rich documentation.

**Characteristics**:
- Executable handler function
- Comprehensive SKILL.md documentation
- Code examples in documentation
- Usage scenarios

**Use Cases**:
- Complex integrations
- User-facing features
- Multi-step processes

---

## Skill Structure

### Directory Layout

#### Minimal Skill Structure

```
skills/<skill-name>/
├── SKILL.md              # Skill metadata and documentation (required)
└── scripts/
    └── handler.py        # Skill implementation (required)
```

#### Complete Skill Structure

```
skills/<skill-name>/
├── SKILL.md              # Skill metadata and documentation
├── README.md             # Additional documentation
├── scripts/
│   ├── __init__.py
│   ├── handler.py        # Main skill handler
│   ├── _utils.py         # Helper functions
│   └── _client.py        # API client
├── tests/
│   ├── __init__.py
│   ├── test_handler.py   # Unit tests
│   └── test_integration.py
└── references/
    └── api_reference.md  # API documentation
```

### File Purposes

| File | Purpose | Required |
|------|---------|----------|
| `SKILL.md` | Skill metadata, description, parameters | Yes |
| `handler.py` | Main skill implementation | Yes (for executable skills) |
| `README.md` | Extended documentation | Recommended |
| `_utils.py` | Helper functions | Optional |
| `_client.py` | External API client | Optional |
| `tests/` | Test files | Recommended |
| `references/` | Reference documentation | Optional |

---

## Skill Metadata

### SKILL.md Structure

The `SKILL.md` file is the heart of a Skill. It defines:

1. **Frontmatter** - Machine-readable metadata
2. **Description** - Human-readable explanation
3. **Parameters** - Input specification
4. **Examples** - Usage examples
5. **Notes** - Additional information

### Frontmatter Fields

| Field | Description | Required | Example |
|-------|-------------|----------|---------|
| `name` | Unique skill identifier | Yes | `jira_issue_create` |
| `description` | Short description | Yes | "Create a Jira issue" |
| `category` | Skill category | Yes | `provider:jira` |
| `provider_type` | Associated provider | For provider skills | `jira` |
| `instance_required` | Needs provider instance | For provider skills | `true` |
| `version` | Skill version | Recommended | `1.0.0` |
| `author` | Skill author | Optional | `team@company.com` |
| `tags` | Skill tags | Optional | `["jira", "issue"]` |

### LLM Context Fields (for Skill Discovery)

These fields help the LLM accurately select the right skill:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `triggers` | list[string] | Keywords/phrases that trigger this skill | `["create issue", "report bug"]` |
| `use_when` | list[string] | Scenarios when to use this skill | `["User wants to create a bug report"]` |
| `avoid_when` | list[string] | Scenarios when NOT to use this skill | `["User wants to search issues"]` |
| `examples` | list[string] | Example user inputs | `["Create a Jira issue for login bug"]` |
| `related` | list[string] | Related skill names | `["jira-search", "jira-bulk"]` |

**Example with LLM Context:**

```yaml
---
name: "jira-issue"
description: "Jira issue skill for CRUD operations"
category: "provider:jira"
provider_type: "jira"
instance_required: "true"

# LLM Context Fields
triggers:
  - create issue
  - get issue
  - report bug

use_when:
  - User wants to create, read, update, or delete Jira issues
  - User mentions bug reports or incident logging

avoid_when:
  - User wants to search multiple issues (use jira-search skill)
  - User wants bulk operations (use jira-bulk skill)

examples:
  - "Create a Jira issue for the login bug"
  - "Get details for PROJ-123"

related:
  - jira-search
  - jira-bulk
---
```

### Parameter Definition

Parameters define what inputs the Skill accepts:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `name` | Parameter name | `summary` |
| `type` | Data type | `string`, `integer`, `boolean`, `array`, `object` |
| `description` | Parameter description | "Issue summary text" |
| `required` | Is required | `true` or `false` |
| `default` | Default value | `"Task"` |
| `enum` | Allowed values | `["Bug", "Task", "Story"]` |

### Response Definition

Skills should document their response format:

| Field | Description | Example |
|-------|-------------|---------|
| `success` | Operation success | `true` |
| `message` | Human-readable result | "Issue PROJ-123 created" |
| `data` | Structured result | `{ "issue_key": "PROJ-123" }` |
| `error` | Error information | `{ "code": "AUTH_FAILED" }` |

---

## Skill Deployment

### Deployment Locations

Skills can be deployed to multiple locations, with different priorities:

#### 1. Workspace Skills (Highest Priority)

**Location**: `{workspace}/skills/`

**Characteristics**:
- User-specific or project-specific
- Overrides built-in Skills
- Ideal for development and customization

**Use Case**: Custom Skills for a specific project or user.

#### 2. User Skills

**Location**: `~/.xuanwu/skills/`

**Characteristics**:
- User-specific across all workspaces
- Personal Skill library
- Survives workspace changes

**Use Case**: Personal productivity Skills.

#### 3. Built-in Skills

**Location**: `app/xuanwu/skills/built_in/`

**Characteristics**:
- Shipped with Xuanwu Core
- Core functionality
- Updated with Core releases

**Use Case**: Essential system Skills.

#### 4. Provider Skills

**Location**: `app/xuanwu/providers/<provider>/skills/`

**Characteristics**:
- Associated with a Provider
- Provider-specific functionality
- Auto-discovered with Provider

**Use Case**: Jira, ServiceNow, etc.

### Deployment Process

#### Step 1: Create Skill Directory

Create the Skill directory in the desired location:

```
~/.xuanwu/skills/my-skill/
├── SKILL.md
└── scripts/
    └── handler.py
```

#### Step 2: Write SKILL.md

Create comprehensive metadata and documentation.

#### Step 3: Implement Handler

Write the Skill implementation (for executable Skills).

#### Step 4: Test Skill

Test the Skill in isolation before deployment.

#### Step 5: Deploy

Copy the Skill directory to the target location.

#### Step 6: Verify Loading

Restart Xuanwu or trigger Skill reload to verify.

### Hot Reloading

Skills support hot reloading in development mode:

- Changes to `SKILL.md` are detected
- Changes to `handler.py` are detected
- No restart required
- Automatic re-registration

---

## Skill Discovery and Loading

### Discovery Process

#### 1. Search Path Resolution

Xuanwu searches for Skills in the following order:

1. **Workspace Skills** - `{workspace}/skills/` (for development)
2. **Skills Root** - `{skills_root}/` (third-party skills, configured via skills_root)
3. **User Skills** - `~/.xuanwu/skills/`
4. **Provider Skills** - `{providers_root}/*/skills/` (configured via providers_root)
5. **Built-in Skills** - `app/xuanwu/providers/*/skills/`

#### 2. Skill Detection

For each directory in the search path:

1. List all subdirectories
2. Check for `SKILL.md` file
3. Parse frontmatter metadata
4. Validate required fields
5. Determine Skill type

#### 3. Registration

Skills are registered based on type:

| Type | Registration Method |
|------|---------------------|
| Executable | Register handler function |
| Markdown | Index for prompt injection |
| Hybrid | Both methods |

### Loading Mechanism

#### Startup Loading

At application startup:

1. Clear existing Skill registry
2. Scan all search paths
3. Load Skills in priority order
4. Register to Agent
5. Log loaded Skills

#### Dynamic Loading

During runtime:

1. Watch filesystem for changes
2. Detect new/modified Skills
3. Validate and load
4. Update Agent registration
5. Log changes

### Skill Registry

The Skill Registry maintains:

- **Skill Index** - All discovered Skills
- **Handler Map** - Function references
- **Metadata Cache** - Parsed SKILL.md
- **Dependency Graph** - Skill relationships

### Verification

To verify Skill loading:

1. Check application logs
2. Query `/api/skills` endpoint
3. Test Skill execution
4. Review error messages

---

## Skill Execution Flow

### Execution Lifecycle

```
User Request
    ↓
Agent Intent Recognition
    ↓
Skill Selection
    ↓
Parameter Extraction
    ↓
Validation
    ↓
Execution
    ↓
Response Formatting
    ↓
Result Delivery
```

### Step-by-Step Flow

#### 1. Intent Recognition

The Agent analyzes the user request:
- Identifies desired action
- Matches to available Skills
- Determines confidence level

#### 2. Skill Selection

The Agent selects the appropriate Skill:
- Matches intent to Skill description
- Checks required parameters
- Validates prerequisites

#### 3. Parameter Extraction

The Agent extracts parameters from the request:
- Identifies named parameters
- Extracts values from context
- Applies default values

#### 4. Validation

Parameters are validated:
- Type checking
- Required field validation
- Enum value validation
- Custom validation rules

#### 5. Execution

The Skill handler is invoked:
- Dependencies injected
- Context provided
- Execution monitored
- Timeout enforced

#### 6. Response Processing

The Skill response is processed:
- Success/failure determination
- Error handling
- Result formatting
- Message generation

#### 7. Result Delivery

The result is delivered to the user:
- Formatted message
- Structured data
- Follow-up suggestions
- Error recovery options

### Execution Context

Skills receive a context object containing:

| Field | Description |
|-------|-------------|
| `user_info` | Authenticated user information |
| `session_key` | Current session identifier |
| `channel` | Communication channel |
| `abort_signal` | Cancellation signal |
| `session_manager` | Session management access |
| `memory_manager` | Memory system access |
| `extra` | Additional context data |

### Error Handling

#### Error Types

| Type | Description | Handling |
|------|-------------|----------|
| `ValidationError` | Invalid input | Return to Agent for clarification |
| `ExecutionError` | Runtime failure | Log and report to user |
| `TimeoutError` | Execution timeout | Cancel and notify |
| `PermissionError` | Access denied | Inform user of restrictions |

#### Error Response

Errors should return:
- Clear error message
- Error code
- Suggested resolution
- Technical details (if appropriate)

---

## Best Practices

### Design Principles

#### 1. Single Responsibility

Each Skill should do one thing well:
- Create a Skill for each distinct action
- Avoid monolithic Skills
- Enable Skill composition

#### 2. Clear Naming

Use descriptive, action-oriented names:
- `jira_issue_create` not `jira_create`
- `file_read` not `read`
- `slack_message_send` not `send_message`

#### 3. Comprehensive Documentation

Document thoroughly in SKILL.md:
- What the Skill does
- When to use it
- All parameters
- Expected results
- Error scenarios
- Usage examples

#### 4. Robust Error Handling

Handle errors gracefully:
- Validate all inputs
- Provide clear error messages
- Suggest corrective actions
- Log for debugging

#### 5. Security Awareness

Protect sensitive data:
- Never log credentials
- Validate permissions
- Sanitize inputs
- Use secure connections

### Implementation Guidelines

#### Input Validation

Validate all parameters:
- Type checking
- Range validation
- Format validation
- Permission checks

#### Response Design

Design clear responses:
- Success/failure indication
- Human-readable message
- Structured data
- Error details

#### Testing Strategy

Test thoroughly:
- Unit tests for logic
- Integration tests for APIs
- E2E tests for workflows
- Error scenario tests

### Documentation Best Practices

#### SKILL.md Content

Include in SKILL.md:
1. Clear description
2. Use cases
3. All parameters
4. Response format
5. Error scenarios
6. Usage examples
7. Related Skills

#### Code Comments

Comment appropriately:
- Explain WHY, not WHAT
- Document complex logic
- Reference external docs
- Note assumptions

---

## Troubleshooting

### Common Issues

#### Skill Not Loading

**Symptoms**: Skill not appearing in API

**Possible Causes**:
- Missing SKILL.md
- Invalid frontmatter
- Syntax errors
- Wrong directory location

**Resolution**:
1. Check SKILL.md exists
2. Validate YAML frontmatter
3. Check file permissions
4. Verify directory path
5. Review application logs

#### Skill Execution Fails

**Symptoms**: Skill throws error when called

**Possible Causes**:
- Missing dependencies
- Invalid parameters
- External API errors
- Permission issues

**Resolution**:
1. Check parameter validation
2. Verify external connectivity
3. Review error logs
4. Test in isolation
5. Check permissions

#### Agent Doesn't Use Skill

**Symptoms**: Agent doesn't invoke Skill

**Possible Causes**:
- Poor description
- Missing examples
- Conflicting Skills
- Low relevance

**Resolution**:
1. Improve SKILL.md description
2. Add usage examples
3. Clarify use cases
4. Check Skill naming
5. Test with explicit prompts

### Debugging Techniques

#### Enable Debug Logging

Set log level to DEBUG:
```bash
export LOG_LEVEL=DEBUG
```

#### Check Skill Registry

Query loaded Skills:
```bash
curl http://localhost:8000/api/skills
```

#### Test Skill Directly

Test Skill via API:
```bash
curl -X POST http://localhost:8000/api/skills/execute \
  -H "Content-Type: application/json" \
  -d '{"skill": "my-skill", "params": {}}'
```

#### Review Logs

Check application logs:
```bash
tail -f logs/xuanwu.log
```

### Getting Help

Resources for Skill development:
- [Xuanwu Documentation](./PROJECT_OVERVIEW.MD)
- [Provider Guide](./PROVIDER-GUIDE.MD)
- [Example Skills](../app/xuanwu/skills/built_in/)
- [Community Forum](https://github.com/xuanwu/xuanwu/discussions)

---

## Summary

### Key Takeaways

1. **Skills are atomic** - Each Skill does one thing well
2. **Documentation is critical** - SKILL.md drives Agent understanding
3. **Deployment is flexible** - Multiple locations with priority
4. **Discovery is automatic** - Skills auto-register on startup
5. **Testing is essential** - Test in isolation and integration
6. **Security matters** - Protect credentials and validate inputs

### Quick Start

1. Create Skill directory
2. Write SKILL.md with metadata
3. Implement handler (if executable)
4. Deploy to workspace or user directory
5. Restart Xuanwu or trigger reload
6. Verify via API
7. Test with Agent

### Next Steps

- Review existing Skills for examples
- Design your Skill's interface
- Write comprehensive documentation
- Implement with tests
- Deploy and validate
- Iterate based on usage

---

## Appendix

### Skill Template

```markdown
---
name: my_skill
description: Brief description of what this skill does
category: utility
version: 1.0.0
author: your@email.com
tags: ["tag1", "tag2"]
---

# My Skill

## Description

Detailed description of the skill's purpose and functionality.

## When to Use

Describe scenarios where this skill is appropriate.

## Parameters

### Input

| Name | Type | Required | Description |
|------|------|----------|-------------|
| param1 | string | Yes | Description of param1 |
| param2 | integer | No | Description of param2 (default: 10) |

### Output

| Name | Type | Description |
|------|------|-------------|
| success | boolean | Whether the operation succeeded |
| message | string | Human-readable result |
| data | object | Structured result data |

## Examples

### Example 1: Basic Usage

Input:
```json
{
  "param1": "value1"
}
```

Output:
```json
{
  "success": true,
  "message": "Operation completed",
  "data": { "result": "value" }
}
```

## Error Handling

### Common Errors

| Error | Cause | Resolution |
|-------|-------|------------|
| INVALID_PARAM | Invalid input | Check parameter format |

## Related Skills

- `related_skill_1` - Description
- `related_skill_2` - Description

## Notes

Additional information, limitations, or considerations.
```

### File Locations Summary

| Location | Path | Priority |
|----------|------|----------|
| Workspace | `{workspace}/skills/` | Highest (dev) |
| Skills Root | `{skills_root}/` | High |
| User | `~/.xuanwu/skills/` | Medium |
| Provider | `{providers_root}/*/skills/` | Low |
| Built-in | `app/xuanwu/providers/*/skills/` | Lowest |
