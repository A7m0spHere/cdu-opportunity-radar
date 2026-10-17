# Include inside a reviewed server block; no filesystem serving directives.
if ($draft_host_allowed = 0) { return 403; }
if ($request_method !~ ^(GET|HEAD)$) { return 405; }
limit_req zone=peer_rate burst=20 nodelay;
limit_req zone=service_rate burst=20 nodelay;
limit_req_status 429;
limit_req_log_level notice;
limit_conn peer_connections 4;
limit_conn service_connections 8;
limit_conn_status 429;
limit_conn_log_level notice;
add_header Cache-Control "no-store, max-age=0" always;
add_header Retry-After $retry_after always;
add_header Allow $read_only_allow always;
error_page 502 504 =503 @upstream_unavailable;
location @upstream_unavailable { return 503 "Service temporarily unavailable\n"; }
location = / { include gateway-upstream.inc; }
location = /app.css { include gateway-upstream.inc; }
location = /app.js { include gateway-upstream.inc; }
location = /favicon.svg { include gateway-upstream.inc; }
location = /api/notices { include gateway-upstream.inc; }
location = /api/notices/v2 { include gateway-upstream.inc; }
location = /api/notices/v3 { include gateway-upstream.inc; }
location = /feed.xml { include gateway-upstream.inc; }
location = /current.xml { include gateway-upstream.inc; }
location = /history.xml { include gateway-upstream.inc; }
location = /rss/v1/all.xml { include gateway-upstream.inc; }
location = /rss/v1/featured.xml { include gateway-upstream.inc; }
location = /approved.xml { include gateway-upstream.inc; }
location = /health { include gateway-upstream.inc; }
location / { return 404; }
