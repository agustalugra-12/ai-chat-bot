import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Save, ArrowLeft, Trash2, Bot, ShieldCheck, Wrench, Waypoints, BookOpenText, Target, FileTerminal } from "lucide-react";

const TABS = [
  { key: "profile", label: "Profile", icon: Bot },
  { key: "prompt", label: "Prompt", icon: FileTerminal },
  { key: "permissions", label: "Tool Permissions", icon: Wrench },
  { key: "workflow", label: "Workflow", icon: Waypoints },
  { key: "knowledge", label: "Knowledge", icon: BookOpenText },
  { key: "intents", label: "Intents", icon: Target },
  { key: "guardrail", label: "Guardrail", icon: ShieldCheck },
];

const SERVICE_TYPES = [
  { code: "extra_bed", label: "Extra Bed" },
  { code: "extra_towel", label: "Extra Towel" },
  { code: "mineral_water", label: "Mineral Water" },
  { code: "cleaning", label: "Cleaning" },
  { code: "laundry", label: "Laundry" },
  { code: "motor_rental", label: "Motor Rental" },
  { code: "airport_pickup", label: "Airport Pickup" },
  { code: "extra_breakfast", label: "Extra Breakfast" },
];

const KB_CATS = [
  "faq", "policy", "checkin", "checkout", "facilities", "location",
  "attractions", "parking", "breakfast", "laundry", "motor_rental",
  "airport_pickup", "promo",
];

export default function BotDetail() {
  const { botId } = useParams();
  const navigate = useNavigate();
  const [bot, setBot] = useState(null);
  const [tools, setTools] = useState([]);
  const [intents, setIntents] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [tab, setTab] = useState("profile");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get(`/bots/${botId}`).then((r) => r.data),
      api.get("/tools").then((r) => r.data),
      api.get("/intents").then((r) => r.data),
      api.get("/workflows").then((r) => r.data),
    ]).then(([b, t, i, w]) => { setBot(b); setTools(t); setIntents(i); setWorkflows(w); });
  }, [botId]);

  const update = (patch) => setBot({ ...bot, ...patch });

  const save = async () => {
    setSaving(true);
    try {
      const { id, code, created_at, updated_at, ...body } = bot;
      const { data } = await api.patch(`/bots/${botId}`, body);
      setBot(data);
      toast.success("Perubahan tersimpan");
    } catch (e) { toast.error(e?.response?.data?.detail || "Gagal simpan"); }
    finally { setSaving(false); }
  };

  const remove = async () => {
    if (!window.confirm(`Hapus AI "${bot.name}"?`)) return;
    await api.delete(`/bots/${botId}`);
    toast.success("Bot dihapus");
    navigate("/ai/bots");
  };

  const toggleInArray = (arr, value) =>
    (arr || []).includes(value) ? (arr || []).filter((v) => v !== value) : [...(arr || []), value];

  if (!bot) return <div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">Memuat…</div>;

  return (
    <div>
      <PageHeader
        tid="bot-detail-header"
        title={
          <div className="flex items-center gap-3">
            <Link to="/ai/bots" className="p-2 rounded-md hover:bg-[hsl(var(--muted))]" data-testid="back-to-bots">
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <span>{bot.name}</span>
            <Badge tone={bot.status === "active" ? "success" : bot.status === "maintenance" ? "warn" : "muted"}>{bot.status}</Badge>
          </div>
        }
        subtitle={bot.description}
        right={
          <div className="flex items-center gap-2">
            <button data-testid="bot-delete" onClick={remove}
              className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-md border border-red-200 text-red-600 hover:bg-red-50">
              <Trash2 className="w-4 h-4" /> Hapus
            </button>
            <button data-testid="bot-save" onClick={save} disabled={saving}
              className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90 disabled:opacity-60">
              <Save className="w-4 h-4" /> {saving ? "Menyimpan..." : "Simpan"}
            </button>
          </div>
        }
      />

      <div className="border-b border-[hsl(var(--border))] bg-white px-8">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              data-testid={`bot-tab-${key}`}
              onClick={() => setTab(key)}
              className={`inline-flex items-center gap-2 text-sm px-4 py-3 border-b-2 transition-colors ${tab === key ? "border-[hsl(var(--primary))] text-[hsl(var(--primary))] font-medium" : "border-transparent text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"}`}
            >
              <Icon className="w-4 h-4" /> {label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-8">
        {tab === "profile" && <ProfileTab bot={bot} onChange={update} />}
        {tab === "prompt" && <PromptTab bot={bot} onChange={update} />}
        {tab === "permissions" && <PermissionsTab bot={bot} onChange={update} tools={tools} services={SERVICE_TYPES} toggleInArray={toggleInArray} />}
        {tab === "workflow" && <WorkflowTab bot={bot} onChange={update} workflows={workflows} />}
        {tab === "knowledge" && <KnowledgeTab bot={bot} onChange={update} categories={KB_CATS} toggleInArray={toggleInArray} />}
        {tab === "intents" && <IntentsTab bot={bot} onChange={update} intents={intents} toggleInArray={toggleInArray} />}
        {tab === "guardrail" && <GuardrailTab bot={bot} onChange={update} />}
      </div>
    </div>
  );
}

