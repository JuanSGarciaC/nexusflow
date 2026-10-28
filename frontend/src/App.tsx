import { lazy } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { LoginPage } from "./auth/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { useAuth } from "./auth/AuthProvider";
import { AppLayout } from "./components/AppLayout";
// Cada página se carga bajo demanda: la librería de gráficos solo se descarga en el panel.
const AuditPage = lazy(() => import("./features/audit/AuditPage").then((m) => ({ default: m.AuditPage })));
const CredentialsPage = lazy(() => import("./features/credentials/CredentialsPage").then((m) => ({ default: m.CredentialsPage })));
const DashboardPage = lazy(() => import("./features/dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const RunDetailPage = lazy(() => import("./features/runs/RunDetailPage").then((m) => ({ default: m.RunDetailPage })));
const RunListPage = lazy(() => import("./features/runs/RunListPage").then((m) => ({ default: m.RunListPage })));
const UsersPage = lazy(() => import("./features/users/UsersPage").then((m) => ({ default: m.UsersPage })));
const WorkflowCreatePage = lazy(() => import("./features/workflows/WorkflowCreatePage").then((m) => ({ default: m.WorkflowCreatePage })));
const WorkflowDetailPage = lazy(() => import("./features/workflows/WorkflowDetailPage").then((m) => ({ default: m.WorkflowDetailPage })));
const WorkflowListPage = lazy(() => import("./features/workflows/WorkflowListPage").then((m) => ({ default: m.WorkflowListPage })));

/** Página de inicio según permisos: no todos los roles ven el panel de métricas. */
function Home() {
  const { can } = useAuth();
  return can("metrics:read") ? <DashboardPage /> : <Navigate to="/workflows" replace />;
}

export const routes = [
  { path: "/login", element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Home /> },
      { path: "workflows", element: <WorkflowListPage /> },
      { path: "workflows/new", element: <WorkflowCreatePage /> },
      { path: "workflows/:workflowId", element: <WorkflowDetailPage /> },
      { path: "runs", element: <RunListPage /> },
      { path: "runs/:runId", element: <RunDetailPage /> },
      { path: "credentials", element: <CredentialsPage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "users", element: <UsersPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];

const router = createBrowserRouter(routes);

export function App() {
  return <RouterProvider router={router} />;
}
