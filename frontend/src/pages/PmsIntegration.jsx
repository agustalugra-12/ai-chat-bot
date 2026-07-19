import { useEffect, useState } from "react";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api, API_BASE } from "@/lib/api";
import { toast } from "sonner";
import {
  Save, Eye, EyeOff, Copy, Check, RefreshCw, Loader2, Zap,
  CheckCircle2, XCircle, ChevronDown, ChevronRight,
} from "lucide-react";

const CAP_LABELS = {
  check_availability: "Cek Ketersediaan Kamar",
  create_booking: "Buat Booking",
  check_booking_status: "Cek Status Booking",
  create_maintenance_ticket: "Buat Tiket Maintenance",
  create_service_request: "Buat Permintaan Layanan",
  cancel_booking: "Ajukan Pembatalan Booking",
  refund: "Refund",
  ota_sync: "OTA Sync",
  payment: "Payment",
  checkin: "Check-in",
};
const CAP_WIRED = new Set(["check_availability", "create_booking", "create_maintenance_ticket", "check_booking_status", "create_service_request", "cancel_booking"]);
const CAP_ORDER = ["check_availability", "create_booking", "check_booking_status", "create_maintenance_ticket", "create_service_request", "cancel_booking", "refund", "ota_sync", "payment", "checkin"];

const SYNC_KINDS = [
  { key: "rule", label: "Business Rule" },
];

function F({ label, hint, children }) {
  return (
    <div>
      <label className="text-xs font-medium">{label}</label>
      {hint && <div className="text-[11px] text-[hsl(var(--muted-foreground))] mb-1">{hint}</div>}
      <div className="mt-1">{children}</div>
    </div>
  );
}

