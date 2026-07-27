import { useEffect, useRef, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, MessagesSquare, Send, Bot, Loader2, UserRound, ChevronUp, ChevronDown } from "lucide-react";
import { ChatMessageContent } from "@/components/ChatMessageContent";

// Nama teknis di database -> label yang dimengerti orang awam. Channel "whatsapp" (WAHA)
// dan "whatsapp_cloud" (Meta Cloud API) sama-sama tampil sebagai "WhatsApp" - beda transport
// itu detail teknis di belakang layar, bukan sesuatu yang perlu dipikirkan pemilik/staf.
const CHANNEL_LABEL = {
  whatsapp: "WhatsApp",
  whatsapp_cloud: "WhatsApp",
  simulator: "Simulator (uji coba)",
};

// Kode tool internal -> label singkat yang gampang dibaca sekilas di tiap bubble chat.
// Tool yang tidak ada di daftar ini sengaja TIDAK ditampilkan (drop ke null) daripada
// menampilkan nama kode mentah yang tidak berarti apa-apa buat orang awam.
const INTENT_LABEL = {
  check_availability: "cek kamar",
  create_booking: "booking",
  lookup_booking: "cek booking",
  cancel_booking: "pembatalan",
  request_handover: "minta bantuan admin",
  restaurant_order: "pesan makanan",
  laundry_request: "laundry",
  housekeeping_request: "housekeeping",
  maintenance_request: "keluhan/kerusakan",
  complaint_ticket: "komplain",
  room_service: "room service",
  airport_pickup: "antar-jemput",
  motor_rental: "sewa motor",
  create_service_request: "permintaan layanan",
  create_maintenance_ticket: "laporan kerusakan",
};

const PESAN_AWAL = 10; // percakapan lama bisa ratusan pesan - fokus 10 terakhir dulu,
// sisanya dimuat lewat tombol "Muat pesan lebih lama" di atas, bukan discroll manual jauh.
const PESAN_TAMBAHAN = 20;

const FILTERS = [
  { key: "all", label: "Semua" },
  { key: "waiting_admin", label: "🔴 Butuh Kamu" },
  { key: "active", label: "AI Aktif" },
  { key: "closed", label: "Selesai" },
];

function waktuSingkat(iso) {
  const d = new Date(iso);
  const now = new Date();
  const jam = d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return `Hari ini, ${jam}`;
  const kemarin = new Date(now);
  kemarin.setDate(now.getDate() - 1);
  if (d.toDateString() === kemarin.toDateString()) return `Kemarin, ${jam}`;
  return `${d.toLocaleDateString("id-ID", { day: "numeric", month: "short" })}, ${jam}`;
}

