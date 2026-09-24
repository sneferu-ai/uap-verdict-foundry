// Route table (spec §5.1). /ui/* is the canonical space; the layout route
// hosts every authenticated screen.

import { useEffect, useRef } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { LoginRedirect, ProtectedRoute } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { TermsPage } from "./pages/TermsPage";
import { CasesDashboardPage } from "./pages/CasesDashboardPage";
import { NewCasePage } from "./pages/NewCasePage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { DeleteConfirmPage } from "./pages/DeleteConfirmPage";
import { ReportPage } from "./pages/ReportPage";
import { FictionPage } from "./pages/FictionPage";
import { BenchmarkPage } from "./pages/BenchmarkPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { WhyPage } from "./pages/WhyPage";

/** Spec §11: on route change, focus moves to <main> (or the page heading
    on main-less pages like login/terms) and the viewport returns to the
    top. Initial mount keeps the browser's natural focus. */
function RouteFocus() {
  const { pathname } = useLocation();
  const initial = useRef(true);
  useEffect(() => {
    if (initial.current) {
      initial.current = false;
      return;
    }
    window.scrollTo(0, 0);
    const target = document.querySelector<HTMLElement>("#main-content, h1");
    target?.focus({ preventScroll: true });
  }, [pathname]);
  return null;
}

export function App() {
  const location = useLocation();
  return (
    // Keyed by path: each route change replays the one orientation
    // transition (DESIGN.md §7 / brand motion direction).
    <div key={location.pathname} className="page-enter">
      <RouteFocus />
      <Routes>
        <Route
          path="/ui/login"
          element={
            <LoginRedirect>
              <LoginPage />
            </LoginRedirect>
          }
        />
        <Route path="/ui/terms" element={<TermsPage />} />
        <Route element={<Layout />}>
          <Route
            path="/ui/cases"
            element={
              <ProtectedRoute>
                <CasesDashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/cases/new"
            element={
              <ProtectedRoute>
                <NewCasePage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/cases/:caseId"
            element={
              <ProtectedRoute>
                <CaseDetailPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/cases/:caseId/delete/confirm"
            element={
              <ProtectedRoute>
                <DeleteConfirmPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/cases/:caseId/report"
            element={
              <ProtectedRoute>
                <ReportPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/cases/:caseId/fiction"
            element={
              <ProtectedRoute>
                <FictionPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/benchmark"
            element={
              <ProtectedRoute>
                <BenchmarkPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ui/why"
            element={
              <ProtectedRoute>
                <WhyPage />
              </ProtectedRoute>
            }
          />
          <Route path="/ui" element={<Navigate to="/ui/cases" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </div>
  );
}
