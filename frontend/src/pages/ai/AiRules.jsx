import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Trash2, Save, ShieldCheck, ChevronDown, ChevronRight } from "lucide-react";

function BotRulesCard({ bot, onSaved }) {
  const [rules, setRules] = useState(bot.guardrail_rules || []);
  const [open, setOpen] = useState(true);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(rules) !== JSON.stringify(bot.guardrail_rules || []);

  const setRule = (i, v) => setRules((rs) => rs.map((r, k) => k === i ? v : r));
  const addRule = () => setRules((rs) => [...rs, ""]);
  const removeRule = (i) => setRules((rs) => rs.filter((_, k) => k !== i));

  const save = async () => {
    setSaving(true);
    try {
      const cleaned = rules.map((r) => r.trim()).filter(Boolean);
      const { data } = await api.patch(`/bots/${bot.id}`, { guardrail_rules: cleaned });
      setRules(cleaned);
      toast.success(`Aturan "${bot.name}" tersimpan`);
      onSaved(data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal menyimpan");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="pelangi-panel overflow-hidden" data-testid={`ai-rules-bot-${bot.id}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between p-4 hover:bg-[hsl(var(--muted))]"
        data-testid={`ai-rules-toggle-${bot.id}`}
      >
        <div className="flex items-center gap-2">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <span className="font-[Manrope] font-semibold">{bot.name}</span>
          <Badge tone={bot.status === "active" ? "success" : "muted"}>{bot.status}</Badge>
          <span className="text-xs text-[hsl(var(--muted-foreground))]">{rules.length} aturan</span>
        </div>
        <Link to={`/ai/bots/${bot.id}`} onClick={(e) => e.stopPropagation()}
          className="text-xs text-[hsl(var(--primary))] hover:underline">buka detail bot</Link>
      </button>

      {open && (
        <div className="p-4 pt-0 space-y-2">
          {rules.length === 0 && (
            <div className="text-xs text-[hsl(var(--muted-foreground))] mb-2">Belum ada aturan untuk bot ini.</div>
          )}
          {rules.map((r, i) => (
            <div key={i} className="flex gap-2 items-start" data-testid={`ai-rules-row-${bot.id}-${i}`}>
              <span className="mt-2.5 text-[hsl(var(--muted-foreground))]">•</span>
              <textarea rows={2} value={r} onChange={(e) => setRule(i, e.target.value)}
                data-testid={`ai-rules-input-${bot.id}-${i}`}
                className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm" />
              <button onClick={() => removeRule(i)} data-testid={`ai-rules-remove-${bot.id}-${i}`}
                className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          <div className="flex items-center justify-between pt-1">
            <button data-testid={`ai-rules-add-${bot.id}`} onClick={addRule}
              className="text-sm text-[hsl(var(--primary))] hover:underline">+ Tambah aturan</button>
            <button
              data-testid={`ai-rules-save-${bot.id}`}
              onClick={save}
              disabled={!dirty || saving}
              className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-md bg-[hsl(var(--primary))] text-white disabled:opacity-40 hover:opacity-90"
            >
              <Save className="w-3.5 h-3.5" /> {saving ? "Menyimpan..." : "Simpan"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AiRules() {
  const [bots, setBots] = useState(null);

  const load = () => api.get("/bots").then((r) => setBots(r.data)).catch(() => toast.error("Gagal memuat daftar AI"));
  useEffect(() => { load(); }, []);

  const onSaved = (updated) => setBots((bs) => bs.map((b) => b.id === updated.id ? updated : b));

  if (!bots) return <div className="p-8 text-sm text-[hsl(var(--muted-foreground))]">Memuat…</div>;

  return (
    <div>
      <PageHeader
        tid="ai-rules-header"
        title="AI Rules"
        subtitle="Guardrail rules per AI — aturan yang WAJIB dipatuhi, disisipkan langsung ke system prompt. Untuk kebijakan bisnis (DP, pembatalan, dst) lihat Business Rules di PMS, bukan di sini."
      />
      <div className="p-8 space-y-4">
        {bots.length === 0 ? (
          <div className="pelangi-panel p-6 text-center text-sm text-[hsl(var(--muted-foreground))]">
            <ShieldCheck className="w-5 h-5 mx-auto mb-2 opacity-50" />
            Belum ada AI. Buat AI dulu di menu <Link to="/ai/bots" className="text-[hsl(var(--primary))] hover:underline">AI List</Link>.
          </div>
        ) : (
          bots.map((b) => <BotRulesCard key={b.id} bot={b} onSaved={onSaved} />)
        )}
      </div>
    </div>
  );
}
