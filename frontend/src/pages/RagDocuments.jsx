import { useEffect, useRef, useState } from "react";
import { PageHeader, Badge, EmptyState } from "@/components/ui-parts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { UploadCloud, FileText, Trash2, Search, Loader2 } from "lucide-react";

export default function RagDocuments() {
  const [docs, setDocs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef();

  const load = async () => setDocs((await api.get("/rag/documents")).data);
  useEffect(() => { load(); }, []);

  const onPick = () => inputRef.current?.click();

  const onFile = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    e.target.value = "";
    setBusy(true);
    try {
      for (const f of files) {
        const fd = new FormData();
        fd.append("file", f);
        const { data } = await api.post("/rag/documents", fd, { headers: { "Content-Type": "multipart/form-data" } });
        toast.success(`${data.title} — ${data.chunk_count} chunks di-index`);
      }
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload gagal");
    } finally { setBusy(false); }
  };

  const remove = async (id) => {
    if (!window.confirm("Hapus dokumen dan semua chunk-nya?")) return;
    await api.delete(`/rag/documents/${id}`);
    toast.success("Dihapus"); load();
  };

  const search = async () => {
    const q = query.trim(); if (!q) return;
    setSearching(true);
    try {
      const { data } = await api.get(`/rag/search?q=${encodeURIComponent(q)}&k=5`);
      setHits(data.hits);
    } finally { setSearching(false); }
  };

  return (
    <div>
      <PageHeader
        tid="rag-header"
        title="RAG Documents"
        subtitle="Upload SOP, manual, atau FAQ (PDF/DOCX/TXT). AI Guest Assistant akan menggunakan dokumen ini sebagai referensi tambahan (BM25 lexical retrieval)."
        right={
          <button data-testid="rag-upload-btn" onClick={onPick} disabled={busy}
            className="inline-flex items-center gap-2 bg-[hsl(var(--primary))] text-white text-sm font-medium px-4 py-2.5 rounded-md hover:opacity-90 disabled:opacity-60">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <UploadCloud className="w-4 h-4" />}
            {busy ? "Mengunggah..." : "Upload Dokumen"}
          </button>
        }
      />
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md"
        onChange={onFile}
        className="hidden"
        data-testid="rag-file-input"
      />

      <div className="p-8 grid grid-cols-1 lg:grid-cols-[1fr_400px] gap-6">
        <div className="pelangi-panel overflow-hidden">
          <div className="px-5 py-4 border-b border-[hsl(var(--border))] flex items-center justify-between">
            <div>
              <div className="font-[Manrope] font-semibold">Dokumen Ter-index</div>
              <div className="text-xs text-[hsl(var(--muted-foreground))]">{docs.length} dokumen · {docs.reduce((s, d) => s + (d.chunk_count || 0), 0)} chunks</div>
            </div>
          </div>
          {docs.length === 0 ? (
            <EmptyState tid="rag-empty" title="Belum ada dokumen" hint="Upload PDF/DOCX SOP homestay Anda untuk mulai" />
          ) : (
            <div className="divide-y divide-[hsl(var(--border))]">
              {docs.map((d) => (
                <div key={d.id} className="p-5 pelangi-row flex items-start gap-3" data-testid={`rag-doc-${d.id}`}>
                  <div className="w-9 h-9 rounded-md bg-[hsl(var(--secondary))] text-[hsl(var(--secondary-foreground))] flex items-center justify-center shrink-0">
                    <FileText className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{d.title}</div>
                    <div className="text-xs text-[hsl(var(--muted-foreground))] mt-0.5">
                      <Badge tone="muted">{d.chunk_count} chunks</Badge>
                      <Badge tone={d.embedded ? "success" : "warn"}>{d.embedded ? "semantic search" : "keyword search saja"}</Badge>
                      <span className="ml-2">{(d.char_count / 1000).toFixed(1)}k karakter</span>
                      <span className="ml-2">· {new Date(d.created_at).toLocaleString("id-ID")}</span>
                    </div>
                    {d.url && (
                      <a href={d.url} target="_blank" rel="noreferrer" className="text-[11px] text-[hsl(var(--primary))] hover:underline mt-1 inline-block">Lihat file asli</a>
                    )}
                  </div>
                  <button data-testid={`rag-delete-${d.id}`} onClick={() => remove(d.id)}
                    className="p-2 rounded-md border border-[hsl(var(--border))] hover:bg-red-50 text-red-600">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Search preview */}
        <div className="pelangi-panel overflow-hidden">
          <div className="px-5 py-4 border-b border-[hsl(var(--border))]">
            <div className="font-[Manrope] font-semibold">Uji Retrieval</div>
            <div className="text-xs text-[hsl(var(--muted-foreground))]">Preview chunks yang akan dipakai AI</div>
          </div>
          <div className="p-4">
            <div className="flex gap-2">
              <input
                data-testid="rag-search-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && search()}
                placeholder="Kata kunci, misal: kebijakan pembatalan..."
                className="flex-1 px-3 py-2 rounded-md border border-[hsl(var(--border))] text-sm bg-white"
              />
              <button data-testid="rag-search-btn" onClick={search} disabled={searching}
                className="px-3 py-2 rounded-md bg-[hsl(var(--primary))] text-white disabled:opacity-50">
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              </button>
            </div>
            <div className="mt-4 space-y-3 max-h-[500px] overflow-y-auto pelangi-scroll">
              {hits.length === 0 && <div className="text-xs text-[hsl(var(--muted-foreground))] text-center py-8">{query ? "Belum ada hasil." : "Ketik query untuk uji BM25."}</div>}
              {hits.map((h, i) => (
                <div key={h.id} className="p-3 rounded-md border border-[hsl(var(--border))]" data-testid={`rag-hit-${i}`}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="text-[11px] font-medium text-[hsl(var(--muted-foreground))]">{h.doc_title}</div>
                    <Badge tone="primary">skor {h.score.toFixed(2)}</Badge>
                  </div>
                  <div className="text-xs whitespace-pre-wrap line-clamp-4">{h.text}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
