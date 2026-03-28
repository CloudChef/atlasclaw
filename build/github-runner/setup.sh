#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE_FILE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

print_info() {
  printf '[INFO] %s\n' "$1"
}

print_warn() {
  printf '[WARN] %s\n' "$1"
}

print_error() {
  printf '[ERROR] %s\n' "$1" >&2
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

check_prerequisites() {
  print_info "Checking prerequisites..."

  if ! command_exists docker; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    print_error "Docker Compose plugin is not available. Please install Docker Compose v2 plugin."
    exit 1
  fi

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    print_error "docker-compose.yml not found: ${COMPOSE_FILE}"
    exit 1
  fi

  if [[ ! -f "${ENV_EXAMPLE_FILE}" ]]; then
    print_error ".env.example not found: ${ENV_EXAMPLE_FILE}"
    exit 1
  fi

  print_info "Prerequisites check passed."
}

prompt_default() {
  local prompt_message="$1"
  local default_value="$2"
  local input_value
  read -r -p "${prompt_message} [${default_value}]: " input_value
  if [[ -z "${input_value}" ]]; then
    printf '%s' "${default_value}"
  else
    printf '%s' "${input_value}"
  fi
}

prompt_required() {
  local prompt_message="$1"
  local input_value
  while true; do
    read -r -p "${prompt_message}: " input_value
    if [[ -n "${input_value}" ]]; then
      printf '%s' "${input_value}"
      return
    fi
    print_warn "This field is required."
  done
}

create_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    read -r -p ".env already exists. Overwrite? [y/N]: " overwrite
    if [[ ! "${overwrite:-}" =~ ^[Yy]$ ]]; then
      print_info "Keeping existing .env file."
      return
    fi
  fi

  print_info "Creating .env file from interactive inputs..."

  local scope
  local repo_url=""
  local org_name=""
  local enterprise_name=""
  local runner_token=""
  local access_token=""

  while true; do
    scope="$(prompt_default "Runner scope (repo/org/enterprise)" "repo")"
    case "${scope}" in
      repo|org|enterprise)
        break
        ;;
      *)
        print_warn "Invalid scope. Use repo, org, or enterprise."
        ;;
    esac
  done

  case "${scope}" in
    repo)
      repo_url="$(prompt_required "GitHub repository URL (e.g. https://github.com/org/repo)")"
      ;;
    org)
      org_name="$(prompt_required "GitHub organization name")"
      ;;
    enterprise)
      enterprise_name="$(prompt_required "GitHub enterprise name")"
      ;;
  esac

  print_info "Authentication setup"
  print_info "For repo scope you can use RUNNER_TOKEN or ACCESS_TOKEN."
  print_info "For org/enterprise scope ACCESS_TOKEN is typically required."

  runner_token="$(prompt_default "RUNNER_TOKEN (leave empty if using ACCESS_TOKEN)" "")"
  access_token="$(prompt_default "ACCESS_TOKEN (leave empty if using RUNNER_TOKEN)" "")"

  if [[ -z "${runner_token}" && -z "${access_token}" ]]; then
    print_error "Either RUNNER_TOKEN or ACCESS_TOKEN must be provided."
    exit 1
  fi

  local runner_name
  local labels
  local runner_group
  local timezone
  local cpu_limit
  local memory_limit
  local memory_reservation

  runner_name="$(prompt_default "Runner name" "xuanwu-runner")"
  labels="$(prompt_default "Runner labels (comma-separated)" "self-hosted,linux,x64,docker")"
  runner_group="$(prompt_default "Runner group" "Default")"
  timezone="$(prompt_default "Timezone" "Asia/Shanghai")"
  cpu_limit="$(prompt_default "CPU limit" "2")"
  memory_limit="$(prompt_default "Memory limit" "4g")"
  memory_reservation="$(prompt_default "Memory reservation" "2g")"

  cat >"${ENV_FILE}" <<EOF
RUNNER_SCOPE=${scope}
REPO_URL=${repo_url}
ORG_NAME=${org_name}
ENTERPRISE_NAME=${enterprise_name}
RUNNER_TOKEN=${runner_token}
ACCESS_TOKEN=${access_token}
RUNNER_NAME=${runner_name}
RUNNER_NAME_PREFIX=runner
RUNNER_GROUP=${runner_group}
LABELS=${labels}
NO_DEFAULT_LABELS=false
RUNNER_WORKDIR=/tmp/github-runner-workdir
CONFIGURED_ACTIONS_RUNNER_FILES_DIR=/runner/data
RUN_AS_ROOT=true
EPHEMERAL=false
DISABLE_AUTO_UPDATE=false
UNSET_CONFIG_VARS=true
GITHUB_HOST=github.com
START_DOCKER_SERVICE=false
TZ=${timezone}
DEBUG_ONLY=false
DEBUG_OUTPUT=false
CPU_LIMIT=${cpu_limit}
MEMORY_LIMIT=${memory_limit}
MEMORY_RESERVATION=${memory_reservation}
LOG_MAX_SIZE=100m
LOG_MAX_FILE=5
EOF

  chmod 600 "${ENV_FILE}"
  print_info ".env created at ${ENV_FILE}"
}

start_runner() {
  print_info "Pulling runner image..."
  docker compose -f "${COMPOSE_FILE}" pull

  print_info "Starting GitHub runner..."
  docker compose -f "${COMPOSE_FILE}" up -d

  print_info "Deployment status:"
  docker compose -f "${COMPOSE_FILE}" ps
}

print_next_steps() {
  cat <<'EOF'

Setup complete.

Next steps:
1) Verify runner is online in GitHub Settings -> Actions -> Runners.
2) Check logs if needed:
   docker compose logs -f github-runner
3) Manage lifecycle:
   docker compose up -d
   docker compose down
   docker compose restart github-runner

EOF
}

main() {
  print_info "GitHub Self-Hosted Runner setup"
  print_info "Target server (from request): 192.168.16.202 (root)"

  check_prerequisites
  create_env_file
  start_runner
  print_next_steps
}

main "$@"
