#!/bin/bash

# ====================================================================
# Document Insights API — Docker Operations CLI
# ====================================================================
# Thin wrapper around `docker compose` for this stack.
# Services : api | worker | mongo | redis | tests
# Modes    : dev (default, live reload) | prod (baked image, no reloader)
#
# The image is multi-stage (base -> deps -> dev-deps -> dev / prod).
# In dev, compose bind-mounts ./app and both processes reload in place:
# a code change costs nothing -- no build, no image layer, no restart.
# Only a change to requirements*.txt needs `./doc-ai.sh build`.
#
#   ./doc-ai.sh init            first run: build + start
#   ./doc-ai.sh up              start
#   MODE=prod ./doc-ai.sh up    start the prod flavour
# ====================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_status()  { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error()   { echo -e "${RED}[X]${NC} $1"; }
print_header()  { echo -e "\n${BLUE}=== $1 ===${NC}"; }
print_info()    { echo -e "${CYAN}[i]${NC} $1"; }

# BuildKit gives us the pip cache mounts and stage-level parallelism.
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

cd "$(dirname "$0")"

# dev applies docker-compose.override.yml (bind mounts + reload).
# prod names the base file explicitly, which ignores the override.
MODE="${MODE:-dev}"
case "$MODE" in
    dev)  COMPOSE="docker compose" ;;
    prod) COMPOSE="docker compose -f docker-compose.yml" ;;
    *)    print_error "Unknown MODE (expected dev or prod)"; exit 1 ;;
esac

# ====================================================================
# BUILD
# ====================================================================

# Full rebuild — only when the layer cache cannot see a dependency change
# (a moved pin, a yanked wheel, a stale base image).
build() {
    print_header "FULL BUILD (mode: ${MODE}, no cache)"
    print_warning "Only needed when dependencies change; source edits need no build at all"
    start=$(date +%s)
    $COMPOSE build --no-cache
    print_status "Built in $(( $(date +%s) - start ))s"
    print_info "Next: ./doc-ai.sh up"
}

# Cached rebuild + recreate. The pip layers are reused unless requirements moved.
rebuild() {
    print_header "QUICK REBUILD (cached, mode: ${MODE})"
    start=$(date +%s)
    $COMPOSE build
    $COMPOSE up -d
    print_status "Rebuilt in $(( $(date +%s) - start ))s"
    show_urls
}

# Build one image stage directly: base | deps | dev-deps | dev | prod
build_target() {
    local stage="${2:-dev}"
    print_header "BUILD IMAGE (target: ${stage})"
    start=$(date +%s)
    docker build --target "${stage}" -t "docs-ai:${stage}" .
    print_status "docs-ai:${stage} built in $(( $(date +%s) - start ))s"
}

# ====================================================================
# LIFECYCLE
# ====================================================================

init() {
    print_header "BUILD + START (mode: ${MODE})"
    if [ ! -f ".env" ]; then
        print_warning ".env not found - the defaults in docker-compose.yml will be used"
        print_info "For explicit control: cp .env.example .env"
    fi
    start=$(date +%s)
    $COMPOSE build
    $COMPOSE up -d
    print_header "READY"
    print_status "Time: $(( $(date +%s) - start ))s"
    show_urls
    if [ "$MODE" = "dev" ]; then
        print_info "Code in ./app reloads live - edit a file and call the API again."
    fi
    print_info "Logs: ./doc-ai.sh logs worker"
}

up() {
    print_status "Starting stack (mode: ${MODE})..."
    $COMPOSE up -d
    print_status "Stack started"
    show_urls
}

down() {
    print_status "Stopping stack..."
    $COMPOSE down
    print_status "Stack stopped"
}

restart() {
    print_header "RESTARTING STACK"
    down
    up
}

restart_one() {
    local service="$2"
    if [ -z "$service" ]; then
        print_error "Service name required"
        print_info "Usage: ./doc-ai.sh restart-svc <api|worker|mongo|redis>"
        exit 1
    fi
    print_status "Restarting $service..."
    $COMPOSE restart "$service"
    print_status "$service restarted"
}

