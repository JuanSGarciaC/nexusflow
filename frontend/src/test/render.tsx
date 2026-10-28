import { render } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";

import { routes } from "../App";
import { Providers, createQueryClient } from "../providers";

/** Renderiza la app completa (providers + rutas reales) en una ruta inicial. */
export function renderApp(path: string) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false, refetchInterval: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return {
    router,
    ...render(
      <Providers queryClient={queryClient}>
        <RouterProvider router={router} />
      </Providers>,
    ),
  };
}
