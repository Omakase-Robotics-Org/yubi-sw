MAKEFILE_DIR := $(patsubst %/,%,$(dir $(realpath $(lastword $(MAKEFILE_LIST)))))

ifneq (,$(wildcard $(MAKEFILE_DIR)/.env))
    include $(MAKEFILE_DIR)/.env
    export
endif

ROS_DISTRO ?= jazzy
YUBI_IMAGE := yubi
YUBI_TAG ?= latest
YUBI_CORE_IMAGE := yubi-core
YUBI_CORE_TAG ?= latest

YUBI_GIT_HASH := $(shell git -C $(MAKEFILE_DIR) rev-parse HEAD)
YUBI_GIT_BRANCH := $(shell git -C $(MAKEFILE_DIR) rev-parse --abbrev-ref HEAD)
YUBI_CORE_GIT_HASH := $(shell git -C $(MAKEFILE_DIR)/yubi-core rev-parse HEAD)
YUBI_CORE_GIT_BRANCH := $(shell git -C $(MAKEFILE_DIR)/yubi-core rev-parse --abbrev-ref HEAD)

.PHONY: help lint test test-config build-config docker docker-yubi docker-yubi-core

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

# ── Lint & syntax ────────────────────────────────────────────────

lint: ## Run ruff linter and formatter check
	uvx ruff check $(MAKEFILE_DIR)/yubi_bringup/ $(MAKEFILE_DIR)/footpedal_ros/ $(MAKEFILE_DIR)/airoa_quest/
	uvx ruff format --check $(MAKEFILE_DIR)/yubi_bringup/ $(MAKEFILE_DIR)/footpedal_ros/ $(MAKEFILE_DIR)/airoa_quest/

# ── Tests ────────────────────────────────────────────────────────

test: ## Run unit tests with pytest
	cd $(MAKEFILE_DIR)/yubi_bringup && uvx --with pyyaml pytest test/ --ignore=test/test_flake8.py --ignore=test/test_pep257.py --ignore=test/test_copyright.py
	cd $(MAKEFILE_DIR)/airoa_quest/airoa_quest_bridge && uvx pytest test/ --ignore=test/test_flake8.py --ignore=test/test_pep257.py --ignore=test/test_copyright.py

test-config: ## Run only build_runtime_configs tests (subset of test)
	cd $(MAKEFILE_DIR)/yubi_bringup && uvx --with pyyaml pytest test/test_build_runtime_configs.py -v

# ── Config ───────────────────────────────────────────────────────

build-config: ## Generate config/_runtime/<variant>/ from common + <variant> [+ local] overlays
	python3 $(MAKEFILE_DIR)/yubi_bringup/tools/build_runtime_configs.py --variant $${ROBOT_VARIANT:-stationary} --with-local

# ── Docker ───────────────────────────────────────────────────────

docker-yubi-core: ## Build yubi-core image
	docker build \
		--build-arg ROS_DISTRO=$(ROS_DISTRO) \
		--build-arg GIT_HASH=$(YUBI_CORE_GIT_HASH) \
		--build-arg GIT_BRANCH=$(YUBI_CORE_GIT_BRANCH) \
		-f $(MAKEFILE_DIR)/yubi-core/docker/Dockerfile \
		-t $(YUBI_CORE_IMAGE):$(YUBI_CORE_TAG) \
		$(MAKEFILE_DIR)/yubi-core

docker-yubi: ## Build yubi image
	docker build \
		--build-arg ROS_DISTRO=$(ROS_DISTRO) \
		--build-arg YUBI_GIT_HASH=$(YUBI_GIT_HASH) \
		--build-arg YUBI_GIT_BRANCH=$(YUBI_GIT_BRANCH) \
		-f $(MAKEFILE_DIR)/docker/Dockerfile \
		-t $(YUBI_IMAGE):$(YUBI_TAG) \
		$(MAKEFILE_DIR)

docker: build-config docker-yubi-core docker-yubi ## Build all Docker images (generates config/_runtime first)
