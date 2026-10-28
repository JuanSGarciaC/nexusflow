import "@mantine/core/styles.css";
import "@mantine/charts/styles.css";
import "@mantine/notifications/styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { Providers, createQueryClient } from "./providers";

const root = document.getElementById("root");
if (!root) throw new Error("Falta #root en index.html");

createRoot(root).render(
  <StrictMode>
    <Providers queryClient={createQueryClient()}>
      <App />
    </Providers>
  </StrictMode>,
);
