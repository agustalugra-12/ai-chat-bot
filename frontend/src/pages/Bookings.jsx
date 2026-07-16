import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api, fmtIDR } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine } from "lucide-react";
import { Modal } from "./KnowledgeBase";

const empty = { guest_name: "", whatsapp: "", check_in: "", check_out: "", room_type: "standard", num_rooms: 1, num_guests: 1, total_amount: 0, dp_amount: 0, notes: "" };

const STATUS_LABEL = { pending: "Pending", confirmed: "Confirmed", cancelled: "Cancelled" };
const STATUS_TONE = { pending: "warn", confirmed: "success", cancelled: "danger" };

export default function Bookings() {
  const [list, setList] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    const [{ data: b }, { data: r }] = await Promise.all([api.get("/bookings"), api.get("/rooms")]);
    setList(b); setRooms(r);
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm({ ...empty, room_type: rooms[0]?.room_type || "standard" }); setOpen(true); };
  const openEdit = (b) => { setEditing(b); setForm({ ...b }); setOpen(true); };

  const save = async () => {
    const p = {
      ...form,
      num_rooms: Number(form.num_rooms), num_guests: Number(form.num_guests),
      total_amount: Number(form.total_amount || 0), dp_amount: Number(form.dp_amount || 0),
    };
    try {
      if (editing) await api.put(`/bookings/${editing.id}`, p);
      else await api.post("/bookings", p);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const setStatus = async (id, status) => {
    await api.put(`/bookings/${id}`, { status });
    toast.success(`Status: ${status}`); load();
  };

  const visible = list.filter((x) => filter === "all" ? true : x.status === filter);

  return (
    <div>
      <PageHeader
        tid="bookings-header"
        title="Booking"
        subtitle="Semua reservasi tamu — baik dari AI, WhatsApp, maupun manual."
        right={
          <button data-testid="booking-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Booking Manual
          </button>
        }
      />

      <div className="p-8 space-y-5">
        <div className="flex gap-2 flex-wrap">
          {["all", "pending", "confirmed", "cancelled"].map((f) => (
            <button key={f} data-testid={`booking-filter-${f}`} onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-full border capitalize ${filter === f ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] bg-white hover:bg-[hsl(var(--muted))]"}`}>
              {f === "all" ? "Semua" : STATUS_LABEL[f]}
            </button>
          ))}
        </div>

        <div className="pelangi-panel overflow-hidden">
          {visible.length === 0 ? <EmptyState tid="bookings-empty" title="Belum ada booking" /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                <tr>
                  <th className="px-5 py-3">Tamu</th>
                  <th className="px-5 py-3">Tanggal</th>
                  <th className="px-5 py-3">Kamar</th>
                  <th className="px-5 py-3">Total</th>
                  <th className="px-5 py-3">Sumber</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((b) => (
                  <tr key={b.id} className="border-b border-[hsl(var(--border))] pelangi-row" data-testid={`booking-row-${b.id}`}>
                    <td className="px-5 py-3">
                      <div className="font-medium">{b.guest_name}</div>
                      <div className="text-xs text-[hsl(var(--muted-foreground))]">{b.whatsapp}</div>
                    </td>
                    <td className="px-5 py-3 text-xs">
                      <div>{b.check_in} → {b.check_out}</div>
                      <div className="text-[hsl(var(--muted-foreground))]">{b.num_guests} tamu · {b.num_rooms} kamar</div>
                    </td>
                    <td className="px-5 py-3 capitalize">{b.room_type}</td>
                    <td className="px-5 py-3 font-[Manrope] font-semibold">{fmtIDR(b.total_amount)}</td>
                    <td className="px-5 py-3"><Badge tone={b.source === "ai" ? "primary" : "muted"}>{b.source}</Badge></td>
                    <td className="px-5 py-3"><Badge tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status]}</Badge></td>
                    <td className="px-5 py-3 text-right">
                      <div className="inline-flex gap-1.5">
                        {b.status !== "confirmed" && <button data-testid={`booking-confirm-${b.id}`} onClick={() => setStatus(b.id, "confirmed")} className="text-[11px] px-2 py-1 rounded-md border border-emerald-500 text-emerald-700 hover:bg-emerald-50">Confirm</button>}
                        {b.status !== "cancelled" && <button data-testid={`booking-cancel-${b.id}`} onClick={() => setStatus(b.id, "cancelled")} className="text-[11px] px-2 py-1 rounded-md border border-[hsl(var(--accent))] text-[hsl(var(--accent))] hover:bg-[hsl(var(--accent))] hover:text-white">Cancel</button>}
                        <button data-testid={`booking-edit-${b.id}`} onClick={() => openEdit(b)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-3.5 h-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Booking" : "Booking Baru"}>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="text-xs font-medium">Nama Tamu</label>
              <input data-testid="booking-form-name" value={form.guest_name} onChange={(e) => setForm({ ...form, guest_name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">WhatsApp</label>
              <input data-testid="booking-form-whatsapp" value={form.whatsapp} onChange={(e) => setForm({ ...form, whatsapp: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Check-in</label>
              <input data-testid="booking-form-checkin" type="date" value={form.check_in} onChange={(e) => setForm({ ...form, check_in: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Check-out</label>
              <input data-testid="booking-form-checkout" type="date" value={form.check_out} onChange={(e) => setForm({ ...form, check_out: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Room Type</label>
              <select data-testid="booking-form-roomtype" value={form.room_type} onChange={(e) => setForm({ ...form, room_type: e.target.value })}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                {rooms.map((r) => <option key={r.id} value={r.room_type}>{r.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium">Jumlah Kamar</label>
              <input data-testid="booking-form-numrooms" type="number" min={1} value={form.num_rooms} onChange={(e) => setForm({ ...form, num_rooms: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Jumlah Tamu</label>
              <input data-testid="booking-form-numguests" type="number" min={1} value={form.num_guests} onChange={(e) => setForm({ ...form, num_guests: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Total (opsional)</label>
              <input type="number" value={form.total_amount} onChange={(e) => setForm({ ...form, total_amount: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">Catatan</label>
              <textarea rows={2} value={form.notes || ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="booking-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
