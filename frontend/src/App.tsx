import { NavLink, Route, Routes } from "react-router-dom";
import { HomeMap } from "./pages/HomeMap";
import { SiteAdvisorWorkspace } from "./pages/SiteAdvisorWorkspace";
import { RouteAdvisorWorkspace } from "./pages/RouteAdvisorWorkspace";
import { FollowUpPage } from "./pages/FollowUpPage";

function App() {
  return (
    <div className="app-shell">
      <nav className="top-nav">
        <span className="brand">Food-Access Planning Workspace</span>
        <NavLink to="/" end>
          Overview
        </NavLink>
        <NavLink to="/site-advisor">Site Advisor</NavLink>
        <NavLink to="/route-advisor">Route Advisor</NavLink>
        <NavLink to="/follow-up">Follow-up</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<HomeMap />} />
          <Route path="/site-advisor" element={<SiteAdvisorWorkspace />} />
          <Route path="/route-advisor" element={<RouteAdvisorWorkspace />} />
          <Route path="/follow-up" element={<FollowUpPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