export default function Conversations() {
  const [list, setList] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("all");
  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PESAN_AWAL);
  const [atBottom, setAtBottom] = useState(true);
  const selectedRef = useRef(null);
  const scrollRef = useRef(null);
  const isAtBottomRef = useRef(true);
  selectedRef.current = selected;

  const scrollToBottom = (behavior = "auto") => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior });
  };
  const scrollToTop = () => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: 0, behavior: "smooth" });
  };
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const bottomNow = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    isAtBottomRef.current = bottomNow;
    setAtBottom(bottomNow);
  };

  // Ganti percakapan yang dibuka -> reset ke 10 pesan terakhir & langsung fokus ke bawah
  // (pesan terbaru), bukan mulai dari atas riwayat yang bisa ratusan pesan panjangnya.
  useEffect(() => {
    setVisibleCount(PESAN_AWAL);
    isAtBottomRef.current = true;
    setAtBottom(true);
    requestAnimationFrame(() => scrollToBottom("auto"));
  }, [selected?.id]); // eslint-disable-line

  // Auto-refresh (polling) menambah pesan baru - ikut ke bawah HANYA kalau operator
  // memang sedang di posisi paling bawah (supaya tidak menyentak orang yang sedang
  // baca riwayat lama ke atas).
  useEffect(() => {
    if (isAtBottomRef.current) requestAnimationFrame(() => scrollToBottom("auto"));
  }, [selected?.messages?.length]); // eslint-disable-line

  const load = async (keepSelectedId) => {
    const { data } = await api.get("/conversations");
    setList(data);
    if (keepSelectedId) {
      const fresh = data.find((c) => c.id === keepSelectedId);
      if (fresh) setSelected(fresh);
    } else if (data.length && !selectedRef.current) {
      setSelected(data[0]);
    }
  };
  useEffect(() => {
    load();
    // Auto-refresh tiap 20 detik supaya percakapan baru/balasan tamu terbaru langsung
    // terlihat tanpa perlu refresh manual - dibaca lewat ref (bukan closure langsung)
    // supaya tidak selalu merujuk ke `selected` dari render pertama/null.
    const timer = setInterval(() => load(selectedRef.current?.id), 20000);
    return () => clearInterval(timer);
  }, []); // eslint-disable-line

  const doHandover = async (id) => {
    await api.patch(`/conversations/${id}/handover`);
    toast.success("Kamu ambil alih — AI berhenti membalas otomatis di percakapan ini");
    load(id);
  };

  const doResume = async (id) => {
    await api.patch(`/conversations/${id}/resume`);
    toast.success("AI aktif lagi — akan membalas otomatis pesan tamu berikutnya");
    load(id);
  };

  const doClose = async (id) => {
    await api.patch(`/conversations/${id}/close`);
    toast.success("Percakapan ditutup");
    load(id);
  };

  const sendReply = async () => {
    const text = replyText.trim();
    if (!text || !selected) return;
    setSending(true);
    try {
      const { data } = await api.post(`/conversations/${selected.id}/reply`, { message: text });
      setReplyText("");
      toast.success(data.sent_to_whatsapp ? "Terkirim ke WhatsApp tamu" : "Tersimpan di percakapan");
      isAtBottomRef.current = true;
      load(selected.id);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Gagal mengirim pesan, coba lagi");
    } finally {
      setSending(false);
    }
  };

  const visible = list.filter((c) => filter === "all" ? true : c.status === filter);
  const jumlahButuhKamu = list.filter((c) => c.status === "waiting_admin").length;

  return (
    <div>
      <PageHeader
        tid="conversations-header"
        title="Percakapan"
        subtitle={
          jumlahButuhKamu > 0
            ? `${jumlahButuhKamu} percakapan sedang menunggu balasanmu.`
            : "Semua obrolan AI dengan tamu lewat WhatsApp."
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] h-[calc(100vh-146px)]">
        {/* List */}
        <div className="border-r border-[hsl(var(--border))] bg-white flex flex-col">
          <div className="p-3 border-b border-[hsl(var(--border))] flex gap-2 flex-wrap">
            {FILTERS.map((f) => {
              const count = f.key === "waiting_admin" ? jumlahButuhKamu : null;
              return (
                <button
                  key={f.key}
                  data-testid={`conv-filter-${f.key}`}
                  onClick={() => setFilter(f.key)}
                  className={`text-xs px-3 py-1.5 rounded-full border font-medium ${filter === f.key ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-white" : "border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]"}`}
                >
                  {f.label}
                  {!!count && filter !== f.key && (
                    <span className="ml-1.5 inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-red-600 text-white text-[10px]">{count}</span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="flex-1 overflow-y-auto pelangi-scroll divide-y divide-[hsl(var(--border))]">
            {visible.length === 0 && (
              <EmptyState
                tid="conv-empty"
                title="Belum ada percakapan di sini"
                hint="Percakapan tamu lewat WhatsApp akan otomatis muncul begitu ada yang chat."
              />
            )}
            {visible.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c)}
                data-testid={`conv-item-${c.id}`}
                className={`w-full text-left p-4 flex gap-3 pelangi-row ${selected?.id === c.id ? "bg-[hsl(var(--muted))]" : ""}`}
              >
                <div className="w-10 h-10 rounded-full bg-[hsl(var(--secondary))] flex items-center justify-center text-sm font-semibold text-[hsl(var(--secondary-foreground))] shrink-0">
                  {(c.guest_name || "T").slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium truncate">{c.guest_name || "Tamu Anonim"}</div>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))] shrink-0">{waktuSingkat(c.updated_at)}</span>
                  </div>
                  {/* Nomor WA ditampilkan di daftar (bukan cuma di detail) - supaya 2 tamu
                      dengan nama sama tapi nomor beda tidak tertukar sekilas pandang (2026-07-27,
                      ditemukan lewat laporan nyata staf salah buka percakapan). */}
                  {c.whatsapp && <div className="text-[11px] text-[hsl(var(--muted-foreground))]">📱 {c.whatsapp}</div>}
                  <div className="text-xs text-[hsl(var(--muted-foreground))] truncate">{c.last_message}</div>
                  <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                    <Badge tone={c.status === "waiting_admin" ? "danger" : c.resolution === "ai_resolved" ? "success" : "muted"}>
                      {c.status === "waiting_admin" ? "🔴 Butuh Kamu" : c.resolution === "ai_resolved" ? "AI Selesai" : c.status === "closed" ? "Selesai" : "AI Aktif"}
                    </Badge>
                    <span className="text-[10px] text-[hsl(var(--muted-foreground))]">{CHANNEL_LABEL[c.channel] || c.channel}</span>
                    {c.nomor_aktif === false && (
                      <Badge tone="warn">Nomor lama (tidak aktif)</Badge>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Detail */}
        <div className="bg-[hsl(var(--background))] flex flex-col">
          {!selected ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
              <MessagesSquare className="w-8 h-8 opacity-40" />
              Pilih percakapan di sebelah kiri untuk membaca isinya
            </div>
          ) : (
            <>
              <div className="bg-white border-b border-[hsl(var(--border))] px-6 py-4 flex items-center justify-between gap-3 flex-wrap">
                <div>
                  <div className="font-[Fraunces] font-semibold text-lg flex items-center gap-2">
                    {selected.guest_name || "Tamu Anonim"}
                    {selected.nomor_aktif === false && <Badge tone="warn">Nomor lama (tidak aktif)</Badge>}
                  </div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">
                    {selected.whatsapp ? `📱 ${selected.whatsapp}` : "Tanpa nomor WhatsApp"} · {CHANNEL_LABEL[selected.channel] || selected.channel}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {selected.status === "waiting_admin" ? (
                    <button data-testid="btn-resume-ai" onClick={() => doResume(selected.id)}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-emerald-600 text-emerald-700 hover:bg-emerald-600 hover:text-white font-medium">
                      <Bot className="w-3.5 h-3.5" /> Aktifkan AI Lagi
                    </button>
                  ) : (
                    <button data-testid="btn-handover" onClick={() => doHandover(selected.id)}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-[hsl(var(--accent))] text-[hsl(var(--accent))] hover:bg-[hsl(var(--accent))] hover:text-white font-medium">
                      <UserRound className="w-3.5 h-3.5" /> Ambil Alih dari AI
                    </button>
                  )}
                  {selected.status !== "closed" && (
                    <button data-testid="btn-close-conv" onClick={() => doClose(selected.id)}
                      className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))] font-medium">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Tutup Percakapan
                    </button>
                  )}
                </div>
              </div>
              {selected.status === "waiting_admin" && (
                <div className="bg-amber-50 border-b border-amber-200 px-6 py-2.5 text-xs text-amber-800 flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  AI sudah berhenti membalas otomatis di sini. Ketik balasanmu di bawah, atau tekan <b>&nbsp;"Aktifkan AI Lagi"&nbsp;</b> kalau ingin AI lanjut menjawab.
                </div>
              )}
              <div className="relative flex-1 min-h-0">
                <div ref={scrollRef} onScroll={handleScroll} className="absolute inset-0 overflow-y-auto pelangi-scroll p-6 chat-bg flex flex-col gap-2">
                  {selected.messages.length > visibleCount && (
                    <button
                      onClick={() => setVisibleCount((v) => v + PESAN_TAMBAHAN)}
                      className="self-center mb-2 inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-white border border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] font-medium shadow-sm"
                    >
                      <ChevronUp className="w-3.5 h-3.5" /> Muat pesan lebih lama ({selected.messages.length - visibleCount} lagi)
                    </button>
                  )}
                  {selected.messages.slice(-visibleCount).map((m, i) => {
                    const intentLabel = INTENT_LABEL[m.intent];
                    return (
                      <div key={i} className={m.role === "user" ? "chat-bubble-guest" : "chat-bubble-ai"}>
                        {m.from_admin && <div className="text-[10px] font-semibold text-emerald-700 mb-0.5">Kamu (balasan manual)</div>}
                        {m.from_system && <div className="text-[10px] font-semibold text-sky-700 mb-0.5">📋 Notifikasi Sistem (PMS)</div>}
                        <ChatMessageContent content={m.content} />
                        <div className="text-[10px] mt-1 text-stone-500 text-right">
                          {new Date(m.timestamp).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
                          {intentLabel && <> · <span className="text-emerald-700">{intentLabel}</span></>}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {!atBottom && (
                  <button
                    onClick={() => scrollToBottom("smooth")}
                    data-testid="btn-scroll-bottom"
                    className="absolute bottom-4 right-6 inline-flex items-center gap-1.5 text-xs px-3 py-2 rounded-full bg-[hsl(var(--primary))] text-white font-medium shadow-lg hover:opacity-90"
                  >
                    <ChevronDown className="w-3.5 h-3.5" /> Pesan terbaru
                  </button>
                )}
              </div>
              {selected.status !== "closed" && (
                <div className="bg-white border-t border-[hsl(var(--border))] p-3 flex items-end gap-2">
                  <textarea
                    data-testid="reply-input"
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendReply(); } }}
                    placeholder="Ketik pesanmu untuk tamu di sini…"
                    rows={2}
                    className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm resize-none"
                  />
                  <button
                    data-testid="btn-send-reply" onClick={sendReply} disabled={sending || !replyText.trim()}
                    className="inline-flex items-center gap-1.5 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90 disabled:opacity-50"
                  >
                    {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Kirim
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
