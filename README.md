# GridPulse AI

GridPulse AI is a Florida building-footprint and clean-energy screening application. It combines OpenStreetMap footprints, Open-Meteo climate records, public incentive references, and Gemini-generated summaries in an interactive React map.

## Architecture

- `Frontend`: React 19, TypeScript, Vite, Leaflet
- `Backend`: FastAPI, Python, Google Gemini, OpenStreetMap/Overpass, Open-Meteo
- Deployment: two Render services configured by `render.yaml`

The mapped footprints are public OSM records. Solar production, costs, savings, carbon reductions, scores, and forecasts are planning estimates, not audited engineering or utility data.

## Local setup

### Backend

1. Create a Gemini API key at <https://aistudio.google.com/app/apikey>.
2. Copy `Backend/.env.example` to `Backend/.env`.
3. Put the real key in `Backend/.env`. Never commit this file.
4. Install and start the API:

```powershell
cd Backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### Frontend

In a second terminal:

```powershell
cd Frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. The frontend defaults to `http://localhost:8000` for local API requests.

## Deploy on Render

1. Push this repository to GitHub.
2. In Render, choose **New > Blueprint** and connect the repository.
3. Render reads `render.yaml` and creates `gridpulse-api` and `gridpulse-web`.
4. For `gridpulse-api`, set `GEMINI_API_KEY` to the real secret.
5. Deploy the API and copy its URL, such as `https://gridpulse-api.onrender.com`.
6. For `gridpulse-web`, set `VITE_API_BASE_URL` to that API URL, without a trailing slash.
7. Deploy the web service and copy its public URL.
8. Set the API's `ALLOWED_ORIGINS` to the exact public web URL, such as `https://gridpulse-web.onrender.com`, then redeploy the API.

Render free services may sleep when idle, so the first request can take approximately one minute. A paid service avoids cold starts.

## Production checks

```powershell
cd Frontend
npm ci
npm run build
```

After deployment, verify:

- `https://YOUR-API-URL/health` returns `{"status":"ok"}`.
- The public website loads over HTTPS.
- Searching for Miami or Orlando returns map dots.
- The browser console has no CORS or mixed-content errors.
- The Gemini key is present only in Render's secret environment settings.

## Data sources

- OpenStreetMap contributors through public Nominatim and Overpass services
- Esri ArcGIS and U.S. Census geocoding fallbacks
- Open-Meteo historical archive and forecast APIs
- IRS and Florida statutory incentive references
- Google Gemini for narrative summaries and natural-language routing

Public APIs can rate-limit or temporarily reject requests. The application uses redundant geocoding providers and reports missing source data rather than presenting it as measured data.
