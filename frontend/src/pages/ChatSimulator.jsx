import { useEffect, useRef, useState } from "react";
import { PageHeader, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { Send, RotateCcw, User2, BotMessageSquare } from "lucide-react";
import { toast } from "sonner";

export default function ChatSimulator() {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [guestName, setGuestName] = useState("Budi");
  const [whatsapp, setWhatsapp] = useState("+628123456789");
  const [lastMeta, setLastMeta] = useState(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    setMessages((m) => [...m, { role: "user", content: q, timestamp: new Date().toISOString() }]);
    setBusy(true);
    try {
      const { data } = await api.post("/chat/message", {
        session_id: sessionId, message: q, guest_name: guestName, whatsapp,
      });
      setSessionId(data.session_id);
      setMessages((m) => [...m, { role: "assistant", content: data.reply, timestamp: new Date().toISOString(), intent: data.tool_used }]);
      setLastMeta({ ms: data.response_time_ms, tool: data.tool_used, result: data.tool_result });
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal mengirim pesan");
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setSessionId(null); setMessages([]); setLastMeta(null);
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const suggestions = [
    "Jam berapa check-in?",
    "Ada kamar tersedia tanggal 20-22 Agustus untuk 2 orang?",
    "Saya mau booking Deluxe untuk 21-23 Agustus, 2 tamu.",
    "Pesan extra bed dong",
    "Nasi goreng masih ada?",
  ];

  return (
    <div>
      <PageHeader
        tid="simulator-header"
        title="Chat Simulator"
        subtitle="Uji AI Guest Assistant seolah-olah Anda tamu yang chat via WhatsApp. Semua percakapan tersimpan di modul Conversations."
        right={
          <button
            data-testid="simulator-reset"
            onClick={reset}
            className="inline-flex items-center gap-2 text-sm px-3.5 py-2 rounded-md border border-[hsl(var(--border))] bg-white hover:bg-[hsl(var(--muted))]"
          >
            <RotateCcw className="w-4 h-4" /> Reset sesi
          </button>
        }
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        {/* Chat panel */}
        <div className="pelangi-panel overflow-hidden flex flex-col h-[calc(100vh-220px)]">
          <div className="flex items-center gap-3 px-5 py-3 border-b border-[hsl(var(--border))] bg-[hsl(var(--primary))] text-white">
            <div className="w-9 h-9 rounded-full bg-white/15 border border-white/20 flex items-center justify-center">
              <BotMessageSquare className="w-4 h-4" />
            </div>
            <div className="flex-1">
              <div className="font-[Manrope] font-semibold text-sm">Pelangi AI · Guest Assistant</div>
              <div className="text-[11px] text-white/70">Online · biasanya balas dalam &lt;3 detik</div>
            </div>
            {lastMeta && (
              <div className="text-[11px] text-white/80">
                {lastMeta.tool && <span className="mr-2 px-2 py-0.5 rounded-full bg-white/15">{lastMeta.tool}</span>}
                {lastMeta.ms}ms
              </div>
            )}
          </div>

          <div ref={scrollRef} className="chat-bg flex-1 overflow-y-auto pelangi-scroll p-6 flex flex-col gap-2" data-testid="chat-viewport">
            {messages.length === 0 && (
              <div className="text-center text-xs text-stone-500 my-auto">
                Mulai percakapan — Pelangi AI siap membantu tamu Anda.
              </div>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                data-testid={`chat-msg-${m.role}-${i}`}
                className={`fade-in-up ${m.role === "user" ? "chat-bubble-guest" : "chat-bubble-ai"}`}
              >
                <div className="whitespace-pre-wrap text-[14px] leading-relaxed">{m.content}</div>
                <div className="text-[10px] mt-1 text-stone-500 text-right">
                  {new Date(m.timestamp).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
                  {m.intent && <> · <span className="text-emerald-700">{m.intent}</span></>}
                </div>
              </div>
            ))}
            {busy && (
              <div className="chat-bubble-ai fade-in-up">
                <div className="flex gap-1 py-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-stone-400 animate-bounce" />
                  <span className="w-1.5 h-1.5 rounded-full bg-stone-400 animate-bounce [animation-delay:120ms]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-stone-400 animate-bounce [animation-delay:240ms]" />
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-[hsl(var(--border))] bg-white p-3 flex items-end gap-2">
            <textarea
              data-testid="chat-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
              rows={1}
              placeholder="Ketik pesan tamu…"
              className="flex-1 resize-none px-3 py-2.5 rounded-md border border-[hsl(var(--border))] bg-white focus:outline-none focus:ring-2 focus:ring-[hsl(var(--primary))] focus:border-transparent text-sm"
            />
            <button
              data-testid="chat-send"
              onClick={send}
              disabled={busy}
              className="p-2.5 rounded-md bg-[hsl(var(--primary))] text-white hover:opacity-90 disabled:opacity-50"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Side panel */}
        <div className="space-y-4">
          <div className="pelangi-panel p-5">
            <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-2">Simulasi tamu</div>
            <label className="block text-xs font-medium mt-2 mb-1">Nama</label>
            <input
              data-testid="sim-guest-name"
              value={guestName} onChange={(e) => setGuestName(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] bg-white text-sm"
            />
            <label className="block text-xs font-medium mt-3 mb-1">WhatsApp</label>
            <input
              data-testid="sim-guest-whatsapp"
              value={whatsapp} onChange={(e) => setWhatsapp(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-[hsl(var(--border))] bg-white text-sm"
            />
            {sessionId && <div className="mt-3 text-[11px] text-[hsl(var(--muted-foreground))]">Session: {sessionId.slice(0, 8)}…</div>}
          </div>

          <div className="pelangi-panel p-5">
            <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-3">Coba pesan cepat</div>
            <div className="space-y-2">
              {suggestions.map((s, i) => (
                <button
                  key={i}
                  data-testid={`sim-suggest-${i}`}
                  onClick={() => setText(s)}
                  className="w-full text-left text-sm px-3 py-2 rounded-md border border-[hsl(var(--border))] bg-white hover:border-[hsl(var(--primary))] hover:bg-[hsl(var(--muted))] transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {lastMeta?.result && (
            <div className="pelangi-panel p-5">
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))] mb-2">Tool result</div>
              <pre className="text-[11px] whitespace-pre-wrap break-words bg-[hsl(var(--muted))] p-3 rounded-md max-h-60 overflow-y-auto">
                {JSON.stringify(lastMeta.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
