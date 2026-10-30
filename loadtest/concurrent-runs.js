// Carga sobre ejecuciones concurrentes (k6). Ver loadtest/README.md.
//
// Cada iteración hace lo que haría un cliente real: crea una ejecución (202) y consulta su
// estado hasta que termina. Se mide la latencia de la API (crear) y el tiempo de punta a
// punta (crear -> terminada), que incluye la cola de Celery y el worker.
//
// El flujo es solo `transform` (sin red externa) a propósito: así se mide el motor y no la
// latencia de un tercero.
import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const PASSWORD = __ENV.NEXUSFLOW_DEMO_PASSWORD;
const RATE = Number(__ENV.RATE || 10); // ejecuciones nuevas por segundo
const DURATION = __ENV.DURATION || "30s";
const TERMINAL = ["succeeded", "failed", "cancelled", "timed_out"];

const runCompletion = new Trend("run_completion_ms", true);
const runSucceeded = new Rate("runs_succeeded");
const runsCreated = new Counter("runs_created");

export const options = {
  scenarios: {
    concurrent_runs: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  thresholds: {
    // Umbrales holgados a propósito (ver loadtest/README.md): detectan regresiones graves
    // sin volverse intermitentes en un runner compartido.
    "http_req_duration{name:create_run}": [`p(95)<${__ENV.MAX_CREATE_P95_MS || 500}`],
    run_completion_ms: [`p(95)<${__ENV.MAX_COMPLETION_P95_MS || 3000}`],
    runs_succeeded: ["rate==1"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

function login(email) {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) fail(`login de ${email} falló: ${res.status}`);
  return { Authorization: `Bearer ${res.json("access_token")}`, "Content-Type": "application/json" };
}

export function setup() {
  if (!PASSWORD) fail("Define NEXUSFLOW_DEMO_PASSWORD (la del seed-demo).");
  const editor = login("demo-editor@example.com");
  const operator = login("demo-operator@example.com");
  const definition = {
    inputs: [{ name: "amount", type: "number" }],
    start: "calc",
    steps: [{ id: "calc", type: "transform", config: { output: { total: "{{ input.amount }}" } } }],
  };
  const created = http.post(
    `${BASE_URL}/api/v1/workflows`,
    JSON.stringify({ name: `carga-${Date.now()}`, definition }),
    { headers: editor },
  );
  if (created.status !== 201) fail(`no se pudo crear el flujo: ${created.status} ${created.body}`);
  const workflowId = created.json("id");
  const published = http.post(
    `${BASE_URL}/api/v1/workflows/${workflowId}/publish`,
    JSON.stringify({ expected_revision: 1 }),
    { headers: editor },
  );
  if (published.status !== 200 && published.status !== 201) fail(`no se pudo publicar: ${published.status}`);
  return { workflowId, operator };
}

export default function (data) {
  const created = http.post(
    `${BASE_URL}/api/v1/workflows/${data.workflowId}/runs`,
    JSON.stringify({ input: { amount: __ITER } }),
    { headers: data.operator, tags: { name: "create_run" } },
  );
  const accepted = check(created, { "ejecución aceptada (202)": (r) => r.status === 202 });
  if (!accepted) {
    runSucceeded.add(false);
    return;
  }
  runsCreated.add(1);
  const runId = created.json("id");
  const started = Date.now();
  let status = "pending";
  while (!TERMINAL.includes(status) && Date.now() - started < 30000) {
    sleep(0.05);
    const polled = http.get(`${BASE_URL}/api/v1/runs/${runId}`, {
      headers: data.operator,
      tags: { name: "get_run" },
    });
    if (polled.status === 200) status = polled.json("status");
  }
  runCompletion.add(Date.now() - started);
  runSucceeded.add(status === "succeeded");
}
