import { useEffect, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, MessagesSquare } from "lucide-react";
import { ChatMessageContent } from "@/components/ChatMessageContent";

export default function Conversations() {
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("all");

  const load = async () => {
    const { data } = await api.get("/conversations");
    setList(data);
    if (data.length && !selected) setSelected(data[0]);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const doHandover = async (id) => {
    await api.patch(`/conversations/${id}/handover`);
    toast.success("Dialihkan ke admin");
    load();
  };

  const doClose = async (id) => {
    await api.patch(`/conversations/${id}/close`);
    toast.success("Percakapan ditutup");
    load();
  };

  const visible = list.filter((c) => filter === "all" ? true : c.status === filter);

  return (
    <div>
      <PageHeader
        tid="conversations-header"
        title="Percakapan"
        subtitle="Semua sesi chat AI dengan tamu — dari WhatsApp maupun Chat Simulator."
      />

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] h-[calc(100vh-146px)]">
        {/* List */}
        <div className="border-r border-[hsl(var(--border))] bg-white flex flex-col">
          <div className="p-3 border-b border-[hsl(var(--border))] flex gap-2 flex-wrap">
            {["all", "active", "waiting_admin", "closed"].map((f) => (
              <button
                key={f}
                data-testid={`conv-filter-${f}`}
                onClick={() => setFilter(f)}
                className={`text-xs px-3 py-1.5 rounded-full border ${filter === f ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}
              >
                {f === "all" ? "Semua" : f.replace("_", " ")}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto pelangi-scroll divide-y divide-[hsl(var(--border))]">
            {visible.length === 0 && <EmptyState tid="conv-empty" title="Belum ada percakapan" hint="Kirim pesan dari Chat Simulator untuk mulai" />}
            {visible.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c)}
                data-testid={`conv-item-${c.id}`}
                className={`w-full text-left p-4 flex gap-3 pelangi-row ${selected?.id === c.id ? "bg-[hsl(var(--muted))]" : ""}`}
              >
                <div className="w-10 h-10 rounded-full bg-[hsl(var(--secondary))] flex items-center justify-center text-sm font-semibold text-[hsl(var(--secondary-foreground))]">
                  {(c.guest_name || "T").slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium truncate">{c.guest_name || "Tamu Anonim"}</div>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))]">{new Date(c.updated_at).toLocaleDateString("id-ID")}</span>
                  </div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))] truncate">{c.last_message}</div>
                  <div className="mt-1 flex gap-1.5">
                    <Badge tone={c.status === "waiting_admin" ? "danger" : c.resolution === "ai_resolved" ? "success" : "muted"}>
                      {c.status === "waiting_admin" ? "Perlu Admin" : c.resolution === "ai_resolved" ? "AI Selesai" : c.status}
                    </Badge>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))]">{c.message_count} pesan</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Detail */}
        <div className="bg-[hsl(var(--background))] flex flex-col">
          {!selected ? (
            <div className="flex-1 flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">Pilih percakapan</div>
          ) : (
            <>
              <div className="bg-white border-b border-[hsl(var(--border))] px-6 py-4 flex items-center justify-between">
                <div>
                  <div className="font-[Manrope] font-semibold text-lg">{selected.guest_name || "Tamu Anonim"}</div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">{selected.whatsapp} · {selected.channel} · session {selected.session_id.slice(0, 8)}…</div>
                </div>
                <div className="flex items-center gap-2">
                  {selected.status !== "waiting_admin" && (
                    <button data-testid="btn-handover" onClick={() => doHandover(selected.id)}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-[hsl(var(--accent))] text-[hsl(var(--accent))] hover:bg-[hsl(var(--accent))] hover:text-white">
                      <AlertTriangle className="w-3 h-3" /> Handover ke Admin
                    </button>
                  )}
                  {selected.status !== "closed" && (
                    <button data-testid="btn-close-conv" onClick={() => doClose(selected.id)}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]">
                      <CheckCircle2 className="w-3 h-3" /> Tutup
                    </button>
                  )}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto pelangi-scroll p-6 chat-bg flex flex-col gap-2">
                {selected.messages.map((m, i) => (
                  <div key={i} className={m.role === "user" ? "chat-bubble-guest" : "chat-bubble-ai"}>
                    <ChatMessageContent content={m.content} />
                    <div className="text-[10px] mt-1 text-stone-500 text-right">
                      {new Date(m.timestamp).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
                      {m.intent && <> · <span className="text-emerald-700">{m.intent}</span></>}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
