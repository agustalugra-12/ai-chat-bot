import React from "react";

// Parse text that may contain [[IMG: url]] and standalone URLs. Returns array of {type: 'text'|'image', value}.
const IMG_MARKER_RE = /\[\[IMG:\s*(https?:\/\/[^\s\]]+)\s*\]\]/gi;
const URL_RE = /(https?:\/\/[^\s)]+\.(?:jpg|jpeg|png|webp|gif))(?![^\s])/gi;

function extractImageUrls(text) {
  const urls = [];
  let stripped = text;

  // 1. explicit [[IMG: url]]
  stripped = stripped.replace(IMG_MARKER_RE, (_, u) => {
    urls.push(u);
    return "";
  });

  // 2. Loose Cloudinary URLs pointing to res.cloudinary.com images
  stripped = stripped.replace(URL_RE, (u) => {
    if (u.includes("res.cloudinary.com") || u.includes("images.unsplash.com")) {
      urls.push(u);
      return "";
    }
    return u;
  });

  return { text: stripped.replace(/\n{3,}/g, "\n\n").trim(), images: urls };
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
