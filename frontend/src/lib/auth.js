import { createContext, useContext, useEffect, useState } from "react";
import { api } from "./api";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem("pelangi_user");
    return raw ? JSON.parse(raw) : null;
  });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("pelangi_token");
    if (token && !user) {
      api.get("/auth/me")
        .then((r) => {
          setUser(r.data);
          localStorage.setItem("pelangi_user", JSON.stringify(r.data));
        })
        .catch(() => {});
    }
  }, []); // eslint-disable-line

  const login = async (email, password) => {
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      localStorage.setItem("pelangi_token", data.token);
      localStorage.setItem("pelangi_user", JSON.stringify(data.user));
      setUser(data.user);
      return data.user;
    } finally {
      setLoading(false);
    }
  };

  const logout = () => {
    localStorage.removeItem("pelangi_token");
    localStorage.removeItem("pelangi_user");
    setUser(null);
  };

  return <AuthCtx.Provider value={{ user, loading, login, logout }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
