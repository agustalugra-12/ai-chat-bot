import React from "react";

// Detect explicit [[IMG: url]] markers and known image-hosting URLs.
const IMG_MARKER_RE = /\[\[IMG:\s*(https?:\/\/[^\s\]]+)\s*\]\]/gi;
// Match any URL that starts with known image hosts (regardless of extension / query).
const IMAGE_HOST_RE = /(https?:\/\/(?:res\.cloudinary\.com|images\.unsplash\.com)\/[^\s)]+)/gi;

function extractImageUrls(text) {
  const urls = [];
  let stripped = text || "";

  // 1. explicit [[IMG: url]]
  stripped = stripped.replace(IMG_MARKER_RE, (_, u) => {
    urls.push(u);
    return "";
  });

  // 2. host-based detection (Cloudinary + Unsplash regardless of format)
  stripped = stripped.replace(IMAGE_HOST_RE, (u) => {
    urls.push(u);
    return "";
  });

  // Clean up leftover list markers / stray punctuation from removed URLs
  stripped = stripped
    .replace(/^\s*[\-\*\d]+[\.\)]?\s*$/gm, "")   // "1.", "2)", "- " on their own line
    .replace(/[ \t]+$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  // Dedupe URLs while preserving order
  const seen = new Set();
  const unique = urls.filter((u) => (seen.has(u) ? false : seen.add(u)));

  return { text: stripped, images: unique };
}

export function ChatMessageContent({ content }) {
  const { text, images } = extractImageUrls(content || "");
  return (
    <>
      {text && <div className="whitespace-pre-wrap text-[14px] leading-relaxed">{text}</div>}
      {images.length > 0 && (
        <div className={`mt-2 grid ${images.length === 1 ? "grid-cols-1" : "grid-cols-2"} gap-1.5 max-w-[280px]`}>
          {images.map((u, i) => (
            <a key={i} href={u} target="_blank" rel="noreferrer" data-testid={`chat-image-${i}`}>
              <img src={u} alt="" loading="lazy" className="rounded-md w-full object-cover max-h-40 border border-black/5" />
            </a>
          ))}
        </div>
      )}
    </>
  );
}
