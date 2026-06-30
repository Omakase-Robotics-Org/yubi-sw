# =============================================================================
# YUBI new-machine provisioning  (Ubuntu 22.04, post-flash)
# =============================================================================
# Sets up a fresh YUBI data-collection laptop from the bare OS to a working node.
# Grounded in the working yubi1 machine. Steps that need a human (encoder
# calibration GUI, the per-device S3 cert, Quest sideload) are NOT faked — they
# print clear instructions instead.
#
#   make install         # apt + python + docker + adb + config  (the automatable core)
#   make recording-stack # build images + bring up yubi-sw and yubi-app stacks
#   make s3-creds         # wire the S3 uploader + cron (needs cert already in ~/iot)
#   make verify           # acceptance gate (PASS/FAIL per check, no secrets printed)
#
# NEVER prints, echoes, or commits any secret/cert/private key.
# =============================================================================

SHELL := /bin/bash

# Repo root = two levels up from deploy/new-machine/
REPO_ROOT := $(abspath $(dir $(realpath $(lastword $(MAKEFILE_LIST))))/../..)
# yubi-app is cloned as a SIBLING of yubi-sw (matches yubi1's ~/projects layout).
PROJECTS_DIR ?= $(abspath $(REPO_ROOT)/..)
YUBI_APP_DIR := $(PROJECTS_DIR)/yubi-app
YUBI_APP_REPO ?= https://github.com/Omakase-Robotics-Org/yubi-app.git

ROBOT_VARIANT ?= stationary          # stationary | portable

# --- S3 / IoT (identifiers, NOT secrets — safe to keep here) -----------------
IOT_DIR        ?= $(HOME)/iot
IOT_ENDPOINT   ?= c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com
IOT_ROLE_ALIAS ?= yubi-uploader-alias
# MUST be unique per device (override: make s3-creds IOT_THING=yubi2)
IOT_THING      ?= $(strip $(shell hostname))
S3_BUCKET      ?= omakase-robotics-data

