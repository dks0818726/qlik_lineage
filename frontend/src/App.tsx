import { Link, Route, Routes } from "react-router-dom";
import { AppDetailsPage } from "./pages/AppDetailsPage";
import { ChatPage } from "./pages/ChatPage";
import { DependencyExplorerPage } from "./pages/DependencyExplorerPage";
import { GraphPage } from "./pages/GraphPage";
import { ImpactAnalysisPage } from "./pages/ImpactAnalysisPage";
import { StatusBar } from "./components/StatusBar";

export function App() {
  return (
    <div style={{ fontFamily: "Segoe UI, Arial, sans-serif", margin: 0 }}>
      <header style={{ padding: "16px 24px", background: "#0c2340", color: "#fff" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Qlik Lineage Copilot</h1>
        <nav style={{ display: "flex", gap: 16, marginTop: 8 }}>
          <NavLink to="/">Graph</NavLink>
          <NavLink to="/explorer">Dependency Explorer</NavLink>
          <NavLink to="/impact">Impact Analysis</NavLink>
          <NavLink to="/chat">Copilot Chat</NavLink>
        </nav>
      </header>
      <StatusBar />
      <main style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<GraphPage />} />
          <Route path="/explorer" element={<DependencyExplorerPage />} />
          <Route path="/impact" element={<ImpactAnalysisPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/app/:appId" element={<AppDetailsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} style={{ color: "#9bd0ff", textDecoration: "none", fontWeight: 600 }}>
      {children}
    </Link>
  );
}