# More processing capacity, same single API process. One consumer group, so
# the stream still delivers each document to exactly one of them.
scale() {
    local n="${2:-3}"
    print_header "SCALING WORKERS TO ${n}"
    $COMPOSE up -d --scale "worker=${n}"
    print_status "${n} worker processes, one consumer group"
    $COMPOSE ps worker
}

# ====================================================================
# MONITORING
# ====================================================================

logs() {
    local service="$2"
    if [ -z "$service" ]; then
        print_info "Logs for all services (Ctrl+C to exit)"
        $COMPOSE logs -f
    else
        print_info "Logs for $service (Ctrl+C to exit)"
        $COMPOSE logs -f "$service"
    fi
}

status() {
    print_header "SERVICE STATUS (mode: ${MODE})"
    $COMPOSE ps
    echo ""
    print_header "RESOURCE USAGE"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
}

# /health returns 503 when a dependency is down, so a 200 here means the stack
# is actually usable - not merely that the process is listening.
health() {
    print_header "HEALTH CHECKS"
    echo -n "API:   "
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        print_status "Healthy"
    else
        print_error "Unhealthy or unreachable (./doc-ai.sh logs api)"
        curl -s http://localhost:8000/health || true
        echo ""
    fi
    echo -n "Mongo: "
    if $COMPOSE exec -T mongo mongosh --quiet --eval "db.adminCommand({ping:1})" > /dev/null 2>&1; then
        print_status "Healthy"
    else
        print_error "Unreachable"
    fi
    echo -n "Redis: "
    if $COMPOSE exec -T redis redis-cli ping > /dev/null 2>&1; then
        print_status "Healthy"
    else
        print_error "Unreachable"
    fi
}

shell() {
    local service="${2:-api}"
    print_info "Opening a shell in '$service'..."
    $COMPOSE exec "$service" bash || $COMPOSE exec "$service" sh
}

# The suite needs a real MongoDB and Redis; both are dependencies of the tests
# service, so compose starts them if they are not already up.
#
#   ./doc-ai.sh test                 whole suite
#   ./doc-ai.sh test unit            unit level only
#   ./doc-ai.sh test integration     integration level only
#   ./doc-ai.sh test edge            every test marked @pytest.mark.edge
#   ./doc-ai.sh test release         whole suite, CI-shaped, no tree
#   ./doc-ai.sh test tests/unit -v   anything else is passed to pytest as-is
#
# The exit code is pytest's, so `./doc-ai.sh test release` is directly usable
# as a release gate. Reports are written to ./test-results on the host.
run_tests() {
    shift || true

    # The bind mount has to exist on the host first, and be writable by the
    # container's non-root user (uid 10001) -- on a Linux CI runner it would
    # otherwise be created root-owned and every report write would fail.
    mkdir -p test-results
    chmod a+rwx test-results 2>/dev/null || true

    local level="$1"
    local args=()
    case "$level" in
        unit)        print_header "TEST SUITE - UNIT";        args=(tests/unit) ;;
        integration) print_header "TEST SUITE - INTEGRATION"; args=(tests/integration) ;;
        edge)        print_header "TEST SUITE - EDGE CASES";  args=(-m edge) ;;
        release)     print_header "TEST SUITE - RELEASE GATE"
                     args=(tests/unit tests/integration --no-tree) ;;
        all|"")      print_header "TEST SUITE"; args=() ;;
        *)           print_header "TEST SUITE"; args=("$@") ;;
    esac
    # Consume the level word only when it was one of ours.
    case "$level" in
        unit|integration|edge|release|all) shift ;;
        "") ;;
        *) set -- ;;
    esac

    # `run` returns the container's exit code; `set -e` would abort before the
    # summary is printed, so the status is captured and re-raised deliberately.
    set +e
    docker compose --profile test run --rm tests pytest "${args[@]}" "$@"
    local status=$?
    set -e

    if [ "$status" -eq 0 ]; then
        print_status "Release gate: PASS (exit 0)"
    else
        print_error "Release gate: FAIL (exit ${status})"
    fi
    print_info "Reports: ./test-results (junit.xml, summary.json, report.txt, html/index.html)"
    return "$status"
}

# ====================================================================
# MAINTENANCE
# ====================================================================

prune() {
    print_status "Pruning unused Docker resources..."
    docker system prune -f
    print_status "Pruning completed"
}

