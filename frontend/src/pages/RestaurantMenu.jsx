import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api, fmtIDR } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine, Trash2 } from "lucide-react";
import { Modal } from "./KnowledgeBase";

const empty = { name: "", category: "food", price: 0, description: "", is_available: true, is_sold_out: false, photo_url: "" };

export default function RestaurantMenu() {
  const [list, setList] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const [cat, setCat] = useState("all");

  const load = async () => setList((await api.get("/menu")).data);
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setOpen(true); };
  const openEdit = (m) => { setEditing(m); setForm({ ...m }); setOpen(true); };

  const save = async () => {
    if (!form.name) return toast.error("Nama menu wajib");
    const p = { ...form, price: Number(form.price) };
    try {
      if (editing) await api.put(`/menu/${editing.id}`, p);
      else await api.post("/menu", p);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus menu?")) return;
    await api.delete(`/menu/${id}`); load();
  };

  const categories = ["all", ...Array.from(new Set(list.map((x) => x.category)))];
  const visible = list.filter((x) => cat === "all" ? true : x.category === cat);

  return (
    <div>
      <PageHeader
        tid="menu-header"
        title="Menu Restoran"
        subtitle="AI akan otomatis merujuk ke daftar menu ini saat tamu bertanya."
        right={
          <button data-testid="menu-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Tambah Menu
          </button>
        }
      />

      <div className="p-8 space-y-5">
        <div className="flex gap-2 flex-wrap">
          {categories.map((c) => (
            <button key={c} data-testid={`menu-cat-${c}`} onClick={() => setCat(c)}
              className={`text-xs px-3 py-1.5 rounded-full border capitalize ${cat === c ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] bg-white hover:bg-[hsl(var(--muted))]"}`}>
              {c === "all" ? "Semua" : c}
            </button>
          ))}
        </div>

        <div className="pelangi-panel overflow-hidden">
          {visible.length === 0 ? <EmptyState tid="menu-empty" title="Belum ada menu" /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                <tr>
                  <th className="px-5 py-3">Menu</th>
                  <th className="px-5 py-3">Kategori</th>
                  <th className="px-5 py-3">Harga</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((m) => (
                  <tr key={m.id} className="border-b border-[hsl(var(--border))] pelangi-row" data-testid={`menu-row-${m.id}`}>
                    <td className="px-5 py-3">
                      <div className="font-medium">{m.name}</div>
                      {m.description && <div className="text-xs text-[hsl(var(--muted-foreground))] line-clamp-1">{m.description}</div>}
                    </td>
                    <td className="px-5 py-3 capitalize text-xs">{m.category}</td>
                    <td className="px-5 py-3 font-[Manrope] font-semibold">{fmtIDR(m.price)}</td>
                    <td className="px-5 py-3">
                      {m.is_sold_out ? <Badge tone="danger">Habis</Badge> : m.is_available ? <Badge tone="success">Tersedia</Badge> : <Badge tone="muted">Non-aktif</Badge>}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="inline-flex gap-1.5">
                        <button data-testid={`menu-edit-${m.id}`} onClick={() => openEdit(m)} className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-3.5 h-3.5" /></button>
                        <button data-testid={`menu-delete-${m.id}`} onClick={() => remove(m.id)} className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-3.5 h-3.5" /></button>
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
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Menu" : "Tambah Menu"}>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="text-xs font-medium">Nama</label>
              <input data-testid="menu-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Kategori</label>
              <select data-testid="menu-form-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                <option value="food">food</option>
                <option value="beverage">beverage</option>
                <option value="snack">snack</option>
                <option value="dessert">dessert</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-medium">Harga (IDR)</label>
              <input data-testid="menu-form-price" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium">Deskripsi</label>
              <textarea rows={2} value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" data-testid="menu-form-available" checked={form.is_available} onChange={(e) => setForm({ ...form, is_available: e.target.checked })} /> Tersedia
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" data-testid="menu-form-soldout" checked={form.is_sold_out} onChange={(e) => setForm({ ...form, is_sold_out: e.target.checked })} /> Habis
            </label>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="menu-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
