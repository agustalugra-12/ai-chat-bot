import { useEffect, useState } from "react";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { History, Save, CheckCircle2 } from "lucide-react";

export default function PromptManagement() {
  const [active, setActive] = useState(null);
  const [content, setContent] = useState("");
  const [versions, setVersions] = useState([]);

  const load = async () => {
    const [{ data: a }, { data: v }] = await Promise.all([api.get("/prompt/active"), api.get("/prompt/versions")]);
    setActive(a); setContent(a.content); setVersions(v);
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!content.trim()) return toast.error("Prompt tidak boleh kosong");
    try {
      await api.post("/prompt", { content });
      toast.success("Prompt baru disimpan & diaktifkan"); load();
    } catch (e) { toast.error("Gagal menyimpan"); }
  };

  const activate = async (id) => {
    await api.post(`/prompt/${id}/activate`);
    toast.success("Prompt diaktifkan"); load();
  };

  return (
    <div>
      <PageHeader
        tid="prompt-header"
        title="Prompt AI"
        subtitle="Kelola sistem prompt AI Guest Assistant. Setiap perubahan membuat versi baru — bisa di-rollback kapan saja."
        right={
          <button data-testid="prompt-save-btn" onClick={save} className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90">
            <Save className="w-4 h-4" /> Simpan Versi Baru
          </button>
        }
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        <div className="pelangi-panel p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="font-[Manrope] font-semibold">System Prompt Aktif</div>
            {active?.version ? <Badge tone="primary">v{active.version}</Badge> : null}
          </div>
          <textarea
            data-testid="prompt-editor"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full h-[520px] p-4 rounded-md border border-[hsl(var(--border))] bg-white font-mono text-xs leading-relaxed pelangi-scroll"
          />
          <div className="mt-3 text-xs text-[hsl(var(--muted-foreground))]">
            Tips: gunakan bagian "GUARDRAIL" agar AI tidak membocorkan data internal.
          </div>
        </div>

        <div className="pelangi-panel">
          <div className="flex items-center gap-2 px-5 py-4 border-b border-[hsl(var(--border))]">
            <History className="w-4 h-4" />
            <div className="font-[Manrope] font-semibold">Riwayat Versi</div>
          </div>
          <div className="divide-y divide-[hsl(var(--border))] max-h-[560px] overflow-y-auto pelangi-scroll">
            {versions.map((v) => (
              <div key={v.id} className="px-5 py-3" data-testid={`prompt-version-${v.version}`}>
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">Versi {v.version}</div>
                  {v.is_active ? <Badge tone="success">aktif</Badge> :
                    <button data-testid={`prompt-activate-${v.version}`} onClick={() => activate(v.id)} className="text-[11px] text-[hsl(var(--primary))] inline-flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> aktifkan</button>}
                </div>
                <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{new Date(v.created_at).toLocaleString("id-ID")}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
