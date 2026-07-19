import { useEffect, useState } from "react";
import { PageHeader, StatCard, Badge } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { Link } from "react-router-dom";
import { ArrowUpRight, Zap, MessagesSquare, CalendarCheck2, Clock3, TrendingUp } from "lucide-react";

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [convs, setConvs] = useState([]);

  useEffect(() => {
    // Service Requests dipindah ke PMS (2026-07-19, reuse sistem tiket) - ai-chat-bot
    // tidak lagi punya data ini sendiri, jadi tidak dipanggil di sini lagi (endpoint-nya
    // sudah dihapus, dulu bikin Promise.all ini gagal total & seluruh Dashboard kosong).
    Promise.all([
      api.get("/analytics/summary").then((r) => r.data),
      api.get("/conversations").then((r) => r.data.slice(0, 6)),
    ]).then(([a, c]) => { setData(a); setConvs(c); });
  }, []);

  const fmt = (ms) => {
    if (!ms) return "—";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div>
      <PageHeader
        tid="dashboard-header"
        title="Selamat datang di Pelangi AI"
        subtitle="Ringkasan performa AI Guest Assistant dan aktivitas homestay Anda hari ini."
        right={
          <Link
            to="/simulator"
            data-testid="cta-open-simulator"
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90"
          >
            <Zap className="w-4 h-4" /> Buka Chat Simulator
          </Link>
        }
      />

      <div className="p-8 space-y-8">
        {/* Stats grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard tid="stat-conversations" label="Total Percakapan" value={data?.total_conversations ?? "—"}
            hint={<span className="inline-flex items-center gap-1"><MessagesSquare className="w-3 h-3" /> semua channel</span>} />
          <StatCard tid="stat-resolution" label="AI Resolution Rate" value={`${data?.resolution_rate ?? 0}%`}
            hint={<span className="inline-flex items-center gap-1"><TrendingUp className="w-3 h-3" /> {data?.human_handover ?? 0} handover</span>} />
          <StatCard tid="stat-bookings" label="Booking dari AI" value={data?.bookings_from_ai ?? "—"}
            hint={<span className="inline-flex items-center gap-1"><CalendarCheck2 className="w-3 h-3" /> konversi {data?.conversion_rate ?? 0}%</span>} />
          <StatCard tid="stat-rt" label="Response Time" value={fmt(data?.avg_response_time_ms)}
            hint={<span className="inline-flex items-center gap-1"><Clock3 className="w-3 h-3" /> rata-rata</span>} />
        </div>

        <div className="grid grid-cols-1 gap-6">
          {/* Recent conversations */}
          <div className="pelangi-panel">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[hsl(var(--border))]">
              <div>
                <div className="font-[Manrope] font-semibold">Percakapan Terbaru</div>
                <div className="text-xs text-[hsl(var(--muted-foreground))]">6 sesi tamu terakhir</div>
              </div>
              <Link to="/conversations" className="text-xs text-[hsl(var(--primary))] font-medium inline-flex items-center gap-1" data-testid="link-see-all-conversations">
                Lihat semua <ArrowUpRight className="w-3 h-3" />
              </Link>
            </div>
            <div className="divide-y divide-[hsl(var(--border))]">
              {convs.length === 0 && (
                <div className="p-8 text-center text-sm text-[hsl(var(--muted-foreground))]">Belum ada percakapan. Coba chat simulator.</div>
              )}
              {convs.map((c) => (
                <Link
                  key={c.id}
                  to="/conversations"
                  className="flex items-center gap-4 px-5 py-3 pelangi-row"
                  data-testid={`conv-row-${c.id}`}
                >
                  <div className="w-9 h-9 rounded-full bg-[hsl(var(--secondary))] flex items-center justify-center font-semibold text-xs text-[hsl(var(--secondary-foreground))]">
                    {(c.guest_name || "T").slice(0, 2).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{c.guest_name || "Tamu Anonim"} · <span className="text-[hsl(var(--muted-foreground))] font-normal">{c.whatsapp || c.channel}</span></div>
                    <div className="text-xs text-[hsl(var(--muted-foreground))] truncate">{c.last_message}</div>
                  </div>
                  <div className="text-right">
                    <Badge tone={c.status === "waiting_admin" ? "danger" : c.resolution === "ai_resolved" ? "success" : "muted"}>
                      {c.status === "waiting_admin" ? "Perlu Admin" : c.resolution === "ai_resolved" ? "Selesai" : "Aktif"}
                    </Badge>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </div>

        {/* Top intents */}
        <div className="pelangi-panel">
          <div className="px-5 py-4 border-b border-[hsl(var(--border))]">
            <div className="font-[Manrope] font-semibold">Aksi AI Terpopuler</div>
            <div className="text-xs text-[hsl(var(--muted-foreground))]">Berdasarkan tool yang paling sering dipanggil AI</div>
          </div>
          <div className="p-5 flex flex-wrap gap-3">
            {(data?.top_intents || []).length === 0 && (
              <div className="text-sm text-[hsl(var(--muted-foreground))]">Belum ada data. AI belum melakukan aksi apa pun.</div>
            )}
            {(data?.top_intents || []).map((t) => (
              <div key={t.intent} className="px-3 py-2 rounded-md border border-[hsl(var(--border))] bg-white">
                <div className="text-xs text-[hsl(var(--muted-foreground))]">{t.intent}</div>
                <div className="font-[Manrope] font-semibold text-lg">{t.count}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
