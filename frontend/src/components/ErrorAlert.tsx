import { Alert, Code, List, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

import { ApiError } from "../api/client";

/** Muestra un error de la API (RFC 9457) con sus errores de campo y el request_id. */
export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  return (
    <Alert color="red" icon={<IconAlertTriangle size={18} />} title={title ?? apiError?.title ?? "Error"} role="alert">
      <Text size="sm">{apiError ? (apiError.detail ?? apiError.title) : String((error as Error).message ?? error)}</Text>
      {apiError && apiError.fieldErrors.length > 0 && (
        <List size="sm" mt="xs">
          {apiError.fieldErrors.map((e, i) => (
            <List.Item key={i}>
              <Code>{e.loc.slice(1).join(".") || "body"}</Code> {e.msg}
            </List.Item>
          ))}
        </List>
      )}
      {apiError?.requestId && (
        <Text size="xs" c="dimmed" mt="xs">
          Referencia: <Code>{apiError.requestId}</Code>
        </Text>
      )}
    </Alert>
  );
}