GREEN := \033[32m
RED   := \033[31m
YEL   := \033[33m
NC    := \033[0m

.PHONY: help install apt python docker adb config ros recording-stack \
        s3-creds verify operator-launcher

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# install = the part that can be fully automated. recording-stack (heavy build)
# and s3-creds (needs a human-placed cert) are deliberately NOT included here.
install: apt python docker adb config ## Automatable core (apt+python+docker+adb+config)
	@echo -e "$(GREEN)== base install done ==$(NC)"
	@echo -e "$(YEL)Next, the steps Make cannot do for you:$(NC)"
	@echo "  1. Calibrate encoders + write udev rules (GUI, grippers plugged in):"
	@echo "       cd $(REPO_ROOT)/tools && sudo -E bash yubi_udev_setup.sh   # --variant portable on portable rig"
	@echo "  2. make recording-stack     # build images + bring up the stacks"
	@echo "  3. Edit per-host config: $(REPO_ROOT)/yubi_bringup/config/local/{robot_config,yubi_devices}.yaml"
	@echo "  4. Install the S3 device cert into $(IOT_DIR) (see SETUP.md §6), then: make s3-creds"
	@echo "  5. Sideload the Quest APK (adb install -r ...; headset in Developer Mode)"
	@echo "  6. make verify"

# ── apt packages ─────────────────────────────────────────────────────────────
apt: ## Install base apt packages (git, make, zenity, notify, dhcp, build tools)
	sudo apt-get update
	sudo apt-get install -y \
		git make curl wget ca-certificates gnupg lsb-release \
		build-essential python3 python3-pip python3-venv \
		zenity libnotify-bin \
		isc-dhcp-server \
		avahi-daemon
	@echo -e "$(GREEN)apt OK$(NC)"
	# zenity + libnotify-bin: the one-click operator launcher (deploy/yubi1) uses them.
	# isc-dhcp-server: optional wired USB-Ethernet DHCP for the Quest (tools/yubi_dhcp_setup.sh).
	# avahi-daemon: the yubi_core/web_video_server containers mount its socket.

# ── Python (host-side: boto3 for the uploader, uv/uvx for repo lint/test) ─────
python: ## Install host python deps (boto3) and uv/uvx
	python3 -m pip install --user --upgrade boto3
	@command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	@echo -e "$(GREEN)python OK (boto3 for uploader; uv/uvx for make lint/test)$(NC)"

# ── Docker CE + compose plugin ────────────────────────────────────────────────
docker: ## Install Docker CE + compose plugin, add user to docker group
	@if command -v docker >/dev/null 2>&1; then \
		echo "docker already present: $$(docker --version)"; \
	else \
		sudo install -m 0755 -d /etc/apt/keyrings; \
		curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
		sudo chmod a+r /etc/apt/keyrings/docker.gpg; \
		echo "deb [arch=$$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $$(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null; \
		sudo apt-get update; \
		sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; \
	fi
	sudo usermod -aG docker $(USER) || true
	@echo -e "$(YEL)Log out/in (or 'newgrp docker') so the docker group applies.$(NC)"
	@echo -e "$(GREEN)docker OK$(NC)"
	# yubi1 reference: Docker 29.5.3, compose plugin v5.1.4 (NOT docker-compose v1).

# ── adb (Quest sideloading) ──────────────────────────────────────────────────
adb: ## Install android adb for sideloading the Quest APK
	sudo apt-get install -y android-tools-adb
	@echo -e "$(GREEN)adb OK$(NC)"

# ── ROS (host) — intentionally a NO-OP ────────────────────────────────────────
ros: ## (no-op) ROS 2 runs INSIDE the docker images, not on the host
	@echo -e "$(YEL)No host ROS install: yubi1 has no host ROS — ROS 2 Jazzy lives in the$(NC)"
	@echo -e "$(YEL)yubi/yubi-core docker images (built by 'make recording-stack').$(NC)"

# ── Clone app repos + scaffold per-host config ────────────────────────────────
config: ## Clone yubi-app sibling, copy .env, scaffold config/local overrides
	@test -d "$(YUBI_APP_DIR)" || git clone "$(YUBI_APP_REPO)" "$(YUBI_APP_DIR)"
	@test -f "$(REPO_ROOT)/.env" || cp "$(REPO_ROOT)/.env.example" "$(REPO_ROOT)/.env"
	@mkdir -p "$(REPO_ROOT)/yubi_bringup/config/local"
	@if [ ! -f "$(REPO_ROOT)/yubi_bringup/config/local/robot_config.yaml" ]; then \
		printf '/**:\n  ros__parameters:\n    api_key: "EDIT-ME"\n    base_url: "http://localhost:8000/api"\n' \
			> "$(REPO_ROOT)/yubi_bringup/config/local/robot_config.yaml"; \
		echo "  wrote template robot_config.yaml (EDIT the api_key)"; fi
	@if [ ! -f "$(REPO_ROOT)/yubi_bringup/config/local/yubi_devices.yaml" ]; then \
		printf 'quest_bridge_node:\n  ros__parameters:\n    quest_ip: "0.0.0.0"\n' \
			> "$(REPO_ROOT)/yubi_bringup/config/local/yubi_devices.yaml"; \
		echo "  wrote template yubi_devices.yaml (EDIT the quest_ip)"; fi
	@grep -qE '^ROBOT_VARIANT=' "$(REPO_ROOT)/.env" || echo "ROBOT_VARIANT=$(ROBOT_VARIANT)" >> "$(REPO_ROOT)/.env"
	@echo -e "$(GREEN)config scaffolded — now EDIT yubi_bringup/config/local/*.yaml$(NC)"

# ── Build images + bring up both stacks ───────────────────────────────────────
recording-stack: ## Build yubi/yubi-core images + docker compose up (yubi-sw and yubi-app)
	cd "$(REPO_ROOT)" && make docker          # builds yubi-core:latest, yubi:latest, generates config/_runtime
	cd "$(REPO_ROOT)" && docker compose up -d
	cd "$(YUBI_APP_DIR)" && make up
	@echo -e "$(GREEN)stacks up — web UI: http://localhost:3000/web$(NC)"

# ── S3 uploader + cron (cert must already be in ~/iot) ────────────────────────
# Does NOT mint or fetch a key. Refuses if the private key is missing.
s3-creds: ## Wire the S3 IoT uploader + */5 cron (needs cert pre-placed in ~/iot)
	@if [ ! -f "$(IOT_DIR)/device.private.key" ]; then \
		echo -e "$(RED)FAIL: $(IOT_DIR)/device.private.key not found.$(NC)"; \
		echo "Mint a NEW per-device cert and place these in $(IOT_DIR) (see SETUP.md §6):"; \
		echo "    device.cert.pem  device.private.key  AmazonRootCA1.pem"; \
		echo "This target NEVER creates or downloads a key."; \
		exit 1; \
	fi
	@mkdir -p "$(IOT_DIR)"
	@chmod 700 "$(IOT_DIR)"
	@chmod 600 "$(IOT_DIR)/device.private.key"
	@chmod 644 "$(IOT_DIR)/device.cert.pem" "$(IOT_DIR)/AmazonRootCA1.pem" 2>/dev/null || true
	@if [ "$(IOT_THING)" = "yubi1" ]; then \
		echo -e "$(RED)Refusing IOT_THING=yubi1 on a new machine. Pass a unique name:$(NC)"; \
		echo "    make s3-creds IOT_THING=yubi2"; exit 1; fi
	@# Install the uploader scripts next to $HOME (matches yubi1 layout). These
	@# come from this deploy/ dir — see yubi_s3_direct.{py,sh} alongside this Makefile.
	@install -m 0755 "$(dir $(realpath $(lastword $(MAKEFILE_LIST))))/yubi_s3_direct.py" "$(HOME)/yubi_s3_direct.py"
	@# Generate the wrapper with this machine's IOT_THING (no secrets in it).
	@printf '#!/usr/bin/env bash\nexec 9>/tmp/yubi_s3_direct.lock; flock -n 9 || exit 0\nexport IOT_CERT=$$HOME/iot/device.cert.pem\nexport IOT_KEY=$$HOME/iot/device.private.key\nexport IOT_ROOT_CA=$$HOME/iot/AmazonRootCA1.pem\nexport IOT_ENDPOINT=%s\nexport IOT_ROLE_ALIAS=%s\nexport IOT_THING=%s\nexport S3_BUCKET=%s\npython3 $$HOME/yubi_s3_direct.py "$$@"\n' \
		"$(IOT_ENDPOINT)" "$(IOT_ROLE_ALIAS)" "$(IOT_THING)" "$(S3_BUCKET)" > "$(HOME)/yubi_s3_direct.sh"
	@chmod 0755 "$(HOME)/yubi_s3_direct.sh"
	@# Install the */5 cron line if not already present.
	@( crontab -l 2>/dev/null | grep -v 'yubi_s3_direct.sh'; \
	   echo "*/5 * * * * $(HOME)/yubi_s3_direct.sh >> $(HOME)/yubi_s3_direct.log 2>&1" ) | crontab -
	@echo -e "$(GREEN)uploader installed for thing '$(IOT_THING)'; cron */5 set.$(NC)"
	@echo "Test now (prints only key-counts, no secrets):  $(HOME)/yubi_s3_direct.sh --test"

# ── Operator one-click launcher (optional) ────────────────────────────────────
operator-launcher: ## Copy the deploy/yubi1 one-click launcher to ~/Desktop
	@cp "$(REPO_ROOT)/deploy/yubi1/start-yubi.sh" "$(HOME)/Desktop/"
	@cp "$(REPO_ROOT)/deploy/yubi1/Start-YUBI.desktop" "$(HOME)/Desktop/"
	@chmod +x "$(HOME)/Desktop/start-yubi.sh" "$(HOME)/Desktop/Start-YUBI.desktop"
	@gio set "$(HOME)/Desktop/Start-YUBI.desktop" metadata::trusted true 2>/dev/null || true
	@echo -e "$(GREEN)launcher on Desktop (review deploy/yubi1/README.md for the Quest-IP prompt)$(NC)"

# ── Verifier / acceptance gate ────────────────────────────────────────────────
verify: ## Acceptance gate: PASS/FAIL per check (never prints secrets)
	@set +e; fail=0; \
	chk() { if eval "$$2" >/dev/null 2>&1; then echo -e "  $(GREEN)PASS$(NC) $$1"; else echo -e "  $(RED)FAIL$(NC) $$1"; fail=1; fi; }; \
	echo "== YUBI node verify =="; \
	chk "docker + compose plugin" 'docker compose version'; \
	chk "python boto3" 'python3 -c "import boto3"'; \
	chk "adb on PATH" 'command -v adb'; \
	chk "/etc/yubi/encoder_limits.yaml" 'test -f /etc/yubi/encoder_limits.yaml'; \
	chk "udev rules 99-yubi.rules" 'test -f /etc/udev/rules.d/99-yubi.rules'; \
	chk "container: minio up" 'docker ps --format "{{.Names}}" | grep -qx minio'; \
	chk "container: yubi up" 'docker ps --format "{{.Names}}" | grep -qx yubi'; \
	chk "container: yubi_core up" 'docker ps --format "{{.Names}}" | grep -qx yubi_core'; \
	chk "container: yubi-app backend up" 'docker ps --format "{{.Names}}" | grep -q yubi-app-backend'; \
	chk "container: yubi-app frontend up" 'docker ps --format "{{.Names}}" | grep -q yubi-app-frontend'; \
	chk "MinIO reachable :9000" 'curl -fsS -o /dev/null http://127.0.0.1:9000/minio/health/live'; \
	chk "web UI :3000" 'curl -fsS -o /dev/null http://localhost:3000/web'; \
	chk "uploader cron installed" 'crontab -l 2>/dev/null | grep -q yubi_s3_direct.sh'; \
	if [ -f "$(IOT_DIR)/device.private.key" ]; then \
		chk "private key perms 0600" '[ "$$(stat -c %a $(IOT_DIR)/device.private.key)" = 600 ]'; \
		chk "S3 end-to-end (IoT creds + AWS + MinIO)" '$(HOME)/yubi_s3_direct.sh --test'; \
	else \
		echo -e "  $(YEL)SKIP$(NC) S3 checks ( $(IOT_DIR)/device.private.key not installed yet )"; \
	fi; \
	echo "======================"; \
	if [ $$fail -eq 0 ]; then echo -e "$(GREEN)ALL PASS$(NC)"; else echo -e "$(RED)SOME FAILED$(NC)"; exit 1; fi
