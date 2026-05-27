# Makefile for Benchmarking Lab
.PHONY: setup generate-data build up down clean deep-clean reset

# Default variables
SCALE_FACTOR ?= 0.1
DATA_DIR_NAME = out-sf$(SCALE_FACTOR)
LAB_DATA_DIR = data

setup:
	@echo "Installing system dependencies..."
	sudo apt update && sudo apt install -y docker.io docker-compose-v2 git python3 python3-pip
	sudo apt install -y python3-pandas
	pip3 install --user psycopg || pip3 install --user psycopg --break-system-packages
	sudo systemctl enable --now docker
	sudo usermod -aG docker $$USER
	@if [ ! -d "ldbc_snb_interactive_impls" ]; then \
		echo "Cloning ldbc_snb_interactive_impls..."; \
		git clone https://github.com/ldbc/ldbc_snb_interactive_impls.git; \
	fi

generate-data:
	@echo "Generating SF$(SCALE_FACTOR) dataset..."
	docker run --rm -v $(PWD)/$(DATA_DIR_NAME):/out ldbc/datagen-standalone:latest \
		--memory 8g -- \
		--scale-factor $(SCALE_FACTOR) \
		--mode raw \
		--format csv \
		--explode-edges \
		--output-dir /out
	sudo chown -R $(USER):$(USER) $(PWD)/$(DATA_DIR_NAME)

build:
	@echo "Building optimized databases for SF$(SCALE_FACTOR)..."
	chmod +x build-databases.sh || true
	SCALE_FACTOR=$(SCALE_FACTOR) bash ./build-databases.sh

up:
	@echo "Starting databases in background (SF$(SCALE_FACTOR))..."
	SCALE_FACTOR=$(SCALE_FACTOR) docker compose up -d
	@echo "Waiting for Neo4j to be ready..."
	@until docker exec neo4j-benchmark cypher-shell -u neo4j -p password "RETURN 1" >/dev/null 2>&1; do \
		printf "."; \
		sleep 2; \
	done
	@echo "\nApplying Neo4j constraints and indexes..."
	cat neo4j_setup.cypher | docker exec -i neo4j-benchmark cypher-shell -u neo4j -p password
	@echo "Neo4j schema applied successfully."

down:
	@echo "Stopping databases..."
	SCALE_FACTOR=$(SCALE_FACTOR) docker compose down

clean: down
	@echo "Cleaning up the environment (Ephemeral)..."
	sudo rm -rf $(LAB_DATA_DIR)
	@echo "Environment cleaned. Original data in $(DATA_DIR_NAME) (if present) has been preserved."

deep-clean: clean
	@echo "Deep cleaning: removing raw generated data as well..."
	sudo rm -rf $(PWD)/$(DATA_DIR_NAME)

reset: clean build up
	@echo "Environment completely regenerated and restarted from scratch for SF$(SCALE_FACTOR)!"
