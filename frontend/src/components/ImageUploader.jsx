import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Upload, X, Loader2, Star } from "lucide-react";

/**
 * ImageUploader - handles multi-image upload to Cloudinary via backend.
 * Props:
 *   value: [{url, public_id}]
 *   onChange: (newArray) => void
 *   folder: "pelangi/kb" | "pelangi/rooms" | "pelangi/menu"
 *   max: number of images (default 5)
 *   tid: data-testid prefix
 *   primaryUrl / onSetPrimary: opsional - kalau diisi, tiap thumbnail dapat tombol
 *   bintang untuk pilih foto utama dari galeri (bukan ketik URL manual).
 */
export function ImageUploader({ value = [], onChange, folder = "pelangi/kb", max = 5, tid = "uploader", primaryUrl, onSetPrimary }) {
  const inputRef = useRef();
  const [busy, setBusy] = useState(false);

  const pickFile = () => inputRef.current?.click();

  const onFileChange = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    e.target.value = "";

    if (value.length + files.length > max) {
      toast.error(`Maksimum ${max} foto`);
      return;
    }
    setBusy(true);
    try {
      const uploaded = [];
      for (const f of files) {
        const fd = new FormData();
        fd.append("file", f);
        const { data } = await api.post(`/uploads/image?folder=${encodeURIComponent(folder)}`, fd, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        uploaded.push({ url: data.url, public_id: data.public_id });
      }
      onChange([...(value || []), ...uploaded]);
      toast.success(`${uploaded.length} foto diunggah`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload gagal");
    } finally {
      setBusy(false);
    }
  };

  const remove = (idx) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  return (
    <div>
      <div className="flex flex-wrap gap-2" data-testid={`${tid}-grid`}>
        {(value || []).map((img, i) => (
          <div key={img.public_id || i} className="relative w-20 h-20 rounded-md overflow-hidden border border-[hsl(var(--border))] group" data-testid={`${tid}-thumb-${i}`}>
            <img src={img.url} alt="" className="w-full h-full object-cover" />
            {onSetPrimary && (
              <button
                type="button"
                data-testid={`${tid}-primary-${i}`}
                onClick={() => onSetPrimary(img.url)}
                title="Jadikan foto utama"
                className={`absolute top-1 left-1 w-5 h-5 rounded-full grid place-items-center transition-opacity ${img.url === primaryUrl ? "bg-amber-400 text-white opacity-100" : "bg-white/80 text-[hsl(var(--muted-foreground))] opacity-0 group-hover:opacity-100 hover:text-amber-500"}`}
              >
                <Star className="w-3 h-3" fill={img.url === primaryUrl ? "currentColor" : "none"} />
              </button>
            )}
            <button
              type="button"
              data-testid={`${tid}-remove-${i}`}
              onClick={() => remove(i)}
              className="absolute top-0 right-0 p-0.5 bg-black/60 text-white rounded-bl-md opacity-0 group-hover:opacity-100 transition-opacity"
              title="Hapus"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        {(value || []).length < max && (
          <button
            type="button"
            data-testid={`${tid}-add`}
            onClick={pickFile}
            disabled={busy}
            className="w-20 h-20 rounded-md border-2 border-dashed border-[hsl(var(--border))] flex flex-col items-center justify-center text-[hsl(var(--muted-foreground))] hover:border-[hsl(var(--primary))] hover:text-[hsl(var(--primary))] disabled:opacity-50 transition-colors"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            <span className="text-[10px] mt-1">Upload</span>
          </button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/gif"
        onChange={onFileChange}
        className="hidden"
        data-testid={`${tid}-input`}
      />
      <div className="text-[11px] text-[hsl(var(--muted-foreground))] mt-1.5">Max {max} foto · JPG/PNG/WebP · &lt;10MB per file</div>
    </div>
  );
}
