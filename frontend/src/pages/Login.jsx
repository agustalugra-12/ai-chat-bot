import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import { Sparkles, LockKeyhole } from "lucide-react";

export default function Login() {
  const { user, login, loading } = useAuth();
  const [email, setEmail] = useState("admin@pelangi.id");
  const [password, setPassword] = useState("Admin123!");
  const navigate = useNavigate();

  if (user) return <Navigate to="/" replace />;

  const onSubmit = async (e) => {
    e.preventDefault();
    try {
      await login(email, password);
      toast.success("Berhasil login");
      navigate("/");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Login gagal");
    }
  };

  return (
    <div className="min-h-screen flex">
      {/* Left hero */}
      <div className="hidden lg:flex flex-1 relative login-hero grain overflow-hidden text-white">
        <div className="relative z-10 flex flex-col justify-between p-12 w-full">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-white/10 backdrop-blur flex items-center justify-center border border-white/15">
              <Sparkles className="w-5 h-5" />
            </div>
            <span className="font-[Manrope] font-semibold text-lg">Pelangi Homestay</span>
          </div>

          <div className="max-w-md">
            <h1 className="font-[Manrope] font-extrabold text-5xl leading-[1.05] tracking-tight">
              Resepsionis digital<br/>
              yang tak pernah tidur.
            </h1>
            <p className="mt-6 text-white/70 text-base leading-relaxed">
              Kelola percakapan tamu, booking, dan layanan homestay dari satu dashboard.
              Ditenagai AI, terhubung ke seluruh PMS Anda.
            </p>
          </div>

          <div className="text-xs text-white/50">
            © 2026 Pelangi Homestay · Guest AI Console
          </div>
        </div>
      </div>

      {/* Right form */}
      <div className="flex-1 flex items-center justify-center px-6 py-16 bg-[hsl(var(--background))]">
        <div className="w-full max-w-sm">
          <div className="flex items-center gap-2 mb-8">
            <LockKeyhole className="w-4 h-4 text-[hsl(var(--primary))]" />
            <span className="text-xs uppercase tracking-[0.2em] text-[hsl(var(--muted-foreground))]">Admin Access</span>
          </div>
          <h2 className="font-[Manrope] font-bold text-3xl mb-2">Masuk ke Console</h2>
          <p className="text-sm text-[hsl(var(--muted-foreground))] mb-8">
            Gunakan akun admin Anda untuk membuka dashboard.
          </p>

          <form onSubmit={onSubmit} className="space-y-4" data-testid="login-form">
            <div>
              <label className="block text-xs font-medium mb-1.5 text-[hsl(var(--foreground))]">Email</label>
              <input
                data-testid="login-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-md border border-[hsl(var(--border))] bg-white focus:outline-none focus:ring-2 focus:ring-[hsl(var(--primary))] focus:border-transparent text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1.5">Password</label>
              <input
                data-testid="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-md border border-[hsl(var(--border))] bg-white focus:outline-none focus:ring-2 focus:ring-[hsl(var(--primary))] focus:border-transparent text-sm"
                required
              />
            </div>
            <button
              data-testid="login-submit"
              disabled={loading}
              type="submit"
              className="w-full py-2.5 rounded-md bg-[hsl(var(--primary))] text-white font-medium text-sm hover:opacity-90 disabled:opacity-60 transition-opacity"
            >
              {loading ? "Masuk..." : "Masuk"}
            </button>
          </form>

          <div className="mt-8 p-4 rounded-lg border border-[hsl(var(--border))] bg-white text-xs text-[hsl(var(--muted-foreground))] space-y-1">
            <div className="font-medium text-[hsl(var(--foreground))]">Demo credentials</div>
            <div>admin@pelangi.id · Admin123!</div>
            <div>superadmin@pelangi.id · Super123!</div>
          </div>
        </div>
      </div>
    </div>
  );
}
