import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { Search, User } from "lucide-react";

export default function GuestProfiles() {
  const [profiles, setProfiles] = useState([]);
  const [search, setSearch] = useState("");

  const load = async (q) => {
    const { data } = await api.get(`/guest-profiles${q ? `?search=${encodeURIComponent(q)}` : ""}`);
    setProfiles(data);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  return (
    <div>
      <PageHeader
        tid="guest-profiles-header"
        title="Profil Tamu (Memory)"
        subtitle="Ingatan AI lintas-percakapan — nama & preferensi yang diingat dari kunjungan sebelumnya, otomatis diisi AI lewat percakapan, bukan diinput manual."
      />
      <div className="p-8 space-y-4">
        <div className="relative max-w-sm">
          <Search className="w-4 h-4 text-[hsl(var(--muted-foreground))] absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            data-testid="guest-profiles-search"
            value={search}
            onChange={(e) => { setSearch(e.target.value); load(e.target.value); }}
            placeholder="Cari nama atau nomor WhatsApp…"
            className="w-full pl-9 pr-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm"
          />
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="guest-profiles-grid">
          {profiles.map((p) => (
            <div key={p.whatsapp} className="pelangi-panel p-4 space-y-2" data-testid={`guest-profile-${p.whatsapp}`}>
              <div className="flex items-center gap-2">
                <div className="w-9 h-9 rounded-full bg-[hsl(var(--secondary))] flex items-center justify-center text-[hsl(var(--secondary-foreground))]">
                  <User className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-sm font-semibold">{p.nama || "Tamu"}</div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">{p.whatsapp}</div>
                </div>
              </div>
              <div className="text-xs text-[hsl(var(--muted-foreground))]">
                {p.total_conversations || 0} percakapan · terakhir {p.last_seen_at ? new Date(p.last_seen_at).toLocaleDateString("id-ID") : "-"}
              </div>
              {(p.preferensi || []).length > 0 && (
                <ul className="text-xs space-y-0.5 pt-1 border-t border-[hsl(var(--border))]">
                  {p.preferensi.map((f, i) => <li key={i}>• {f}</li>)}
                </ul>
              )}
            </div>
          ))}
          {profiles.length === 0 && (
            <div className="col-span-full text-center text-sm text-[hsl(var(--muted-foreground))] py-8">Belum ada profil tamu tercatat.</div>
          )}
        </div>
      </div>
    </div>
  );
}
