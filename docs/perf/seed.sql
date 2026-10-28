-- Volumen sintético para medir rendimiento (ver docs/performance.md). Datos ficticios.
INSERT INTO users (id, email, password_hash, role, is_active) VALUES ('00000000-0000-4000-8000-000000000001','perf@example.com','x','admin',true);
INSERT INTO workflows (id, name, status, created_by)
SELECT ('00000000-0000-4000-9000-' || lpad(g::text,12,'0'))::uuid, 'wf-'||g, 'active', '00000000-0000-4000-8000-000000000001' FROM generate_series(1,20) g;
INSERT INTO workflow_versions (id, workflow_id, version, revision, definition, definition_hash, created_by, updated_by, published_at)
SELECT gen_random_uuid(), w.id, 1, 1, '{"start":"a","steps":[]}'::jsonb, md5(w.id::text)||md5(w.id::text), w.created_by, w.created_by, now() - interval '31 days' FROM workflows w;
-- 50 000 ejecuciones en 30 días; ~97 % terminadas
INSERT INTO workflow_runs (id, workflow_id, version_id, status, trigger_type, input_hash, input, created_at, started_at, finished_at, deadline_at, error)
SELECT gen_random_uuid(), v.workflow_id, v.id,
  CASE WHEN r < 0.80 THEN 'succeeded' WHEN r < 0.93 THEN 'failed' WHEN r < 0.95 THEN 'cancelled' WHEN r < 0.97 THEN 'timed_out' WHEN r < 0.985 THEN 'running' WHEN r < 0.995 THEN 'pending' ELSE 'needs_review' END,
  'manual', md5(g::text)||md5(g::text), '{}'::jsonb, t, t + interval '50 ms', t + (random()*3000)::int * interval '1 ms', t + interval '48 hours',
  CASE WHEN r >= 0.80 AND r < 0.93 THEN jsonb_build_object('class','http_503','message','Service Unavailable','step_id','a') END
FROM (SELECT g, random() r, now() - random()*interval '30 days' t, (g % 20) + 1 wf FROM generate_series(1,50000) g) x
JOIN workflow_versions v ON v.workflow_id = ('00000000-0000-4000-9000-' || lpad(x.wf::text,12,'0'))::uuid;
-- 3 pasos por ejecución
INSERT INTO step_runs (id, run_id, step_id, step_type, sequence, status, side_effect, idempotency_key, attempt_count, max_attempts, timeout_seconds, created_at, started_at, finished_at, next_attempt_at)
SELECT gen_random_uuid(), r.id, 's'||s, CASE s WHEN 1 THEN 'http_request' WHEN 2 THEN 'condition' ELSE 'transform' END, s,
  CASE WHEN r.status IN ('pending','running') AND s = 1 THEN r.status::text ELSE 'succeeded' END,
  'none', r.id||':s'||s, 1, 3, 30, r.created_at, r.started_at, r.finished_at,
  CASE WHEN r.status = 'pending' AND s = 1 THEN now() + interval '1 hour' END
FROM workflow_runs r, generate_series(1,3) s;
INSERT INTO step_attempts (step_run_id, attempt_number, started_at, finished_at, duration_ms, outcome, error_class)
SELECT sr.id, 1, sr.started_at, sr.finished_at, (random()*800)::int, CASE WHEN random() < 0.1 THEN 'failed' ELSE 'succeeded' END, CASE WHEN random() < 0.1 THEN 'http_503' END
FROM step_runs sr WHERE sr.started_at IS NOT NULL;
INSERT INTO audit_log (occurred_at, actor_user_id, action, resource_type, resource_id, details)
SELECT now() - random()*interval '30 days', '00000000-0000-4000-8000-000000000001',
  (ARRAY['auth.login.succeeded','auth.login.failed','workflow.published','run.created','user.role_changed'])[1 + (g % 5)], 'run', g::text, '{}'::jsonb
FROM generate_series(1,100000) g;
ANALYZE;
SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname IN ('workflow_runs','step_runs','step_attempts','audit_log') ORDER BY 1;
