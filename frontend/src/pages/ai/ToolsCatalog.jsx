import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine, Trash2 } from "lucide-react";
import { Modal } from "@/pages/KnowledgeBase";

const empty = { name: "", code: "", description: "", endpoint: "", category: "general", status: "active" };
const STATUS_TONE = { active: "success", inactive: "muted", maintenance: "warn" };

export default function ToolsCatalog() {
  const [tools, setTools] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);

  const load = async () => setTools((await api.get("/tools")).data);
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setOpen(true); };
  const openEdit = (t) => { setEditing(t); setForm({ ...t }); setOpen(true); };

  const save = async () => {
    if (!form.name.trim()) return toast.error("Nama wajib");
    try {
      if (editing) await api.patch(`/tools/${editing.id}`, form);
      else await api.post("/tools", form);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus tool ini?")) return;
    await api.delete(`/tools/${id}`); load();
  };

  const grouped = tools.reduce((a, t) => { (a[t.category] = a[t.category] || []).push(t); return a; }, {});

  return (
    <div>
      <PageHeader
        tid="tools-header"
        title="Tools Catalog"
        subtitle="Daftar semua tool yang dapat dipanggil AI. Setiap AI memilih tool yang boleh dipakai di halaman AI Management."
        right={
          <button data-testid="tool-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Tool Baru
          </button>
        }
      />
      <div className="p-8">
        {tools.length === 0 ? <EmptyState tid="tools-empty" title="Belum ada tool" /> : (
          <div className="space-y-6">
            {Object.entries(grouped).map(([cat, arr]) => (
              <div key={cat}>
                <div className="text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-2">{cat}</div>
                <div className="pelangi-panel overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="text-left text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                      <tr>
                        <th className="px-5 py-3">Tool</th>
                        <th className="px-5 py-3">Endpoint</th>
                        <th className="px-5 py-3">Status</th>
                        <th className="px-5 py-3 text-right">Aksi</th>
                      </tr>
                    </thead>
                    <tbody>
                      {arr.map((t) => (
                        <tr key={t.id} className="border-b border-[hsl(var(--border))] pelangi-row" data-testid={`tool-row-${t.code}`}>
                          <td className="px-5 py-3">
                            <div className="font-medium">{t.name}</div>
                            <div className="text-xs text-[hsl(var(--muted-foreground))]">{t.code}</div>
                            {t.description && <div className="text-xs mt-0.5">{t.description}</div>}
                          </td>
                          <td className="px-5 py-3 text-xs font-mono">{t.endpoint || "-"}</td>
                          <td className="px-5 py-3"><Badge tone={STATUS_TONE[t.status] || "muted"}>{t.status}</Badge></td>
                          <td className="px-5 py-3 text-right">
                            <div className="inline-flex gap-1.5">
                              <button data-testid={`tool-edit-${t.code}`} onClick={() => openEdit(t)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-3.5 h-3.5" /></button>
                              <button data-testid={`tool-delete-${t.code}`} onClick={() => remove(t.id)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-3.5 h-3.5" /></button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Tool" : "Tool Baru"}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium">Nama</label><input data-testid="tool-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
              <div><label className="text-xs font-medium">Code</label><input data-testid="tool-form-code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="auto dari nama" className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
            </div>
            <div><label className="text-xs font-medium">Deskripsi</label><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
            <div><label className="text-xs font-medium">Endpoint</label><input value={form.endpoint} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm font-mono" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium">Kategori</label><input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
              <div><label className="text-xs font-medium">Status</label>
                <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                  <option value="active">active</option><option value="inactive">inactive</option><option value="maintenance">maintenance</option>
                </select>
              </div>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="tool-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
