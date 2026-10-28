import { AppShell, Burger, Button, Group, Loader, NavLink, Stack, Text, Title } from "@mantine/core";
import { Suspense } from "react";
import { useDisclosure } from "@mantine/hooks";
import {
  IconChartBar,
  IconHistory,
  IconKey,
  IconLogout,
  IconRoute,
  IconShieldCheck,
  IconUsers,
} from "@tabler/icons-react";
import { NavLink as RouterLink, Outlet, useLocation } from "react-router";

import { useAuth } from "../auth/AuthProvider";

const NAV = [
  { to: "/", label: "Panel", icon: IconChartBar, permission: "metrics:read", exact: true },
  { to: "/workflows", label: "Flujos", icon: IconRoute, permission: "workflows:read" },
  { to: "/runs", label: "Ejecuciones", icon: IconHistory, permission: "runs:read" },
  { to: "/credentials", label: "Credenciales", icon: IconKey, permission: "credentials:manage" },
  { to: "/audit", label: "Auditoría", icon: IconShieldCheck, permission: "audit:read" },
  { to: "/users", label: "Usuarios", icon: IconUsers, permission: "users:read" },
] as const;

export function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();
  const { state, logout, can } = useAuth();
  const location = useLocation();
  const user = state.status === "authenticated" ? state.user : null;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 230, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Abrir menú" />
            <Title order={4}>NexusFlow</Title>
          </Group>
          {user && (
            <Group gap="sm">
              <Text size="sm" c="dimmed" visibleFrom="xs">
                {user.email} · {user.role}
              </Text>
              <Button variant="subtle" size="xs" leftSection={<IconLogout size={16} />} onClick={() => void logout()}>
                Salir
              </Button>
            </Group>
          )}
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="sm" aria-label="Navegación principal">
        <Stack gap={2}>
          {NAV.filter((item) => can(item.permission)).map((item) => {
            const active =
              "exact" in item ? location.pathname === item.to : location.pathname.startsWith(item.to);
            return (
              <NavLink
                key={item.to}
                component={RouterLink}
                to={item.to}
                label={item.label}
                leftSection={<item.icon size={18} />}
                active={active}
                onClick={close}
              />
            );
          })}
        </Stack>
      </AppShell.Navbar>
      <AppShell.Main>
        <Suspense fallback={<Loader aria-label="Cargando página" />}>
          <Outlet />
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
