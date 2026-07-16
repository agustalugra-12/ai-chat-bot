export function PageHeader({ title, subtitle, right, tid }) {
  return (
    <div className="flex items-start justify-between gap-4 px-8 pt-8 pb-6 border-b border-[hsl(var(--border))] bg-[hsl(var(--background))]" data-testid={tid}>
      <div>
        <h1 className="font-[Manrope] font-bold text-3xl tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-[hsl(var(--muted-foreground))] mt-1.5 max-w-2xl">{subtitle}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

export function StatCard({ label, value, hint, accent, tid }) {
  return (
    <div className="pelangi-panel p-5 fade-in-up" data-testid={tid}>
      <div className="text-[11px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] font-medium">{label}</div>
      <div className={`mt-3 font-[Manrope] font-bold text-3xl tracking-tight ${accent || ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">{hint}</div>}
    </div>
  );
}

export function Badge({ tone = "neutral", children }) {
  const map = {
    neutral: "bg-stone-100 text-stone-700",
    primary: "bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))]",
    success: "bg-emerald-100 text-emerald-800",
    warn: "bg-amber-100 text-amber-800",
    danger: "bg-[hsl(var(--accent))]/15 text-[hsl(var(--accent))]",
    muted: "bg-stone-100 text-stone-500",
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium ${map[tone]}`}>
      {children}
    </span>
  );
}

export function EmptyState({ title, hint, tid }) {
  return (
    <div className="py-16 text-center" data-testid={tid}>
      <div className="text-sm text-[hsl(var(--muted-foreground))]">{title}</div>
      {hint && <div className="text-xs mt-1.5 text-[hsl(var(--muted-foreground))]/70">{hint}</div>}
    </div>
  );
}
