-- ComandaFácil · Pedidos online (cardápio digital)
-- Tabelas isoladas (prefixo cf_). Ninguém acessa as tabelas direto: RLS ligado, sem policies.
-- O acesso é só pelas funções cf_* abaixo (SECURITY DEFINER), que validam tudo no servidor.

create table if not exists public.cf_stores (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9-]{3,40}$'),
  name text not null,
  whatsapp text not null default '',
  token_hash text not null,
  accepting boolean not null default false,
  delivery_enabled boolean not null default true,
  pickup_enabled boolean not null default true,
  delivery_fee_cents integer not null default 0 check (delivery_fee_cents >= 0),
  min_order_cents integer not null default 0 check (min_order_cents >= 0),
  hours_text text not null default '',
  notice text not null default '',
  last_seen_at timestamptz,
  order_seq integer not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.cf_menu_items (
  store_id uuid not null references public.cf_stores(id) on delete cascade,
  pdv_product_id integer not null,
  name text not null,
  category text not null default 'Outros',
  description text not null default '',
  price_cents integer not null check (price_cents >= 0),
  available boolean not null default true,
  sort integer not null default 0,
  primary key (store_id, pdv_product_id)
);

create table if not exists public.cf_orders (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references public.cf_stores(id) on delete cascade,
  seq integer not null,
  code text not null unique,
  status text not null default 'novo'
    check (status in ('novo','aceito','preparo','pronto','saiu','entregue','recusado','cancelado')),
  kind text not null check (kind in ('entrega','retirada')),
  customer_name text not null,
  customer_phone text not null,
  address text not null default '',
  note text not null default '',
  payment_method text not null,
  change_for_cents integer,
  subtotal_cents integer not null,
  delivery_fee_cents integer not null default 0,
  total_cents integer not null,
  reject_reason text not null default '',
  client_ip text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_cf_orders_store_status on public.cf_orders(store_id, status, created_at);
create index if not exists idx_cf_orders_phone on public.cf_orders(store_id, customer_phone, created_at);
create index if not exists idx_cf_orders_ip on public.cf_orders(client_ip, created_at);

create table if not exists public.cf_order_items (
  id bigserial primary key,
  order_id uuid not null references public.cf_orders(id) on delete cascade,
  pdv_product_id integer not null,
  name text not null,
  unit_price_cents integer not null,
  quantity integer not null check (quantity between 1 and 99),
  note text not null default ''
);
create index if not exists idx_cf_order_items_order on public.cf_order_items(order_id);

alter table public.cf_stores enable row level security;
alter table public.cf_menu_items enable row level security;
alter table public.cf_orders enable row level security;
alter table public.cf_order_items enable row level security;
revoke all on public.cf_stores, public.cf_menu_items, public.cf_orders, public.cf_order_items from anon, authenticated;
revoke all on sequence public.cf_order_items_id_seq from anon, authenticated;

-- ------------------------------------------------------------------ utilidades internas
create or replace function public.cf_hash(p_token text) returns text
language sql immutable set search_path = '' as $$
  select encode(sha256(convert_to(coalesce(p_token, ''), 'utf8')), 'hex')
$$;

create or replace function public.cf_store_by_token(p_slug text, p_token text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  select id into v_id from public.cf_stores
   where slug = lower(coalesce(p_slug, '')) and token_hash = public.cf_hash(p_token);
  if v_id is null then
    raise exception 'acesso negado' using errcode = '28000';
  end if;
  update public.cf_stores set last_seen_at = now() where id = v_id;
  return v_id;
end $$;

create or replace function public.cf_client_ip() returns text
language plpgsql stable set search_path = '' as $$
declare h text;
begin
  begin
    h := current_setting('request.headers', true)::jsonb ->> 'x-forwarded-for';
  exception when others then h := null;
  end;
  return left(split_part(coalesce(h, ''), ',', 1), 64);
end $$;

-- ---------------------------------------------------------------------- API pública
create or replace function public.cf_get_menu(p_slug text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare s public.cf_stores; items jsonb;
begin
  select * into s from public.cf_stores where slug = lower(coalesce(p_slug, ''));
  if not found then return jsonb_build_object('ok', false, 'error', 'loja_nao_encontrada'); end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', pdv_product_id, 'name', name, 'category', category,
           'description', description, 'price_cents', price_cents) order by category, sort, name), '[]'::jsonb)
    into items from public.cf_menu_items where store_id = s.id and available;
  return jsonb_build_object('ok', true,
    'store', jsonb_build_object('name', s.name, 'whatsapp', s.whatsapp, 'accepting', s.accepting,
      'delivery_enabled', s.delivery_enabled, 'pickup_enabled', s.pickup_enabled,
      'delivery_fee_cents', s.delivery_fee_cents, 'min_order_cents', s.min_order_cents,
      'hours_text', s.hours_text, 'notice', s.notice),
    'items', items);
