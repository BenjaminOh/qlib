# /etc/nginx/conf.d/qlib-url-green.inc
# Green slot — host ports 25024 (web) / 25025 (api)
set $qlib_web_url http://host.docker.internal:25024;
set $qlib_api_url http://host.docker.internal:25025;
