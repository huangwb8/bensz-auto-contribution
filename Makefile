IMAGE ?= huangwb8/bensz-auto-contribution
VERSION ?=
PUSH ?= 1
DRY_RUN ?= 0
FORCE ?= 0
SKIP_TESTS ?= 0
ALLOW_DIRTY ?= 0
VERIFY_PULL ?= 1

export IMAGE VERSION PUSH DRY_RUN FORCE SKIP_TESTS ALLOW_DIRTY VERIFY_PULL

.PHONY: dockerhub-publish

dockerhub-publish:
	@bash tools/dockerhub-publish.sh
