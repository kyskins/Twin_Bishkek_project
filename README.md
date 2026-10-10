# Twin Bishkek

An Apple-inspired Twin Bishkek scenario interface. It pairs a scroll-reactive procedural 3D city with Python scenario calculations, a 0–5 year implementation horizon, saved scenario comparisons, and factor explanations.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

`app.py` starts the local calculation API automatically on `127.0.0.1:8765`. To serve the frontend and API without Streamlit, run `python server.py` and open `http://127.0.0.1:8765`.

For a continuously running VPS deployment with Docker, persistent SQLite storage, and automatic HTTPS, see [DEPLOY_RU.md](DEPLOY_RU.md). The deployment serves the frontend and API on one origin so calculations work on a public domain.

The interface needs an internet connection to load Three.js, web fonts, 2GIS MapGL, and the Open-Meteo air-quality feed.

For the optional cloud MapGL traffic preview, create a demo key with **Map Tiles API** access and start the app with `TWINGIS_MAPGL_KEY` set in the environment. For example, in the same terminal:

```bash
export TWINGIS_MAPGL_KEY="paste-your-new-demo-key-here"
streamlit run app.py
```

The key is returned by the local `/api/config` endpoint to initialize MapGL in the browser, so restrict its allowed website/origin in the 2GIS dashboard. Do not commit a key to source control. For local development, allow only the local app origin. This cloud demo map is separate from the On-Premise Traffic API Proxy deployment below.

Changing a scenario input calls `TwinBishkekSimulator` through `/api/simulate` and updates the KPIs, factor breakdown, procedural city model, and the scenario overlay on the right-hand map. The two maps stay synchronized. Saved comparisons stay in the browser's local storage. The scenario clock covers 00:00–23:00 and models different weekday/weekend traffic and object activity profiles. The right map follows 769 main-road and district-connection lines from the OpenStreetMap snapshot embedded in `frontend/index.html` (snapshot date: 2026-10-08). The revised green/yellow/orange/red colors vary by road class, peak-hour profile, center/district proximity, observed project effect, and (after sufficient samples) a cautious city-level adjustment from stored 2GIS scores. A strip below the map ranks the busiest named roads in the model; it is explicitly marked as a theoretical ranking, not a measured street-level prediction. Selected-hour air and weather values use the nearest available CAMS/Open-Meteo forecast record. CAMS Global air forecasts are 3-hourly and updated twice daily, so calling the API every 15 minutes does not mean the air forecast itself is refreshed that often.

The calculation coefficients are illustrative and have not been calibrated against measured Bishkek data. The 0–5 year horizon stages the prototype effect linearly. Treat all outputs as scenario exploration, not official planning forecasts.

## Map and environmental data

- The left map uses the current 2GIS traffic layer. The right map uses a clean 2GIS base map and overlays a model on OpenStreetMap road geometry; line width scales with zoom. When MapGL emits a current `trafficscore`, the app stores its value, selected area, visible-map center, zoom, timestamp, hour, and weekday/weekend in the API server's SQLite archive. The app limits saving to one observation per 15 minutes for the same map view; the score is never assigned to an individual street. `/api/data-status` shows how many traffic observations and environmental snapshots are stored; `/api/traffic-history` reports the selected profile. The SQLite archive defaults to `data/twin_bishkek.sqlite3` on the machine running the server. New runs do not create retroactive observations.
- Current PM2.5, PM10, gases, European AQI, weather, and a 24-hour air-quality context come from the Open-Meteo Air Quality API using CAMS Global. In Bishkek, the global forecast grid is about 45 km, so these values describe regional background rather than a neighborhood sensor reading. When the API starts, a background collector requests all model areas about every 15 minutes and stores new provider-time snapshots in SQLite. The process must remain running; collection pauses when it is stopped. Provider data may update less often than the collector polls.
- 2GIS traffic scores are captured from the open MapGL map while its browser tab is running, at most once every 15 minutes for the same map view. MapGL demo access provides a visible-area score, not a street-by-street traffic feed, so the background collector cannot independently gather segment-level traffic. The local history starts nudging the general traffic profile only after at least eight observations across four different days for the selected area, hour, and day type. This is conservative local calibration, not training this assistant or fitting a machine-learning model. The archive defaults to `data/twin_bishkek.sqlite3` on the API server; delete that file to clear the collected history.
- MoveGreen's AQ.kg map is linked in the interface for checking local sensor readings. It is not ingested automatically by this prototype.
- Street geometry is embedded in `frontend/index.html` and credited to [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) under the ODbL. The bundled geometry is a snapshot, so refreshing it is a separate data update. Residential and courtyard roads are not included. The road colors and ranked list are model estimates, not confirmed future jam locations. [2GIS documents hourly, directional traffic by road segment](https://docs.2gis.com/en/pro/data/built-in/cars-detailed) for Kyrgyzstan, updated monthly, but says access is by request on paid 2GIS Pro plans and not included in its demo plan. Map Tiles demo access alone does not provide this dataset. Licensed segment data would let us replace heuristic road classes with actual weekday/weekend hour profiles and calibrate scenario effects.
- Open-Meteo/CAMS air and weather responses are cached for 15 minutes and each distinct successful provider-time snapshot is stored in SQLite. The database path defaults to `data/twin_bishkek.sqlite3`; set `TWINGIS_DATA_DIR` to choose another persistent directory. The linked AQ.kg / MoveGreen map is not ingested automatically because this prototype has no documented/public data API credential for it. [Google Routes can return traffic categories on requested route polylines](https://developers.google.com/maps/documentation/routes/traffic_on_polylines); [Yandex JS API exposes a current traffic provider](https://www.yandex.com/dev/jsapi-v2-1/doc/en/v2-1/ref/reference/traffic.provider.Actual). These interfaces do not provide this app a permission-free bulk export of all Bishkek street history. The app does not scrape map tiles or extract third-party map colors.

## Optional: On-Premise 2GIS Traffic API Proxy

The 2GIS Traffic API Proxy is an On-Premise service, not a cloud API key that can be enabled from this app. The map configuration template is in `deploy/traffic-proxy-map.values.example.yaml`; replace its placeholders only in the deployment environment, never commit credentials.

Before deploying it, obtain an On-Premise license and installation access from 2GIS, prepare a Kubernetes cluster and the required registry credentials, and install the 2GIS core services (including License and API Keys) and Maps services. Then configure and deploy the map traffic proxy with the version-matched 2GIS Helm chart. The proxy fetches map traffic tiles from `jam.api.2gis.com` and serves them to 2GIS Maps services.

This prototype already includes a cloud MapGL client. The On-Premise proxy is a separate deployment path and would require configuring that client for the deployed 2GIS services. See the [2GIS Traffic API Proxy documentation](https://docs.2gis.com/en/on-premise-api-platform/architecture/trafficproxy), [installation guide](https://docs.2gis.com/en/on-premise-api-platform/installation), and [maps service requirements](https://docs.2gis.com/en/on-premise-api-platform/architecture/maps).
