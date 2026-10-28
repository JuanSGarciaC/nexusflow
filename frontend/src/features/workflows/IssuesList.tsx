import { Alert, Code, List, Text } from "@mantine/core";
import { IconCircleCheck, IconAlertTriangle } from "@tabler/icons-react";

import type { Schemas } from "../../api/client";

export function IssuesList({ issues }: { issues: Schemas["ValidationIssue"][] | undefined }) {
  if (!issues) return null;
  if (issues.length === 0) {
    return (
      <Alert color="green" icon={<IconCircleCheck size={18} />} title="Definición válida">
        Se puede publicar.
      </Alert>
    );
  }
  return (
    <Alert color="orange" icon={<IconAlertTriangle size={18} />} title={`${issues.length} incidencia(s): no se puede publicar`}>
      <List size="sm">
        {issues.map((issue, i) => (
          <List.Item key={i}>
            <Code>{issue.location}</Code> <Text span fw={600}>{issue.code}</Text>: {issue.message}
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}
