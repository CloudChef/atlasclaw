# Xuanwu Provider Development Guide

## Table of Contents

1. [Overview](#overview)
2. [Provider Architecture](#provider-architecture)
3. [Directory Structure](#directory-structure)
4. [Provider Configuration](#provider-configuration)
5. [Skills Development](#skills-development)
6. [Jira Provider Example](#jira-provider-example)
7. [Best Practices](#best-practices)
8. [Testing](#testing)

---

## Overview

### What is a Provider?

A **Provider** in Xuanwu is a modular integration package that connects the AI Agent to external enterprise systems. Providers encapsulate:

- **Authentication logic** - How to connect to the external system
- **Skills** - Actions the Agent can perform on the system
- **Configuration** - Connection parameters and settings
- **Documentation** - How the Agent should use the integration

### Provider Types

| Type | Description | Example |
|------|-------------|---------|
| **ITSM** | IT Service Management | Jira, ServiceNow |
| **CRM** | Customer Relationship Management | Salesforce, HubSpot |
| **Monitoring** | System monitoring | Datadog, Prometheus |
| **Communication** | Messaging platforms | Slack, Teams |
| **Custom** | Internal systems | Internal APIs |

---

## Provider Architecture

### Design Principles

1. **Self-Contained** - Each provider is an independent package
2. **Configuration-Driven** - Connection details via configuration files
3. **Skill-Based** - Functionality exposed through Skills
4. **Permission-Aware** - Respects user's actual system permissions
5. **Documented** - Comprehensive documentation for the Agent

### Component Interaction

```
User Request
    ↓
Agent Runner
    ↓
Provider Skill
    ↓
Provider Configuration
    ↓
External System API
    ↓
Response
```

### Provider Lifecycle

1. **Registration** - Provider configured in `xuanwu.json`
2. **Discovery** - Skills auto-discovered at startup
3. **Selection** - User or Agent selects provider instance
4. **Execution** - Skill executes with provider credentials
5. **Response** - Results returned to Agent

---

## Directory Structure

### Standard Provider Layout

```
providers/<provider-name>/
├── PROVIDER.md              # Provider documentation (required)
├── README.md                # Human-readable description
├── skills/                  # Provider Skills
│   └── <skill-name>/
│       ├── SKILL.md         # Skill metadata and documentation
│       └── scripts/         # Skill implementation
│           ├── __init__.py
│           ├── handler.py   # Main skill handler
│           └── _utils.py    # Helper functions
└── references/              # Reference documentation
    └── api_mapping.md       # API endpoint mapping
```

### File Purposes

| File | Purpose | Required |
|------|---------|----------|
| `PROVIDER.md` | Provider metadata and capabilities | Yes |
| `README.md` | Human-readable overview | Recommended |
| `SKILL.md` | Skill definition and usage | Yes |
| `handler.py` | Skill implementation | Yes |
| `references/` | Additional documentation | Optional |

---

## Provider Configuration

### Configuration Schema

Providers are configured in `xuanwu.json` under `service_providers`:

```json
{
  "service_providers": {
    "<provider-type>": {
      "<instance-name>": {
        "base_url": "https://api.example.com",
        "username": "${USERNAME}",
        "password": "${PASSWORD}",
        "api_version": "2",
        "default_project": "DEFAULT"
      }
    }
  }
}
```

### Configuration Fields

| Field | Description | Required |
|-------|-------------|----------|
| `base_url` | API base URL | Yes |
| `username` | Authentication username | Optional |
| `password` | Authentication password/token | Optional |
| `api_version` | API version to use | Optional |
| `default_project` | Default project/key | Optional |

### Environment Variables

Sensitive configuration should use environment variables:

```json
{
  "password": "${JIRA_PASSWORD}"
}
```

Set in environment:
```bash
export JIRA_PASSWORD="your-secure-password"
```

---

## PROVIDER.md Structure

Each provider requires a `PROVIDER.md` file that serves two purposes:
1. **Human documentation** - Connection parameters, configuration examples
2. **LLM context** - Semantic information for skill discovery

### Frontmatter Schema

The PROVIDER.md file uses YAML frontmatter to define LLM context fields:

```yaml
---
# === Required Fields ===
provider_type: jira              # Unique identifier (must match directory name)
display_name: Jira               # Human-readable name
version: "1.0.0"                 # Provider version

# === LLM Context Fields (for Skill Discovery) ===
# Semantic matching keywords (English only)
keywords:
  - issue
  - story
  - sprint
  - project
  - backlog

# High-level capabilities description
capabilities:
  - Create and manage issues
  - Search issues with JQL
  - Track project progress
  - Manage sprints and boards

# When users should consider this provider
use_when:
  - User mentions issue tracking or project management
  - User wants to create, search, or update issues
  - User references Jira or Atlassian products

# When NOT to use this provider (disambiguation)
avoid_when:
  - User is asking about documentation (use Confluence provider)
  - User wants to manage code repositories (use Bitbucket/GitHub provider)
---
```

### LLM Context Fields Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider_type` | string | Yes | Unique provider identifier |
| `display_name` | string | Yes | Human-readable name |
| `version` | string | No | Provider version |
| `keywords` | list[string] | No | Semantic matching keywords (English only) |
| `capabilities` | list[string] | No | High-level capability descriptions |
| `use_when` | list[string] | No | Scenarios when this provider applies |
| `avoid_when` | list[string] | No | Scenarios to use a different provider |

### Writing Effective LLM Context

**Keywords**: Focus on domain-specific terms users might say:
- Good: `issue`, `story`, `sprint`, `bug`
- Avoid: Generic terms like `create`, `update`, `manage`

**use_when**: Describe user intent scenarios:
- Good: "User wants to create or track bugs"
- Avoid: "User uses Jira" (too obvious)

**avoid_when**: Critical for disambiguation:
- Include similar providers that might be confused
- Explain which provider should be used instead

---

## Skills Development

### Skill Types

#### 1. Action Skills

Perform operations on the external system:
- Create resources
- Update resources
- Delete resources
- Execute commands

#### 2. Query Skills

Retrieve information from the external system:
- List resources
- Get details
- Search
- Report generation

#### 3. Workflow Skills

Combine multiple actions into workflows:
- Multi-step processes
- Conditional logic
- Error handling

### Skill Metadata

Each skill requires metadata defining:

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Unique skill identifier | `jira_issue_create` |
| `description` | What the skill does | "Create a Jira issue" |
| `category` | Skill category | `provider:jira` |
| `provider_type` | Associated provider | `jira` |
| `instance_required` | Needs provider instance | `true` |

### Skill Parameters

Skills accept parameters defined by:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `name` | Parameter name | `summary` |
| `type` | Data type | `str`, `int`, `bool` |
| `description` | Parameter description | "Issue summary" |
| `required` | Is required | `true` / `false` |
| `default` | Default value | `"Task"` |

### Skill Response

Skills return structured responses:

| Field | Description |
|-------|-------------|
| `success` | Operation success status |
| `message` | Human-readable result |
| `data` | Structured result data |
| `error` | Error details (if failed) |

---

## Jira Provider Example

### Provider Overview

The Jira Provider enables the AI Agent to interact with Jira instances for issue tracking and project management.

### Capabilities

#### Issue Management
- Create new issues
- Update existing issues
- Delete issues
- Retrieve issue details
- List issues by project

#### Project Operations
- List available projects
- Get project details
- List project components
- List issue types

#### Search and Reporting
- Search issues with JQL
- List active issues
- Generate reports

### Configuration Requirements

#### Connection Parameters
- **Base URL** - Jira instance URL (e.g., `https://jira.company.com`)
- **Authentication** - Username/password or API token
- **API Version** - REST API version (typically "2")
- **Default Project** - Default project key for operations

#### Authentication Methods
- **Basic Auth** - Username and password/token
- **API Token** - Personal access token (recommended)
- **OAuth** - OAuth 2.0 (if supported by instance)

### Skills Provided

#### 1. Issue Creation Skill

**Purpose**: Create new Jira issues

**Input Requirements**:
- Summary (title) of the issue
- Description of the issue
- Issue type (Bug, Task, Story, etc.)
- Project key (optional, uses default)
- Priority (optional)

**Auto-Detection Features**:
- Detects required fields for the project
- Automatically selects first available component if required
- Validates issue type against project

**Output**:
- Created issue key (e.g., "PROJ-123")
- Issue ID
- Project key
- Confirmation message

#### 2. Issue Retrieval Skill

**Purpose**: Get details of an existing issue

**Input Requirements**:
- Issue key (e.g., "PROJ-123")

**Output**:
- Issue summary
- Description
- Status
- Assignee
- Reporter
- Created/updated dates
- Comments
- Custom fields

#### 3. Issue Update Skill

**Purpose**: Modify existing issues

**Input Requirements**:
- Issue key
- Fields to update (summary, description, status, etc.)

**Supported Updates**:
- Summary
- Description
- Priority
- Assignee
- Labels
- Custom fields

#### 4. Issue Deletion Skill

**Purpose**: Remove issues from Jira

**Input Requirements**:
- Issue key

**Safety Features**:
- Confirmation required
- Permission checks
- Audit logging

#### 5. Issue Listing Skill

**Purpose**: List issues in a project

**Input Requirements**:
- Project key (optional)
- Maximum results (optional)

**Output**:
- List of issues with key, summary, status
- Total count
- Pagination info

### Provider Tools

The Jira Provider includes tools for:

#### Instance Management
- **List Instances** - Show configured Jira instances
- **Select Instance** - Choose which instance to use
- **Test Connection** - Verify connectivity

#### Metadata Retrieval
- **Get Projects** - List accessible projects
- **Get Issue Types** - List available issue types
- **Get Components** - List project components
- **Get Priorities** - List priority levels

### Error Handling

#### Common Error Scenarios

| Error | Cause | Resolution |
|-------|-------|------------|
| Authentication Failed | Invalid credentials | Check username/password |
| Project Not Found | Invalid project key | Verify project exists |
| Issue Type Required | Missing issue type | Specify valid issue type |
| Component Required | Project requires component | Select available component |
| Permission Denied | Insufficient permissions | Check user permissions |
| API Rate Limited | Too many requests | Wait and retry |

#### Error Response Format

Errors return structured information:
- Error code
- Human-readable message
- Suggested resolution
- Technical details (for debugging)

### Security Considerations

#### Authentication Security
- Store credentials in environment variables
- Use API tokens instead of passwords when possible
- Implement token rotation
- Never log credentials

#### Permission Enforcement
- Respect Jira's native permissions
- Don't bypass RBAC
- Log access for audit
- Validate user actions

#### Data Protection
- Don't cache sensitive data
- Sanitize logs
- Use HTTPS only
- Validate SSL certificates

### Documentation Requirements

#### PROVIDER.md Content

Must include:
1. Provider name and version
2. Supported Jira versions
3. Authentication methods
4. Required permissions
5. Available skills overview
6. Configuration examples
7. Troubleshooting guide

#### SKILL.md Content

Must include:
1. Skill name and description
2. When to use this skill
3. Input parameters
4. Expected output
5. Error scenarios
6. Usage examples
7. Related skills

#### API Mapping Documentation

Should document:
1. Jira REST API endpoints used
2. Authentication flow
3. Rate limits
4. Data transformations
5. Field mappings

---

## Best Practices

### Design Principles

#### 1. User-Centric Design
- Skills should match user workflows
- Use business terminology, not technical terms
- Provide sensible defaults
- Guide users through complex operations

#### 2. Robust Error Handling
- Validate inputs before API calls
- Provide clear error messages
- Suggest corrective actions
- Graceful degradation

#### 3. Performance Optimization
- Cache metadata when appropriate
- Use pagination for large datasets
- Implement request batching
- Handle timeouts gracefully

#### 4. Security First
- Never expose credentials
- Validate all inputs
- Use principle of least privilege
- Log security events

### Implementation Guidelines

#### Configuration Management
- Support multiple instances
- Use environment variables for secrets
- Validate configuration at startup
- Provide helpful error messages

#### API Client Design
- Reuse connections
- Implement retry logic
- Handle rate limiting
- Support connection pooling

#### Response Processing
- Normalize API responses
- Handle missing fields
- Convert data types
- Format for Agent consumption

### Testing Strategy

#### Unit Tests
- Test skill logic in isolation
- Mock external API calls
- Validate input/output
- Test error scenarios

#### Integration Tests
- Test with real Jira instance
- Verify end-to-end workflows
- Test authentication flows
- Validate error handling

#### E2E Tests
- Full Agent workflow tests
- Multiple skill coordination
- User scenario simulation
- Performance validation

---

## Testing

### Test Environment Setup

#### Requirements
- Test Jira instance
- Test user account
- Sample projects and issues
- Test data fixtures

#### Configuration
```json
{
  "service_providers": {
    "jira": {
      "test": {
        "base_url": "https://jira-test.company.com",
        "username": "test-user",
        "password": "${TEST_JIRA_PASSWORD}",
        "default_project": "TEST"
      }
    }
  }
}
```

### Test Cases

#### Connection Tests
- Valid credentials
- Invalid credentials
- Network errors
- Timeout scenarios

#### Skill Tests
- Happy path scenarios
- Validation failures
- Permission errors
- Edge cases

#### Integration Tests
- Multi-skill workflows
- Error recovery
- Concurrent operations
- Large data handling

### Test Data Management

#### Fixtures
- Sample projects
- Test issues
- User accounts
- Configuration variants

#### Cleanup
- Delete test issues
- Reset test data
- Remove temporary resources
- Restore original state

---

## Provider Deployment and Loading

### Provider Locations

Providers can be deployed to multiple locations:

#### 1. Built-in Providers

**Location**: `app/xuanwu/providers/<provider-name>/`

**Characteristics**:
- Shipped with Xuanwu Core
- Core integrations (Jira, etc.)
- Updated with Core releases
- Always available

**Use Case**: Essential system integrations maintained by the Core team.

#### 2. External Providers (Recommended)

**Location**: `<providers_root>/<provider-name>/` (default: `../providers/`)

**Characteristics**:
- External directory configured via `providers_root` in `xuanwu.json`
- Enterprise integrations maintained separately from user data
- Version controlled independently
- Default location: sibling directory to project root

**Use Case**: Enterprise provider integrations (Jira, ServiceNow, etc.).

#### 3. Workspace Providers (Development)

**Location**: `<workspace>/providers/<provider-name>/` (optional)

**Characteristics**:
- Development-time overrides
- Local testing and debugging
- Highest priority if exists

**Use Case**: Local development and testing of Providers.

#### 3. User Providers

**Location**: `~/.xuanwu/providers/<provider-name>/`

**Characteristics**:
- User-specific across all workspaces
- Personal integrations
- Survives workspace changes

**Use Case**: Personal or experimental Providers.

### Provider Discovery and Loading

#### Discovery Process

Xuanwu discovers Providers through this process:

1. **Search Path Resolution**
   - Scan built-in Providers directory
   - Scan workspace Providers directory
   - Scan user Providers directory

2. **Provider Detection**
   - Look for `PROVIDER.md` in each subdirectory
   - Parse Provider metadata
   - Validate required fields

3. **Skill Discovery**
   - Scan `skills/` subdirectory
   - Load each Skill's `SKILL.md`
   - Register Skills to Agent

#### Loading Mechanism

**Startup Loading**:
1. Clear Provider registry
2. Scan all Provider locations
3. Load Provider metadata
4. Discover and load Skills
5. Register Skills to Agent
6. Log loaded Providers and Skills

**Dynamic Loading**:
- File system watchers detect changes
- New/modified Providers auto-reload
- No restart required in development

### Provider Configuration Loading

#### Configuration Sources

Provider configuration is loaded from `xuanwu.json`:

```json
{
  "service_providers": {
    "<provider-type>": {
      "<instance-name>": {
        "base_url": "...",
        "username": "...",
        "password": "..."
      }
    }
  }
}
```

#### Instance Registration

Each configured instance becomes available to the Agent:
- Instance name is displayed to users
- Credentials are injected at runtime
- Multiple instances per Provider type

### Deployment Process

#### Step 1: Create Provider Directory

Create the Provider structure:

```
providers/my-provider/
├── PROVIDER.md
├── README.md
└── skills/
    └── my-skill/
        ├── SKILL.md
        └── scripts/
            └── handler.py
```

#### Step 2: Write Documentation

Create `PROVIDER.md` with:
- Provider metadata
- Supported versions
- Authentication methods
- Available Skills

#### Step 3: Implement Skills

Create Skills following the [Skill Development Guide](./SKILL-GUIDE.MD).

#### Step 4: Configure Instance

Add to `xuanwu.json`:

```json
{
  "service_providers": {
    "my-provider": {
      "production": {
        "base_url": "https://api.example.com",
        "api_key": "${API_KEY}"
      }
    }
  }
}
```

#### Step 5: Deploy Provider

Copy Provider directory to target location:
- External (recommended): `<providers_root>/` (configured in `xuanwu.json`, default: `../providers/`)
- Built-in: `app/xuanwu/providers/`
- Workspace (dev): `<workspace>/providers/`
- User: `~/.xuanwu/providers/`

#### Step 6: Verify Loading

1. Restart Xuanwu (or trigger reload)
2. Check logs for Provider loading
3. Query `/api/skills` endpoint
4. Test Provider functionality

### Verification

#### Check Provider Loading

Review application logs:
```
[Xuanwu] Provider loaded: my-provider
[Xuanwu] Skills loaded: 3 from my-provider
```

#### List Available Skills

```bash
curl http://localhost:8000/api/skills | grep my-provider
```

#### Test Provider

```bash
# List configured instances
curl http://localhost:8000/api/providers/my-provider/instances

# Execute a Skill
curl -X POST http://localhost:8000/api/skills/execute \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "my-provider/my-skill",
    "params": {...}
  }'
```

### Hot Reloading

In development mode:
- Changes to `PROVIDER.md` trigger reload
- Changes to Skills trigger reload
- Configuration changes trigger reload
- No restart required

### Troubleshooting Deployment

#### Provider Not Loading

**Symptoms**: Provider not appearing in API

**Check**:
1. `PROVIDER.md` exists and is valid
2. Directory is in correct location
3. File permissions are correct
4. No syntax errors in metadata

#### Skills Not Registered

**Symptoms**: Provider loads but Skills missing

**Check**:
1. `SKILL.md` exists in each Skill directory
2. Skill frontmatter is valid
3. Handler file exists (for executable Skills)
4. Check logs for parsing errors

#### Configuration Not Found

**Symptoms**: Provider loads but no instances available

**Check**:
1. `xuanwu.json` has Provider configuration
2. Instance name is correct
3. Environment variables are set
4. Configuration syntax is valid

---

## Deployment Environments

### Development

**Characteristics**:
- Local configuration files
- Environment variables in `.env`
- Hot reloading enabled
- Debug logging

**Best Practices**:
- Use test instances
- Keep credentials in `.env`
- Enable verbose logging
- Use workspace Providers

### Staging

**Characteristics**:
- Production-like environment
- Integration testing
- Performance validation
- Shared configuration

**Best Practices**:
- Mirror production setup
- Use staging instances
- Test all Skills
- Validate error handling

### Production

**Characteristics**:
- High availability
- Secure credential storage
- Monitoring and alerting
- Backup and recovery

**Best Practices**:
- Use secret management (Vault, etc.)
- Enable audit logging
- Monitor performance
- Regular security updates

---

## Provider Distribution

### Built-in Providers
- Included in Xuanwu Core
- Auto-discovered at startup
- Updated with Core releases

### Custom Providers
- Placed in workspace directory
- Loaded dynamically
- Independent versioning

### Third-Party Providers
- Distributed as packages
- Installed via pip or manual
- Registered in configuration

---

## Summary

### Key Takeaways

1. **Providers are modular** - Self-contained integration packages
2. **Skills are the interface** - Agent interacts through Skills
3. **Configuration is flexible** - Multiple instances, environment-based
4. **Documentation is critical** - Both for humans and AI
5. **Security is paramount** - Respect permissions, protect credentials
6. **Testing is essential** - Unit, integration, and E2E tests

### Next Steps

1. Review existing Provider implementations
2. Design your Provider's Skill set
3. Create Provider documentation
4. Implement Skills with tests
5. Deploy and validate

### Resources

- [OpenSpec Provider Guide](../openspec/PROVIDERS.md)
- [Jira REST API Documentation](https://developer.atlassian.com/cloud/jira/platform/rest/v2/)
- [Xuanwu Core Documentation](./PROJECT_OVERVIEW.MD)
- [Skill Development Guide](./SKILL-GUIDE.MD)
