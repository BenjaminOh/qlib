# /etc/nginx/conf.d/qlib-url-blue.inc
# Blue slot — host ports 25022 (web) / 25023 (api)
# We use the Docker bridge gateway IP (172.17.0.1) instead of
# host.docker.internal because nginx-waf-blue lives on its own network and
# host.docker.internal isn't auto-injected on Linux Docker. 172.17.0.1 maps
# to the host from any container that's on the default bridge.
set $qlib_web_url http://172.17.0.1:25022;
set $qlib_api_url http://172.17.0.1:25023;