end $$;

create or replace function public.cf_create_order(p_slug text, p_order jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  s public.cf_stores; v_kind text; v_name text; v_phone text; v_addr text; v_pay text; v_note text;
  v_change integer; v_ip text; it jsonb; v_pid integer; v_qty integer; v_inote text;
  v_sub integer := 0; v_fee integer := 0; v_total integer; v_seq integer; v_code text; v_oid uuid;
  mi public.cf_menu_items; v_seen integer[] := '{}'; v_n integer := 0; v_lines jsonb := '[]'::jsonb;
begin
  select * into s from public.cf_stores where slug = lower(coalesce(p_slug, '')) for update;
  if not found then return jsonb_build_object('ok', false, 'error', 'loja_nao_encontrada'); end if;
  if not s.accepting then return jsonb_build_object('ok', false, 'error', 'loja_fechada'); end if;

  v_kind := p_order ->> 'kind';
  v_name := btrim(coalesce(p_order ->> 'name', ''));
  v_phone := regexp_replace(coalesce(p_order ->> 'phone', ''), '\D', '', 'g');
  v_addr := btrim(coalesce(p_order ->> 'address', ''));
  v_pay := p_order ->> 'payment';
  v_note := left(btrim(coalesce(p_order ->> 'note', '')), 200);
  if v_kind not in ('entrega', 'retirada') then return jsonb_build_object('ok', false, 'error', 'tipo_invalido'); end if;
  if v_kind = 'entrega' and not s.delivery_enabled then return jsonb_build_object('ok', false, 'error', 'entrega_indisponivel'); end if;
  if v_kind = 'retirada' and not s.pickup_enabled then return jsonb_build_object('ok', false, 'error', 'retirada_indisponivel'); end if;
  if char_length(v_name) < 2 or char_length(v_name) > 80 then return jsonb_build_object('ok', false, 'error', 'nome_invalido'); end if;
  if char_length(v_phone) < 10 or char_length(v_phone) > 13 then return jsonb_build_object('ok', false, 'error', 'telefone_invalido'); end if;
  if v_kind = 'entrega' and (char_length(v_addr) < 6 or char_length(v_addr) > 200) then
    return jsonb_build_object('ok', false, 'error', 'endereco_invalido'); end if;
  if v_kind = 'retirada' then v_addr := ''; end if;
  if v_pay not in ('Dinheiro', 'Pix', 'Cartão de débito', 'Cartão de crédito', 'Vale-refeição') then
    return jsonb_build_object('ok', false, 'error', 'pagamento_invalido'); end if;
  if jsonb_typeof(p_order -> 'items') <> 'array' or jsonb_array_length(p_order -> 'items') not between 1 and 40 then
    return jsonb_build_object('ok', false, 'error', 'itens_invalidos'); end if;

  v_ip := public.cf_client_ip();
  if (select count(*) from public.cf_orders where store_id = s.id and customer_phone = v_phone
        and status = 'novo' and created_at > now() - interval '10 minutes') >= 3
     or (v_ip <> '' and (select count(*) from public.cf_orders where client_ip = v_ip
        and created_at > now() - interval '1 hour') >= 15) then
    return jsonb_build_object('ok', false, 'error', 'muitos_pedidos');
  end if;

  for it in select * from jsonb_array_elements(p_order -> 'items') loop
    begin
      v_pid := (it ->> 'id')::integer; v_qty := (it ->> 'qty')::integer;
    exception when others then
      return jsonb_build_object('ok', false, 'error', 'itens_invalidos');
    end;
    if v_qty is null or v_qty < 1 or v_qty > 99 or v_pid = any(v_seen) then
      return jsonb_build_object('ok', false, 'error', 'itens_invalidos'); end if;
    v_seen := v_seen || v_pid;
    select * into mi from public.cf_menu_items where store_id = s.id and pdv_product_id = v_pid and available;
    if not found then return jsonb_build_object('ok', false, 'error', 'item_indisponivel', 'item', v_pid); end if;
    v_inote := left(btrim(coalesce(it ->> 'note', '')), 140);
    v_sub := v_sub + mi.price_cents * v_qty;
    v_lines := v_lines || jsonb_build_array(jsonb_build_object('pid', v_pid, 'name', mi.name,
                 'price', mi.price_cents, 'qty', v_qty, 'note', v_inote));
  end loop;

  if v_sub < s.min_order_cents then
    return jsonb_build_object('ok', false, 'error', 'pedido_minimo', 'min_order_cents', s.min_order_cents); end if;
  if v_kind = 'entrega' then v_fee := s.delivery_fee_cents; end if;
  v_total := v_sub + v_fee;
  v_change := null;
  if v_pay = 'Dinheiro' and (p_order ->> 'change_for_cents') is not null then
    begin v_change := (p_order ->> 'change_for_cents')::integer; exception when others then v_change := null; end;
    if v_change is not null and v_change < v_total then v_change := null; end if;
  end if;

  update public.cf_stores set order_seq = order_seq + 1 where id = s.id returning order_seq into v_seq;
  v_code := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 10));
  insert into public.cf_orders(store_id, seq, code, kind, customer_name, customer_phone, address, note,
      payment_method, change_for_cents, subtotal_cents, delivery_fee_cents, total_cents, client_ip)
    values (s.id, v_seq, v_code, v_kind, v_name, v_phone, v_addr, v_note, v_pay, v_change, v_sub, v_fee, v_total, v_ip)
    returning id into v_oid;
  insert into public.cf_order_items(order_id, pdv_product_id, name, unit_price_cents, quantity, note)
    select v_oid, (l ->> 'pid')::integer, l ->> 'name', (l ->> 'price')::integer, (l ->> 'qty')::integer, l ->> 'note'
      from jsonb_array_elements(v_lines) l;
  return jsonb_build_object('ok', true, 'code', v_code, 'seq', v_seq, 'total_cents', v_total,
                            'subtotal_cents', v_sub, 'delivery_fee_cents', v_fee);
