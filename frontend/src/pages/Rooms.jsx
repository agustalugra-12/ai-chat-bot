import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api, fmtIDR } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine, Trash2 } from "lucide-react";
import { Modal } from "./KnowledgeBase";
import { ImageUploader } from "@/components/ImageUploader";

const empty = {
  name: "", room_type: "", price_per_night: 0, capacity: 2,
  photo_url: "", images: [], facilities: [], total_units: 1, is_available: true, description: "",
};

export default function Rooms() {
  const [rooms, setRooms] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const [facInput, setFacInput] = useState("");

  const load = async () => setRooms((await api.get("/rooms")).data);
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setFacInput(""); setOpen(true); };
  const openEdit = (r) => { setEditing(r); setForm({ ...r }); setFacInput(""); setOpen(true); };

  const save = async () => {
    if (!form.name || !form.room_type) return toast.error("Nama & tipe wajib");
    const payload = { ...form, price_per_night: Number(form.price_per_night), capacity: Number(form.capacity), total_units: Number(form.total_units) };
    try {
      if (editing) await api.put(`/rooms/${editing.id}`, payload);
      else await api.post("/rooms", payload);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus kamar ini?")) return;
    await api.delete(`/rooms/${id}`); toast.success("Dihapus"); load();
  };

  const addFac = () => {
    const v = facInput.trim(); if (!v) return;
    setForm({ ...form, facilities: [...(form.facilities || []), v] });
    setFacInput("");
  };
  const rmFac = (i) => setForm({ ...form, facilities: form.facilities.filter((_, k) => k !== i) });

  return (
    <div>
      <PageHeader
        tid="rooms-header"
        title="Manajemen Kamar"
        subtitle="Data kamar yang tampil dan dipakai AI untuk menjawab ketersediaan & harga."
        right={
          <button data-testid="room-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Tambah Kamar
          </button>
        }
      />

      <div className="p-8">
        {rooms.length === 0 ? <EmptyState tid="rooms-empty" title="Belum ada kamar" /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {rooms.map((r) => (
              <div key={r.id} className="pelangi-panel overflow-hidden fade-in-up" data-testid={`room-card-${r.id}`}>
                <div className="aspect-[16/10] bg-stone-100 overflow-hidden">
                  {r.photo_url ? (
                    <img src={r.photo_url} alt={r.name} className="w-full h-full object-cover" />
                  ) : (<div className="w-full h-full flex items-center justify-center text-stone-400 text-xs">No photo</div>)}
                </div>
                <div className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-[Manrope] font-bold text-lg leading-tight">{r.name}</div>
                      <div className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">Tipe: {r.room_type} · Kapasitas {r.capacity}</div>
                    </div>
                    <Badge tone={r.is_available ? "success" : "danger"}>{r.is_available ? "Available" : "Off"}</Badge>
                  </div>
                  <div className="mt-3 flex items-baseline gap-1">
                    <span className="font-[Manrope] font-bold text-xl">{fmtIDR(r.price_per_night)}</span>
                    <span className="text-xs text-[hsl(var(--muted-foreground))]">/ malam</span>
                  </div>
                  {r.facilities?.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {r.facilities.slice(0, 4).map((f, i) => <Badge key={i} tone="muted">{f}</Badge>)}
                      {r.facilities.length > 4 && <Badge tone="muted">+{r.facilities.length - 4}</Badge>}
                    </div>
                  )}
                  <div className="mt-4 flex gap-2">
                    <button data-testid={`room-edit-${r.id}`} onClick={() => openEdit(r)} className="flex-1 text-xs px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))] inline-flex items-center justify-center gap-1"><PencilLine className="w-3 h-3" /> Edit</button>
                    <button data-testid={`room-delete-${r.id}`} onClick={() => remove(r.id)} className="text-xs px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-3 h-3" /></button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Kamar" : "Tambah Kamar"}>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="text-xs font-medium">Nama Kamar</label>
              <input data-testid="room-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Room Type (slug)</label>
              <input data-testid="room-form-type" placeholder="deluxe / suite / standard" value={form.room_type} onChange={(e) => setForm({ ...form, room_type: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Harga / malam (IDR)</label>
              <input data-testid="room-form-price" type="number" value={form.price_per_night} onChange={(e) => setForm({ ...form, price_per_night: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Kapasitas</label>
              <input data-testid="room-form-capacity" type="number" value={form.capacity} onChange={(e) => setForm({ ...form, capacity: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Total Unit</label>
              <input data-testid="room-form-units" type="number" value={form.total_units} onChange={(e) => setForm({ ...form, total_units: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">Galeri Foto (dikirim AI ke tamu)</label>
              <div className="mt-1">
                <ImageUploader
                  value={form.images}
                  onChange={(imgs) => setForm({ ...form, images: imgs })}
                  folder="pelangi/rooms"
                  max={6}
                  tid="room-uploader"
                  primaryUrl={form.photo_url}
                  onSetPrimary={(url) => setForm({ ...form, photo_url: url })}
                />
                <p className="text-[11px] text-[hsl(var(--muted-foreground))] mt-1.5">Klik ikon bintang pada foto untuk jadikan foto utama (ditampilkan di kartu kamar).</p>
              </div>
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">Fasilitas</label>
              <div className="flex gap-2 mt-1">
                <input value={facInput} onChange={(e) => setFacInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addFac())}
                  placeholder="AC, WiFi, ..." className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" data-testid="room-form-fac-input" />
                <button onClick={addFac} type="button" className="text-xs px-3 py-2 rounded-md border border-[hsl(var(--border))]" data-testid="room-form-fac-add">Add</button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(form.facilities || []).map((f, i) => (
                  <button key={i} onClick={() => rmFac(i)} className="text-[11px] px-2 py-0.5 rounded-full bg-[hsl(var(--muted))] hover:bg-red-100">{f} ×</button>
                ))}
              </div>
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">Deskripsi</label>
              <textarea rows={3} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <label className="col-span-2 flex items-center gap-2 text-sm">
              <input type="checkbox" data-testid="room-form-available" checked={form.is_available} onChange={(e) => setForm({ ...form, is_available: e.target.checked })} />
              Tersedia untuk booking
            </label>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="room-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
