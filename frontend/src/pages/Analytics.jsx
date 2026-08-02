import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, StatCard } from "@/components/ui-parts";
import { api } from "@/lib/api";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

const fmtWaktu = (iso) => {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
};

export default function Analytics() {
  const [data, setData] = useState(null);
  useEffect(() => { api.get("/analytics/summary").then((r) => setData(r.data)); }, []);

  const fmtMs = (ms) => !ms ? "—" : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;

  return (
    <div>
      <PageHeader
        tid="analytics-header"
        title="Analytics"
        subtitle="Performa AI Guest Assistant dan aktivitas percakapan."
      />

      <div className="p-8 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard tid="a-conv" label="Total Percakapan" value={data?.total_conversations ?? "—"} />
          <StatCard tid="a-res" label="Resolution Rate" value={`${data?.resolution_rate ?? 0}%`} />
          <StatCard tid="a-book" label="Bookings from AI" value={data?.bookings_from_ai ?? "—"} />
          <StatCard tid="a-rt" label="Avg Response Time" value={fmtMs(data?.avg_response_time_ms)} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="pelangi-panel p-5">
            <div className="font-[Fraunces] font-semibold mb-4">Percakapan per Hari</div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data?.daily_series || []}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                  <XAxis dataKey="date" stroke="#7D7A73" fontSize={11} />
                  <YAxis stroke="#7D7A73" fontSize={11} allowDecimals={false} />
                  <Tooltip />
                  <Line type="monotone" dataKey="count" stroke="hsl(143 25% 22%)" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="pelangi-panel p-5">
            <div className="font-[Fraunces] font-semibold mb-4">Aksi AI Terpopuler</div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data?.top_intents || []}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                  <XAxis dataKey="intent" stroke="#7D7A73" fontSize={11} />
                  <YAxis stroke="#7D7A73" fontSize={11} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" fill="hsl(16 55% 52%)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <div className="pelangi-panel p-5">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))]">Human Handover</div>
              <div className="font-[Fraunces] font-bold text-2xl mt-1">{data?.human_handover ?? 0}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))]">Conversion Rate</div>
              <div className="font-[Fraunces] font-bold text-2xl mt-1">{data?.conversion_rate ?? 0}%</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))]">AI Resolved</div>
              <div className="font-[Fraunces] font-bold text-2xl mt-1">
                {data ? Math.round((data.total_conversations * data.resolution_rate) / 100) : 0}
              </div>
            </div>
          </div>
        </div>

        {/* Learning Engine v1 (2026-08-02, PRD "AI Receptionist Intelligence Engine"
            Modul 14) - alasan handover cuma mulai kesimpan hari ini (lihat
            _tool_request_handover), jadi chart ini akan kosong dulu utk percakapan lama
            sebelum fix itu, baru terisi utk handover baru ke depan - bukan bug. */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="pelangi-panel p-5">
            <div className="font-[Fraunces] font-semibold mb-4">Top Alasan Handover</div>
            <div className="h-64">
              {(data?.top_handover_reasons || []).length === 0 ? (
                <div className="h-full flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))] text-center px-4">
                  Belum ada data - alasan handover baru mulai tercatat sejak hari ini.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.top_handover_reasons} layout="vertical" margin={{ left: 24 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                    <XAxis type="number" stroke="#7D7A73" fontSize={11} allowDecimals={false} />
                    <YAxis type="category" dataKey="reason" stroke="#7D7A73" fontSize={10} width={160} />
                    <Tooltip />
                    <Bar dataKey="count" fill="hsl(0 65% 55%)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="pelangi-panel p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="font-[Fraunces] font-semibold">Percakapan Perlu Ditinjau</div>
              <Link to="/conversations" className="text-xs underline text-[hsl(var(--muted-foreground))]">
                Buka Conversations →
              </Link>
            </div>
            <div className="h-64 overflow-y-auto">
              {(data?.flagged_conversations || []).length === 0 ? (
                <div className="h-full flex items-center justify-center text-sm text-[hsl(var(--muted-foreground))]">
                  Tidak ada percakapan menunggu admin saat ini.
                </div>
              ) : (
                <table className="w-full text-xs">
                  <thead className="text-[hsl(var(--muted-foreground))] uppercase text-[10px]">
                    <tr>
                      <th className="text-left pb-2">Tamu</th>
                      <th className="text-left pb-2">Alasan</th>
                      <th className="text-left pb-2">Waktu</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.flagged_conversations.map((f) => (
                      <tr key={f.session_id} className="border-t border-[hsl(var(--border))]">
                        <td className="py-2 font-medium">{f.guest_name}</td>
                        <td className="py-2">{f.reason}</td>
                        <td className="py-2 whitespace-nowrap">{fmtWaktu(f.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