end $$;

create or replace function public.cf_order_status(p_code text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare o public.cf_orders; s public.cf_stores; items jsonb;
begin
  select * into o from public.cf_orders where code = upper(btrim(coalesce(p_code, '')));
  if not found then return jsonb_build_object('ok', false, 'error', 'pedido_nao_encontrado'); end if;
  select * into s from public.cf_stores where id = o.store_id;
  select coalesce(jsonb_agg(jsonb_build_object('name', name, 'qty', quantity, 'note', note,
           'price_cents', unit_price_cents) order by id), '[]'::jsonb) into items
    from public.cf_order_items where order_id = o.id;
  return jsonb_build_object('ok', true, 'status', o.status, 'seq', o.seq, 'kind', o.kind,
    'store_name', s.name, 'whatsapp', s.whatsapp, 'total_cents', o.total_cents,
    'subtotal_cents', o.subtotal_cents, 'delivery_fee_cents', o.delivery_fee_cents,
    'payment', o.payment_method, 'reject_reason', o.reject_reason,
    'created_at', o.created_at, 'updated_at', o.updated_at, 'items', items);
end $$;

-- ------------------------------------------------------------------------ API do PDV
create or replace function public.cf_pdv_publish_menu(p_slug text, p_token text, p_items jsonb, p_settings jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_id uuid; it jsonb; v_ids integer[] := '{}'; v_pid integer; n integer := 0;
begin
  v_id := public.cf_store_by_token(p_slug, p_token);
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) > 500 then
    return jsonb_build_object('ok', false, 'error', 'itens_invalidos'); end if;
  for it in select * from jsonb_array_elements(p_items) loop
    v_pid := (it ->> 'id')::integer;
    v_ids := v_ids || v_pid;
    insert into public.cf_menu_items(store_id, pdv_product_id, name, category, description, price_cents, available, sort)
      values (v_id, v_pid, left(it ->> 'name', 120), left(coalesce(nullif(it ->> 'category', ''), 'Outros'), 60),
              left(coalesce(it ->> 'description', ''), 300), greatest(0, (it ->> 'price_cents')::integer),
              coalesce((it ->> 'available')::boolean, true), coalesce((it ->> 'sort')::integer, 0))
      on conflict (store_id, pdv_product_id) do update set name = excluded.name, category = excluded.category,
        description = excluded.description, price_cents = excluded.price_cents, available = excluded.available,
        sort = excluded.sort;
    n := n + 1;
  end loop;
  delete from public.cf_menu_items where store_id = v_id and not (pdv_product_id = any(v_ids));
  if p_settings is not null and jsonb_typeof(p_settings) = 'object' then
    update public.cf_stores set
      name = coalesce(nullif(left(p_settings ->> 'name', 80), ''), name),
      whatsapp = coalesce(regexp_replace(p_settings ->> 'whatsapp', '\D', '', 'g'), whatsapp),
      delivery_enabled = coalesce((p_settings ->> 'delivery_enabled')::boolean, delivery_enabled),
      pickup_enabled = coalesce((p_settings ->> 'pickup_enabled')::boolean, pickup_enabled),
      delivery_fee_cents = greatest(0, coalesce((p_settings ->> 'delivery_fee_cents')::integer, delivery_fee_cents)),
      min_order_cents = greatest(0, coalesce((p_settings ->> 'min_order_cents')::integer, min_order_cents)),
      hours_text = left(coalesce(p_settings ->> 'hours_text', hours_text), 200),
      notice = left(coalesce(p_settings ->> 'notice', notice), 300)
     where id = v_id;
  end if;
  return jsonb_build_object('ok', true, 'items', n);
end $$;

create or replace function public.cf_pdv_set_accepting(p_slug text, p_token text, p_flag boolean) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  v_id := public.cf_store_by_token(p_slug, p_token);
  update public.cf_stores set accepting = coalesce(p_flag, false) where id = v_id;
  return jsonb_build_object('ok', true, 'accepting', coalesce(p_flag, false));
end $$;

create or replace function public.cf_pdv_pull_orders(p_slug text, p_token text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare v_id uuid; s public.cf_stores; res jsonb;
begin
  v_id := public.cf_store_by_token(p_slug, p_token);
  select * into s from public.cf_stores where id = v_id;
  select coalesce(jsonb_agg(jsonb_build_object('code', o.code, 'seq', o.seq, 'kind', o.kind,
      'name', o.customer_name, 'phone', o.customer_phone, 'address', o.address, 'note', o.note,
      'payment', o.payment_method, 'change_for_cents', o.change_for_cents,
      'subtotal_cents', o.subtotal_cents, 'delivery_fee_cents', o.delivery_fee_cents,
      'total_cents', o.total_cents, 'created_at', o.created_at,
      'items', (select coalesce(jsonb_agg(jsonb_build_object('id', i.pdv_product_id, 'name', i.name,
                 'qty', i.quantity, 'note', i.note, 'price_cents', i.unit_price_cents) order by i.id), '[]'::jsonb)
                from public.cf_order_items i where i.order_id = o.id)) order by o.created_at), '[]'::jsonb)
    into res from public.cf_orders o where o.store_id = v_id and o.status = 'novo';
  return jsonb_build_object('ok', true, 'accepting', s.accepting, 'orders', res);
end $$;

create or replace function public.cf_pdv_set_status(p_slug text, p_token text, p_code text, p_status text,
                                                    p_reason text default '') returns jsonb
language plpgsql security definer set search_path = '' as $$
declare v_id uuid; o public.cf_orders; ok boolean;
begin
  v_id := public.cf_store_by_token(p_slug, p_token);
  select * into o from public.cf_orders where store_id = v_id and code = upper(btrim(coalesce(p_code, ''))) for update;
  if not found then return jsonb_build_object('ok', false, 'error', 'pedido_nao_encontrado'); end if;
  if o.status = p_status then return jsonb_build_object('ok', true, 'status', o.status); end if;
  ok := case o.status
    when 'novo'    then p_status in ('aceito', 'recusado')
    when 'aceito'  then p_status in ('preparo', 'pronto', 'saiu', 'entregue', 'cancelado')
    when 'preparo' then p_status in ('pronto', 'saiu', 'entregue', 'cancelado')
    when 'pronto'  then p_status in ('saiu', 'entregue', 'cancelado')
    when 'saiu'    then p_status in ('entregue', 'cancelado')
    else false end;
  if not ok then
    return jsonb_build_object('ok', false, 'error', 'transicao_invalida', 'status', o.status); end if;
  update public.cf_orders set status = p_status, updated_at = now(),
         reject_reason = case when p_status in ('recusado', 'cancelado') then left(coalesce(p_reason, ''), 200)
                              else reject_reason end
   where id = o.id;
  return jsonb_build_object('ok', true, 'status', p_status);
end $$;

-- só as funções da API ficam acessíveis; helpers internos não
revoke all on function public.cf_hash(text), public.cf_store_by_token(text, text), public.cf_client_ip() from public, anon, authenticated;
revoke all on function public.cf_get_menu(text), public.cf_create_order(text, jsonb), public.cf_order_status(text),
  public.cf_pdv_publish_menu(text, text, jsonb, jsonb), public.cf_pdv_set_accepting(text, text, boolean),
  public.cf_pdv_pull_orders(text, text), public.cf_pdv_set_status(text, text, text, text, text) from public;
grant execute on function public.cf_get_menu(text), public.cf_create_order(text, jsonb), public.cf_order_status(text),
  public.cf_pdv_publish_menu(text, text, jsonb, jsonb), public.cf_pdv_set_accepting(text, text, boolean),
  public.cf_pdv_pull_orders(text, text), public.cf_pdv_set_status(text, text, text, text, text) to anon, authenticated;
