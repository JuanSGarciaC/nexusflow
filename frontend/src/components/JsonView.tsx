import { Code } from "@mantine/core";

/** JSON formateado y escapado por React (nunca se inyecta como HTML). */
export function JsonView({ value, maxHeight = 320 }: { value: unknown; maxHeight?: number }) {
  return (
    <Code block style={{ maxHeight, overflow: "auto", whiteSpace: "pre" }}>
      {value === undefined ? "—" : JSON.stringify(value, null, 2)}
    </Code>
  );
}
