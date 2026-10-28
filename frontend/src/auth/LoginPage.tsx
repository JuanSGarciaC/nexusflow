import { Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";

import { ErrorAlert } from "../components/ErrorAlert";
import { useAuth } from "./AuthProvider";

export function LoginPage() {
  const { state, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<unknown>(null);
  const form = useForm({
    mode: "uncontrolled",
    initialValues: { email: "", password: "" },
    validate: {
      email: (v) => (/^\S+@\S+$/.test(v) ? null : "Email no válido"),
      password: (v) => (v.length > 0 ? null : "Obligatoria"),
    },
  });

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (state.status === "authenticated") return <Navigate to={from} replace />;

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="sm" p="xl" radius="md" w={380}>
        <form
          onSubmit={form.onSubmit(async (values) => {
            setError(null);
            try {
              await login(values.email, values.password);
              navigate(from, { replace: true });
            } catch (e) {
              setError(e);
            }
          })}
        >
          <Stack>
            <div>
              <Title order={2}>NexusFlow</Title>
              <Text c="dimmed" size="sm">
                Automatización de procesos
              </Text>
            </div>
            <ErrorAlert error={error} title="No se pudo iniciar sesión" />
            <TextInput label="Email" type="email" autoComplete="username" required key={form.key("email")} {...form.getInputProps("email")} />
            <PasswordInput label="Contraseña" autoComplete="current-password" required key={form.key("password")} {...form.getInputProps("password")} />
            <Button type="submit" loading={form.submitting}>
              Entrar
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