cache() {
    print_header "CLEARING BUILD CACHE"
    print_warning "The next build re-downloads every wheel"
    read -p "Type YES to continue: " -r
    [ "$REPLY" = "YES" ] || { print_info "Cancelled"; exit 0; }
    docker builder prune -a -f
    docker system prune -f
    print_status "Build cache cleared"
}

# Destructive: also drops mongo-data and redis-data.
clean() {
    print_header "CLEAN UP (destructive)"
    print_warning "Removes containers, images AND named volumes (stored documents are lost)"
    read -p "Type YES to continue: " -r
    [ "$REPLY" = "YES" ] || { print_info "Cancelled"; exit 0; }
    docker compose --profile test down -v --remove-orphans
    docker rmi docs-ai:dev docs-ai:prod docs-ai-ui:dev docs-ai-ui:prod 2>/dev/null || true
    print_status "Cleanup completed"
}

# ====================================================================
# UTILITY
# ====================================================================

show_urls() {
    echo ""
    print_header "SERVICE URLS"
    echo "  UI:       http://localhost:${UI_PORT:-3000}"
    echo "  API:      http://localhost:8000"
    echo "  OpenAPI:  http://localhost:8000/docs"
    echo "  Health:   http://localhost:8000/health"
    echo "  Mongo:    localhost:27017"
    echo "  Redis:    localhost:6379"
    echo ""
}

usage() {
    echo ""
    echo "=================================================================================="
    echo "                  DOCUMENT INSIGHTS API - DOCKER OPERATIONS CLI"
    echo "=================================================================================="
    echo ""
    echo "SERVICES : api | worker | mongo | redis | tests"
    echo "MODES    : dev (default, ./app bind-mounted, live reload) | prod (baked image)"
    echo "           Select with an env var, e.g.:  MODE=prod ./doc-ai.sh up"
    echo ""
    echo "BUILD"
    echo "  init                       - Build images and start the stack"
    echo "  build                      - Full rebuild, no cache (dependencies changed)"
    echo "  rebuild                    - Fast cached rebuild + recreate containers"
    echo "  build-target [stage]       - Build one stage (base|deps|dev-deps|dev|prod)"
    echo ""
    echo "LIFECYCLE"
    echo "  up | start                 - Start the stack (detached)"
    echo "  down | stop                - Stop and remove containers"
    echo "  restart                    - Restart the whole stack"
    echo "  restart-svc <service>      - Restart a single service"
    echo "  scale [n]                  - Run n worker processes (default 3)"
    echo ""
    echo "MONITORING"
    echo "  logs [service]             - Stream logs (all, or one service)"
    echo "  status                     - Container status + resource usage"
    echo "  health                     - Health checks (api / mongo / redis)"
    echo "  shell [service]            - Shell into a container (default: api)"
    echo "  test                       - Whole suite against real Mongo and Redis"
    echo "  test unit                  - Unit level only"
    echo "  test integration           - Integration level only"
    echo "  test edge                  - Edge-case tests only (-m edge)"
    echo "  test release               - Release gate: whole suite, non-zero exit on failure"
    echo "  test [pytest args]          - Anything else is forwarded to pytest"
    echo "                               Reports land in ./test-results"
    echo ""
    echo "MAINTENANCE"
    echo "  prune                      - Remove unused Docker resources"
    echo "  cache                      - Clear the Docker build cache (keeps images)"
    echo "  clean                      - Remove containers, images, volumes (destructive)"
    echo ""
}

# ====================================================================
# MAIN
# ====================================================================

case "$1" in
    init)         init ;;
    build)        build ;;
    rebuild)      rebuild ;;
    build-target) build_target "$@" ;;
    up|start)     up ;;
    down|stop)    down ;;
    restart)      restart ;;
    restart-svc)  restart_one "$@" ;;
    scale)        scale "$@" ;;
    logs)         logs "$@" ;;
    status)       status ;;
    health)       health ;;
    shell)        shell "$@" ;;
    test)         run_tests "$@" ;;
    prune)        prune ;;
    cache)        cache ;;
    clean)        clean ;;
    *)            usage; exit 1 ;;
esac
