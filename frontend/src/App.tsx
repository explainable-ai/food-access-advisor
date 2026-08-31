import { NavLink, Route, Routes } from "react-router-dom";
import { HomeMap } from "./pages/HomeMap";
import { SiteAdvisorWorkspace } from "./pages/SiteAdvisorWorkspace";
import { RouteAdvisorWorkspace } from "./pages/RouteAdvisorWorkspace";
import { FollowUpPage } from "./pages/FollowUpPage";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { StaffAuthControls } from "./components/StaffAuthControls";

function App() {
  return (
    <div className="app-shell">
      <nav className="top-nav">
        <span className="brand">Food-Access Planning Workspace</span>
        <div className="nav-tabs">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "nav-tab active" : "nav-tab")}>
            Overview
          </NavLink>
          <NavLink to="/site-advisor" className={({ isActive }) => (isActive ? "nav-tab active" : "nav-tab")}>
            Site Analysis
          </NavLink>
          <NavLink to="/route-advisor" className={({ isActive }) => (isActive ? "nav-tab active" : "nav-tab")}>
            Route Analysis
          </NavLink>
          <NavLink to="/follow-up" className={({ isActive }) => (isActive ? "nav-tab active" : "nav-tab")}>
            Follow-up
          </NavLink>
        </div>
        <StaffAuthControls />
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<HomeMap />} />
          <Route path="/site-advisor" element={<SiteAdvisorWorkspace />} />
          <Route path="/route-advisor" element={<RouteAdvisorWorkspace />} />
          <Route path="/follow-up" element={<FollowUpPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
