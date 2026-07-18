import { useEffect, useState } from "react";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { RefreshCw } from "lucide-react";

const ACTION_LABEL = {
  pms_integration_update: "Ubah Konfigurasi PMS",
  pms_capability_toggle: "Ubah Capability PMS",
  pms_webhook_token_regenerate: "Generate Ulang Webhook Token",
  pms_sync_rule: "Sync Business Rules",
  pms_sync_hotel_profile: "Sync Hotel Profile",
  pms_sync_faq: "Sync FAQ",
  pms_sync_prompt: "Sync Prompt",
  waha_connect: "Sambungkan WAHA",
  waha_disconnect: "Putuskan WAHA",
  conversation_handover: "Handover ke Admin",
  conversation_resume_ai: "Aktifkan AI Lagi",
  conversation_manual_reply: "Balasan Manual Staf",
  conversation_close: "Tutup Percakapan",
};

export default function AuditLog() {
  const [logs, setLogs] = useState([]);
  const [actions, setActions] = useState([]);
  const [action, setAction] = useState("");

  const load = async () => {
    const q = action ? `?action=${encodeURIComponent(action)}` : "";
    const { data } = await api.get(`/audit-log${q}`);
    setLogs(data);
  };
  const loadActions = async () => {
    const { data } = await api.get("/audit-log/actions");
    setActions(data);
  };

  useEffect(() => { load(); loadActions(); }, []); // eslint-disable-line
  useEffect(() => { load(); }, [action]); // eslint-disable-line

  return (
    <div>
      <PageHeader
        tid="audit-log-header"
        title="Audit Log"
        subtitle="Riwayat aksi admin sensitif — konfigurasi Integrasi PMS, koneksi WAHA, Human Handover."
        right={
          <button onClick={load} className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-md border border-[hsl(var(--border))] hover:bg-stone-50">
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        }
      />
      <div className="p-8 space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => setAction("")}
            className={`text-xs px-3 py-1.5 rounded-full border ${!action ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}
          >
            Semua
          </button>
          {actions.map((a) => (
            <button
              key={a}
              data-testid={`audit-filter-${a}`}
              onClick={() => setAction(a)}
              className={`text-xs px-3 py-1.5 rounded-full border ${action === a ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}
            >
              {ACTION_LABEL[a] || a}
            </button>
          ))}
        </div>

        <div className="pelangi-panel overflow-x-auto">
          <table className="w-full text-sm" data-testid="audit-log-table">
            <thead>
              <tr className="text-left text-xs text-[hsl(var(--muted-foreground))] border-b border-[hsl(var(--border))]">
                <th className="p-3">Waktu</th>
                <th className="p-3">User</th>
                <th className="p-3">Aksi</th>
                <th className="p-3">Detail</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 && (
                <tr><td colSpan={4} className="p-6 text-center text-[hsl(var(--muted-foreground))]">Belum ada aksi tercatat.</td></tr>
              )}
              {logs.map((l) => (
                <tr key={l.id} className="border-b border-[hsl(var(--border))]/60">
                  <td className="p-3 whitespace-nowrap text-xs">{new Date(l.at).toLocaleString("id-ID")}</td>
                  <td className="p-3 text-xs">{l.user_email || l.user_id || "-"}</td>
                  <td className="p-3"><Badge tone="primary">{ACTION_LABEL[l.action] || l.action}</Badge></td>
                  <td className="p-3 text-xs text-[hsl(var(--muted-foreground))] max-w-[420px] truncate" title={l.detail}>{l.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
