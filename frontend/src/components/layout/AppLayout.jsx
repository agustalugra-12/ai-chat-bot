import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import {
  LayoutDashboard, MessagesSquare, BotMessageSquare, BookOpenText,
  BedDouble, UtensilsCrossed, CalendarCheck2, Bell, FileTerminal,
  BarChart3, Settings as SettingsIcon, LogOut, Sparkles,
} from "lucide-react";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true, tid: "nav-dashboard" },
  { to: "/conversations", label: "Percakapan", icon: MessagesSquare, tid: "nav-conversations" },
  { to: "/simulator", label: "Chat Simulator", icon: BotMessageSquare, tid: "nav-simulator" },
  { to: "/knowledge-base", label: "Knowledge Base", icon: BookOpenText, tid: "nav-kb" },
  { to: "/rooms", label: "Kamar", icon: BedDouble, tid: "nav-rooms" },
  { to: "/menu", label: "Menu Resto", icon: UtensilsCrossed, tid: "nav-menu" },
  { to: "/bookings", label: "Booking", icon: CalendarCheck2, tid: "nav-bookings" },
  { to: "/service-requests", label: "Service Requests", icon: Bell, tid: "nav-service" },
  { to: "/prompt", label: "Prompt AI", icon: FileTerminal, tid: "nav-prompt" },
  { to: "/analytics", label: "Analytics", icon: BarChart3, tid: "nav-analytics" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, tid: "nav-settings" },
];

export default function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const onLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen flex bg-[hsl(var(--background))]">
      {/* Sidebar */}
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

        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto pelangi-scroll">
          {nav.map(({ to, label, icon: Icon, end, tid }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              data-testid={tid}
              className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}
            >
              <Icon className="w-4 h-4" strokeWidth={1.9} />
              <span>{label}</span>
            </NavLink>
          ))}
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

      {/* Main */}
      <main className="flex-1 min-w-0 pelangi-scroll overflow-x-hidden">
        <Outlet />
      </main>
    </div>
  );
}
