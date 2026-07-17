import { useEffect, useRef, useState } from "react";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Save, Wifi, WifiOff, Loader2, RefreshCw } from "lucide-react";

const WAHA_STATUS_LABEL = {
  WORKING: { label: "Terhubung", tone: "success" },
  SCAN_QR_CODE: { label: "Menunggu Kode/Scan", tone: "warn" },
  STARTING: { label: "Memulai…", tone: "warn" },
  STOPPED: { label: "Terputus", tone: "muted" },
  FAILED: { label: "Gagal/Terputus", tone: "danger" },
};

function KoneksiWhatsApp() {
  const [status, setStatus] = useState(null);
  const [phone, setPhone] = useState("");
  const [pairCode, setPairCode] = useState(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  const load = async () => {
    try {
      const { data } = await api.get("/waha/status");
      setStatus(data);
      if (data.status === "WORKING") setPairCode(null);
    } catch (e) { /* diam - status belum tentu selalu bisa diambil */ }
  };

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, 8000);
    return () => clearInterval(pollRef.current);
  }, []);

  const connect = async () => {
    const clean = phone.replace(/[^0-9]/g, "");
    if (!clean || clean.length < 8) { toast.error("Isi nomor WhatsApp yang valid (format 62xxx)"); return; }
    setBusy(true);
    setPairCode(null);
    try {
      const { data } = await api.post("/waha/connect", { phone_number: clean });
      setPairCode(data.code);
      toast.success("Kode pairing dibuat - masukkan sekarang di WhatsApp");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal membuat kode pairing");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm("Putuskan koneksi WhatsApp? Bot tidak akan menerima/membalas pesan sampai disambungkan lagi.")) return;
    setBusy(true);
    try {
      await api.post("/waha/disconnect");
      toast.success("Koneksi diputus");
      setPairCode(null);
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal memutus koneksi");
    } finally {
      setBusy(false);
    }
  };

  const st = WAHA_STATUS_LABEL[status?.status] || { label: status?.status || "Memuat…", tone: "neutral" };
  const nomor = status?.me?.id ? status.me.id.split("@")[0] : null;

  return (
    <div className="pelangi-panel p-5 space-y-4" data-testid="waha-panel">
      <div className="flex items-center justify-between">
        <div className="font-[Manrope] font-semibold">Koneksi WhatsApp (WAHA)</div>
        <button onClick={load} className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]" title="Refresh status">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="flex items-center gap-2">
        {status?.status === "WORKING" ? <Wifi className="w-4 h-4 text-emerald-600" /> : <WifiOff className="w-4 h-4 text-[hsl(var(--muted-foreground))]" />}
        <Badge tone={st.tone}>{st.label}</Badge>
        {nomor && <span className="text-xs text-[hsl(var(--muted-foreground))]">Nomor: {nomor}</span>}
      </div>

      <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md p-2.5">
        Jangan sambung/putus berulang-ulang dalam waktu singkat — WhatsApp bisa memberi
        pembatasan sementara ("reachout timelock") pada nomor. Kalau baru gagal, tunggu
        beberapa menit sebelum coba lagi.
      </div>

      {status?.status !== "WORKING" && (
        <div className="space-y-2">
          <label className="text-xs font-medium">Nomor WhatsApp yang mau disambungkan</label>
          <div className="flex gap-2">
            <input
              value={phone} onChange={(e) => setPhone(e.target.value)}
              placeholder="628123456789" data-testid="waha-phone-input"
              className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm"
            />
            <button
              onClick={connect} disabled={busy} data-testid="waha-connect-btn"
              className="inline-flex items-center gap-1.5 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2 rounded-md hover:opacity-90 disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Sambungkan
            </button>
          </div>
          {pairCode && (
            <div className="text-sm bg-emerald-50 border border-emerald-200 rounded-md p-3" data-testid="waha-pair-code">
              Kode pairing: <span className="font-mono font-bold text-base">{pairCode}</span>
              <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1">
                Buka WhatsApp di HP nomor tsb → titik tiga/Settings → Perangkat Tertaut → Tautkan Perangkat
                → "Tautkan dengan nomor telepon" → masukkan kode ini sekarang (berlaku singkat).
              </div>
            </div>
          )}
        </div>
      )}

      {status?.status === "WORKING" && (
        <button
          onClick={disconnect} disabled={busy} data-testid="waha-disconnect-btn"
          className="inline-flex items-center gap-1.5 text-sm font-medium px-4 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50 disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Putuskan Koneksi
        </button>
      )}
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

        <KoneksiWhatsApp />
      </div>
    </div>
  );
}
