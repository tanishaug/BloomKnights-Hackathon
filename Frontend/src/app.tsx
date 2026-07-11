import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Activity, Building2, CheckCircle2, DollarSign, Leaf, Loader2, MapPin, Moon, Search, Sparkles, Sun, Target, Zap } from "lucide-react";
import MapComponent from "./components/MapComponent";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const DEFAULT_LOCATION = { lat: 25.7617, lon: -80.1918, displayName: "Miami, Florida" };
const FLORIDA_CITY_SUGGESTIONS = ["Jacksonville, Florida", "Miami, Florida", "Tampa, Florida", "Orlando, Florida", "St. Petersburg, Florida", "Hialeah, Florida", "Tallahassee, Florida", "Fort Lauderdale, Florida", "Port St. Lucie, Florida", "Cape Coral, Florida", "Gainesville, Florida", "Sarasota, Florida", "Key West, Florida"];

export interface Building {
  id: number; osm_id?: number; name: string; address?: string; lat: number; lon: number; type?: string;
  gross_cost?: number; net_cost?: number; annual_savings?: number; payback_years?: number; roi_pct?: number;
  carbon_reduction_tons?: number; investment_score?: number; roi_status?: string; rank?: number;
  annual_electricity_kwh?: number; solar_capacity_kw?: number; annual_solar_generation_kwh?: number;
  future_usage_predictions?: { year: number; predicted_kwh: number }[]; prediction_confidence?: string;
  methodology?: string; data_sources?: string[]; data_quality?: string;
  energy_baseline_type?: string; disclosed_electricity_kwh?: number; berdo_disclosure_year?: number;
  site_eui?: number; energy_star_score?: number; compliance_status?: string;
  incentives?: { name: string; value: string; source_url: string; status: string }[];
  source_id?: string; source_url?: string; building_source?: string; source_fields?: string;
  roof_area?: number; floors?: number | null; year_built?: string | null; energy_data_quality?: string;
}

interface Location { lat: number; lon: number; displayName: string; osmType?: string; osmId?: number; locationType?: string; }

const money = (value?: number, compact = false) => value == null ? "N/A" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, notation: compact ? "compact" : "standard" }).format(value);
const number = (value?: number) => value == null ? "N/A" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
const scoreTier = (score?: number) => score != null && score >= 75 ? "high" : score != null && score >= 50 ? "moderate" : "low";
const apiError = async (res: Response) => { const body = await res.json().catch(() => ({})); return body.detail || `Request failed (${res.status})`; };

