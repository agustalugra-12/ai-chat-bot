import { useEffect, useState } from "react";
import { PageHeader, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, PencilLine, Trash2, Save, X, Waypoints } from "lucide-react";
import { Modal } from "@/pages/KnowledgeBase";

const empty = { name: "", description: "", steps: [] };

export default function WorkflowsCatalog() {
  const [list, setList] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);

  const load = async () => setList((await api.get("/workflows")).data);
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm({ ...empty }); setOpen(true); };
  const openEdit = (w) => { setEditing(w); setForm({ name: w.name, description: w.description || "", steps: [...(w.steps || [])] }); setOpen(true); };

  const save = async () => {
    if (!form.name.trim()) return toast.error("Nama wajib");
    const payload = { ...form, steps: form.steps.map((s, i) => ({ order: i + 1, name: s.name, description: s.description || "" })) };
    try {
      if (editing) await api.patch(`/workflows/${editing.id}`, payload);
      else await api.post("/workflows", payload);
      toast.success("Tersimpan"); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus workflow?")) return;
    await api.delete(`/workflows/${id}`); load();
  };

  const addStep = () => setForm({ ...form, steps: [...form.steps, { order: form.steps.length + 1, name: "", description: "" }] });
  const setStep = (i, key, v) => {
    const next = [...form.steps]; next[i] = { ...next[i], [key]: v }; setForm({ ...form, steps: next });
  };
  const removeStep = (i) => setForm({ ...form, steps: form.steps.filter((_, k) => k !== i) });

  return (
    <div>
      <PageHeader
        tid="workflows-header"
        title="Workflows"
        subtitle="Struktur langkah kerja yang dapat diassign ke setiap AI (Booking, Guest Service, dsb)."
        right={
          <button data-testid="workflow-add-btn" onClick={openNew}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> Workflow Baru
          </button>
        }
      />
      <div className="p-8">
        {list.length === 0 ? <EmptyState tid="workflows-empty" title="Belum ada workflow" /> : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {list.map((w) => (
              <div key={w.id} className="pelangi-panel p-5" data-testid={`workflow-card-${w.code}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-3">
                    <div className="w-9 h-9 rounded-lg bg-[hsl(var(--secondary))] text-[hsl(var(--secondary-foreground))] flex items-center justify-center"><Waypoints className="w-4 h-4" /></div>
                    <div>
                      <div className="font-[Manrope] font-semibold">{w.name}</div>
                      <div className="text-xs text-[hsl(var(--muted-foreground))]">{w.description || "—"}</div>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button data-testid={`workflow-edit-${w.code}`} onClick={() => openEdit(w)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"><PencilLine className="w-3.5 h-3.5" /></button>
                    <button data-testid={`workflow-delete-${w.code}`} onClick={() => remove(w.id)} className="p-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                </div>
                <ol className="mt-4 space-y-2">
                  {(w.steps || []).map((s) => (
                    <li key={s.order} className="flex gap-3 text-sm">
                      <div className="w-6 h-6 rounded-full bg-[hsl(var(--primary))] text-white flex items-center justify-center text-xs font-medium shrink-0">{s.order}</div>
                      <div className="flex-1">
                        <div className="font-medium">{s.name}</div>
                        {s.description && <div className="text-xs text-[hsl(var(--muted-foreground))]">{s.description}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        )}
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title={editing ? "Edit Workflow" : "Workflow Baru"}>
          <div className="space-y-3">
            <div><label className="text-xs font-medium">Nama</label><input data-testid="workflow-form-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
            <div><label className="text-xs font-medium">Deskripsi</label><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" /></div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium">Langkah</label>
                <button data-testid="workflow-step-add" onClick={addStep} className="text-xs text-[hsl(var(--primary))]">+ tambah</button>
              </div>
              <div className="mt-2 space-y-2 max-h-72 overflow-y-auto pelangi-scroll">
                {form.steps.map((s, i) => (
                  <div key={i} className="flex gap-2 items-start" data-testid={`workflow-step-${i}`}>
                    <div className="w-6 h-6 mt-2 rounded-full bg-[hsl(var(--primary))] text-white flex items-center justify-center text-xs shrink-0">{i + 1}</div>
                    <div className="flex-1 grid grid-cols-1 gap-1.5">
                      <input value={s.name} onChange={(e) => setStep(i, "name", e.target.value)} placeholder="Nama step" data-testid={`workflow-step-name-${i}`} className="px-3 py-1.5 rounded-md border border-[hsl(var(--border))] text-sm" />
                      <input value={s.description || ""} onChange={(e) => setStep(i, "description", e.target.value)} placeholder="Deskripsi" data-testid={`workflow-step-desc-${i}`} className="px-3 py-1.5 rounded-md border border-[hsl(var(--border))] text-xs" />
                    </div>
                    <button onClick={() => removeStep(i)} className="p-2 rounded-md text-red-600 hover:bg-red-50"><X className="w-3.5 h-3.5" /></button>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="workflow-form-save" onClick={save} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white inline-flex items-center gap-2"><Save className="w-4 h-4" /> Simpan</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
