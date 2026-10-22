PYTHON ?= python3
RUFF ?= $(shell command -v ruff || echo "$(PYTHON) -m ruff")

.PHONY: test lint check clean gui icon appimage verify-appimage

test:
	$(PYTHON) -m pytest

lint:
	$(RUFF) check .

check: lint test

gui:
	$(PYTHON) despertador_gui.py

icon:
	chmod +x scripts/make_icon.sh
	./scripts/make_icon.sh

appimage:
	chmod +x build_appimage.sh scripts/make_icon.sh scripts/verify_appimage.sh
	./build_appimage.sh

verify-appimage:
	chmod +x scripts/verify_appimage.sh
	./scripts/verify_appimage.sh

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
	rm -rf build dist Despertador.AppDir
