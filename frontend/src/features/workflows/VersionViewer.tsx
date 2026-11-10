import { Skeleton, Stack, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JsonView } from "../../components/JsonView";
import { LazyFlowCanvas } from "../canvas/LazyFlowCanvas";

/** Clave compartida con el detalle de ejecución: una versión publicada es inmutable. */
export function versionQuery(workflowId: string, version: number) {
  return {
    queryKey: ["workflow", workflowId, "version", version],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/workflows/{workflow_id}/versions/{version}", {
          params: { path: { workflow_id: workflowId, version } },
        }),
      ),
  };
}

/** Una versión del flujo en solo lectura: lienzo y JSON. */
export function VersionViewer({ workflowId, version }: { workflowId: string; version: number }) {
  const detail = useQuery(versionQuery(workflowId, version));
  if (detail.error) return <ErrorAlert error={detail.error} />;
  if (!detail.data) return <Skeleton h={300} radius="lg" />;
  return (
    <Stack mt="lg">
      <Title order={4}>Versión {detail.data.version}</Title>
      <LazyFlowCanvas definition={detail.data.definition} height={340} />
      <JsonView value={detail.data.definition} maxHeight={420} />
    </Stack>
  );
}