function ProfileTab({ bot, onChange }) {
  const F = ({ label, children }) => (
    <div><label className="text-xs font-medium">{label}</label><div className="mt-1">{children}</div></div>
  );
  return (
    <div className="pelangi-panel p-5 max-w-2xl space-y-4">
      <F label="Nama">
        <input data-testid="bot-profile-name" value={bot.name} onChange={(e) => onChange({ name: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
      </F>
      <F label="Deskripsi">
        <input data-testid="bot-profile-desc" value={bot.description || ""} onChange={(e) => onChange({ description: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
      </F>
      <F label="Persona">
        <textarea data-testid="bot-profile-persona" rows={3} value={bot.persona || ""} onChange={(e) => onChange({ persona: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
      </F>
      <div className="grid grid-cols-2 gap-3">
        <F label="Channel">
          <select data-testid="bot-profile-channel" value={bot.channel_type || ""} onChange={(e) => onChange({ channel_type: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
            {["simulator", "whatsapp", "telegram", "website", "mobile"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </F>
        <F label="Bahasa">
          <select data-testid="bot-profile-language" value={bot.language || "id"} onChange={(e) => onChange({ language: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
            <option value="id">Bahasa Indonesia</option>
            <option value="en">English</option>
          </select>
        </F>
      </div>
      <F label="Nomor Channel (placeholder untuk WhatsApp/Telegram)">
        <input data-testid="bot-profile-channelid" placeholder="+628xxxx (belum aktif)" value={bot.channel_id || ""} onChange={(e) => onChange({ channel_id: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
      </F>
      <F label="Status">
        <select data-testid="bot-profile-status" value={bot.status} onChange={(e) => onChange({ status: e.target.value })} className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white">
          <option value="active">active</option>
          <option value="inactive">inactive</option>
          <option value="maintenance">maintenance</option>
        </select>
      </F>
    </div>
  );
}

function PromptTab({ bot, onChange }) {
  return (
    <div className="pelangi-panel p-5">
      <div className="text-sm text-[hsl(var(--muted-foreground))] mb-3">
        System prompt yang digunakan AI ini. Perubahan langsung dipakai saat chat berikutnya.
      </div>
      <textarea
        data-testid="bot-prompt-editor"
        rows={22}
        value={bot.prompt || ""}
        onChange={(e) => onChange({ prompt: e.target.value })}
        className="w-full p-4 rounded-md border border-[hsl(var(--border))] bg-white font-mono text-xs leading-relaxed pelangi-scroll"
      />
    </div>
  );
}

function PermissionsTab({ bot, onChange, tools, services, toggleInArray }) {
  const grouped = tools.reduce((acc, t) => { (acc[t.category] = acc[t.category] || []).push(t); return acc; }, {});
  return (
    <div className="space-y-6">
      <div className="pelangi-panel p-5">
        <div className="font-[Manrope] font-semibold mb-1">Tool Permissions</div>
        <div className="text-xs text-[hsl(var(--muted-foreground))] mb-4">Centang tool yang boleh dipakai AI ini. Tool yang tidak dicentang akan ditolak sistem saat runtime.</div>
        <div className="space-y-4">
          {Object.entries(grouped).map(([cat, arr]) => (
            <div key={cat}>
              <div className="text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-2">{cat}</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {arr.map((t) => {
                  const active = (bot.tool_codes || []).includes(t.code);
                  return (
                    <label key={t.code} data-testid={`perm-tool-${t.code}`}
                      className={`flex items-start gap-2 p-3 rounded-md border cursor-pointer transition-colors ${active ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/5" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}>
                      <input type="checkbox" checked={active} onChange={() => onChange({ tool_codes: toggleInArray(bot.tool_codes, t.code) })}
                        className="mt-0.5" data-testid={`perm-tool-cb-${t.code}`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium">{t.name}</div>
                        <div className="text-xs text-[hsl(var(--muted-foreground))]">{t.description}</div>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="pelangi-panel p-5">
        <div className="font-[Manrope] font-semibold mb-1">Allowed Service Types</div>
        <div className="text-xs text-[hsl(var(--muted-foreground))] mb-4">Jika bot punya tool service request, tipe berikut yang boleh diproses.</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {services.map((s) => {
            const active = (bot.allowed_service_types || []).includes(s.code);
            return (
              <label key={s.code} data-testid={`perm-svc-${s.code}`}
                className={`flex items-center gap-2 p-2.5 rounded-md border cursor-pointer text-sm ${active ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/5" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}>
                <input type="checkbox" checked={active} onChange={() => onChange({ allowed_service_types: toggleInArray(bot.allowed_service_types, s.code) })} />
                {s.label}
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function WorkflowTab({ bot, onChange, workflows }) {
  const wf = workflows.find((w) => w.id === bot.workflow_id);
  return (
    <div className="pelangi-panel p-5 space-y-4">
      <div>
        <label className="text-xs font-medium">Workflow</label>
        <select data-testid="bot-workflow-select" value={bot.workflow_id || ""} onChange={(e) => onChange({ workflow_id: e.target.value || null })}
          className="mt-1 w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white max-w-md">
          <option value="">— tanpa workflow —</option>
          {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>
      {wf && (
        <div>
          <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-3">Langkah-langkah</div>
          <ol className="space-y-2">
            {wf.steps.map((s) => (
              <li key={s.order} className="flex gap-3 items-start" data-testid={`bot-workflow-step-${s.order}`}>
                <div className="w-7 h-7 rounded-full bg-[hsl(var(--primary))] text-white flex items-center justify-center text-xs font-medium shrink-0">{s.order}</div>
                <div>
                  <div className="text-sm font-medium">{s.name}</div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">{s.description}</div>
                </div>
              </li>
            ))}
          </ol>
          <div className="text-[11px] text-[hsl(var(--muted-foreground))] mt-3">
            Kelola workflow global di menu <Link to="/ai/workflows" className="text-[hsl(var(--primary))] hover:underline">Workflows</Link>.
          </div>
        </div>
      )}
    </div>
  );
}

function KnowledgeTab({ bot, onChange, categories, toggleInArray }) {
  return (
    <div className="pelangi-panel p-5">
      <div className="font-[Manrope] font-semibold mb-1">Knowledge Categories</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))] mb-4">Bot hanya akan mengambil konteks dari kategori KB yang dicentang. Kelola konten di menu Knowledge Base.</div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {categories.map((c) => {
          const active = (bot.knowledge_categories || []).includes(c);
          return (
            <label key={c} data-testid={`bot-kb-${c}`}
              className={`flex items-center gap-2 p-2.5 rounded-md border cursor-pointer text-sm capitalize ${active ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/5" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}>
              <input type="checkbox" checked={active} onChange={() => onChange({ knowledge_categories: toggleInArray(bot.knowledge_categories, c) })} />
              {c.replace(/_/g, " ")}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function IntentsTab({ bot, onChange, intents, toggleInArray }) {
  return (
    <div className="pelangi-panel p-5">
      <div className="font-[Manrope] font-semibold mb-1">Allowed Intents</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))] mb-4">Intent yang boleh diproses AI ini. Kelola katalog intent di menu <Link to="/ai/intents" className="text-[hsl(var(--primary))] hover:underline">Intents</Link>.</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {intents.map((i) => {
          const active = (bot.allowed_intents || []).includes(i.code);
          return (
            <label key={i.code} data-testid={`bot-intent-${i.code}`}
              className={`flex items-start gap-2 p-3 rounded-md border cursor-pointer ${active ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/5" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}>
              <input type="checkbox" checked={active} onChange={() => onChange({ allowed_intents: toggleInArray(bot.allowed_intents, i.code) })} className="mt-0.5" />
              <div>
                <div className="text-sm font-medium">{i.code}</div>
                <div className="text-xs text-[hsl(var(--muted-foreground))]">{i.name} — {i.description}</div>
                {i.tool_codes?.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {i.tool_codes.map((tc) => <Badge key={tc} tone="muted">{tc}</Badge>)}
                  </div>
                )}
              </div>
            </label>
          );
        })}
      </div>
    </div>
  );
}

function GuardrailTab({ bot, onChange }) {
  const rules = bot.guardrail_rules || [];
  const setRule = (i, v) => {
    const next = [...rules]; next[i] = v; onChange({ guardrail_rules: next });
  };
  const addRule = () => onChange({ guardrail_rules: [...rules, ""] });
  const removeRule = (i) => onChange({ guardrail_rules: rules.filter((_, k) => k !== i) });

  return (
    <div className="pelangi-panel p-5">
      <div className="font-[Manrope] font-semibold mb-1">Guardrail Rules</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))] mb-4">Aturan yang WAJIB dipatuhi AI. Akan disisipkan ke system prompt.</div>
      <div className="space-y-2">
        {rules.map((r, i) => (
          <div key={i} className="flex gap-2 items-start" data-testid={`bot-guard-row-${i}`}>
            <span className="mt-2.5 text-[hsl(var(--muted-foreground))]">•</span>
            <textarea rows={2} value={r} onChange={(e) => setRule(i, e.target.value)} data-testid={`bot-guard-input-${i}`}
              className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
            <button onClick={() => removeRule(i)} data-testid={`bot-guard-remove-${i}`}
              className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
        <button data-testid="bot-guard-add" onClick={addRule}
          className="text-sm text-[hsl(var(--primary))] hover:underline">+ Tambah aturan</button>
      </div>
    </div>
  );
}