export default function PmsIntegration() {
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState(null);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [showEndpoints, setShowEndpoints] = useState(false);
  const [logs, setLogs] = useState([]);
  const [syncBusy, setSyncBusy] = useState(null);
  const [syncResult, setSyncResult] = useState({});

  const load = async () => {
    const { data } = await api.get("/pms-integration");
    setCfg(data);
    setForm(data);
  };
  const loadLogs = async () => {
    try { const { data } = await api.get("/pms-integration/logs?limit=30"); setLogs(data); } catch (e) { /* diam */ }
  };

  useEffect(() => { load(); loadLogs(); }, []);

  if (!cfg || !form) return <div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">Memuat…</div>;

  const dirty = JSON.stringify({ pms_base_url: form.pms_base_url, pms_api_key: form.pms_api_key, bot_whatsapp_number: form.bot_whatsapp_number, endpoints: form.endpoints })
    !== JSON.stringify({ pms_base_url: cfg.pms_base_url, pms_api_key: cfg.pms_api_key, bot_whatsapp_number: cfg.bot_whatsapp_number, endpoints: cfg.endpoints });

  const save = async () => {
    setBusy(true);
    try {
      const { data } = await api.put("/pms-integration", {
        pms_base_url: form.pms_base_url, pms_api_key: form.pms_api_key,
        bot_whatsapp_number: form.bot_whatsapp_number, endpoints: form.endpoints,
      });
      setCfg(data); setForm(data);
      toast.success("Konfigurasi Integrasi PMS tersimpan");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal menyimpan");
    } finally { setBusy(false); }
  };

  const toggleCap = async (key, val) => {
    setBusy(true);
    try {
      const { data } = await api.post("/pms-integration/capabilities", { [key]: val });
      setCfg(data); setForm(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal mengubah kapabilitas");
    } finally { setBusy(false); }
  };

  const testConn = async () => {
    setTesting(true);
    try {
      const { data } = await api.post("/pms-integration/test");
      setTestResult(data);
      if (data.ok) toast.success("Test Connection berhasil"); else toast.error("Test Connection gagal");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal menguji koneksi");
    } finally { setTesting(false); }
  };

  const regenToken = async () => {
    if (!window.confirm("Token webhook lama akan langsung tidak berlaku (WAHA akan otomatis disinkronkan ke token baru). Lanjutkan?")) return;
    setBusy(true);
    try {
      const { data } = await api.post("/pms-integration/regenerate-webhook-token");
      setCfg(data); setForm(data);
      toast.success("Token webhook baru dibuat & WAHA disinkronkan");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal generate ulang token");
    } finally { setBusy(false); }
  };

  const doSync = async (jenis) => {
    setSyncBusy(jenis);
    try {
      const { data } = await api.post(`/pms-integration/sync/${jenis}`);
      setSyncResult((s) => ({ ...s, [jenis]: data }));
      if (data.ok) toast.success(`Sync ${jenis} berhasil`); else toast.info(data.message);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal sync");
    } finally { setSyncBusy(null); }
  };

  const salin = async (label, value) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setCopied(label);
    setTimeout(() => setCopied(null), 2000);
  };

  const webhookUrl = cfg.webhook_token ? `${API_BASE}/webhook/waha?token=${cfg.webhook_token}` : null;

  return (
    <div>
      <PageHeader
        tid="pms-integration-header"
        title="Integrasi PMS"
        subtitle="Konfigurasi koneksi ke Pelangi PMS — bisa diubah dari sini tanpa perlu edit .env atau deploy ulang."
        right={
          <button data-testid="pms-save" onClick={save} disabled={!dirty || busy}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90 disabled:opacity-50">
            <Save className="w-4 h-4" /> Simpan
          </button>
        }
      />

      <div className="p-8 space-y-6">
        {/* Status + Test Connection */}
        <div className="pelangi-panel p-5 space-y-3" data-testid="pms-status-panel">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              {cfg.last_test_ok ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <XCircle className="w-4 h-4 text-[hsl(var(--muted-foreground))]" />}
              <Badge tone={cfg.last_test_ok ? "success" : cfg.last_test_ok === false ? "danger" : "muted"}>
                {cfg.last_test_ok === null ? "Belum pernah dites" : cfg.last_test_ok ? "Terhubung" : "Gagal"}
              </Badge>
              {cfg.last_test_at && <span className="text-xs text-[hsl(var(--muted-foreground))]">Terakhir dites: {new Date(cfg.last_test_at).toLocaleString("id-ID")}</span>}
              {cfg.last_test_latency_ms != null && <span className="text-xs text-[hsl(var(--muted-foreground))]">· {cfg.last_test_latency_ms}ms</span>}
            </div>
            <button onClick={testConn} disabled={testing} data-testid="pms-test-btn"
              className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50 disabled:opacity-50">
              {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />} Test Connection
            </button>
          </div>
          {(testResult || cfg.last_test_message) && (
            <div className={`text-xs rounded-md p-2.5 ${(testResult?.ok ?? cfg.last_test_ok) ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}>
              {testResult?.message || cfg.last_test_message}
            </div>
          )}
        </div>

        {/* Endpoint & Kredensial */}
        <div className="pelangi-panel p-5 space-y-4">
          <div className="font-[Manrope] font-semibold">Endpoint &amp; Kredensial PMS</div>
          <F label="PMS URL">
            <input data-testid="pms-url" value={form.pms_base_url || ""} onChange={(e) => setForm({ ...form, pms_base_url: e.target.value })}
              placeholder="https://api.pelangihomestay.com" className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm font-mono" />
          </F>
          <F label="API Key">
            <div className="relative">
              <input data-testid="pms-api-key" type={showKey ? "text" : "password"} value={form.pms_api_key || ""}
                onChange={(e) => setForm({ ...form, pms_api_key: e.target.value })}
                className="w-full px-3 py-2 pr-10 rounded-md border border-[hsl(var(--border))] text-sm font-mono" />
              <button type="button" onClick={() => setShowKey((s) => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 text-[hsl(var(--muted-foreground))]">
                {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <div className="text-[11px] text-[hsl(var(--muted-foreground))] mt-1">Diambil dari halaman "Integrasi AI Chat Bot Eksternal" di dashboard Pelangi PMS.</div>
          </F>
          <F label="Nomor WhatsApp Bot" hint="Catatan referensi - koneksi WhatsApp sungguhan dikelola di halaman Settings > Koneksi WhatsApp (WAHA).">
            <input data-testid="pms-bot-number" value={form.bot_whatsapp_number || ""} onChange={(e) => setForm({ ...form, bot_whatsapp_number: e.target.value })}
              placeholder="628123456789" className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
          </F>

          <div>
            <button type="button" onClick={() => setShowEndpoints((s) => !s)} className="text-xs font-medium text-[hsl(var(--muted-foreground))] flex items-center gap-1">
              {showEndpoints ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />} Path Endpoint (lanjutan)
            </button>
            {showEndpoints && (
              <div className="mt-2 space-y-2 pl-1">
                {Object.entries(form.endpoints || {}).map(([k, v]) => (
                  <F key={k} label={k}>
                    <input value={v} onChange={(e) => setForm({ ...form, endpoints: { ...form.endpoints, [k]: e.target.value } })}
                      className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm font-mono" />
                  </F>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Webhook URL */}
        <div className="pelangi-panel p-5 space-y-3">
          <div className="font-[Manrope] font-semibold">Webhook URL (masuk dari WAHA)</div>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            URL ini yang dikonfigurasikan di sesi WAHA untuk mengirim pesan WhatsApp masuk ke ai-chat-bot.
            Regenerate token akan otomatis menyinkronkan konfigurasi WAHA - tidak perlu langkah manual lagi.
          </p>
          <div className="flex items-center gap-2">
            <input readOnly value={webhookUrl || "-"} data-testid="pms-webhook-url" className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-xs font-mono bg-stone-50" onFocus={(e) => e.target.select()} />
            <button onClick={() => salin("webhook", webhookUrl)} className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50">
              {copied === "webhook" ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <button onClick={regenToken} disabled={busy} data-testid="pms-regen-token" className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50 disabled:opacity-50">
              <RefreshCw className="w-3.5 h-3.5" /> Generate Ulang
            </button>
          </div>
        </div>

        {/* Capabilities */}
        <div className="pelangi-panel p-5 space-y-3">
          <div className="font-[Manrope] font-semibold">Capabilities</div>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Kapabilitas bertanda <Badge tone="muted">belum tersambung</Badge> tersimpan tapi TIDAK melakukan apa pun kalau
            diaktifkan - endpoint PMS-nya belum dibangun. Jangan diaktifkan dulu sampai tersedia.
          </p>
          <div className="grid sm:grid-cols-2 gap-2">
            {CAP_ORDER.map((key) => {
              const wired = CAP_WIRED.has(key);
              const val = !!form.capabilities?.[key];
              return (
                <label key={key} className={`flex items-center justify-between gap-2 px-3 py-2 rounded-md border border-[hsl(var(--border))] ${wired ? "" : "opacity-60"}`}>
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{CAP_LABELS[key]}</span>
                    {!wired && <Badge tone="muted">belum tersambung</Badge>}
                  </div>
                  <input
                    type="checkbox" checked={val} disabled={busy}
                    data-testid={`cap-${key}`}
                    onChange={(e) => { setForm({ ...form, capabilities: { ...form.capabilities, [key]: e.target.checked } }); toggleCap(key, e.target.checked); }}
                  />
                </label>
              );
            })}
          </div>
        </div>

        {/* Sync */}
        <div className="pelangi-panel p-5 space-y-3">
          <div className="font-[Manrope] font-semibold">Sync dari PMS</div>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            PMS belum expose endpoint untuk item di bawah ini (baru ada Ketersediaan/Booking Request/Tiket) -
            tombol tetap disediakan, hasilnya akan melaporkan status apa adanya, bukan pura-pura berhasil.
          </p>
          <div className="grid sm:grid-cols-2 gap-2">
            {SYNC_KINDS.map((s) => {
              const res = syncResult[s.key] || cfg.last_sync?.[s.key];
              return (
                <div key={s.key} className="px-3 py-2 rounded-md border border-[hsl(var(--border))] space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{s.label}</span>
                    <button onClick={() => doSync(s.key)} disabled={syncBusy === s.key} data-testid={`sync-${s.key}`}
                      className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50 disabled:opacity-50">
                      {syncBusy === s.key ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />} Sync
                    </button>
                  </div>
                  {res && <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{res.message}</div>}
                </div>
              );
            })}
          </div>
        </div>

        {/* Logs */}
        <div className="pelangi-panel p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-[Manrope] font-semibold">Riwayat Request/Response PMS</div>
            <button onClick={loadLogs} className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"><RefreshCw className="w-4 h-4" /></button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="pms-logs-table">
              <thead>
                <tr className="text-left text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                  <th className="py-1.5 pr-3">Waktu</th>
                  <th className="py-1.5 pr-3">Method</th>
                  <th className="py-1.5 pr-3">Endpoint</th>
                  <th className="py-1.5 pr-3">Status</th>
                  <th className="py-1.5 pr-3">Latency</th>
                  <th className="py-1.5">Detail</th>
                </tr>
              </thead>
              <tbody>
                {logs.length === 0 && (
                  <tr><td colSpan={6} className="py-4 text-center text-[hsl(var(--muted-foreground))]">Belum ada request tercatat.</td></tr>
                )}
                {logs.map((l) => (
                  <tr key={l.id} className="border-b border-[hsl(var(--border))]/60">
                    <td className="py-1.5 pr-3 whitespace-nowrap">{new Date(l.at).toLocaleString("id-ID")}</td>
                    <td className="py-1.5 pr-3">{l.method}</td>
                    <td className="py-1.5 pr-3 font-mono">{l.endpoint}</td>
                    <td className="py-1.5 pr-3">
                      <Badge tone={l.ok ? "success" : "danger"}>{l.status_code ?? "error"}</Badge>
                    </td>
                    <td className="py-1.5 pr-3">{l.latency_ms}ms</td>
                    <td className="py-1.5 max-w-[240px] truncate" title={l.detail}>{l.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
