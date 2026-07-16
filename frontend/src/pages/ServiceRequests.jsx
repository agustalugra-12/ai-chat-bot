import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";

const LABELS = {
  extra_bed: "Extra Bed", extra_towel: "Tambahan Handuk", mineral_water: "Air Mineral",
  cleaning: "Cleaning Room", laundry: "Laundry", motor_rental: "Rental Motor",
  airport_pickup: "Airport Pickup", extra_breakfast: "Breakfast Tambahan",
};

const STATUSES = ["new", "in_progress", "done", "cancelled"];
const TONE = { new: "warn", in_progress: "primary", done: "success", cancelled: "muted" };

export default function ServiceRequests() {
  const [list, setList] = useState([]);
  const [filter, setFilter] = useState("all");

  const load = async () => setList((await api.get("/service-requests")).data);
  useEffect(() => { load(); }, []);

  const setStatus = async (id, status) => {
    await api.patch(`/service-requests/${id}`, { status });
    toast.success(`Status: ${status}`); load();
  };

  const visible = list.filter((s) => filter === "all" ? true : s.status === filter);

  return (
    <div>
      <PageHeader
        tid="sr-header"
        title="Service Requests"
        subtitle="Semua permintaan layanan dari tamu — otomatis masuk dari AI atau bisa ditambahkan admin."
      />

      <div className="p-8 space-y-5">
        <div className="flex gap-2 flex-wrap">
          {["all", ...STATUSES].map((f) => (
            <button key={f} data-testid={`sr-filter-${f}`} onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-full border capitalize ${filter === f ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] bg-white hover:bg-[hsl(var(--muted))]"}`}>
              {f === "all" ? "Semua" : f.replace("_", " ")}
            </button>
          ))}
        </div>

        <div className="pelangi-panel overflow-hidden">
          {visible.length === 0 ? <EmptyState tid="sr-empty" title="Tidak ada request" /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                <tr>
                  <th className="px-5 py-3">Tamu</th>
                  <th className="px-5 py-3">Layanan</th>
                  <th className="px-5 py-3">Qty</th>
                  <th className="px-5 py-3">Catatan</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3 text-right">Ubah</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr key={s.id} className="border-b border-[hsl(var(--border))] pelangi-row" data-testid={`sr-row-${s.id}`}>
                    <td className="px-5 py-3">
                      <div className="font-medium">{s.guest_name}</div>
                      <div className="text-xs text-[hsl(var(--muted-foreground))]">{s.whatsapp}</div>
                    </td>
                    <td className="px-5 py-3">{LABELS[s.service_type] || s.service_type}</td>
                    <td className="px-5 py-3">{s.quantity}</td>
                    <td className="px-5 py-3 text-xs text-[hsl(var(--muted-foreground))] max-w-xs truncate">{s.notes || "-"}</td>
                    <td className="px-5 py-3"><Badge tone={TONE[s.status]}>{s.status}</Badge></td>
                    <td className="px-5 py-3 text-right">
                      <select data-testid={`sr-status-${s.id}`} value={s.status} onChange={(e) => setStatus(s.id, e.target.value)}
                        className="text-xs px-2 py-1 rounded-md border border-[hsl(var(--border))] bg-white">
                        {STATUSES.map((st) => <option key={st} value={st}>{st}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
