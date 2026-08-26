PY ?= python3
VENV ?= .venv
# Auto-detect venv python for run/checkconf/health if venv exists
VENV_PY := $(VENV)/bin/python
WIN_VENV_PY := $(VENV)/Scripts/python.exe
# Prefer venv python when present, else system PY
PYTHON := $(if $(wildcard $(VENV_PY)),$(VENV_PY),$(if $(wildcard $(WIN_VENV_PY)),$(WIN_VENV_PY),$(PY)))
BIN := $(VENV)/bin
WIN_BIN := $(VENV)/Scripts

.PHONY: run checkconf health venv clean help test

help:
	@echo "Usage:"
	@echo "  make venv                          # create isolated .venv and install deps"
	@echo "  make test                          # run pytest suite"
	@echo "  make run DIR=/path/to/batch [ARGS=\"--dry-run\"]"
	@echo "  make checkconf                     # test tracker/auth + image hosts + scrapers"
	@echo "  make health                        # check deps"
	@echo "  make clean                         # remove caches"
	@echo ""
	@echo "Manual venv (any OS):"
	@echo "  python3 -m venv .venv"
	@echo "  .venv/bin/pip install -r requirements.txt   # Linux/macOS"
	@echo "  .venv\\Scripts\\pip install -r requirements.txt  # Windows"
	@echo "  .venv/bin/python -m simurg up ./my-batch --dry-run"

# Usage: make run DIR=/path/to/batch [ARGS="--dry-run"]
run:
	@test -n "$(DIR)" || (echo "Usage: make run DIR=/path/to/batch [ARGS=\"--dry-run\"]" && exit 1)
	$(PYTHON) -m simurg up "$(DIR)" $(ARGS)

checkconf:
	$(PYTHON) -m simurg checkconf

health:
	$(PYTHON) -m simurg health

venv:
	@echo "Creating venv at $(VENV)..."
	@if $(PY) -m venv $(VENV) 2>/dev/null; then \
		echo "venv created with $(PY) -m venv"; \
	elif command -v uv >/dev/null 2>&1; then \
		echo "python3 -m venv failed, falling back to 'uv venv --seed $(VENV)'"; \
		uv venv --seed $(VENV); \
	else \
		echo "ERROR: $(PY) -m venv failed and 'uv' not found."; \
		echo "On Debian/Ubuntu: sudo apt install python3.11-venv"; \
		echo "Or: pip install virtualenv && virtualenv $(VENV)"; \
		echo "Or: pipx install uv && uv venv --seed $(VENV)"; \
		exit 1; \
	fi
	@echo "Upgrading pip..."
	@if [ -f "$(VENV_PY)" ]; then $(VENV_PY) -m pip install --upgrade pip; \
	elif [ -f "$(WIN_VENV_PY)" ]; then $(WIN_VENV_PY) -m pip install --upgrade pip; fi
	@echo "Installing requirements..."
	@if command -v uv >/dev/null 2>&1 && [ -f "$(VENV_PY)" ]; then \
		echo "Trying uv pip install..."; \
		uv pip install --python $(VENV_PY) -r requirements.txt || $(VENV_PY) -m pip install -r requirements.txt; \
	elif [ -f "$(VENV_PY)" ]; then $(VENV_PY) -m pip install -r requirements.txt; \
	elif [ -f "$(WIN_VENV_PY)" ]; then $(WIN_VENV_PY) -m pip install -r requirements.txt; \
	else $(BIN)/pip install -r requirements.txt; fi
	@echo ""
	@echo "Done. Activate with:"
	@echo "  source $(VENV)/bin/activate      # Linux/macOS"
	@echo "  .\\$(VENV)\\Scripts\\activate       # Windows"
	@echo "Then: make run DIR=./my-batch ARGS=\"--dry-run\""

test:
	$(PYTHON) -m pytest -q

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

hooks:
	$(VENV)/bin/pre-commit install

clean:
	rm -rf __pycache__ simurg/__pycache__ simurg/*/__pycache__ simurg/*/*/__pycache__ .torrents .failed .pytest_cache tests/__pycache__ .coverage
	@echo "clean done (venv preserved; remove manually with rm -rf $(VENV) if needed)"
