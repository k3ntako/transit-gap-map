# NYC transit access + population density map
PY := .venv/bin/python
PORT ?= 8000

.PHONY: help setup fetch network service distance transit access aggregate \
        palette surface build verify all serve clean lock

help:
	@echo "make setup    create .venv (Python 3.14) and install dependencies"
	@echo "make fetch    stations, boundaries, census, GTFS (~68 MB), OSM extract (~153 MB)"
	@echo "make network  parse the .pbf into a cached walking graph (~4-5 min)"
	@echo "make service  scheduled trains and buses per hour, from GTFS"
	@echo "make distance multi-source Dijkstra: nearest station per graph node"
	@echo "make transit  rail transit graph: station-to-station travel time"
	@echo "make access   jobs reachable, time to a tenth, hubs, detour"
	@echo "make palette  generate the validated color system (web/data/palette.json)"
	@echo "make surface  walk-time raster + network walkshed polygons"
	@echo "make build    aggregate to tracts and write web/data/ (runs the above)"
	@echo "make verify   spot checks and the barrier test"
	@echo "make all      fetch -> network -> service -> distance -> transit"
	@echo "              -> access -> palette -> surface -> build -> verify"
	@echo "make serve    serve web/ at http://localhost:$(PORT) (no caching)"

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

# Needs only the station tables and the GTFS feeds, not the walk graph.
service: fetch
	$(PY) -m pipeline.service

# The frequency-weighted pass reads station_service.parquet, so service first.
distance: service
	$(PY) -m pipeline.distance

# Rail only, and independent of the walk graph: this reads the three rail
# GTFS feeds and nothing else, so it re-runs in seconds while stage 2 is the
# expensive one.
transit: fetch
	$(PY) -m pipeline.transit

# The travel-time matrix joins the walk graph to the rail graph, so it needs
# both -- plus the LODES jobs file that `fetch` brings down.
access: distance transit
	$(PY) -m pipeline.accessibility

aggregate: access
	$(PY) -m pipeline.aggregate

palette:
	$(PY) -m pipeline.palette

# Reads palette.json for the raster ramp, so palette comes first.
surface: palette
	$(PY) -m pipeline.surface

# Depends on fetch so a missing input downloads itself rather than
# surfacing as a FileNotFoundError deep in stage 5.
build: access
	$(PY) -m pipeline.build

verify:
	$(PY) -m pipeline.verify

all: fetch network service distance transit access palette surface build verify

# serve.py rather than python -m http.server: the stdlib server sends no
# Cache-Control, so a browser can keep serving an edited page from its own
# disk cache and the edit looks broken. See the docstring.
serve:
	@PORT=$(PORT) $(PY) serve.py

clean:
	rm -rf data/processed/*
	find web/data -type f ! -name .gitkeep -delete
