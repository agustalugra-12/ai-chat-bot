import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "@/lib/auth";
import Login from "@/pages/Login";
import AppLayout from "@/components/layout/AppLayout";
import Dashboard from "@/pages/Dashboard";
import Conversations from "@/pages/Conversations";
import ChatSimulator from "@/pages/ChatSimulator";
import KnowledgeBase from "@/pages/KnowledgeBase";
import Rooms from "@/pages/Rooms";
import RestaurantMenu from "@/pages/RestaurantMenu";
import Bookings from "@/pages/Bookings";
import ServiceRequests from "@/pages/ServiceRequests";
import PromptManagement from "@/pages/PromptManagement";
import Analytics from "@/pages/Analytics";
import Settings from "@/pages/Settings";
import RagDocuments from "@/pages/RagDocuments";
import BotList from "@/pages/ai/BotList";
import BotDetail from "@/pages/ai/BotDetail";
import ToolsCatalog from "@/pages/ai/ToolsCatalog";
import IntentsCatalog from "@/pages/ai/IntentsCatalog";
import WorkflowsCatalog from "@/pages/ai/WorkflowsCatalog";

function ProtectedRoute({ children }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster richColors position="top-right" />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="conversations" element={<Conversations />} />
            <Route path="simulator" element={<ChatSimulator />} />
            <Route path="knowledge-base" element={<KnowledgeBase />} />
            <Route path="rooms" element={<Rooms />} />
            <Route path="menu" element={<RestaurantMenu />} />
            <Route path="bookings" element={<Bookings />} />
            <Route path="service-requests" element={<ServiceRequests />} />
            <Route path="prompt" element={<PromptManagement />} />
            <Route path="rag" element={<RagDocuments />} />
            <Route path="ai/bots" element={<BotList />} />
            <Route path="ai/bots/:botId" element={<BotDetail />} />
            <Route path="ai/tools" element={<ToolsCatalog />} />
            <Route path="ai/intents" element={<IntentsCatalog />} />
            <Route path="ai/workflows" element={<WorkflowsCatalog />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
