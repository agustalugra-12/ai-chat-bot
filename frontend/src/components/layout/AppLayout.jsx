import { useState } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import {
  LayoutDashboard, MessagesSquare, BotMessageSquare, BookOpenText,
  BedDouble, UtensilsCrossed, CalendarCheck2, Bell, FileTerminal,
  BarChart3, Settings as SettingsIcon, LogOut, Sparkles, FileStack,
  Bot, Wrench, Target, Waypoints, ChevronDown, ChevronRight, Plug,
} from "lucide-react";

const topNav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, tid: "nav-dashboard" },
  { to: "/conversations", label: "Percakapan", icon: MessagesSquare, tid: "nav-conversations" },
  { to: "/simulator", label: "Chat Simulator", icon: BotMessageSquare, tid: "nav-simulator" },
];

const aiSection = [
  { to: "/ai/bots", label: "AI List", icon: Bot, tid: "nav-ai-bots" },
  { to: "/ai/tools", label: "Tools", icon: Wrench, tid: "nav-ai-tools" },
  { to: "/ai/intents", label: "Intents", icon: Target, tid: "nav-ai-intents" },
  { to: "/ai/workflows", label: "Workflows", icon: Waypoints, tid: "nav-ai-workflows" },
  { to: "/prompt", label: "Prompt (legacy)", icon: FileTerminal, tid: "nav-prompt" },
];

const dataSection = [
  { to: "/knowledge-base", label: "Knowledge Base", icon: BookOpenText, tid: "nav-kb" },
  { to: "/rag", label: "RAG Documents", icon: FileStack, tid: "nav-rag" },
  { to: "/rooms", label: "Kamar", icon: BedDouble, tid: "nav-rooms" },
  { to: "/menu", label: "Menu Resto", icon: UtensilsCrossed, tid: "nav-menu" },
];

const opsSection = [
  { to: "/bookings", label: "Booking", icon: CalendarCheck2, tid: "nav-bookings" },
  { to: "/service-requests", label: "Service Requests", icon: Bell, tid: "nav-service" },
];

const bottomNav = [
  { to: "/analytics", label: "Analytics", icon: BarChart3, tid: "nav-analytics" },
  { to: "/pms-integration", label: "Integrasi PMS", icon: Plug, tid: "nav-pms-integration" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, tid: "nav-settings" },
];

function NavItem({ to, label, icon: Icon, end, tid }) {
  return (
    <NavLink to={to} end={end} data-testid={tid}
      className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}>
      <Icon className="w-4 h-4" strokeWidth={1.9} />
      <span>{label}</span>
    </NavLink>
  );
}

function SectionHeader({ label }) {
  return <div className="px-3 pt-4 pb-1 text-[10px] uppercase tracking-widest text-[hsl(var(--muted-foreground))] font-semibold">{label}</div>;
}

export default function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [aiOpen, setAiOpen] = useState(location.pathname.startsWith("/ai") || location.pathname === "/prompt");

  const onLogout = () => { logout(); navigate("/login"); };

  return (
    <div className="min-h-screen flex bg-[hsl(var(--background))]">
      <aside className="w-[260px] shrink-0 border-r border-[hsl(var(--border))] bg-white flex flex-col h-screen sticky top-0">
        <div className="px-5 py-6 flex items-center gap-3 border-b border-[hsl(var(--border))]">
          <div className="w-9 h-9 rounded-lg bg-[hsl(var(--primary))] flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-white" strokeWidth={2.2} />
          </div>
          <div>
            <div className="font-[Manrope] font-bold text-[15px] leading-tight">Pelangi AI</div>
            <div className="text-[11px] text-[hsl(var(--muted-foreground))] uppercase tracking-widest">Guest Console</div>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 overflow-y-auto pelangi-scroll">
          {topNav.map((n) => <NavItem key={n.to} {...n} />)}

          <button
            data-testid="nav-ai-toggle"
            onClick={() => setAiOpen((v) => !v)}
            className="sidebar-link w-full mt-2"
          >
            <Bot className="w-4 h-4" strokeWidth={1.9} />
            <span className="flex-1 text-left">AI Management</span>
            {aiOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
          {aiOpen && (
            <div className="pl-3 space-y-0.5 border-l border-[hsl(var(--border))] ml-4 mt-1">
              {aiSection.map((n) => <NavItem key={n.to} {...n} />)}
            </div>
          )}

          <SectionHeader label="Data" />
          {dataSection.map((n) => <NavItem key={n.to} {...n} />)}

          <SectionHeader label="Operations" />
          {opsSection.map((n) => <NavItem key={n.to} {...n} />)}

          <SectionHeader label="System" />
          {bottomNav.map((n) => <NavItem key={n.to} {...n} />)}
        </nav>

        <div className="border-t border-[hsl(var(--border))] p-3">
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="w-9 h-9 rounded-full bg-[hsl(var(--secondary))] flex items-center justify-center text-[hsl(var(--secondary-foreground))] font-semibold text-sm">
              {(user?.name || "?").split(" ").map((s) => s[0]).slice(0, 2).join("")}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate" data-testid="current-user-name">{user?.name}</div>
              <div className="text-[11px] text-[hsl(var(--muted-foreground))] uppercase tracking-wider">{user?.role?.replace("_", " ")}</div>
            </div>
            <button
              data-testid="logout-btn"
              onClick={onLogout}
              className="p-2 rounded-md hover:bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]"
              title="Logout"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 pelangi-scroll overflow-x-hidden">
        <Outlet />
      </main>
    </div>
  );
}
