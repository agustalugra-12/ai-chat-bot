import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash2, PencilLine, X, Search } from "lucide-react";
import { ImageUploader } from "@/components/ImageUploader";

const LABELS = {
  faq: "FAQ", policy: "Kebijakan", checkin: "Check-in", checkout: "Check-out",
  facilities: "Fasilitas", location: "Lokasi", attractions: "Tempat Wisata",
  parking: "Parkir", breakfast: "Breakfast", laundry: "Laundry",
  motor_rental: "Sewa Motor", airport_pickup: "Airport Pickup", promo: "Promo",
};

const emptyForm = { category: "faq", title: "", content: "", is_active: true, images: [] };

export default function KnowledgeBase() {
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [tab, setTab] = useState("all");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(null);
  const [open, setOpen] = useState(false);

  const load = async () => {
    const [{ data: items }, { data: cats }] = await Promise.all([
      api.get("/knowledge-base"),
      api.get("/knowledge-base/categories"),
    ]);
    setItems(items);
    setCategories(cats.categories);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const openNew = () => { setEditing(null); setForm(emptyForm); setOpen(true); };
  const openEdit = (item) => {
    setEditing(item);
    setForm({ category: item.category, title: item.title, content: item.content, is_active: item.is_active, images: item.images || [] });
    setOpen(true);
  };

  const save = async () => {
    if (!form.title.trim() || !form.content.trim()) { toast.error("Judul & konten wajib diisi"); return; }
    try {
      if (editing) {
        await api.put(`/knowledge-base/${editing.id}`, form);
        toast.success("Item diperbarui");
      } else {
        await api.post("/knowledge-base", form);
        toast.success("Item ditambahkan");
      }
      setOpen(false); load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal menyimpan");
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus item ini?")) return;
    await api.delete(`/knowledge-base/${id}`);
    toast.success("Item dihapus"); load();
  };

  const query = search.trim().toLowerCase();
  const visible = items
    .filter((i) => tab === "all" ? true : i.category === tab)
    .filter((i) => !query || i.title.toLowerCase().includes(query) || i.content.toLowerCase().includes(query));

  return (
    <div>
      <PageHeader
        tid="kb-header"
        title="Knowledge Base"
        subtitle="Sumber jawaban AI. Perubahan di sini langsung dipakai AI tanpa mengubah prompt."
        right={
          <button data-testid="kb-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Tambah Item
          </button>
        }
      />

      <div className="p-8 space-y-6">
        <div className="relative max-w-md">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--muted-foreground))]" />
          <input
            data-testid="kb-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Cari judul atau isi konten..."
            className="w-full pl-9 pr-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white"
          />
        </div>

        <div className="flex gap-2 flex-wrap">
          <FilterChip active={tab === "all"} onClick={() => setTab("all")} label="Semua" tid="kb-tab-all" count={items.length} />
          {categories.map((c) => (
            <FilterChip
              key={c} tid={`kb-tab-${c}`}
              active={tab === c} onClick={() => setTab(c)}
              label={LABELS[c] || c}
              count={items.filter((x) => x.category === c).length}
            />
          ))}
        </div>

        <div className="pelangi-panel overflow-hidden">
          {visible.length === 0 ? (
            <EmptyState tid="kb-empty" title={query ? `Tidak ada hasil untuk "${search}"` : "Belum ada item pada kategori ini"} />
          ) : (
            <div className="divide-y divide-[hsl(var(--border))]">
              {visible.map((item) => (
                <div key={item.id} className="p-5 pelangi-row flex gap-4" data-testid={`kb-item-${item.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge tone="primary">{LABELS[item.category] || item.category}</Badge>
                      {!item.is_active && <Badge tone="muted">nonaktif</Badge>}
                      {item.images?.length > 0 && <Badge tone="success">{item.images.length} foto</Badge>}
                    </div>
                    <div className="font-[Manrope] font-semibold">{item.title}</div>
                    <div className="text-sm text-[hsl(var(--muted-foreground))] mt-1 whitespace-pre-wrap line-clamp-3">{item.content}</div>
                    {item.images?.length > 0 && (
                      <div className="mt-2 flex gap-1.5">
                        {item.images.slice(0, 4).map((img, i) => (
                          <img key={i} src={img.url} alt="" className="w-12 h-12 rounded-md object-cover border border-[hsl(var(--border))]" />
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-start gap-2">
                    <button data-testid={`kb-edit-${item.id}`} onClick={() => openEdit(item)}
                      className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-4 h-4" /></button>
                    <button data-testid={`kb-delete-${item.id}`} onClick={() => remove(item.id)}
                      className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-4 h-4" /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Item" : "Tambah Item"}>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium">Kategori</label>
              <select data-testid="kb-form-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                {categories.map((c) => <option key={c} value={c}>{LABELS[c] || c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium">Judul</label>
              <input data-testid="kb-form-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Konten</label>
              <textarea data-testid="kb-form-content" rows={6} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Foto (untuk dikirim AI ke tamu)</label>
              <div className="mt-1">
                <ImageUploader
                  value={form.images}
                  onChange={(imgs) => setForm({ ...form, images: imgs })}
                  folder="pelangi/kb"
                  max={5}
                  tid="kb-uploader"
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" data-testid="kb-form-active" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              Aktif (dipakai AI)
            </label>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="kb-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function FilterChip({ active, onClick, label, count, tid }) {
  return (
    <button
      data-testid={tid}
      onClick={onClick}
      className={`text-xs px-3 py-1.5 rounded-full border inline-flex items-center gap-2 ${active ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] bg-white hover:bg-[hsl(var(--muted))]"}`}
    >
      {label} <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${active ? "bg-white/20" : "bg-[hsl(var(--muted))]"}`}>{count}</span>
    </button>
  );
}

export function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" data-testid="modal-backdrop">
      <div className="bg-white rounded-xl w-full max-w-lg border border-[hsl(var(--border))] shadow-xl" data-testid="modal">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[hsl(var(--border))]">
          <div className="font-[Manrope] font-semibold">{title}</div>
          <button data-testid="modal-close" onClick={onClose} className="p-1.5 rounded-md hover:bg-[hsl(var(--muted))]"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