export default function App() {
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeIds, setActiveIds] = useState<number[]>([]);
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [nlQuery, setNlQuery] = useState("");
  const [searchAnswer, setSearchAnswer] = useState("");
  const [budget, setBudget] = useState("500000");
  const [portfolio, setPortfolio] = useState<{ remaining: number; ids: number[] } | null>(null);
  const [report, setReport] = useState("");
  const [reportSections, setReportSections] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"search" | "optimize" | "report" | null>(null);
  const [error, setError] = useState("");
  const [location, setLocation] = useState<Location>(DEFAULT_LOCATION);
  const [locationQuery, setLocationQuery] = useState("Miami, Florida");
  const [locationBusy, setLocationBusy] = useState(false);
  const [scanRadius, setScanRadius] = useState(10000);
  const [theme, setTheme] = useState<"light" | "dark">(() => localStorage.getItem("gridpulse-theme") === "light" ? "light" : "dark");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gridpulse-theme", theme);
  }, [theme]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setBuildings([]); setSelectedId(null); setActiveIds([]); setPortfolio(null);
    const focus = location.osmType && location.osmId ? `&focus_osm_type=${encodeURIComponent(location.osmType)}&focus_osm_id=${location.osmId}` : "";
    fetch(`${API_BASE}/api/buildings?lat=${location.lat}&lon=${location.lon}&radius=${scanRadius}&limit=3000${focus}`, { signal: controller.signal })
      .then(async res => { if (!res.ok) throw new Error(await apiError(res)); return res.json(); })
      .then((data: Building[]) => { const next = Array.isArray(data) ? data : []; setBuildings(next); setSelectedId(next[0]?.id ?? null); setActiveIds([]); setPortfolio(null); setReport(""); setReportSections({}); })
      .catch(err => { if (err.name !== "AbortError") setError(`Unable to load grid assets. ${err.message}`); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [location, scanRadius]);

  const searchLocation = async (event: FormEvent) => {
    event.preventDefault(); if (!locationQuery.trim()) return;
    setLocationBusy(true); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(locationQuery.trim())}`);
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json(); const match = data.results?.[0];
      if (!match) throw new Error("No matching city or address was found in Florida.");
      const cityScaleTypes = new Set(["administrative", "city", "town", "municipality"]);
      setScanRadius(cityScaleTypes.has(match.type) ? 25000 : 10000);
      setLocation({ lat: match.lat, lon: match.lon, displayName: match.display_name, osmType: match.osm_type, osmId: match.osm_id, locationType: match.type });
    } catch (err) { setError(err instanceof Error ? err.message : "Location search failed."); } finally { setLocationBusy(false); }
  };

  const selected = buildings.find(b => b.id === selectedId) || null;
  const displayedBuildings = portfolio ? buildings.filter(b => portfolio.ids.includes(b.id)) : buildings;
  const filtered = displayedBuildings.filter(b => `${b.name} ${b.address || ""} ${b.type || ""}`.toLowerCase().includes(directoryQuery.toLowerCase()));
  const totals = buildings.reduce((a, b) => ({ savings: a.savings + (b.annual_savings || 0), carbon: a.carbon + (b.carbon_reduction_tons || 0), cost: a.cost + (b.net_cost || 0) }), { savings: 0, carbon: 0, cost: 0 });
  const portfolioBuildings = buildings.filter(b => portfolio?.ids.includes(b.id));

  const runSearch = async (event: FormEvent) => {
    event.preventDefault(); if (!nlQuery.trim()) return;
    setBusy("search"); setError(""); setPortfolio(null);
    try {
      const res = await fetch(`${API_BASE}/api/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: nlQuery.trim(), lat: location.lat, lon: location.lon, radius: scanRadius }) });
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json(); const ids = Array.isArray(data.selected_ids) ? data.selected_ids : [];
      setActiveIds(ids); setSearchAnswer(data.answer || `${ids.length} matching assets found.`); if (ids[0]) setSelectedId(ids[0]);
    } catch (err) { setError(err instanceof Error ? err.message : "Search failed."); } finally { setBusy(null); }
  };

  const optimize = async () => {
    const amount = Number(budget); if (!Number.isFinite(amount) || amount <= 0) { setError("Enter a budget greater than zero."); return; }
    setBusy("optimize"); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/optimize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ budget: amount, lat: location.lat, lon: location.lon, radius: scanRadius }) });
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json(); const ids = Array.isArray(data.selected_ids) ? data.selected_ids : [];
      if (Array.isArray(data.buildings) && data.buildings.length) setBuildings(data.buildings);
      setPortfolio({ ids, remaining: data.remaining_budget ?? Math.max(0, amount) }); setActiveIds([]); setSelectedId(ids[0] ?? null);
    } catch (err) { setError(err instanceof Error ? err.message : "Optimization failed."); } finally { setBusy(null); }
  };

  const generateReport = async () => {
    if (!selected) return; setBusy("report"); setReport(""); setReportSections({}); setError("");
    try {
      const assetId = selected.osm_id ?? selected.id;
      const res = await fetch(`${API_BASE}/api/buildings/${assetId}/report?lat=${location.lat}&lon=${location.lon}&radius=${scanRadius}`);
      if (!res.ok) throw new Error(await apiError(res)); const data = await res.json(); setReport(data.report || "Investment analysis generated."); setReportSections(data.sections && typeof data.sections === "object" ? data.sections : {});
    } catch (err) { setError(err instanceof Error ? err.message : "AI report failed."); } finally { setBusy(null); }
  };

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Zap size={20} fill="currentColor" /></span><div><strong>GridPulse <i>AI</i></strong><small>Urban energy intelligence</small></div></div>
      <form className="location-search" onSubmit={searchLocation}><MapPin size={16} /><input aria-label="Search a Florida city or address" list="florida-city-suggestions" value={locationQuery} onChange={e => setLocationQuery(e.target.value)} placeholder="Florida city, school, landmark, or address" /><datalist id="florida-city-suggestions">{FLORIDA_CITY_SUGGESTIONS.map(city => <option value={city} key={city} />)}</datalist><select aria-label="Search radius" value={scanRadius} onChange={e => setScanRadius(Number(e.target.value))}><option value={5000}>5 km</option><option value={10000}>10 km</option><option value={15000}>15 km</option><option value={25000}>25 km city</option></select><button disabled={locationBusy}>{locationBusy ? <Loader2 className="spin" /> : <Search />} Search area</button></form>
      <div className="header-actions"><button className="theme-toggle" type="button" onClick={() => setTheme(current => current === "dark" ? "light" : "dark")} aria-label={`Currently ${theme} mode. Switch to ${theme === "dark" ? "light" : "dark"} mode`}>{theme === "dark" ? <Moon size={17} /> : <Sun size={17} />}<span>{theme === "dark" ? "Dark" : "Light"}</span></button><div className="header-meta"><span className="live-dot" /> <div><strong>REAL PUBLIC BUILDING DATA</strong><small>OpenStreetMap footprints · Open-Meteo climate · source links included</small></div></div></div>
    </header>

    <section className="kpi-grid">
      <Kpi icon={<Building2 />} label="Buildings loaded" value={loading ? "—" : number(buildings.length)} hint={`Up to 3,000 real OSM footprints within ${scanRadius / 1000} km`} tone="blue" />
      <Kpi icon={<Target />} label="Investment opportunity" value={money(totals.cost, true)} hint="Net deployable capital" tone="violet" />
      <Kpi icon={<DollarSign />} label="Annual savings" value={money(totals.savings, true)} hint="Projected every year" tone="green" />
      <Kpi icon={<Leaf />} label="Carbon reduction" value={`${number(totals.carbon)} t`} hint="CO₂e avoided / year" tone="amber" />
    </section>

    {error && <div className="error-banner"><Activity size={16} />{error}<button onClick={() => setError("")}>Dismiss</button></div>}

    <section className="workspace">
      <aside className="control-panel panel">
        <div className="panel-heading"><div><span className="eyebrow">DISCOVERY</span><h2>Ranked assets</h2></div><span className="count">{filtered.length}</span></div>
        <div className="field search-field"><Search size={16} /><input aria-label="Filter assets" value={directoryQuery} onChange={e => setDirectoryQuery(e.target.value)} placeholder="Search name, address, type" /></div>
        <div className="asset-list">
          {loading && <StateMessage icon={<Loader2 className="spin" />} text="Scanning urban assets…" />}
          {!loading && !filtered.length && <StateMessage icon={<Search />} text="No assets match this filter." />}
          {filtered.map(b => <button key={b.id} className={`asset-row ${selectedId === b.id ? "selected" : ""} ${!portfolio && activeIds.includes(b.id) ? "active" : ""}`} onClick={() => { setSelectedId(b.id); setActiveIds([]); setReport(""); setReportSections({}); }}>
            <span className={`rank rank-${scoreTier(b.investment_score)}`}>{b.rank ?? "·"}</span><span className="asset-copy"><strong>{b.name}</strong><small><MapPin size={11} /> {b.address || b.type || "Mapped asset"}</small></span><span className="asset-score"><strong>{number(b.investment_score)}</strong><small>SCORE</small></span>
          </button>)}
        </div>
      </aside>

      <div className="map-panel panel">
        <MapComponent buildings={displayedBuildings} center={location} theme={theme} selectedBuildingId={selectedId} optimizedIds={portfolio ? [] : activeIds} onSelectBuilding={id => { setSelectedId(id); setActiveIds([]); setReport(""); setReportSections({}); }} />
        <div className="map-title"><span className="eyebrow">{location.displayName} · {scanRadius / 1000} KM SCAN</span><strong>Mapped building footprints</strong></div>
        <div className="legend"><span><i className="high" />High 75–100</span><span><i className="moderate" />Moderate 50–74</span><span><i className="low" />Low 0–49</span></div>
      </div>

      <aside className="detail-panel panel">
        {!selected ? <StateMessage icon={<Building2 />} text="Select an asset to inspect its energy profile." /> : <>
          <div className="detail-head"><div><span className="eyebrow">ASSET #{selected.rank ?? selected.id}</span><h2>{selected.name}</h2><p>{selected.address || `${selected.lat.toFixed(4)}, ${selected.lon.toFixed(4)}`}</p></div><div className={`score-ring score-${scoreTier(selected.investment_score)}`}><strong>{number(selected.investment_score)}</strong><span>SCORE</span></div></div>
          <div className="metric-grid"><Metric label="Installation" value={money(selected.gross_cost ?? (selected.net_cost != null ? selected.net_cost / .7 : undefined))} /><Metric label="Net cost" value={money(selected.net_cost)} accent /><Metric label="Annual savings" value={money(selected.annual_savings)} /><Metric label="Payback" value={selected.payback_years == null ? "N/A" : `${selected.payback_years} yrs`} /><Metric label="Carbon / yr" value={`${number(selected.carbon_reduction_tons)} t`} /><Metric label="Solar system" value={selected.solar_capacity_kw == null ? "N/A" : `${number(selected.solar_capacity_kw)} kW`} /></div>
          <div className="section-block"><div className="section-label"><span>Current annual consumption</span><strong>{selected.annual_electricity_kwh == null ? "Not publicly available" : `${number(selected.annual_electricity_kwh)} kWh`}</strong></div><div className="energy-track"><i style={{ width: "0%" }} /></div><small>{selected.energy_baseline_type || "No public meter record"} · {number(selected.annual_solar_generation_kwh)} kWh modeled solar potential</small></div>
          <div className="section-block"><div className="section-label"><span>5-year demand forecast</span><small>{selected.prediction_confidence || "unknown"} confidence</small></div><div className="forecast-bars">{(selected.future_usage_predictions || []).map((p, _index, arr) => { const max = Math.max(...arr.map(x => x.predicted_kwh), 1); return <div key={p.year}><i style={{ height: `${Math.max(18, p.predicted_kwh / max * 100)}%` }} /><span>{String(p.year).slice(-2)}</span></div>; })}{!selected.future_usage_predictions?.length && <span className="muted">Forecast unavailable</span>}</div></div>
          <div className="provenance"><CheckCircle2 size={16} /><div><strong>Real mapped footprint · Energy data: {selected.energy_data_quality || "not available"}</strong><span>{selected.building_source || selected.data_sources?.join(" · ")} {selected.source_url && <>· <a href={selected.source_url} target="_blank" rel="noreferrer">View source record</a></>}</span></div></div>
          {(selected.incentives?.length ? selected.incentives : [{ name: "Federal clean energy credit", value: `${money((selected.gross_cost ?? (selected.net_cost || 0) / .7) - (selected.net_cost || 0))} estimated credit`, source_url: "", status: "Eligibility review required" }]).map(incentive => <div className="incentive" key={incentive.name}><Sun size={17} /><div><strong>{incentive.name}</strong><span>{incentive.value} · {incentive.status}</span></div></div>)}
          <button className="primary-button" disabled={busy === "report"} onClick={generateReport}>{busy === "report" ? <Loader2 className="spin" /> : <Sparkles />} Generate Gemini feasibility report</button>
          {report && <div className="ai-report"><span className="eyebrow">AI INVESTMENT REPORT</span>{Object.entries(reportSections).length ? Object.entries(reportSections).map(([title, content]) => <section key={title}><h3>{title}</h3><p>{content}</p></section>) : <p>{report}</p>}</div>}
        </>}
      </aside>
    </section>

    <section className="action-grid">
      <div className="action-card panel"><div className="action-icon violet"><Sparkles /></div><div className="action-main"><span className="eyebrow">AI ASSET ROUTING</span><h3>Ask your portfolio</h3><form onSubmit={runSearch}><input value={nlQuery} onChange={e => setNlQuery(e.target.value)} placeholder="e.g. Show the highest-impact low-carbon assets" /><button disabled={busy === "search"}>{busy === "search" ? <Loader2 className="spin" /> : <Search />} Search</button></form>{searchAnswer && <p className="response">{searchAnswer}</p>}</div></div>
      <div className="action-card panel"><div className="action-icon green"><DollarSign /></div><div className="action-main"><span className="eyebrow">CAPITAL ALLOCATION</span><h3>Budget optimizer</h3><div className="budget-row"><label>$<input type="number" min="1" value={budget} onChange={e => setBudget(e.target.value)} /></label><button onClick={optimize} disabled={busy === "optimize"}>{busy === "optimize" ? <Loader2 className="spin" /> : <Target />} Optimize</button></div>{portfolio && <div className="portfolio-result"><strong>{portfolio.ids.length} assets selected · {money(portfolioBuildings.reduce((sum, b) => sum + (b.net_cost || 0), 0))} deployed</strong><span>{money(portfolioBuildings.reduce((sum, b) => sum + (b.annual_savings || 0), 0))}/yr savings · {money(portfolio.remaining)} remaining</span><button onClick={() => { setPortfolio(null); setSelectedId(buildings[0]?.id ?? null); }}>Show all buildings</button></div>}</div></div>
    </section>
  </main>;
}

function Kpi({ icon, label, value, hint, tone }: { icon: ReactNode; label: string; value: string; hint: string; tone: string }) { return <div className="kpi panel"><span className={`kpi-icon ${tone}`}>{icon}</span><div><small>{label}</small><strong>{value}</strong><span>{hint}</span></div></div>; }
function Metric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) { return <div className="metric"><span>{label}</span><strong className={accent ? "accent" : ""}>{value}</strong></div>; }
function StateMessage({ icon, text }: { icon: ReactNode; text: string }) { return <div className="state-message">{icon}<span>{text}</span></div>; }
