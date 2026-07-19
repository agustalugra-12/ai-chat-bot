import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Save, Wifi, WifiOff, Smartphone, Globe, Check, XCircle, Loader2, RefreshCw } from "lucide-react";

const WAHA_STATUS_LABEL = {
  WORKING: { label: "Terhubung", tone: "success" },
  SCAN_QR_CODE: { label: "Menunggu Kode/Scan", tone: "warn" },
  STARTING: { label: "Memulai…", tone: "warn" },
  STOPPED: { label: "Terputus", tone: "muted" },
  FAILED: { label: "Gagal/Terputus", tone: "danger" },
};

function KoneksiWhatsApp() {
  // Sejak 2026-07-19 tiap AI bisa punya nomor WA sendiri-sendiri - kelola sambung/putus
  // per nomor di tab "Koneksi WhatsApp" pada masing-masing AI (AI List), bukan di sini
  // lagi. Panel ini cuma ringkasan status semua nomor + link cepat ke tab tersebut.
  const [sessions, setSessions] = useState(null);

  const load = () => api.get("/waha/sessions").then((r) => setSessions(r.data)).catch(() => setSessions([]));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  return (
    <div className="pelangi-panel p-5 space-y-3" data-testid="waha-panel">
      <div className="flex items-center gap-2 font-[Manrope] font-semibold">
        <Smartphone className="w-4 h-4" /> Koneksi WhatsApp
      </div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">
        Tiap AI bisa punya nomor WhatsApp sendiri. Kelola sambung/putus dari menu <Link to="/ai/bots" className="text-[hsl(var(--primary))] hover:underline">AI List</Link> → pilih AI → tab "Koneksi WhatsApp".
      </div>
      {sessions === null ? (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">Memuat…</p>
      ) : sessions.length === 0 ? (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">Belum ada nomor WhatsApp yang tersambung.</p>
      ) : (
        <div className="space-y-2">
          {sessions.map((s) => {
            const st = WAHA_STATUS_LABEL[s.status] || { label: s.status, tone: "neutral" };
            const nomor = s.me?.id ? s.me.id.split("@")[0] : null;
            return (
              <div key={s.name} className="flex items-center justify-between px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm">
                <div className="flex items-center gap-2">
                  {s.status === "WORKING" ? <Wifi className="w-3.5 h-3.5 text-emerald-600" /> : <WifiOff className="w-3.5 h-3.5 text-[hsl(var(--muted-foreground))]" />}
                  <span className="font-medium">{s.linked_bot?.name || s.name}</span>
                  {nomor && <span className="text-xs text-[hsl(var(--muted-foreground))]">{nomor}</span>}
                </div>
                <Badge tone={st.tone}>{st.label}</Badge>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ModelLLM({ s, setS }) {
  const [options, setOptions] = useState(null);

  useEffect(() => {
    api.get("/settings/llm-options").then(({ data }) => setOptions(data)).catch(() => {});
  }, []);

  if (!options) return null;
  const provider = s.llm_provider || options.default_provider;
  const models = options.providers[provider] || [];
  const model = s.llm_model || options.default_model;

  const onProviderChange = (p) => {
    const firstModel = options.providers[p]?.[0] || "";
    setS({ ...s, llm_provider: p, llm_model: firstModel });
  };

  return (
    <div className="pelangi-panel p-5 space-y-4">
      <div className="font-[Manrope] font-semibold">Provider LLM</div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium">Provider</label>
          <select
            data-testid="set-llm-provider"
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white"
          >
            {Object.keys(options.providers).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium">Model</label>
          <select
            data-testid="set-llm-model"
            value={model}
            onChange={(e) => setS({ ...s, llm_model: e.target.value })}
            className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white"
          >
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">
        Berlaku untuk semua percakapan AI (WhatsApp & Chat Simulator). Semua provider di atas dipanggil lewat kunci LLM yang sama.
      </div>
    </div>
  );
}

const WEB_SYNC_KINDS = [
  { key: "hotel_profile", label: "Profil Hotel", hint: "Nama, alamat, telepon, email → Informasi Hotel di atas" },
  { key: "faq", label: "FAQ", hint: "Pertanyaan umum → Knowledge Base kategori FAQ" },
];

function SinkronisasiWebPelangi() {
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState({});

  const load = async () => setCfg((await api.get("/web-content-integration")).data);
  useEffect(() => { load(); }, []);

  const saveUrl = async () => {
    try {
      await api.put("/web-content-integration", { base_url: cfg.base_url });
      toast.success("URL tersimpan");
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal menyimpan"); }
  };

  const doSync = async (jenis) => {
    setBusy(jenis);
    try {
      const { data } = await api.post(`/web-content-integration/sync/${jenis}`);
      setResult((r) => ({ ...r, [jenis]: data }));
      if (data.ok) toast.success(data.message); else toast.error(data.message);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal sync");
    } finally {
      setBusy(null);
    }
  };

  if (!cfg) return null;

  return (
    <div className="pelangi-panel p-5 space-y-4">
      <div className="flex items-center gap-2 font-[Manrope] font-semibold">
        <Globe className="w-4 h-4" /> Sinkronisasi Konten Web-Pelangi
      </div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">
        Profil hotel & FAQ ditarik dari situs marketing (pelangihomestay.com) supaya tidak perlu diisi dua kali.
      </div>
      <div>
        <label className="text-xs font-medium">URL API Konten</label>
        <div className="mt-1 flex gap-2">
          <input
            data-testid="web-content-url"
            value={cfg.base_url || ""}
            onChange={(e) => setCfg({ ...cfg, base_url: e.target.value })}
            className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm"
          />
          <button onClick={saveUrl} className="text-sm px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]">Simpan</button>
        </div>
      </div>
      <div className="space-y-2">
        {WEB_SYNC_KINDS.map((s) => {
          const res = result[s.key] || cfg.last_sync?.[s.key];
          return (
            <div key={s.key} className="px-3 py-2 rounded-md border border-[hsl(var(--border))] space-y-1">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium">{s.label}</div>
                  <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{s.hint}</div>
                </div>
                <button
                  data-testid={`web-sync-${s.key}`} onClick={() => doSync(s.key)} disabled={busy === s.key}
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50 disabled:opacity-50 shrink-0"
                >
                  {busy === s.key ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />} Sync
                </button>
              </div>
              {res && (
                <div className={`text-[11px] flex items-center gap-1 ${res.ok ? "text-emerald-600" : "text-red-600"}`}>
                  {res.ok ? <Check className="w-3 h-3" /> : <XCircle className="w-3 h-3" />} {res.message}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Settings() {
  const [s, setS] = useState(null);

  const load = async () => setS((await api.get("/settings")).data);
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      const { id, updated_at, ...body } = s;
      await api.put("/settings", body);
      toast.success("Settings tersimpan"); load();
    } catch (e) { toast.error("Gagal menyimpan"); }
  };

  if (!s) return <div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">Memuat…</div>;

  const F = ({ label, testid, children }) => (
    <div>
      <label className="text-xs font-medium">{label}</label>
      <div className="mt-1">{children}</div>
    </div>
  );

  return (
    <div>
      <PageHeader
        tid="settings-header"
        title="Settings"
        subtitle="Konfigurasi informasi hotel dan opsi AI."
        right={
          <button data-testid="settings-save" onClick={save}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Save className="w-4 h-4" /> Simpan
          </button>
        }
      />
      <div className="p-8 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="pelangi-panel p-5 space-y-4">
          <div className="font-[Manrope] font-semibold">Informasi Hotel</div>
          <F label="Nama Hotel">
            <input data-testid="set-hotel-name" value={s.hotel_name || ""} onChange={(e) => setS({ ...s, hotel_name: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
          </F>
          <F label="Alamat">
            <input data-testid="set-address" value={s.address || ""} onChange={(e) => setS({ ...s, address: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
          </F>
          <F label="Link Google Maps">
            <input data-testid="set-maps-url" value={s.maps_url || ""} onChange={(e) => setS({ ...s, maps_url: e.target.value })} placeholder="https://maps.app.goo.gl/..." className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            <div className="text-[11px] text-[hsl(var(--muted-foreground))] mt-1">Dibagikan AI kalau tamu tanya lokasi/peta. Ambil dari Google Maps → cari lokasi → tombol "Bagikan" → salin link.</div>
          </F>
          <div className="grid grid-cols-2 gap-3">
            <F label="Telepon"><input data-testid="set-phone" value={s.phone || ""} onChange={(e) => setS({ ...s, phone: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></F>
            <F label="Email"><input data-testid="set-email" value={s.email || ""} onChange={(e) => setS({ ...s, email: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></F>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <F label="Jam Check-in"><input data-testid="set-checkin" value={s.checkin_time || ""} onChange={(e) => setS({ ...s, checkin_time: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></F>
            <F label="Jam Check-out"><input data-testid="set-checkout" value={s.checkout_time || ""} onChange={(e) => setS({ ...s, checkout_time: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></F>
          </div>
        </div>

        <div className="pelangi-panel p-5 space-y-4">
          <div className="font-[Manrope] font-semibold">Opsi AI & Channel</div>
          <label className="flex items-start gap-3 cursor-pointer" data-testid="set-wa-toggle-label">
            <input type="checkbox" checked={!!s.whatsapp_enabled} onChange={(e) => setS({ ...s, whatsapp_enabled: e.target.checked })} data-testid="set-wa-toggle" className="mt-1" />
            <div>
              <div className="text-sm font-medium">Aktifkan integrasi WhatsApp</div>
              <div className="text-xs text-[hsl(var(--muted-foreground))]">Aktifkan setelah service VPS WA terhubung. Chat Simulator tetap jalan tanpa ini.</div>
            </div>
          </label>
          <label className="flex items-start gap-3 cursor-pointer" data-testid="set-stock-toggle-label">
            <input type="checkbox" checked={!!s.show_stock_count} onChange={(e) => setS({ ...s, show_stock_count: e.target.checked })} data-testid="set-stock-toggle" className="mt-1" />
            <div>
              <div className="text-sm font-medium">Tampilkan jumlah stok kamar ke tamu</div>
              <div className="text-xs text-[hsl(var(--muted-foreground))]">Jika dimatikan, AI hanya menjawab "Available / Not Available".</div>
            </div>
          </label>
        </div>

        <ModelLLM s={s} setS={setS} />
        <SinkronisasiWebPelangi />
        <KoneksiWhatsApp />
      </div>
    </div>
  );
}
