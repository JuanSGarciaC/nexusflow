import { Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-start" mb="lg" wrap="wrap">
      <Stack gap={4}>
        <Title order={2}>{title}</Title>
        {description && <Text c="dimmed" size="sm">{description}</Text>}
      </Stack>
      {actions && <Group gap="sm">{actions}</Group>}
    </Group>
  );
}
