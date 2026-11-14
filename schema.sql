-- Caché de respuestas externas (la API gratuita de disponibilidad tiene pocas consultas al día)
create table if not exists public.cine_cache (
  key text primary key,
  data jsonb not null,
  fetched_at timestamptz not null default now()
);
alter table public.cine_cache enable row level security;  -- sin políticas: solo la clave de servicio accede
