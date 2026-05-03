# /etc/nginx/conf.d/qlib-url-blue.inc
# Blue slot — host ports 25022 (web) / 25023 (api)
# Sequential with newsro 25011-12 + mail-news 25021 in the tmanager.kr group.
set $qlib_web_url http://host.docker.internal:25022;
set $qlib_api_url http://host.docker.internal:25023;
