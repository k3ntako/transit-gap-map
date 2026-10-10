# NYC transit access + population density map
PY := .venv/bin/python
PORT ?= 8000

.PHONY: help setup fetch network distance aggregate palette surface build verify all serve clean lock

help:
	@echo "make setup    create .venv (Python 3.14) and install dependencies"
	@echo "make fetch    download stations, boundaries, census, OSM extract (~153 MB)"
	@echo "make network  parse the .pbf into a cached walking graph (~4-5 min)"
	@echo "make distance multi-source Dijkstra: nearest station per graph node"
	@echo "make palette  generate the validated color system (web/data/palette.json)"
	@echo "make surface  walk-time raster + network walkshed polygons"
	@echo "make build    aggregate to tracts and write web/data/ (runs the above)"
	@echo "make verify   spot checks and the barrier test"
	@echo "make all      fetch -> network -> distance -> palette -> surface -> build -> verify"
	@echo "make serve    serve the map at http://localhost:$(PORT)"

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip -q
	$(PY) -m pip install -r requirements.txt
	@echo
	@echo "Next: cp .env.example .env and paste a free Census key from"
	@echo "      https://api.census.gov/data/key_signup.html"

lock:
	$(PY) -m pip freeze > requirements.lock.txt

fetch:
	$(PY) -m pipeline.fetch

network:
	$(PY) -m pipeline.network

distance:
	$(PY) -m pipeline.distance

aggregate: fetch
	$(PY) -m pipeline.aggregate

palette:
	$(PY) -m pipeline.palette

# Reads palette.json for the raster ramp, so palette comes first.
surface: palette
	$(PY) -m pipeline.surface

# Depends on fetch so a missing input downloads itself rather than
# surfacing as a FileNotFoundError deep in stage 4.
build: fetch
	$(PY) -m pipeline.build

verify:
	$(PY) -m pipeline.verify

all: fetch network distance palette surface build verify

serve:
	@echo "http://localhost:$(PORT)/"
	@cd web && python3 -m http.server $(PORT)

clean:
	rm -rf data/processed/*
	find web/data -type f ! -name .gitkeep -delete
