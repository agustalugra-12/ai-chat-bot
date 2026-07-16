import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Save } from "lucide-react";

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
      </div>
    </div>
  );
}
