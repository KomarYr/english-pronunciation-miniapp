#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly APP_DIR=/opt/english-pronunciation-miniapp
readonly COMPOSE_FILE="$APP_DIR/compose.prod.yaml"
readonly RELEASE_ENV="$APP_DIR/release.env"
readonly IMAGE_REPO=ghcr.io/komaryr/english-pronunciation-miniapp
readonly HEALTH_URL=http://127.0.0.1:8000/health
readonly SCRIPT_PATH=/usr/local/sbin/ama-deploy

die() {
    printf 'ama-deploy: %s\n' "$*" >&2
    exit 1
}

check_root_path() {
    local path="$1"
    local kind="$2"
    local owner
    local mode
    local permissions

    [[ ! -L "$path" ]] || die "$path must not be a symlink"
    if [[ "$kind" == directory ]]; then
        [[ -d "$path" ]] || die "$path is not a directory"
    else
        [[ -f "$path" ]] || die "$path is not a regular file"
    fi

    owner=$(/usr/bin/stat -c '%U:%G' -- "$path") || die "cannot stat $path"
    [[ "$owner" == root:root ]] || die "$path must be owned by root:root"

    mode=$(/usr/bin/stat -c '%a' -- "$path") || die "cannot read mode for $path"
    permissions=$((8#$mode))
    (( (permissions & 0022) == 0 )) || die "$path is writable by group or others"
}

[[ $# -eq 0 ]] || die "takes no command-line arguments"
[[ "$EUID" -eq 0 ]] || die "must run as root"

check_root_path /opt directory
check_root_path "$APP_DIR" directory
check_root_path /usr/local directory
check_root_path /usr/local/sbin directory
check_root_path "$SCRIPT_PATH" file
check_root_path "$COMPOSE_FILE" file
check_root_path "$RELEASE_ENV" file
check_root_path "$APP_DIR/.env" file

env_mode=$(/usr/bin/stat -c '%a' -- "$APP_DIR/.env") || die "cannot read .env mode"
env_permissions=$((8#$env_mode))
(( (env_permissions & 0077) == 0 )) || die ".env must not be accessible to group or others"

exec 9>/run/lock/ama-deploy.lock
/usr/bin/flock -n 9 || die "another deploy is running"

IFS= read -r tag || die "missing tag on stdin"
IFS= read -r registry_user || die "missing registry username on stdin"
IFS= read -r ghcr_token || die "missing GHCR token on stdin"
extra_input=""
if IFS= read -r extra_input || [[ -n "$extra_input" ]]; then
    die "unexpected extra input"
fi

[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid release tag"
[[ -n "$registry_user" && "$registry_user" != *[[:space:]]* ]] || die "invalid registry username"
[[ -n "$ghcr_token" ]] || die "empty GHCR token"

release_lines=()
mapfile -t release_lines < "$RELEASE_ENV"
[[ "${#release_lines[@]}" -eq 1 ]] || die "release.env must contain exactly one TAG line"
[[ "${release_lines[0]}" == TAG=* ]] || die "release.env must contain exactly one TAG line"
old_tag="${release_lines[0]#TAG=}"
[[ "$old_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "release.env has no valid previous TAG"

docker_config=$(/usr/bin/mktemp -d /run/ama-deploy.XXXXXX)
tag_tmp=""

cleanup() {
    if [[ -n "${docker_config:-}" && -d "$docker_config" ]]; then
        /usr/bin/docker --config "$docker_config" logout ghcr.io >/dev/null 2>&1 || true
        if ! /usr/bin/rm -rf -- "$docker_config"; then
            printf 'ama-deploy: warning: could not remove temporary Docker config\n' >&2
        fi
    fi
    if [[ -n "$tag_tmp" && -e "$tag_tmp" ]]; then
        /usr/bin/rm -f -- "$tag_tmp" || true
    fi
}
trap cleanup EXIT

printf '%s' "$ghcr_token" | /usr/bin/docker --config "$docker_config" login ghcr.io \
    --username "$registry_user" --password-stdin
unset ghcr_token

/usr/bin/docker --config "$docker_config" pull "$IMAGE_REPO:$tag"
cd "$APP_DIR"

compose() {
    /usr/bin/docker compose --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE" "$@"
}

write_tag() {
    local next_tag="$1"
    [[ "$next_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
    tag_tmp=$(/usr/bin/mktemp "$APP_DIR/.release.env.XXXXXX")
    if ! {
        printf 'TAG=%s\n' "$next_tag" > "$tag_tmp" &&
            /usr/bin/chown root:root "$tag_tmp" &&
            /usr/bin/chmod 0644 "$tag_tmp" &&
            /usr/bin/mv -f -- "$tag_tmp" "$RELEASE_ENV";
    }; then
        /usr/bin/rm -f -- "$tag_tmp" || true
        tag_tmp=""
        return 1
    fi
    tag_tmp=""
}

wait_healthy() {
    local attempt
    for ((attempt = 0; attempt < 12; attempt++)); do
        if /usr/bin/curl --fail --silent --max-time 2 \
            --output /dev/null "$HEALTH_URL"; then
            return 0
        fi
        /usr/bin/sleep 3
    done
    return 1
}

rollback() {
    printf 'ama-deploy: restoring %s\n' "$old_tag" >&2
    write_tag "$old_tag" || die "could not restore previous release tag"
    compose up -d --pull never || die "rollback start failed"
    wait_healthy || die "rollback health check failed; manual recovery needed"
}

write_tag "$tag"
if ! compose config --quiet; then
    write_tag "$old_tag" || die "could not restore previous release tag"
    die "new Compose config failed; previous tag restored"
fi

if ! compose up -d --pull never; then
    rollback
    die "deploy failed; previous image restored"
fi

if ! wait_healthy; then
    rollback
    die "health check failed; previous image restored"
fi

printf 'ama-deploy: %s is healthy\n' "$tag"
