import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Bot, ChevronRight, Cpu } from "lucide-react";
import { Modal } from "@/pages/KnowledgeBase";

const CHANNELS = [
  { code: "simulator", label: "Simulator" },
  { code: "whatsapp", label: "WhatsApp" },
  { code: "telegram", label: "Telegram" },
  { code: "website", label: "Website" },
  { code: "mobile", label: "Mobile App" },
];

const STATUS_TONE = { active: "success", inactive: "muted", maintenance: "warn" };

export default function BotList() {
  const [bots, setBots] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", persona: "", channel_type: "simulator", status: "active" });

  const load = async () => setBots((await api.get("/bots")).data);
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!form.name.trim()) return toast.error("Nama wajib diisi");
    try {
      const { data } = await api.post("/bots", {
        ...form, prompt: `Anda adalah ${form.name}.`, language: "id",
        tool_codes: [], knowledge_categories: [], allowed_service_types: [],
        guardrail_rules: [], allowed_intents: [],
      });
      toast.success(`Bot "${data.name}" dibuat`); setOpen(false); load();
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal"); }
  };

  return (
    <div>
      <PageHeader
        tid="bots-header"
        title="AI Management"
        subtitle="Kelola beberapa AI Persona: Booking, Guest Service, dsb. Masing-masing punya prompt, permission, workflow, dan knowledge sendiri."
        right={
          <button data-testid="bot-add-btn" onClick={() => setOpen(true)}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Plus className="w-4 h-4" /> AI Baru
          </button>
        }
      />
      <div className="p-8">
        {bots.length === 0 ? (
          <EmptyState tid="bots-empty" title="Belum ada AI" hint="Buat AI baru untuk mulai" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {bots.map((b) => (
              <Link to={`/ai/bots/${b.id}`} key={b.id} data-testid={`bot-card-${b.code}`}
                className="pelangi-panel p-5 fade-in-up hover:border-[hsl(var(--primary))] transition-colors">
                <div className="flex items-start gap-3">
                  <div className="w-11 h-11 rounded-lg bg-[hsl(var(--secondary))] flex items-center justify-center text-[hsl(var(--secondary-foreground))]">
                    <Bot className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge tone={STATUS_TONE[b.status] || "muted"}>{b.status}</Badge>
                      <Badge tone="muted">{b.channel_type || "-"}</Badge>
                    </div>
                    <div className="font-[Manrope] font-bold text-lg leading-tight">{b.name}</div>
                    <div className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5 line-clamp-2">{b.description || "—"}</div>
                    <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
                      <div className="px-2 py-0.5 rounded-full bg-stone-100 text-stone-700">{b.tool_codes?.length || 0} tools</div>
                      <div className="px-2 py-0.5 rounded-full bg-stone-100 text-stone-700">{b.knowledge_categories?.length || 0} KB categories</div>
                      <div className="px-2 py-0.5 rounded-full bg-stone-100 text-stone-700">{b.guardrail_rules?.length || 0} guardrails</div>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-[hsl(var(--muted-foreground))] mt-1" />
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {open && (
        <Modal onClose={() => setOpen(false)} title="AI Bot Baru">
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium">Nama</label>
              <input data-testid="bot-new-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" placeholder="Owner AI / Restaurant AI ..." />
            </div>
            <div>
              <label className="text-xs font-medium">Deskripsi</label>
              <input data-testid="bot-new-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium">Persona</label>
              <textarea data-testid="bot-new-persona" rows={2} value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium">Channel</label>
                <select data-testid="bot-new-channel" value={form.channel_type} onChange={(e) => setForm({ ...form, channel_type: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                  {CHANNELS.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium">Status</label>
                <select data-testid="bot-new-status" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
                  <option value="active">active</option>
                  <option value="inactive">inactive</option>
                  <option value="maintenance">maintenance</option>
                </select>
              </div>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="text-sm px-4 py-2 rounded-md border border-[hsl(var(--border))]">Batal</button>
            <button data-testid="bot-new-save" onClick={create} className="text-sm px-4 py-2 rounded-md bg-[hsl(var(--primary))] text-white">Buat</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
