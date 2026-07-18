do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'better_auth_app') then
    create role better_auth_app nologin bypassrls;
  end if;
end
$$;

grant connect on database postgres to better_auth_app;
grant usage on schema public to better_auth_app;
grant select, insert, update, delete on table
  public."user",
  public.session,
  public.account,
  public.verification
to better_auth_app;
