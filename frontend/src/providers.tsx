import { MantineProvider, createTheme, type MantineColorsTuple } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "./api/client";
import { AuthProvider } from "./auth/AuthProvider";

// Diez tonos alrededor de --nf-accent-a (tokens.css); Mantine exige la tupla completa.
const nexus: MantineColorsTuple = [
  "#f1edff", "#ddd4ff", "#b9a7ff", "#9478ff", "#7c5cff",
  "#6a45fb", "#5f38ef", "#4f2bd4", "#4526bd", "#391da6",
];

export const theme = createTheme({
  primaryColor: "nexus",
  primaryShade: 4,
  colors: { nexus },
  defaultRadius: "md",
  // Pila del sistema: una fuente autohospedada no compensaba su peso (ver ADR-0013).
  fontFamily: '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif',
  headings: { fontFamily: '"Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif', fontWeight: "650" },
});

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Un 4xx no se arregla reintentando: solo se reintentan errores de red o 5xx.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
        refetchOnWindowFocus: false,
        staleTime: 5_000,
      },
    },
  });
}

export function Providers({ children, queryClient }: { children: ReactNode; queryClient: QueryClient }) {
  return (
    <MantineProvider theme={theme} forceColorScheme="dark">
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
