import { useEffect, useState } from "react";
import { PageHeader, StatCard } from "@/components/ui-parts";
import { api } from "@/lib/api";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

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
            <div className="font-[Manrope] font-semibold mb-4">Percakapan per Hari</div>
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
            <div className="font-[Manrope] font-semibold mb-4">Aksi AI Terpopuler</div>
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
              <div className="font-[Manrope] font-bold text-2xl mt-1">{data?.human_handover ?? 0}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))]">Conversion Rate</div>
              <div className="font-[Manrope] font-bold text-2xl mt-1">{data?.conversion_rate ?? 0}%</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-[hsl(var(--muted-foreground))]">AI Resolved</div>
              <div className="font-[Manrope] font-bold text-2xl mt-1">
                {data ? Math.round((data.total_conversations * data.resolution_rate) / 100) : 0}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
