import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "./api/client";
import { AuthProvider } from "./auth/AuthProvider";

export const theme = createTheme({ primaryColor: "blue", defaultRadius: "md" });

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
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
