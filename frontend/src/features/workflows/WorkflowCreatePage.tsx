import { Button, Group, JsonInput, Select, Stack, TextInput, Textarea } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { IssuesList } from "./IssuesList";
import { TEMPLATES, parseJson, pretty } from "./templates";

type DefinitionIn = Schemas["WorkflowDefinition-Input"];

export function WorkflowCreatePage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState<string>("transform");
  const [text, setText] = useState(pretty(TEMPLATES.transform?.definition));
  const [issues, setIssues] = useState<Schemas["ValidationIssue"][]>();

  const parsed = parseJson(text);

  const validate = useMutation({
    mutationFn: (definition: DefinitionIn) =>
      unwrap(api.POST("/api/v1/workflows/validate", { body: { definition } })),
    onSuccess: (result) => setIssues(result.issues),
  });
  const create = useMutation({
    mutationFn: (definition: DefinitionIn) =>
      unwrap(
        api.POST("/api/v1/workflows", {
          body: { name, description: description || null, definition },
        }),
      ),
    onSuccess: (created) => navigate(`/workflows/${created.id}`),
  });

  return (
    <RequirePermission permission="workflows:write">
      <PageHeader title="Nuevo flujo" description="Se crea como borrador v1. Publícalo para poder ejecutarlo." />
      <Stack maw={900}>
        <TextInput label="Nombre" required maxLength={120} value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Textarea label="Descripción" maxLength={2000} value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
        <Select
          label="Plantilla"
          data={Object.entries(TEMPLATES).map(([value, t]) => ({ value, label: t.label }))}
          value={template}
          onChange={(v) => {
            if (!v) return;
            setTemplate(v);
            setText(pretty(TEMPLATES[v]?.definition));
            setIssues(undefined);
          }}
        />
        <JsonInput
          label="Definición (JSON)"
          description="Ver docs/workflow-dsl.md"
          autosize
          minRows={14}
          maxRows={30}
          formatOnBlur
          value={text}
          onChange={(v) => {
            setText(v);
            setIssues(undefined);
          }}
          error={parsed.ok ? undefined : parsed.message}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
        />
        <IssuesList issues={issues} />
        <ErrorAlert error={validate.error ?? create.error} />
        <Group>
          <Button
            variant="default"
            disabled={!parsed.ok}
            loading={validate.isPending}
            onClick={() => parsed.ok && validate.mutate(parsed.value as DefinitionIn)}
          >
            Validar
          </Button>
          <Button
            disabled={!parsed.ok || !name.trim()}
            loading={create.isPending}
            onClick={() => parsed.ok && create.mutate(parsed.value as DefinitionIn)}
          >
            Crear borrador
          </Button>
        </Group>
      </Stack>
    </RequirePermission>
  );
}

