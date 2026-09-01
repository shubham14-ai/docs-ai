# Convenience targets. Everything here is a thin wrapper: the Docker targets
# delegate to ./doc-ai.sh, the bare-pytest targets run against whatever Mongo
# and Redis are reachable (integration tests skip themselves when they are not).
#
# The reporting lives in tests/report_plugin.py, so every target below prints
# the same tree + summary + release box and writes ./test-results.

.PHONY: test test-unit test-integration test-edge test-release \
        docker-test docker-test-unit docker-test-integration \
        docker-test-edge docker-test-release clean-results

PYTEST ?= python -m pytest

# --- local (no containers) -------------------------------------------------

test:                    ## Whole suite
	$(PYTEST)

test-unit:               ## Unit level only
	$(PYTEST) tests/unit

test-integration:        ## Integration level only (needs Mongo + Redis)
	$(PYTEST) tests/integration

test-edge:               ## Every test marked @pytest.mark.edge
	$(PYTEST) -m edge

test-release:            ## Release gate: non-zero exit if anything fails
	$(PYTEST) tests/unit tests/integration --no-tree

# --- containerised (Mongo + Redis started by compose) ----------------------

docker-test:
	./doc-ai.sh test

docker-test-unit:
	./doc-ai.sh test unit

docker-test-integration:
	./doc-ai.sh test integration

docker-test-edge:
	./doc-ai.sh test edge

docker-test-release:     ## The CI entry point
	./doc-ai.sh test release

clean-results:
	rm -rf test-results
