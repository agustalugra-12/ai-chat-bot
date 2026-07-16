import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine, Trash2 } from "lucide-react";
import { Modal } from "@/pages/KnowledgeBase";

const empty = { name: "", code: "", description: "", tool_codes: [] };

export default function IntentsCatalog() {
  const [intents, setIntents] = useState([]);
  const [tools, setTools] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    const [{ data: i }, { data: t }] = await Promise.all([api.get("/intents"), api.get("/tools")]);
    setIntents(i); setTools(t);
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setOpen(true); };
  const openEdit = (it) => { setEditing(it); setForm({ ...it }); setOpen(true); };

  const save = async () => {
    if (!form.name.trim()) return toast.error("Nama wajib");
    try {
      if (editing) await api.patch(`/intents/${editing.id}`, form);
      else await api.post("/intents", form);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus intent?")) return;
    await api.delete(`/intents/${id}`); load();
  };

  const toggleTool = (code) => {
    const arr = form.tool_codes.includes(code) ? form.tool_codes.filter((c) => c !== code) : [...form.tool_codes, code];
    setForm({ ...form, tool_codes: arr });
  };

  return (
    <div>
      <PageHeader
        tid="intents-header"
        title="Intents"
        subtitle="Katalog intent yang dapat dideteksi AI. Setiap intent bisa dipetakan ke satu atau lebih tool."
        right={
          <button data-testid="intent-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Intent Baru
          </button>
        }
      />
      <div className="p-8">
        {intents.length === 0 ? <EmptyState tid="intents-empty" title="Belum ada intent" /> : (
          <div className="pelangi-panel overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                <tr>
                  <th className="px-5 py-3">Intent</th>
                  <th className="px-5 py-3">Tools terkait</th>
                  <th className="px-5 py-3 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {intents.map((it) => (
                  <tr key={it.id} className="border-b border-[hsl(var(--border))] pelangi-row" data-testid={`intent-row-${it.code}`}>
                    <td className="px-5 py-3">
                      <div className="font-mono text-xs font-semibold">{it.code}</div>
                      <div className="text-sm">{it.name}</div>
                      {it.description && <div className="text-xs text-[hsl(var(--muted-foreground))]">{it.description}</div>}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap gap-1">
                        {(it.tool_codes || []).map((tc) => <Badge key={tc} tone="primary">{tc}</Badge>)}
                        {(it.tool_codes || []).length === 0 && <span className="text-xs text-[hsl(var(--muted-foreground))]">—</span>}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="inline-flex gap-1.5">
                        <button data-testid={`intent-edit-${it.code}`} onClick={() => openEdit(it)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-3.5 h-3.5" /></button>
                        <button data-testid={`intent-delete-${it.code}`} onClick={() => remove(it.id)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Intent" : "Intent Baru"}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium">Nama</label><input data-testid="intent-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
              <div><label className="text-xs font-medium">Code</label><input data-testid="intent-form-code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} placeholder="BOOK_ROOM" className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm font-mono" /></div>
            </div>
            <div><label className="text-xs font-medium">Deskripsi</label><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
            <div>
              <label className="text-xs font-medium">Tools terkait</label>
              <div className="mt-1 max-h-56 overflow-y-auto pelangi-scroll border border-[hsl(var(--border))] rounded-md p-2 space-y-1">
                {tools.map((t) => (
                  <label key={t.code} data-testid={`intent-form-tool-${t.code}`} className="flex items-center gap-2 text-xs p-1.5 rounded hover:bg-[hsl(var(--muted))]">
                    <input type="checkbox" checked={form.tool_codes.includes(t.code)} onChange={() => toggleTool(t.code)} />
                    <span className="font-mono">{t.code}</span>
                    <span className="text-[hsl(var(--muted-foreground))]">— {t.name}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="intent-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
